"""Memory against real PostgreSQL 16 + pgvector, as the least-privilege app role
(so row-level security applies). Same setup as test_postgres.py."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from alembic import command
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import text

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, DatabaseSettings
from agentic_platform.core.errors import DatabasePermissionError
from agentic_platform.db import Database
from agentic_platform.db.memory import MemoryRepository
from agentic_platform.db.migrations import alembic_config
from agentic_platform.memory import MemoryScope
from agentic_platform.memory.adapters.postgres import PostgresMemoryStore
from agentic_platform.services.clock import ClockService
from tests.conftest import FIXED_NOW, principal, understanding
from tests.fakes import ScriptedLLM
from tests.memory_contract import CONTRACT, ContractCheck, make_manager

pytestmark = pytest.mark.postgres

T = TypeVar("T")


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


# --- the same contract as the in-memory adapter ------------------------------------------


@pytest.mark.parametrize("check", CONTRACT, ids=lambda c: c.__name__)
def test_postgres_store_contract(app_url: str, check: ContractCheck) -> None:
    with_db(app_url, lambda db: check(make_manager(PostgresMemoryStore(db))))


# --- row-level security --------------------------------------------------------------------


def test_rls_hides_other_tenants_even_without_a_filter(app_url: str) -> None:
    a, b = f"rls-a-{uuid.uuid4().hex[:8]}", f"rls-b-{uuid.uuid4().hex[:8]}"

    async def body(db: Database) -> None:
        m = make_manager(PostgresMemoryStore(db))
        await m.long_term.remember(MemoryScope(a, "u"), "p", "k", "tenant A secret")
        await m.long_term.remember(MemoryScope(b, "u"), "p", "k", "tenant B secret")

        async with db.unit_of_work(read_only=True) as uow:
            await MemoryRepository(uow.session).bind_tenant(a)
            # Deliberately unfiltered SQL: only RLS stands between tenants here.
            rows = (await uow.session.execute(text("SELECT tenant_id FROM memory_item"))).all()
            assert {r[0] for r in rows} == {a}

        async with db.unit_of_work(read_only=True) as uow:  # no tenant bound: sees nothing
            count = await uow.session.scalar(text("SELECT count(*) FROM memory_item"))
            assert count == 0

        with pytest.raises(DatabasePermissionError):  # WITH CHECK: cannot write as tenant B
            async with db.unit_of_work() as uow:
                await MemoryRepository(uow.session).bind_tenant(a)
                await uow.session.execute(
                    text(
                        "INSERT INTO memory_item (tenant_id, kind, content) "
                        "VALUES (:t, 'long_term', 'x')"
                    ),
                    {"t": b},
                )

    with_db(app_url, body)


def test_pgvector_search_and_embedding_model_isolation(app_url: str) -> None:
    scope = MemoryScope(f"vec-{uuid.uuid4().hex[:8]}", "u")

    async def body(db: Database) -> None:
        m = make_manager(PostgresMemoryStore(db))
        await m.semantic.ingest(
            scope, source_ref="faq", text="Refunds are processed within seven working days."
        )
        hits = await m.semantic.search(scope, "how long do refunds take to process", k=3)
        assert hits and hits[0].record.source_ref == "faq" and 0 < hits[0].score <= 1.0001

        other_model = make_manager(PostgresMemoryStore(db), embedding_dimensions=64)
        assert await other_model.semantic.search(scope, "refunds processed", k=3) == []

        async with db.unit_of_work(read_only=True) as uow:
            await MemoryRepository(uow.session).bind_tenant(scope.tenant_id)
            column_type = await uow.session.scalar(
                text("SELECT pg_typeof(embedding)::text FROM memory_item LIMIT 1")
            )
        assert column_type == "vector"

    with_db(app_url, body)


def test_maintenance_purge_crosses_tenants(app_url: str, owner_url: str) -> None:
    async def body(db: Database) -> None:
        for t in (f"pa-{uuid.uuid4().hex[:6]}", f"pb-{uuid.uuid4().hex[:6]}"):
            async with db.unit_of_work() as uow:
                repo = MemoryRepository(uow.session)
                await repo.bind_tenant(t)
                await uow.session.execute(
                    text(
                        "INSERT INTO memory_item (tenant_id, kind, content, expires_at) "
                        "VALUES (:t, 'episodic', 'old', now() - interval '1 day')"
                    ),
                    {"t": t},
                )
        assert await PostgresMemoryStore(db).purge_expired() >= 2

    with_db(app_url, body)

    async def leftovers(db: Database) -> Any:
        async with db.unit_of_work(read_only=True) as uow:  # owner bypasses RLS
            return await uow.session.scalar(
                text("SELECT count(*) FROM memory_item WHERE expires_at <= now()")
            )

    assert with_db(owner_url, leftovers) == 0


# --- agent end to end: recall + episodes in PostgreSQL ---------------------------------------


def test_agent_memory_end_to_end(app_url: str) -> None:
    settings = AppSettings(database=DatabaseSettings(url=app_url))
    scope = MemoryScope(f"e2e-{uuid.uuid4().hex[:8]}", "asha")
    clock = ClockService(now_utc=lambda: FIXED_NOW)

    def agent(*script: object) -> tuple[Agent, ScriptedLLM]:
        llm = ScriptedLLM(script=list(script))
        deps = build_dependencies(settings, llm=llm, clock=clock)
        saver: BaseCheckpointSaver[Any] = PostgresCheckpointSaver(deps.database)  # type: ignore[arg-type]
        return Agent(deps, checkpointer=saver), llm

    first, _ = agent(understanding(intent="greet"), "Hello!")
    memory = first.dependencies.memory
    assert memory is not None and memory.store_name == "postgres"
    with_db(
        app_url,
        lambda db: make_manager(PostgresMemoryStore(db)).semantic.ingest(
            scope, source_ref="hr", text="Paid leave is 18 days per year for every employee."
        ),
    )
    asha = principal(scope.tenant_id, "asha")
    first.run("Hello", thread_id=f"{scope.tenant_id}-t1", principal=asha)

    second, llm = agent(understanding(intent="ask_leave"), "You get 18 days.")
    second.run(
        "How many days of paid leave per year?", thread_id=f"{scope.tenant_id}-t2", principal=asha
    )
    prompt = llm.calls[1].messages[-1].content
    assert "Paid leave is 18 days per year" in prompt  # semantic recall from pgvector

    episodes = with_db(
        app_url, lambda db: make_manager(PostgresMemoryStore(db)).episodic.recent(scope)
    )
    assert [e.thread_id for e in episodes] == [f"{scope.tenant_id}-t2", f"{scope.tenant_id}-t1"]
    assert all(e.expires_at is not None for e in episodes)
