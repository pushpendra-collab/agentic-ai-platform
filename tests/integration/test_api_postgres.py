"""Phase 6 against real PostgreSQL 16 (+ Redis 7 when TEST_REDIS_URL is set), as the
least-privilege app role so row-level security applies:

* the HTTP API end to end: readiness, the approval flow with request correlation into
  `audit_log`, tenant isolation of conversations, approvals, memory and audit;
* maintenance on real SQL: expired rows removed, others kept, batches, two runners
  at once, repeat runs, statistics in `audit_log`;
* an executed approval never runs again after its idempotency record is purged;
* migration 0005 down/up.

Same setup as test_postgres.py (TEST_DATABASE_URL / TEST_DATABASE_APP_URL).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

import pytest
from alembic import command
from sqlalchemy import text

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import AgentDependencies, build_dependencies
from agentic_platform.core.config import (
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    LLMSettings,
    MaintenanceSettings,
    MemorySettings,
    RedisSettings,
)
from agentic_platform.db import Database
from agentic_platform.db.migrations import alembic_config
from agentic_platform.maintenance import MaintenanceRunner, build_maintenance_runner, default_jobs
from agentic_platform.security.policy import ApprovalPolicy
from agentic_platform.services.clock import ClockService
from agentic_platform.services.idempotency import IdempotencyService
from agentic_platform.tools.memory import REMEMBER_FACT
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token
from tests.conftest import FIXED_NOW, principal, understanding
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.postgres

T = TypeVar("T")

FACT_ARGS = '{"key": "reply_language", "content": "Prefers Hindi replies."}'
RLS_TABLES = (
    "conversation_thread",
    "approval_request",
    "audit_log",
    "memory_item",
    "agent_checkpoints",
    "agent_checkpoint_blobs",
    "agent_checkpoint_writes",
)


@pytest.fixture
def owner_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def app_url(owner_url: str) -> str:
    url = os.environ.get("TEST_DATABASE_APP_URL")
    if not url:
        pytest.skip("TEST_DATABASE_APP_URL not set")
    return url


@pytest.fixture
def settings_pg(app_url: str) -> AppSettings:
    redis_url = os.environ.get("TEST_REDIS_URL")
    return AppSettings(
        env="test",
        llm=LLMSettings(anthropic_api_key="test-key"),
        database=DatabaseSettings(url=app_url),
        redis=RedisSettings(url=redis_url, key_prefix=f"agentic-test-{uuid.uuid4().hex[:12]}")
        if redis_url
        else RedisSettings(),
        auth=AuthSettings(token_secret=TOKEN_SECRET),
        memory=MemorySettings(agent_fact_writes="approval"),
    )


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def pg_agent(settings: AppSettings, *script: object) -> Agent:
    deps = build_dependencies(
        settings,
        llm=ScriptedLLM(script=list(script)),
        clock=ClockService(now_utc=lambda: FIXED_NOW),
    )
    assert deps.database is not None
    return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database))


def with_db(url: str, fn: Callable[[Database], Awaitable[T]]) -> T:
    async def body() -> T:
        db = Database(DatabaseSettings(url=url))
        try:
            return await fn(db)
        finally:
            await db.dispose()

    return asyncio.run(body())


def owner_sql(owner_url: str, sql: str, **params: Any) -> list[Any]:
    async def body(db: Database) -> list[Any]:
        async with db.unit_of_work() as uow:
            result = await uow.session.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []

    return with_db(owner_url, body)


def with_deps(settings: AppSettings, fn: Callable[[AgentDependencies], Awaitable[T]]) -> T:
    async def body() -> T:
        deps = build_dependencies(settings, llm=ScriptedLLM())
        try:
            return await fn(deps)
        finally:
            if deps.database is not None:
                await deps.database.dispose()
            if deps.cache.redis is not None:
                await deps.cache.redis.dispose()

    return asyncio.run(body())


# --- HTTP API ---------------------------------------------------------------------------------


def test_readiness_with_real_dependencies(settings_pg: AppSettings) -> None:
    agent = pg_agent(settings_pg)
    with api_client(settings_pg, agent) as client:
        response = client.get("/ready")
    assert response.status_code == 200, response.text
    checks = response.json()["checks"]
    assert checks["database"]["status"] == "ok"  # reachable and at the migration head
    expected_redis = "ok" if settings_pg.redis.configured else "not_configured"
    assert checks["redis"]["status"] == expected_redis
    assert "agentic_app" not in response.text and "localhost" not in response.text


def test_approval_flow_over_http_is_correlated_in_the_audit_log(
    settings_pg: AppSettings, owner_url: str
) -> None:
    tenant = uid("acme")
    agent = pg_agent(
        settings_pg,
        understanding(intent="remember_preference", tool_calls=[(REMEMBER_FACT, FACT_ARGS)]),
        "Done, I'll remember that.",
    )
    asha = bearer(token(agent, tenant, "asha"))
    auditor = bearer(token(agent, tenant, "aud", ["auditor"]))
    with api_client(settings_pg, agent) as client:
        paused = client.post(
            "/api/v1/agent/turn",
            json={"message": "Remember I prefer Hindi replies", "thread_id": "t-hitl"},
            headers=asha,
        )
        assert paused.status_code == 202, paused.text
        request_id = paused.headers["x-request-id"]
        approval_id = paused.json()["approval_required"]["approval_id"]
        assert paused.json()["request_id"] == request_id

        # Private request: another member of the tenant cannot see it (RLS + service rule).
        ravi = bearer(token(agent, tenant, "ravi"))
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=ravi).status_code == 404

        decided = client.post(
            f"/api/v1/approvals/{approval_id}/approve", json={"resume": True}, headers=asha
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["next_action"] == "resumed"
        assert decided.json()["turn"]["status"] == "completed"
        fact = client.get("/api/v1/memory/facts/preferences/reply_language", headers=asha)
        assert fact.json()["content"] == "Prefers Hindi replies."

        trail = client.get(
            "/api/v1/audit", params={"thread_id": "t-hitl"}, headers=auditor
        ).json()["entries"]
        actions = {e["action"]: e for e in trail}
        for action in ("approval.requested", "memory.fact.create", "operation.executed"):
            assert actions[action]["request_id"] == request_id, action  # one correlation id
        assert "approval.decided" in [
            e["action"] for e in client.get("/api/v1/audit", headers=auditor).json()["entries"]
        ]
        # Another tenant's auditor sees nothing of it.
        other = bearer(token(agent, uid("globex"), "aud", ["auditor"]))
        other_trail = client.get("/api/v1/audit", headers=other).json()["entries"]
        assert all(e["request_id"] != request_id for e in other_trail)
    (row,) = owner_sql(
        owner_url,
        "SELECT requester_only, status FROM approval_request WHERE id = CAST(:id AS uuid)",
        id=approval_id,
    )
    assert tuple(row) == (True, "executed")


def test_tenant_isolation_over_http(settings_pg: AppSettings, owner_url: str) -> None:
    acme, globex = uid("acme"), uid("globex")
    agent = pg_agent(settings_pg, understanding(), "Noted.", understanding(), "Hello globex.")
    a_user = bearer(token(agent, acme, "u1", ["member", "knowledge_manager"]))
    b_user = bearer(token(agent, globex, "u1", ["member", "knowledge_manager"]))
    with api_client(settings_pg, agent) as client:
        assert (
            client.post(
                "/api/v1/agent/turn",
                json={"message": "Our secret code is 7781", "thread_id": "same-id"},
                headers=a_user,
            ).status_code
            == 200
        )
        client.put(
            "/api/v1/memory/facts/company/code",
            json={"content": "Code 7781", "shared": True},
            headers=a_user,
        )
        # Tenant B cannot see A's conversation, memory, approvals or audit...
        assert client.get("/api/v1/conversations/same-id", headers=b_user).status_code == 404
        assert client.get("/api/v1/conversations", headers=b_user).json() == {
            "conversations": []
        }
        assert client.get("/api/v1/memory/facts", headers=b_user).json() == {"facts": []}
        assert (
            client.get("/api/v1/memory/facts/company/code?shared=true", headers=b_user).status_code
            == 404
        )
        assert client.get("/api/v1/approvals", headers=b_user).json() == {"approvals": []}
        # ...and the same public id names B's own, separate conversation.
        mine = client.post(
            "/api/v1/agent/turn", json={"message": "Hello", "thread_id": "same-id"}, headers=b_user
        )
        assert mine.status_code == 200 and "7781" not in mine.text
        view = client.get("/api/v1/conversations/same-id", headers=b_user).json()
        assert view["message_count"] == 2
    owners = owner_sql(
        owner_url,
        "SELECT tenant_id, owner_user_id FROM conversation_thread WHERE thread_id = 'same-id' "
        "AND tenant_id IN (:a, :b) ORDER BY tenant_id",
        a=acme,
        b=globex,
    )
    assert sorted(tuple(r) for r in owners) == sorted([(acme, "u1"), (globex, "u1")])
    rls = dict(
        owner_sql(
            owner_url,
            "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(:t)",
            t=list(RLS_TABLES),
        )
    )
    assert all(rls[t] for t in RLS_TABLES), rls  # RLS remains enabled everywhere


def test_idempotent_turns_over_http(settings_pg: AppSettings) -> None:
    tenant = uid("acme")
    agent = pg_agent(settings_pg, understanding(), "Hello once.")
    headers = bearer(token(agent, tenant, "asha"))
    body = {"message": "Hi", "thread_id": "t-idem", "idempotency_key": "submit-1"}
    with api_client(settings_pg, agent) as client:
        first = client.post("/api/v1/agent/turn", json=body, headers=headers)
        second = client.post("/api/v1/agent/turn", json=body, headers=headers)
        reused = client.post(
            "/api/v1/agent/turn", json={**body, "message": "Different"}, headers=headers
        )
    assert (first.status_code, first.json()["replayed"]) == (200, False)
    assert (second.status_code, second.json()["replayed"]) == (200, True)
    # the stored response, not a new turn
    assert second.json()["message"] == "Hello once."
    assert second.json()["request_id"] == first.json()["request_id"]
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(agent.dependencies.llm.calls) == 2  # type: ignore[attr-defined]


# --- maintenance on PostgreSQL ----------------------------------------------------------------


def test_maintenance_removes_only_expired_rows(settings_pg: AppSettings, owner_url: str) -> None:
    tenant = uid("maint")
    who = principal(tenant, "asha")
    batch = settings_pg.model_copy(
        update={"maintenance": MaintenanceSettings(batch_size=2, max_batches=1000)}
    )

    async def seed(deps: AgentDependencies) -> list[str]:
        assert deps.memory is not None and deps.idempotency is not None
        for key in ("a", "b", "c"):
            await deps.memory.long_term.remember(
                who.memory_scope, "prefs", key, f"fact {key}", ttl=timedelta(hours=1)
            )
        await deps.memory.long_term.remember(who.memory_scope, "prefs", "keep", "kept forever")
        ids = []
        for ttl in (60, 60, 7 * 24 * 3600):
            record = await deps.security.approvals.request(
                who, operation="system.test_op", operation_hash="h" * 64, summary="op",
                required_permission="tool.execute", risk="high",
                policy=ApprovalPolicy(ttl_seconds=ttl), thread_id="t1", request_id=uid("r"),
            )  # fmt: skip
            ids.append(str(record.id))
        idem: IdempotencyService = deps.idempotency
        for key in ("k1", "k2", "k3"):
            claim = await idem.claim(f"test.{tenant}", key, {"k": key})
            await idem.complete(claim, {"ok": True})
        return ids

    approval_ids = with_deps(batch, seed)
    # Make some rows due, using the database clock (as the owner: no RLS).
    owner_sql(
        owner_url,
        "UPDATE memory_item SET expires_at = now() - interval '1 minute' "
        "WHERE tenant_id = :t AND key IN ('a', 'b', 'c')",
        t=tenant,
    )
    owner_sql(
        owner_url,
        "UPDATE approval_request SET expires_at = now() - interval '1 minute' "
        "WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))",
        t=tenant,
        ids=approval_ids[:2],
    )
    owner_sql(
        owner_url,
        "UPDATE idempotency_record SET expires_at = now() - interval '1 minute' "
        "WHERE scope = :s AND idempotency_key IN ('k1', 'k2')",
        s=f"test.{tenant}",
    )

    async def maintain(deps: AgentDependencies) -> Any:
        runner = build_maintenance_runner(deps)
        assert [j.name for j in runner.jobs] == [
            "memory.expired",
            "approvals.expired",
            "idempotency.expired",
        ]
        return await runner.run_all(), await runner.run_all()

    first, second = with_deps(batch, maintain)
    for report in first + second:
        assert (report.status, report.errors, report.complete) == ("ok", 0, True), report
    memory, approvals, idempotency = first
    assert memory.affected >= 3 and memory.batches >= 2  # 2 per batch
    assert approvals.affected >= 2 and idempotency.affected >= 2
    keys = owner_sql(
        owner_url, "SELECT key FROM memory_item WHERE tenant_id = :t ORDER BY key", t=tenant
    )
    assert [k for (k,) in keys] == ["keep"]
    statuses = owner_sql(
        owner_url,
        "SELECT id::text, status FROM approval_request WHERE tenant_id = :t",
        t=tenant,
    )
    assert dict(statuses) == {
        approval_ids[0]: "expired",
        approval_ids[1]: "expired",
        approval_ids[2]: "pending",
    }
    left = owner_sql(
        owner_url,
        "SELECT idempotency_key FROM idempotency_record WHERE scope = :s",
        s=f"test.{tenant}",
    )
    assert [k for (k,) in left] == ["k3"]
    # statistics recorded as system events, correlated by run id
    runs = owner_sql(
        owner_url,
        "SELECT tool_name, status, actor_type, tenant_id, details->>'affected' "
        "FROM audit_log WHERE action = 'maintenance.run' AND request_id = :r",
        r=memory.run_id,
    )
    assert [tuple(r) for r in runs] == [
        ("memory.expired", "ok", "system", None, str(memory.affected))
    ]
    expiry_events = owner_sql(
        owner_url,
        "SELECT count(*) FROM audit_log WHERE action = 'approval.expired' AND tenant_id = :t",
        t=tenant,
    )
    assert expiry_events[0][0] == 2  # once per approval, not again on the second run


def test_two_runners_never_process_a_row_twice(settings_pg: AppSettings, owner_url: str) -> None:
    tenant = uid("race")
    who = principal(tenant, "asha")

    async def seed(deps: AgentDependencies) -> None:
        for _ in range(8):
            await deps.security.approvals.request(
                who, operation="system.test_op", operation_hash="h" * 64, summary="op",
                required_permission="tool.execute", risk="high",
                policy=ApprovalPolicy(ttl_seconds=60), thread_id="t1", request_id=uid("r"),
            )  # fmt: skip

    with_deps(settings_pg, seed)
    owner_sql(
        owner_url,
        "UPDATE approval_request SET expires_at = now() - interval '1 minute' WHERE tenant_id = :t",
        t=tenant,
    )

    async def race(deps: AgentDependencies) -> Any:
        jobs = [j for j in default_jobs(deps) if j.name == "approvals.expired"]
        runners = [
            MaintenanceRunner(jobs, settings=MaintenanceSettings(batch_size=2), audit=None)
            for _ in range(2)
        ]  # no lock: only SKIP LOCKED and compare-and-set protect the rows
        return await asyncio.gather(*(r.run_job("approvals.expired") for r in runners))

    reports = with_deps(settings_pg, race)
    assert all(r.status == "ok" for r in reports)
    rows = owner_sql(
        owner_url, "SELECT status, count(*) FROM approval_request WHERE tenant_id = :t "
        "GROUP BY status", t=tenant,
    )  # fmt: skip
    assert dict(rows) == {"expired": 8}
    per_approval = owner_sql(
        owner_url,
        "SELECT entity_refs->0->>'id', count(*) FROM audit_log WHERE action = 'approval.expired' "
        "AND tenant_id = :t GROUP BY 1",
        t=tenant,
    )
    assert len(per_approval) == 8 and all(count == 1 for _, count in per_approval)


def test_executed_approval_never_reruns_after_its_record_is_purged(
    settings_pg: AppSettings, owner_url: str
) -> None:
    tenant = uid("acme")
    # Without Redis: a cached fast-path result would answer as a replay before the
    # (purged) PostgreSQL record is consulted; this test is about the purge itself.
    agent = pg_agent(
        settings_pg.model_copy(update={"redis": RedisSettings()}),
        understanding(intent="remember_preference", tool_calls=[(REMEMBER_FACT, FACT_ARGS)]),
        "Done.",
    )
    asha = principal(tenant, "asha")

    async def body() -> Any:
        try:
            paused = await agent.arun("Remember Hindi", thread_id="t1", principal=asha)
            approval_id = paused.interrupts[0].value["approval_id"]
            approvals = agent.dependencies.security.approvals
            await approvals.decide(asha, approval_id, approve=True)
            resumed = await agent.aresume("t1", principal=asha)
            assert resumed.tool_results[-1].status == "ok"
            # the idempotency record expires and maintenance purges it
            await _expire_execution_record(owner_url, tenant, approval_id)
            purged = await build_maintenance_runner(agent.dependencies).run_job(
                "idempotency.expired"
            )
            assert purged.affected >= 1
            # a second attempt to run the same approved action (for example a replayed
            # resume after a crash) must not execute it again
            pending = _pending_action(approval_id)
            again = await agent.dependencies.tools.execute_approved(
                pending,
                principal=asha,
                resume_value=None,
                thread_id="t1",
                request_id=paused.request_id,
            )
            return paused.request_id, again
        finally:
            await agent.aclose()

    request_id, again = asyncio.run(body())
    assert (again.status, again.error_code) == ("error", "APPROVAL_ALREADY_USED")
    writes = owner_sql(
        owner_url,
        "SELECT count(*) FROM audit_log WHERE action = 'memory.fact.create' AND request_id = :r",
        r=request_id,
    )
    assert writes[0][0] == 1


def _pending_action(approval_id: str) -> Any:
    from agentic_platform.tools.base import PendingAction
    from agentic_platform.tools.memory import RememberFactInput
    from agentic_platform.tools.registry import operation_hash

    tool_input = RememberFactInput.model_validate_json(FACT_ARGS)
    return PendingAction(
        approval_id=approval_id,
        tool_name=REMEMBER_FACT,
        arguments_json=tool_input.model_dump_json(),
        operation_hash=operation_hash(REMEMBER_FACT, tool_input),
        summary="s",
        risk="medium",
        expires_at="",
    )


async def _expire_execution_record(owner_url: str, tenant: str, approval_id: str) -> None:
    db = Database(DatabaseSettings(url=owner_url))
    try:
        async with db.unit_of_work() as uow:
            result = await uow.session.execute(
                text(
                    "UPDATE idempotency_record SET expires_at = now() - interval '1 minute' "
                    "WHERE scope = 'approval.execute' AND idempotency_key = :k"
                ),
                {"k": f"{tenant}/{approval_id}"},
            )
            assert result.rowcount == 1  # type: ignore[attr-defined]
    finally:
        await db.dispose()


# --- migration 0005 ---------------------------------------------------------------------------


def test_migration_0005_down_and_up(owner_url: str) -> None:
    config = alembic_config(owner_url)

    def column() -> list[Any]:
        return owner_sql(
            owner_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'approval_request' AND column_name = 'requester_only'",
        )

    def check() -> list[Any]:
        return owner_sql(
            owner_url,
            "SELECT conname FROM pg_constraint WHERE conrelid = 'approval_request'::regclass "
            "AND conname = 'ck_approval_request_requester_only_self'",
        )

    command.downgrade(config, "0004")
    try:
        assert column() == [] and check() == []
    finally:
        command.upgrade(config, "head")
    assert len(column()) == 1 and len(check()) == 1
    rls = owner_sql(
        owner_url, "SELECT relrowsecurity FROM pg_class WHERE relname = 'approval_request'"
    )
    assert rls[0][0] is True  # RLS untouched by 0005
