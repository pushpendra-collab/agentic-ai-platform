"""Phase 10: `ReadOnlyErpDatabase` against a real PostgreSQL (throw-away database).

No ERP exists for this repository (Q1). A throw-away database shaped like a system of
record and a throw-away SELECT-only role stand in for it; both are dropped afterwards.
Each safety layer is shown on its own: identity check, statement guard, read-only
transaction (with the guard deliberately switched off), statement timeout.

Needs TEST_DATABASE_URL (a role that may CREATE DATABASE / ROLE) and TEST_DATABASE_APP_URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import pytest

from agentic_platform.business.adapters import postgres as erp_pg
from agentic_platform.business.adapters import sql_guard
from agentic_platform.business.adapters.postgres import ErpQuery, ReadOnlyErpDatabase
from agentic_platform.business.erp_contract import ErpContract
from agentic_platform.business.ports import (
    ErpSafetyViolationError,
    ErpTimeoutError,
    ErpUnavailableError,
)
from agentic_platform.core.config import ErpSettings

pytestmark = pytest.mark.postgres

ROWS = ErpQuery("test.items", "SELECT sku, qty FROM stock_item ORDER BY sku")

# Phase 14 (ADR-063): business reads need the principal's tenant and a contract's tenant
# mechanism. These tests exercise the connection layers (identity, read-only transaction,
# timeout, reachability), so they use a minimal contract: RLS session-setting isolation and
# no capability mapping (no schema check). The binding sets `erp_test.tenant` per read.
LAYERS = ErpContract.model_validate({
    "contract_version": "TEST-LAYERS",
    "tenant_isolation": {
        "mechanism": "rls_session_setting", "setting": "erp_test.tenant",
        "tenant_value": "platform_tenant_id",
        "confirmation": {"confirmed_by": "TEST", "confirmed_on": "2026-10-05",
                         "reference": "TEST-ONLY"},
    },
})  # fmt: skip
TENANT = "tenant_a"
# The refusal codes a write gets from the server (not from the statement guard).
SERVER_REFUSAL = r"DATABASE_READ_ONLY|DATABASE_PERMISSION_DENIED"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _url(base: str, database: str, user: str | None = None, password: str = "") -> str:
    parts = urlsplit(_plain(base))
    netloc = parts.netloc
    if user is not None:
        netloc = f"{quote(user)}:{quote(password)}@{netloc.rsplit('@', 1)[-1]}"
    return urlunsplit(parts._replace(netloc=netloc, path=f"/{database}"))


@dataclass(frozen=True)
class Scratch:
    database: str
    owner_url: str
    reader_url: str


@pytest.fixture
def scratch() -> Iterator[Scratch]:
    owner = os.environ.get("TEST_DATABASE_URL")
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = uuid.uuid4().hex[:10]
    database, reader = f"agentic_p10_erp_{suffix}", f"agentic_p10_reader_{suffix}"

    async def create() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
            await admin.execute(f"CREATE ROLE \"{reader}\" LOGIN PASSWORD '{suffix}' "
                                "NOSUPERUSER NOBYPASSRLS")  # fmt: skip
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip("the test role may not create databases or roles")
        finally:
            await admin.close()
        conn = await asyncpg.connect(_url(owner, database))
        try:
            await conn.execute("""
                CREATE TABLE stock_item (sku text PRIMARY KEY, qty int NOT NULL);
                INSERT INTO stock_item VALUES ('SKU-1', 5), ('SKU-2', 7);
            """)
            await conn.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{reader}"')
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{reader}"')
            await conn.execute(f'GRANT SELECT ON stock_item TO "{reader}"')
        finally:
            await conn.close()

    async def drop() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            await admin.execute(f'DROP ROLE IF EXISTS "{reader}"')
        finally:
            await admin.close()

    asyncio.run(create())
    try:
        yield Scratch(database, _url(owner, database), _url(owner, database, reader, suffix))
    finally:
        asyncio.run(drop())


def erp(url: str, expected: str, **extra: object) -> ErpSettings:
    return ErpSettings(adapter="postgres", database_url=url, expected_database=expected,
                       **extra)  # type: ignore[arg-type]  # fmt: skip


async def _rows(url: str) -> list[tuple[str, int]]:
    conn = await asyncpg.connect(url)
    try:
        return [tuple(r) for r in await conn.fetch("SELECT sku, qty FROM stock_item ORDER BY 1")]
    finally:
        await conn.close()


def test_read_only_role_reads_after_proving_identity(scratch: Scratch) -> None:
    async def body() -> list[dict[str, object]]:
        db = ReadOnlyErpDatabase(erp(scratch.reader_url, scratch.database), contract=LAYERS)
        try:
            rows = await db.fetch(ROWS, tenant_id=TENANT)
            assert db.verified
            return rows
        finally:
            await db.dispose()

    assert asyncio.run(body()) == [{"sku": "SKU-1", "qty": 5}, {"sku": "SKU-2", "qty": 7}]


@pytest.mark.parametrize("case", ["wrong_name", "superuser", "agent_db"])
def test_identity_check_fails_closed(scratch: Scratch, case: str) -> None:
    if case == "wrong_name":
        settings = erp(scratch.reader_url, "erp_prod")
        expected = "unexpected database name"
    elif case == "superuser":  # the local owner role is a superuser
        settings = erp(scratch.owner_url, scratch.database)
        expected = "superuser"
    else:
        app = os.environ.get("TEST_DATABASE_APP_URL")
        if not app:
            pytest.skip("TEST_DATABASE_APP_URL not set")
        settings = erp(_plain(app), urlsplit(app).path.lstrip("/"))
        expected = "alembic_version"

    async def body() -> None:
        db = ReadOnlyErpDatabase(settings, contract=LAYERS)
        try:
            await db.fetch(ROWS, tenant_id=TENANT)
        finally:
            await db.dispose()

    with pytest.raises(ErpSafetyViolationError, match=expected):
        asyncio.run(body())


def test_read_only_transaction_blocks_writes_even_without_the_guard(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch the statement guard off and send writes as the SUPERUSER owner: the
    read-only session/transaction alone must refuse them, and nothing changes."""
    before = asyncio.run(_rows(scratch.owner_url))
    monkeypatch.setattr(erp_pg, "check_read_only_sql", lambda sql: None)
    # Phase 14: the guard moved to sql_guard (ErpQuery checks itself there).
    monkeypatch.setattr(sql_guard, "check_read_only_sql", lambda sql: None)
    writes = [
        ErpQuery("test.insert", "INSERT INTO stock_item VALUES ('SKU-X', 1) RETURNING sku"),
        ErpQuery("test.update", "UPDATE stock_item SET qty = 0 RETURNING sku"),
        ErpQuery("test.delete", "DELETE FROM stock_item RETURNING sku"),
        ErpQuery("test.ddl", "CREATE TABLE probe_x (a int)"),
    ]

    async def body() -> list[str]:
        db = ReadOnlyErpDatabase(erp(scratch.owner_url, scratch.database), contract=LAYERS)
        db._verified = True  # skip the identity check: test the transaction layer alone
        refused: list[str] = []
        try:
            for query in writes:
                # The server refused it (read-only transaction), not the switched-off guard.
                with pytest.raises(ErpSafetyViolationError, match=SERVER_REFUSAL):
                    await db.fetch(query, tenant_id=TENANT)
                refused.append(query.name)
        finally:
            await db.dispose()
        return refused

    assert asyncio.run(body()) == [q.name for q in writes]
    assert asyncio.run(_rows(scratch.owner_url)) == before


def test_read_only_role_cannot_write_either(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = asyncio.run(_rows(scratch.owner_url))
    monkeypatch.setattr(erp_pg, "check_read_only_sql", lambda sql: None)
    # Phase 14: the guard moved to sql_guard (ErpQuery checks itself there).
    monkeypatch.setattr(sql_guard, "check_read_only_sql", lambda sql: None)

    async def body() -> None:
        db = ReadOnlyErpDatabase(erp(scratch.reader_url, scratch.database), contract=LAYERS)
        try:
            with pytest.raises(ErpSafetyViolationError, match=SERVER_REFUSAL):
                await db.fetch(
                    ErpQuery("test.delete", "DELETE FROM stock_item RETURNING sku"),
                    tenant_id=TENANT,
                )
        finally:
            await db.dispose()

    asyncio.run(body())
    assert asyncio.run(_rows(scratch.owner_url)) == before


def test_statement_timeout_is_enforced(scratch: Scratch) -> None:
    slow = ErpQuery("test.slow", "SELECT count(*) FROM generate_series(1, 500000000)")

    async def body() -> None:
        db = ReadOnlyErpDatabase(erp(scratch.reader_url, scratch.database,
                                     statement_timeout_ms=200), contract=LAYERS)  # fmt: skip
        try:
            await db.fetch(slow, tenant_id=TENANT)
        finally:
            await db.dispose()

    with pytest.raises(ErpTimeoutError):
        asyncio.run(body())


def test_unreachable_erp_is_unavailable(scratch: Scratch) -> None:
    async def body() -> None:
        db = ReadOnlyErpDatabase(erp("postgresql://ro:x@127.0.0.1:1/erp", "erp",
                                     connect_timeout_seconds=2), contract=LAYERS)  # fmt: skip
        try:
            await db.fetch(ROWS, tenant_id=TENANT)
        finally:
            await db.dispose()

    with pytest.raises(ErpUnavailableError):
        asyncio.run(body())
