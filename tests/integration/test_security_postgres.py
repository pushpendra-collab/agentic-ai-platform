"""Phase 5 against a real PostgreSQL 16, mostly as the least-privilege app role (so
row-level security applies): migration 0004 up/down, tenant RLS on conversations,
approvals, audit records and checkpoints (with deliberately unfiltered SQL),
conversation ownership across agent restarts, the approval lifecycle end to end,
expiry, concurrent decisions and single execution.

Same setup as test_postgres.py (TEST_DATABASE_URL / TEST_DATABASE_APP_URL).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, DatabaseSettings, MemorySettings
from agentic_platform.core.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ConversationNotFoundError,
    DatabasePermissionError,
)
from agentic_platform.db import Database
from agentic_platform.db.migrations import alembic_config
from agentic_platform.db.tenancy import bind_tenant
from agentic_platform.security import Authorizer, SecurityAudit, acting_as
from agentic_platform.security.policy import ApprovalPolicy
from agentic_platform.services.approvals import ApprovalService, PostgresApprovalStore
from agentic_platform.services.audit import AuditEvent, AuditService
from agentic_platform.services.clock import ClockService
from agentic_platform.services.conversations import PostgresConversationStore
from agentic_platform.tools.memory import REMEMBER_FACT
from tests.conftest import FIXED_NOW, principal, understanding
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.postgres

T = TypeVar("T")

PHASE5_TABLES = ("conversation_thread", "approval_request")
RLS_TABLES = (
    "conversation_thread",
    "approval_request",
    "audit_log",
    "agent_checkpoints",
    "agent_checkpoint_blobs",
    "agent_checkpoint_writes",
)
FACT_ARGS = '{"key": "reply_language", "content": "Prefers Hindi replies."}'


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


def with_db(url: str, fn: Callable[[Database], Awaitable[T]]) -> T:
    async def body() -> T:
        db = Database(DatabaseSettings(url=url))
        try:
            return await fn(db)
        finally:
            await db.dispose()

    return asyncio.run(body())


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def approvals_for(db: Database) -> ApprovalService:
    return ApprovalService(
        PostgresApprovalStore(db), Authorizer(SecurityAudit(AuditService(db)))
    )


async def request_approval(
    service: ApprovalService, tenant: str, user: str = "asha", **policy: Any
) -> Any:
    return await service.request(
        principal(tenant, user),
        operation="system.test_op",
        operation_hash="h" * 64,
        summary="A test operation",
        required_permission="tool.execute",
        risk="high",
        policy=ApprovalPolicy(**policy),
        thread_id="t1",
        request_id=uid("req"),
    )


# --- migration 0004 ------------------------------------------------------------------------


def test_migration_0004_down_and_up(owner_url: str, app_url: str) -> None:
    app_role = make_url(app_url).username
    config = alembic_config(owner_url)

    async def inspect(db: Database) -> dict[str, Any]:
        async with db.unit_of_work(read_only=True) as uow:
            tables = set(
                (
                    await uow.session.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
            rls = dict(
                (
                    await uow.session.execute(
                        text("SELECT relname, relrowsecurity FROM pg_class WHERE relkind = 'r'")
                    )
                ).all()
            )
            can_update = await uow.session.scalar(
                text("SELECT has_table_privilege(:role, 'audit_log', 'UPDATE')"),
                {"role": app_role},
            )
            return {"tables": tables, "rls": rls, "app_can_update_audit": can_update}

    command.downgrade(config, "0003")
    try:
        before = with_db(owner_url, inspect)
        assert not set(PHASE5_TABLES) & before["tables"]
        assert not any(before["rls"].get(t) for t in RLS_TABLES)
        assert before["app_can_update_audit"] is True  # Phase 2-4 grants restored
        assert before["rls"]["memory_item"] is True  # Phase 4 RLS untouched
    finally:
        command.upgrade(config, "head")
    after = with_db(owner_url, inspect)
    assert set(PHASE5_TABLES) <= after["tables"]
    assert all(after["rls"][t] for t in RLS_TABLES)
    assert after["app_can_update_audit"] is False


# --- row-level security with deliberately unfiltered SQL -----------------------------------


def test_rls_isolates_tenants_on_every_phase5_table(app_url: str) -> None:
    a, b = uid("ta"), uid("tb")

    async def body(db: Database) -> None:
        conversations = PostgresConversationStore(db)
        approvals = approvals_for(db)
        audit = AuditService(db)
        saver = PostgresCheckpointSaver(db)
        for tenant in (a, b):
            await conversations.open(tenant, "t1", "asha")
            await request_approval(approvals, tenant)
            await audit.record(AuditEvent(action="test.rls", status="ok", tenant_id=tenant))
            with acting_as(principal(tenant, "asha")):
                agent = Agent(
                    build_dependencies(
                        AppSettings(database=DatabaseSettings(url=app_url)),
                        llm=ScriptedLLM(script=[understanding(), "Hi."]),
                        database=db,
                    ),
                    checkpointer=saver,
                )
                await agent.arun("hello", principal=principal(tenant, "asha"), thread_id="t1")
        await audit.record(AuditEvent(action="test.rls.system", status="ok"))  # no tenant

        queries = {
            "conversation_thread": "SELECT tenant_id FROM conversation_thread",
            "approval_request": "SELECT tenant_id FROM approval_request",
            "audit_log": "SELECT tenant_id FROM audit_log",
            "agent_checkpoints": "SELECT split_part(thread_id, '/', 1) FROM agent_checkpoints",
            "agent_checkpoint_blobs": (
                "SELECT split_part(thread_id, '/', 1) FROM agent_checkpoint_blobs"
            ),
            "agent_checkpoint_writes": (
                "SELECT split_part(thread_id, '/', 1) FROM agent_checkpoint_writes"
            ),
        }
        async with db.unit_of_work(read_only=True) as uow:
            await bind_tenant(uow.session, a)
            for table, sql in queries.items():
                seen = set((await uow.session.execute(text(sql))).scalars())
                assert seen == {a}, table  # no other tenant, no tenant-less system rows
        async with db.unit_of_work(read_only=True) as uow:  # no tenant bound: nothing
            for table, sql in queries.items():
                assert list((await uow.session.execute(text(sql))).scalars()) == [], table

        inserts = [
            "INSERT INTO conversation_thread (tenant_id, thread_id, owner_user_id) "
            "VALUES (:b, 'x', 'eve')",
            "INSERT INTO audit_log (tenant_id, actor_type, action, status) "
            "VALUES (:b, 'user', 'forged', 'ok')",
            "INSERT INTO agent_checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
            "checkpoint_type, checkpoint) VALUES (:b || '/x', '', '1', 't', '\\x00')",
            "INSERT INTO approval_request (tenant_id, requester_id, thread_id, request_id, "
            "operation, operation_hash, required_permission, approver_permission, risk, "
            "summary, reason, expires_at) VALUES (:b, 'eve', 't', 'r', 'o', 'h', 'p', 'p', "
            "'low', 's', 'r', now())",
        ]
        for statement in inserts:
            with pytest.raises(DatabasePermissionError):
                async with db.unit_of_work() as uow:
                    await bind_tenant(uow.session, a)  # bound to A, writing B's row
                    await uow.session.execute(text(statement), {"b": b})

    with_db(app_url, body)


def test_audit_log_reads_are_tenant_scoped(app_url: str) -> None:
    a, b = uid("aa"), uid("ab")

    async def body(db: Database) -> None:
        audit = AuditService(db)
        await audit.record(AuditEvent(action="a.only", status="ok", tenant_id=a, thread_id="t"))
        await audit.record(AuditEvent(action="b.only", status="ok", tenant_id=b, thread_id="t"))
        assert [r.action for r in await audit.for_tenant(a)] == ["a.only"]
        assert [r.action for r in await audit.for_thread("t", tenant_id=b)] == ["b.only"]

    with_db(app_url, body)


def test_checkpoint_saver_cannot_reach_another_tenant(app_url: str) -> None:
    a, b = uid("ca"), uid("cb")
    settings = AppSettings(database=DatabaseSettings(url=app_url))

    async def body(db: Database) -> None:
        saver = PostgresCheckpointSaver(db)
        agent = Agent(
            build_dependencies(
                settings, llm=ScriptedLLM(script=[understanding(), "Hi."]), database=db
            ),
            checkpointer=saver,
        )
        await agent.arun("secret", principal=principal(a), thread_id="t1")
        config: Any = {"configurable": {"thread_id": f"{a}/t1", "checkpoint_ns": ""}}
        with acting_as(principal(a)):
            assert await saver.aget_tuple(config) is not None
        with acting_as(principal(b)):  # even if code computed A's key by mistake
            assert await saver.aget_tuple(config) is None
            assert [c async for c in saver.alist(None)] == []

    with_db(app_url, body)


# --- conversation ownership on PostgreSQL ------------------------------------------------------


def test_conversation_ownership_survives_restarts(app_url: str) -> None:
    tenant = uid("own")
    settings = AppSettings(database=DatabaseSettings(url=app_url))
    clock = ClockService(now_utc=lambda: FIXED_NOW)

    def agent(*script: object) -> Agent:
        deps = build_dependencies(settings, llm=ScriptedLLM(script=list(script)), clock=clock)
        return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database))  # type: ignore[arg-type]

    asha, ravi = principal(tenant, "asha"), principal(tenant, "ravi")
    assert agent(understanding(), "Hi.").run("Hello", thread_id="t1", principal=asha).status == (
        "completed"
    )
    restarted = agent(understanding(), "Again.")
    with pytest.raises(ConversationNotFoundError):
        restarted.run("Let me in", thread_id="t1", principal=ravi)
    with pytest.raises(ConversationNotFoundError):
        restarted.resume("t1", principal=ravi)
    assert restarted.thread_status("t1", principal=ravi).exists is False
    other_tenant = principal(uid("other"), "asha")
    assert restarted.thread_status("t1", principal=other_tenant).exists is False
    assert restarted.run("Again", thread_id="t1", principal=asha).status == "completed"
    assert restarted.thread_status("t1", principal=asha).message_count == 4

    forgetting = agent()
    counts = forgetting.forget_conversation("t1", principal=asha)
    assert counts["episodic"] == 2
    assert forgetting.thread_status("t1", principal=asha).exists is False


# --- approvals end to end on PostgreSQL ------------------------------------------------------


def test_approval_flow_across_processes(app_url: str) -> None:
    tenant = uid("hitl")
    settings = AppSettings(
        database=DatabaseSettings(url=app_url),
        memory=MemorySettings(agent_fact_writes="approval"),
    )
    clock = ClockService(now_utc=lambda: FIXED_NOW)

    def agent(*script: object) -> Agent:
        deps = build_dependencies(settings, llm=ScriptedLLM(script=list(script)), clock=clock)
        return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database))  # type: ignore[arg-type]

    asha = principal(tenant, "asha")
    paused = agent(understanding(tool_calls=[(REMEMBER_FACT, FACT_ARGS)])).run(
        "Remember I prefer Hindi", thread_id="t1", principal=asha
    )
    assert paused.status == "interrupted"
    approval_id = paused.interrupts[0].value["approval_id"]

    # "Approver process": a fresh agent/service reads the request from PostgreSQL.
    async def decide(db: Database) -> Any:
        service = approvals_for(db)
        with pytest.raises(ApprovalNotFoundError):  # another tenant cannot even see it
            await service.get(principal(uid("x"), "kim", roles=("tenant_admin",)), approval_id)
        return await service.decide(asha, approval_id, approve=True)

    assert with_db(app_url, decide).status == "approved"

    resuming = agent("Saved.")
    done = resuming.resume("t1", principal=asha)
    assert done.status == "completed"
    assert done.tool_results[-1].status == "ok"

    memory = resuming.dependencies.authorized_memory()
    assert memory is not None

    async def check(db: Database) -> None:
        fact = await memory.get_fact(asha, "preferences", "reply_language")
        assert fact is not None and fact.content == "Prefers Hindi replies."
        record = await approvals_for(db).get(asha, approval_id)
        assert (record.status, record.execution_status) == ("executed", "ok")
        actions = [r.action for r in await AuditService(db).for_tenant(tenant, limit=100)]
        for action in (
            "approval.requested",
            "approval.decided",
            "operation.executed",
            "memory.fact.create",
        ):
            assert actions.count(action) == 1, action
        assert actions.count("tool.call") == 2  # the request and the approved execution
        rows = await AuditService(db).for_thread("t1", tenant_id=tenant)
        assert {r.request_id for r in rows if r.action != "memory.fact.create"} == {
            paused.request_id
        }

        # Executing the same approval again replays the stored result (idempotency).
        pending = await _pending(approval_id)
        replay = await resuming.dependencies.tools.execute_approved(
            pending,
            principal=asha,
            resume_value=None,
            thread_id="t1",
            request_id=paused.request_id,
        )
        assert replay.status == "ok"
        after = [r.action for r in await AuditService(db).for_tenant(tenant, limit=100)]
        assert after.count("memory.fact.create") == 1

    with_db(app_url, check)


async def _pending(approval_id: str) -> Any:
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


def test_expiry_uses_the_database_clock(app_url: str, owner_url: str) -> None:
    tenant = uid("exp")

    async def body(db: Database) -> tuple[str, str]:
        service = approvals_for(db)
        first = await request_approval(service, tenant)
        second = await request_approval(service, tenant)
        return str(first.id), str(second.id)

    first, second = with_db(app_url, body)

    async def backdate(db: Database) -> None:  # owner: not subject to RLS
        async with db.unit_of_work() as uow:
            await uow.session.execute(
                text(
                    "UPDATE approval_request SET expires_at = now() - interval '1 second' "
                    "WHERE id IN (:a, :b)"
                ),
                {"a": uuid.UUID(first), "b": uuid.UUID(second)},
            )

    with_db(owner_url, backdate)
    approver = principal(tenant, "kim", roles=("member", "approver"))

    async def check(db: Database) -> None:
        service = approvals_for(db)
        with pytest.raises(ApprovalExpiredError):
            await service.decide(approver, first, approve=True)
        assert (await service.get(approver, first)).status == "expired"
        assert await service.expire_due() >= 1  # maintenance job, all tenants
        assert (await service.get(approver, second)).status == "expired"
        actions = [r.action for r in await AuditService(db).for_tenant(tenant)]
        assert actions.count("approval.expired") == 2

    with_db(app_url, check)


def test_concurrent_decisions_have_one_winner(app_url: str) -> None:
    tenant = uid("race")

    async def body(db: Database) -> None:
        service = approvals_for(db)
        record = await request_approval(service, tenant)
        kim = principal(tenant, "kim", roles=("member", "approver"))
        lee = principal(tenant, "lee", roles=("member", "approver"))
        results = await asyncio.gather(
            service.decide(kim, record.id, approve=True),
            service.decide(lee, record.id, approve=False),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, BaseException)]
        assert len(winners) == 1 and len(losers) == 1
        assert isinstance(losers[0], ApprovalAlreadyDecidedError)
        audit = await AuditService(db).for_tenant(tenant)
        assert [r.action for r in audit].count("approval.decided") == 1

    with_db(app_url, body)


def test_tenant_bound_read_only_unit_still_rejects_writes(app_url: str) -> None:
    """The tenant binding is folded into the read-only setup statement: both must hold."""
    from agentic_platform.core.errors import DatabaseReadOnlyError

    tenant = uid("ro")

    async def body(db: Database) -> None:
        async with db.unit_of_work(read_only=True, tenant_id=tenant) as uow:
            bound = await uow.session.scalar(text("SELECT current_setting('app.tenant_id')"))
            read_only = await uow.session.scalar(text("SHOW transaction_read_only"))
            assert (bound, read_only) == (tenant, "on")
        with pytest.raises(DatabaseReadOnlyError):
            async with db.unit_of_work(read_only=True, tenant_id=tenant) as uow:
                await uow.session.execute(
                    text(
                        "INSERT INTO conversation_thread (tenant_id, thread_id, owner_user_id) "
                        "VALUES (:t, 'x', 'u')"
                    ),
                    {"t": tenant},
                )

    with_db(app_url, body)
