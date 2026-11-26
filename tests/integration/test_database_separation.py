"""Phase 8: the migration job never touches a database it does not own.

The Agent DB is separate from any system of record (for example an existing ERP
database). This test creates a throw-away database that looks like such a system (it
holds a table but no `alembic_version`), points the real migration path at it and checks
that nothing is created there. The throw-away database is dropped afterwards; no other
database is modified.

Needs TEST_DATABASE_URL with a role that may CREATE DATABASE (the local Docker owner).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from alembic import command

from agentic_platform.db.migrations import ForeignDatabaseError, alembic_config

pytestmark = pytest.mark.postgres


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(_plain(url))
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture
def foreign_database() -> Iterator[str]:
    owner = os.environ.get("TEST_DATABASE_URL")
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"agentic_p8_foreign_{uuid.uuid4().hex[:10]}"

    async def create() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip("the test role may not create databases")
        finally:
            await admin.close()
        conn = await asyncpg.connect(_with_database(owner, name))
        try:  # what a system of record looks like to the migration job
            await conn.execute("CREATE TABLE stock_item (id int PRIMARY KEY, sku text)")
            await conn.execute("INSERT INTO stock_item VALUES (1, 'SKU-1')")
        finally:
            await conn.close()

    async def drop() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(create())
    try:
        yield _with_database(owner, name)
    finally:
        asyncio.run(drop())


def test_migration_job_refuses_a_database_it_does_not_own(
    foreign_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTIC_MIGRATE_NON_EMPTY_DATABASE", raising=False)
    with pytest.raises(ForeignDatabaseError, match=r"public\.stock_item"):
        command.upgrade(alembic_config(foreign_database), "head")

    async def inspect() -> tuple[object, object, object, object]:
        conn = await asyncpg.connect(foreign_database)
        try:
            return (
                await conn.fetchval("SELECT to_regclass('alembic_version')"),
                await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'vector'"),
                await conn.fetchval(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
                ),
                await conn.fetchval("SELECT sku FROM stock_item WHERE id = 1"),
            )
        finally:
            await conn.close()

    version_table, vector, tables, sku = asyncio.run(inspect())
    assert version_table is None  # Alembic did not even create its bookkeeping table
    assert vector == 0  # no extension installed
    assert tables == 1  # only the pre-existing table
    assert sku == "SKU-1"  # data untouched


def test_agent_database_is_still_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard does not get in the way of the Agent DB (it has `alembic_version`)."""
    owner = os.environ.get("TEST_DATABASE_URL")
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.delenv("AGENTIC_MIGRATE_NON_EMPTY_DATABASE", raising=False)
    command.upgrade(alembic_config(owner), "head")
