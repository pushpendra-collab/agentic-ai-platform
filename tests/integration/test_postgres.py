"""Integration tests against a real PostgreSQL.

Run `docker compose up -d postgres`, then set:

    TEST_DATABASE_URL=postgresql://agentic_owner:agentic_owner_dev@localhost:5432/agentic_test
    TEST_DATABASE_APP_URL=postgresql://agentic_app:agentic_app_dev@localhost:5432/agentic_test

Without TEST_DATABASE_URL these tests are skipped. They use the dedicated
`agentic_test` database and create/drop their own test-only table.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

import pytest
from alembic import command
from sqlalchemy import String, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, DatabaseSettings, normalise_postgres_url
from agentic_platform.core.errors import (
    DatabaseConflictError,
    DatabasePermissionError,
    DatabaseReadOnlyError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
    RecordNotFoundError,
)
from agentic_platform.db import Database, Repository
from agentic_platform.db.migrations import alembic_config
from agentic_platform.services.database_health import DatabaseHealthService
from tests.conftest import understanding
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.postgres

T = TypeVar("T")


# --- test-only schema (never part of application metadata) --------------------------


class _TestBase(DeclarativeBase):
    pass


class Widget(_TestBase):
    __tablename__ = "it_widgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)


class WidgetRepository(Repository[Widget]):
    model = Widget

    async def by_code(self, code: str) -> Widget | None:
        result: Widget | None = await self.session.scalar(select(Widget).where(Widget.code == code))
        return result


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def owner_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture
def app_url() -> str:
    url = os.environ.get("TEST_DATABASE_APP_URL")
    if not url:
        pytest.skip("TEST_DATABASE_APP_URL not set")
    return url


def run(fn: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(fn())  # type: ignore[arg-type]


@pytest.fixture
def widgets_table(owner_url: str) -> Iterator[None]:
    async def setup(create: bool) -> None:
        engine = create_async_engine(normalise_postgres_url(owner_url))
        async with engine.begin() as conn:
            await conn.run_sync(_TestBase.metadata.drop_all)
            if create:
                await conn.run_sync(_TestBase.metadata.create_all)
        await engine.dispose()

    asyncio.run(setup(create=True))
    yield
    asyncio.run(setup(create=False))


def database(url: str, **overrides: Any) -> Database:
    return Database(DatabaseSettings(url=url, **overrides))


async def using(db: Database, fn: Callable[[Database], Awaitable[T]]) -> T:
    try:
        return await fn(db)
    finally:
        await db.dispose()


# --- migrations + health -----------------------------------------------------------


def test_migrations_and_health(owner_url: str) -> None:
    config = alembic_config(owner_url)
    command.downgrade(config, "base")

    async def health() -> Any:
        return await using(database(owner_url), lambda db: DatabaseHealthService(db).check())

    before = asyncio.run(health())
    assert before.status == "degraded"
    assert before.migrations.current_revision is None

    command.upgrade(config, "head")
    after = asyncio.run(health())
    assert after.status == "ok"
    assert after.migrations.up_to_date is True
    assert after.server_version.startswith("16")
    assert after.read_only_replica is False
    assert after.latency_ms is not None

    command.downgrade(config, "base")
    command.upgrade(config, "head")  # downgrade path is reversible


def test_wrong_password_is_unavailable_not_retryable(owner_url: str) -> None:
    bad = owner_url.replace("agentic_owner_dev", "wrong-password")
    if bad == owner_url:
        pytest.skip("TEST_DATABASE_URL does not use the default dev password")

    async def body(db: Database) -> None:
        with pytest.raises(DatabaseUnavailableError) as info:
            async with db.unit_of_work() as uow:  # connections are opened lazily
                await uow.session.execute(text("SELECT 1"))
        assert info.value.retryable is False

    run(lambda: using(database(bad), body))


# --- unit of work + repository --------------------------------------------------------


def test_commit_rollback_and_repository(owner_url: str, widgets_table: None) -> None:
    async def body(db: Database) -> None:
        async with db.unit_of_work() as uow:
            repo = WidgetRepository(uow.session)
            created = await repo.add(Widget(code="A"))
            assert created.id is not None  # flushed: database default visible

        with pytest.raises(RuntimeError):
            async with db.unit_of_work() as uow:
                await WidgetRepository(uow.session).add(Widget(code="B"))
                raise RuntimeError("business rule failed")  # non-DB errors propagate unchanged

        async with db.unit_of_work(read_only=True) as uow:
            repo = WidgetRepository(uow.session)
            assert await repo.count() == 1
            assert (await repo.by_code("A")) is not None
            assert (await repo.by_code("B")) is None  # rolled back
            assert [w.code for w in await repo.list_page(limit=10_000)] == ["A"]
            with pytest.raises(RecordNotFoundError):
                await repo.get_required(999_999)

        async with db.unit_of_work() as uow:
            repo = WidgetRepository(uow.session)
            await repo.delete(await repo.get_required(created.id))
        async with db.unit_of_work(read_only=True) as uow:
            assert await WidgetRepository(uow.session).count() == 0

    run(lambda: using(database(owner_url), body))


def test_unique_violation_is_conflict(owner_url: str, widgets_table: None) -> None:
    async def body(db: Database) -> None:
        async with db.unit_of_work() as uow:
            await WidgetRepository(uow.session).add(Widget(code="DUP"))
        with pytest.raises(DatabaseConflictError) as info:
            async with db.unit_of_work() as uow:
                await WidgetRepository(uow.session).add(Widget(code="DUP"))
        assert "uq_" in info.value.detail or "constraint=" in info.value.detail
        assert "DUP" not in info.value.detail  # no row values in errors

    run(lambda: using(database(owner_url), body))


def test_read_only_unit_rejects_writes(owner_url: str, widgets_table: None) -> None:
    async def body(db: Database) -> None:
        with pytest.raises(DatabaseReadOnlyError):
            async with db.unit_of_work(read_only=True) as uow:
                await WidgetRepository(uow.session).add(Widget(code="RO"))

    run(lambda: using(database(owner_url), body))


def test_statement_timeout(owner_url: str) -> None:
    async def body(db: Database) -> None:
        with pytest.raises(DatabaseTimeoutError):
            async with db.unit_of_work(read_only=True) as uow:
                await uow.session.execute(text("SELECT pg_sleep(2)"))

    run(lambda: using(database(owner_url, statement_timeout_ms=200), body))


def test_pool_exhaustion_is_unavailable(owner_url: str) -> None:
    async def body(db: Database) -> None:
        async with db.unit_of_work(read_only=True):  # holds the only connection
            with pytest.raises(DatabaseUnavailableError):
                async with db.unit_of_work(read_only=True):
                    pass

    run(
        lambda: using(
            database(owner_url, pool_size=1, max_overflow=0, pool_timeout_seconds=0.3), body
        )
    )


# --- least privilege -----------------------------------------------------------------


def test_app_role_has_dml_but_no_ddl(app_url: str, widgets_table: None) -> None:
    async def body(db: Database) -> None:
        async with db.unit_of_work() as uow:  # DML allowed via default privileges
            await WidgetRepository(uow.session).add(Widget(code="APP"))
        with pytest.raises(DatabasePermissionError):
            async with db.unit_of_work() as uow:
                await uow.session.execute(text("CREATE TABLE app_should_not_create (id int)"))
        with pytest.raises(DatabasePermissionError):
            async with db.unit_of_work() as uow:
                await uow.session.execute(text("DROP TABLE it_widgets"))

    run(lambda: using(database(app_url), body))


# --- end to end: agent → tool → service → repository → PostgreSQL --------------------


def test_agent_database_health_tool(owner_url: str) -> None:
    command.upgrade(alembic_config(owner_url), "head")
    settings = AppSettings(database=DatabaseSettings(url=owner_url))
    llm = ScriptedLLM(
        script=[
            understanding(
                intent="check_database", tool_calls=[("system.database_health", "{}")]
            ),
            "The database is up.",
        ]
    )
    agent = Agent(build_dependencies(settings, llm=llm))
    response = agent.run("Is the database working?")

    assert response.status == "completed"
    (result,) = response.tool_results
    assert result.tool_name == "system.database_health"
    assert result.status == "ok"
    assert result.output is not None
    assert result.output["status"] == "ok"
    assert result.output["schema_up_to_date"] is True
    assert "system.database_health" in llm.calls[0].system  # visible to routing
