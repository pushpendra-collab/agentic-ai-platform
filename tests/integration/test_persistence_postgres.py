"""Phase 3 integration tests against a real PostgreSQL: checkpointer, audit log,
idempotency, and models-vs-migration drift.

Same setup as test_postgres.py (TEST_DATABASE_URL / TEST_DATABASE_APP_URL).
Tests use unique thread ids / keys, so they never depend on each other's rows
(audit rows cannot be deleted by design).
"""

from __future__ import annotations

import asyncio
import operator
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict, TypeVar

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from langgraph.checkpoint.base import create_checkpoint, empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, DatabaseSettings, normalise_postgres_url
from agentic_platform.core.errors import (
    DatabasePermissionError,
    IdempotencyInProgressError,
    IdempotencyKeyReuseError,
    IdempotencyLeaseLostError,
)
from agentic_platform.db import Database
from agentic_platform.db.migrations import alembic_config
from agentic_platform.db.models import target_metadata
from agentic_platform.services.audit import AuditEvent, AuditService
from agentic_platform.services.clock import ClockService
from agentic_platform.services.idempotency import IdempotencyService
from tests.conftest import FIXED_NOW, LOCAL_TENANT, understanding
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.postgres

T = TypeVar("T")


# --- fixtures ----------------------------------------------------------------------------


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


def with_db(url: str, fn: Callable[[Database], Awaitable[T]], **overrides: Any) -> T:
    async def body() -> T:
        db = Database(DatabaseSettings(url=url, **overrides))
        try:
            return await fn(db)
        finally:
            await db.dispose()

    return asyncio.run(body())


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


# --- schema --------------------------------------------------------------------------------


def test_models_match_migrations(owner_url: str) -> None:
    """The ORM models and migration 0002 describe exactly the same schema."""

    async def diff() -> list[Any]:
        engine = create_async_engine(normalise_postgres_url(owner_url))
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda sync: compare_metadata(
                        MigrationContext.configure(
                            sync, opts={"compare_type": True, "compare_server_default": True}
                        ),
                        target_metadata,
                    )
                )
        finally:
            await engine.dispose()

    assert asyncio.run(diff()) == []


# --- checkpointer --------------------------------------------------------------------------


def _config(thread_id: str, checkpoint_id: str | None = None) -> Any:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def test_saver_put_get_list_writes_delete(owner_url: str) -> None:
    thread = uid("t")

    async def body(db: Database) -> None:
        saver = PostgresCheckpointSaver(db)
        assert await saver.aget_tuple(_config(thread)) is None

        first = empty_checkpoint()
        first["channel_values"] = {"messages": ["hi"], "count": 1}
        v1 = saver.get_next_version(None, None)
        first["channel_versions"] = {"messages": v1, "count": v1}
        cfg1 = await saver.aput(
            _config(thread), first, {"source": "input", "step": -1, "parents": {}}, {
                "messages": v1,
                "count": v1,
            }
        )

        second = create_checkpoint(first, None, 1)
        v2 = saver.get_next_version(v1, None)
        second["channel_values"] = {"messages": ["hi", "there"], "count": 1}
        second["channel_versions"] = {"messages": v2, "count": v1}
        cfg2 = await saver.aput(
            cfg1, second, {"source": "loop", "step": 0, "parents": {}}, {"messages": v2}
        )

        # Pending writes: regular ones are not overwritten, special ones are.
        await saver.aput_writes(cfg2, [("messages", "a"), ("count", 2)], task_id="task-1")
        await saver.aput_writes(cfg2, [("messages", "CHANGED")], task_id="task-1")
        await saver.aput_writes(cfg2, [("__error__", "boom")], task_id="task-2")
        await saver.aput_writes(cfg2, [("__error__", "boom-2")], task_id="task-2")

        latest = await saver.aget_tuple(_config(thread))
        assert latest is not None
        assert latest.config["configurable"]["checkpoint_id"] == second["id"]
        assert latest.checkpoint["channel_values"] == {"messages": ["hi", "there"], "count": 1}
        assert latest.metadata == {"source": "loop", "step": 0, "parents": {}}
        assert latest.parent_config == _config(thread, first["id"])
        assert latest.pending_writes == [
            ("task-1", "messages", "a"),
            ("task-1", "count", 2),
            ("task-2", "__error__", "boom-2"),
        ]

        older = await saver.aget_tuple(_config(thread, first["id"]))
        assert older is not None
        assert older.checkpoint["channel_values"] == {"messages": ["hi"], "count": 1}
        assert older.parent_config is None

        listed = [t.checkpoint["id"] async for t in saver.alist(_config(thread))]
        assert listed == [second["id"], first["id"]]
        filtered = [t async for t in saver.alist(_config(thread), filter={"source": "input"})]
        assert [t.checkpoint["id"] for t in filtered] == [first["id"]]
        before = [t async for t in saver.alist(_config(thread), before=cfg2)]
        assert [t.checkpoint["id"] for t in before] == [first["id"]]
        assert len([t async for t in saver.alist(_config(thread), limit=1)]) == 1

        await saver.adelete_thread(thread)
        assert await saver.aget_tuple(_config(thread)) is None
        assert [t async for t in saver.alist(_config(thread))] == []

    with_db(owner_url, body)


class _HitlState(TypedDict):
    log: Annotated[list[str], operator.add]


def test_interrupt_and_resume_survive_a_new_saver(owner_url: str) -> None:
    """The mechanism the HITL approval gate will use: pause, persist, resume later."""
    thread = uid("hitl")

    def build(db: Database) -> Any:
        graph: StateGraph[_HitlState] = StateGraph(_HitlState)

        def ask(state: _HitlState) -> dict[str, list[str]]:
            decision = interrupt({"question": "approve?"})
            return {"log": [f"decision={decision}"]}

        graph.add_node("ask", ask)
        graph.add_edge(START, "ask")
        graph.add_edge("ask", END)
        return graph.compile(checkpointer=PostgresCheckpointSaver(db))

    config: Any = {"configurable": {"thread_id": thread}}

    async def pause(db: Database) -> Any:
        return await build(db).ainvoke({"log": ["start"]}, config)

    async def resume(db: Database) -> Any:
        return await build(db).ainvoke(Command(resume="approved"), config)

    paused = with_db(owner_url, pause)
    assert "__interrupt__" in paused
    resumed = with_db(owner_url, resume)  # new engine, new saver: state came from PostgreSQL
    assert resumed["log"] == ["start", "decision=approved"]


def test_agent_conversation_survives_restart(app_url: str) -> None:
    """Runs as the least-privilege app role: DML on checkpoint tables is enough."""
    thread = uid("conv")
    settings = AppSettings(database=DatabaseSettings(url=app_url))
    clock = ClockService(now_utc=lambda: FIXED_NOW)

    def agent(*script: object) -> tuple[Agent, ScriptedLLM]:
        llm = ScriptedLLM(script=list(script))
        deps = build_dependencies(settings, llm=llm, clock=clock)
        return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database)), llm  # type: ignore[arg-type]

    first, _ = agent(understanding(tool_calls=[("system.current_time", "{}")]), "It is 06:30.")
    assert first.run("What time is it?", thread_id=thread).status == "completed"

    second, llm = agent(understanding(intent="thanks"), "You're welcome.")  # "restart"
    response = second.run("Thanks!", thread_id=thread)
    assert response.status == "completed"
    assert response.tool_results == []  # per-turn state reset
    history = [(m.role, m.content) for m in llm.calls[0].messages]
    assert history == [
        ("user", "What time is it?"),
        ("assistant", "It is 06:30."),
        ("user", "Thanks!"),
    ]


# --- audit log ------------------------------------------------------------------------------


def test_audit_append_and_read(app_url: str) -> None:
    thread = uid("audit")
    tenant = uid("tenant")

    async def body(db: Database) -> None:
        service = AuditService(db)
        first = await service.record(
            AuditEvent(
                action="tool.call", status="ok", tool_name="t", thread_id=thread, tenant_id=tenant
            )
        )
        second = await service.record(
            AuditEvent(
                action="tool.call",
                status="denied",
                error_code="TOOL_NOT_ALLOWED",
                thread_id=thread,
                tenant_id=tenant,
                entity_refs=[{"type": "test", "id": "1"}],
            )
        )
        assert second > first
        rows = await service.for_thread(thread, tenant_id=tenant)
        assert [(r.id, r.status) for r in rows] == [(first, "ok"), (second, "denied")]
        assert rows[1].entity_refs == [{"type": "test", "id": "1"}]
        assert rows[0].details == {} and rows[0].occurred_at

        # Inside a caller's unit of work: rolled back together with it.
        rolled_back = uid("rb")
        with pytest.raises(RuntimeError):
            async with db.unit_of_work() as uow:
                await service.record(
                    AuditEvent(action="x", status="ok", thread_id=rolled_back, tenant_id=tenant),
                    uow=uow,
                )
                raise RuntimeError("business change failed")
        assert await service.for_thread(rolled_back, tenant_id=tenant) == []

    with_db(app_url, body)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET status = 'ok'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
@pytest.mark.parametrize("role", ["app", "owner"])
def test_audit_log_is_append_only(
    owner_url: str, app_url: str, statement: str, role: str
) -> None:
    url = app_url if role == "app" else owner_url

    async def body(db: Database) -> None:
        await AuditService(db).record(AuditEvent(action="seed", status="ok"))
        with pytest.raises(DatabasePermissionError):
            async with db.unit_of_work() as uow:
                await uow.session.execute(text(statement))

    with_db(url, body)


def test_agent_tool_calls_are_audited(owner_url: str) -> None:
    thread = uid("e2e")
    settings = AppSettings(database=DatabaseSettings(url=owner_url))
    llm = ScriptedLLM(
        script=[
            understanding(
                intent="check_database",
                tool_calls=[("system.database_health", "{}"), ("system.nope", "{}")],
            ),
            "Checked.",
        ]
    )
    agent = Agent(build_dependencies(settings, llm=llm))
    response = agent.run("Is the database working?", thread_id=thread)
    assert response.status == "completed"

    rows = with_db(
        owner_url, lambda db: AuditService(db).for_thread(thread, tenant_id=LOCAL_TENANT)
    )
    assert [(r.tool_name, r.status, r.error_code) for r in rows] == [
        ("system.database_health", "ok", None),
        ("system.nope", "error", "TOOL_NOT_FOUND"),
    ]
    assert all(r.request_id == response.request_id for r in rows)
    assert all(r.actor_type == "agent" and r.input_hash for r in rows)
    # Phase 5: the authenticated (local) principal is recorded as tenant and actor.
    assert all(r.tenant_id == LOCAL_TENANT and r.actor_id == "local" for r in rows)


# --- idempotency ----------------------------------------------------------------------------


def test_idempotency_claim_replay_and_reuse(app_url: str) -> None:
    key = uid("k")

    async def body(db: Database) -> None:
        service = IdempotencyService(db)
        claim = await service.claim("test.op", key, {"amount": 1, "to": "a"})
        assert not claim.replayed

        with pytest.raises(IdempotencyInProgressError) as busy:
            await service.claim("test.op", key, {"to": "a", "amount": 1})
        assert busy.value.retryable is True

        await service.complete(claim, {"result_id": 42})
        replay = await service.claim("test.op", key, {"to": "a", "amount": 1})
        assert replay.replayed and replay.response == {"result_id": 42}

        with pytest.raises(IdempotencyKeyReuseError):
            await service.claim("test.op", key, {"amount": 2, "to": "a"})
        # Same key in a different scope is independent.
        assert not (await service.claim("other.op", key, {"x": 1})).replayed

    with_db(app_url, body)


def test_idempotency_failed_attempt_can_be_retried(app_url: str) -> None:
    key = uid("k")

    async def body(db: Database) -> None:
        service = IdempotencyService(db)
        first = await service.claim("test.op", key, {"a": 1})
        await service.fail(first, error_code="DOWNSTREAM_TIMEOUT")
        retry = await service.claim("test.op", key, {"a": 1})
        assert not retry.replayed and retry.claim_token != first.claim_token
        with pytest.raises(IdempotencyLeaseLostError):
            await service.complete(first, {"stale": True})  # old token no longer holds it
        await service.complete(retry, {"ok": True})
        assert (await service.claim("test.op", key, {"a": 1})).response == {"ok": True}

    with_db(app_url, body)


def test_idempotency_expired_lease_and_ttl(app_url: str) -> None:
    key, other = uid("k"), uid("k")

    async def body(db: Database) -> None:
        service = IdempotencyService(db, lease_seconds=0.2, ttl_seconds=0.4)
        crashed = await service.claim("test.op", key, {"a": 1})  # worker "dies" here
        await asyncio.sleep(0.3)
        takeover = await service.claim("test.op", key, {"a": 1})  # lease expired
        assert not takeover.replayed
        with pytest.raises(IdempotencyLeaseLostError):
            await service.complete(crashed, {})

        done = await service.claim("test.op", other, {"a": 1})
        await service.complete(done, {"v": 1})
        await asyncio.sleep(0.5)  # record expired: the key is free again, any request
        fresh = await service.claim("test.op", other, {"different": True})
        assert not fresh.replayed
        assert await service.purge_expired() >= 1

    with_db(app_url, body)


def test_idempotency_completion_commits_with_the_write(app_url: str) -> None:
    key = uid("k")

    async def body(db: Database) -> None:
        service = IdempotencyService(db)
        claim = await service.claim("test.op", key, {"a": 1})
        with pytest.raises(RuntimeError):
            async with db.unit_of_work() as uow:
                await service.complete(claim, {"v": 1}, uow=uow)
                raise RuntimeError("business write failed")  # rolls back the completion too
        with pytest.raises(IdempotencyInProgressError):
            await service.claim("test.op", key, {"a": 1})
        async with db.unit_of_work() as uow:
            await service.complete(claim, {"v": 2}, uow=uow)
        assert (await service.claim("test.op", key, {"a": 1})).response == {"v": 2}

    with_db(app_url, body)


def test_concurrent_claims_have_one_winner(app_url: str) -> None:
    key = uid("k")

    async def body(db: Database) -> None:
        service = IdempotencyService(db)
        results = await asyncio.gather(
            *(service.claim("test.op", key, {"a": 1}) for _ in range(5)),
            return_exceptions=True,
        )
        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, BaseException)]
        assert len(winners) == 1 and not winners[0].replayed
        assert len(losers) == 4
        assert all(isinstance(e, IdempotencyInProgressError) for e in losers)

    with_db(app_url, body, pool_size=5, max_overflow=0)
