"""Phase 9: `scripts/inspect_erp_security` against a real PostgreSQL.

No ERP is available to this repository (Q1), so the inspector is verified against a
throw-away database shaped like a system of record: a tenant table under RLS whose policy
reads `current_setting('app.tenant_id')`, a table without RLS, a view, a sequence and a
Flyway history table. A throw-away login role with SELECT only stands in for the ERP
read-only role. Both are dropped afterwards; no other database or role is modified.

Needs TEST_DATABASE_URL with a role that may CREATE DATABASE and CREATE ROLE (the local
Docker owner). TEST_DATABASE_URL's own database is used only for the Agent DB refusal.
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
from scripts.inspect_erp_security import InspectionRefused, evaluate, inspect

pytestmark = pytest.mark.postgres

SNAPSHOT_SQL = """
SELECT (SELECT array_agg(row(id, tenant_id, name)::text ORDER BY id) FROM customer),
       (SELECT array_agg(sku ORDER BY sku) FROM stock_item),
       (SELECT last_value FROM customer_id_seq),
       (SELECT count(*) FROM pg_class WHERE relname LIKE 'probe%')
"""


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _url(base: str, database: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(_plain(base))
    netloc = parts.netloc
    if user is not None:
        host = netloc.rsplit("@", 1)[-1]
        netloc = f"{quote(user)}:{quote(password or '')}@{host}"
    return urlunsplit(parts._replace(netloc=netloc, path=f"/{database}"))


@dataclass(frozen=True)
class ScratchErp:
    database: str
    owner_url: str
    reader_url: str


@pytest.fixture
def scratch_erp() -> Iterator[ScratchErp]:
    owner = os.environ.get("TEST_DATABASE_URL")
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = uuid.uuid4().hex[:10]
    database, reader, password = f"agentic_p9_erp_{suffix}", f"agentic_p9_reader_{suffix}", suffix

    async def create() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
            await admin.execute(
                f"CREATE ROLE \"{reader}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
            await admin.execute(f'ALTER ROLE "{reader}" SET default_transaction_read_only = on')
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip("the test role may not create databases or roles")
        finally:
            await admin.close()
        conn = await asyncpg.connect(_url(owner, database))
        try:  # what an ERP looks like to the inspector
            await conn.execute("""
                CREATE SEQUENCE customer_id_seq;
                CREATE TABLE customer (
                    id int PRIMARY KEY DEFAULT nextval('customer_id_seq'),
                    tenant_id text NOT NULL, name text NOT NULL);
                ALTER TABLE customer ENABLE ROW LEVEL SECURITY;
                ALTER TABLE customer FORCE ROW LEVEL SECURITY;
                CREATE POLICY tenant_isolation ON customer
                    USING (tenant_id = current_setting('app.tenant_id', true));
                CREATE TABLE stock_item (sku text PRIMARY KEY, qty int NOT NULL);
                CREATE VIEW customer_names AS SELECT name FROM customer;
                CREATE TABLE flyway_schema_history (
                    installed_rank int PRIMARY KEY, version text, description text,
                    success boolean NOT NULL);
                INSERT INTO flyway_schema_history VALUES
                    (1, '1', 'baseline', true), (2, '2.1', 'stock', true);
                INSERT INTO customer (tenant_id, name) VALUES ('t1', 'A'), ('t2', 'B');
                INSERT INTO stock_item VALUES ('SKU-1', 5);
            """)
            await conn.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{reader}"')
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{reader}"')
            await conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{reader}"')
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
        yield ScratchErp(database, _url(owner, database), _url(owner, database, reader, password))
    finally:
        asyncio.run(drop())


async def _snapshot(url: str) -> tuple[object, ...]:
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(SNAPSHOT_SQL)
        assert row is not None
        return tuple(row)
    finally:
        await conn.close()


def _statuses(findings: list[object]) -> dict[str, str]:
    return {f.check: f.status for f in findings}  # type: ignore[attr-defined]


def test_read_only_role_passes_and_nothing_changes(scratch_erp: ScratchErp) -> None:
    before = asyncio.run(_snapshot(scratch_erp.owner_url))
    facts = asyncio.run(inspect(scratch_erp.reader_url, scratch_erp.database))
    after = asyncio.run(_snapshot(scratch_erp.owner_url))
    assert before == after  # rows, sequence position and catalog unchanged

    findings = evaluate(facts)
    result = _statuses(findings)
    assert "FAIL" not in result.values(), [f for f in findings if f.status == "FAIL"]
    assert result["role.nosuperuser"] == result["role.nobypassrls"] == "PASS"
    assert result["role.default_read_only"] == "PASS"
    assert result["privilege.no_table_writes"] == result["privilege.no_ddl"] == "PASS"
    # every INSERT/UPDATE/DELETE plan refused by the server with insufficient_privilege
    assert result["probe.writes_refused"] == "PASS"
    assert {p.sqlstate for p in facts.probes} == {"42501"}
    assert {p.operation for p in facts.probes} == {"INSERT", "UPDATE", "DELETE"}
    # RLS facts from the live catalog
    tables = {t.name: t for t in facts.readable_tables}
    assert tables["customer"].rls and tables["customer"].force_rls
    assert tables["customer"].policies == 1
    assert not tables["stock_item"].rls
    assert result["rls.enabled"] == "WARN"  # stock_item is readable without RLS
    assert facts.tenant_settings == ["app.tenant_id"]
    assert facts.definer_views == ["public.customer_names"]
    assert [(s.latest_version, s.applied, s.failed) for s in facts.flyway] == [("2.1", 2, 0)]


def test_privileged_role_fails_and_its_writes_are_never_executed(scratch_erp: ScratchErp) -> None:
    before = asyncio.run(_snapshot(scratch_erp.owner_url))
    facts = asyncio.run(inspect(scratch_erp.owner_url, scratch_erp.database))
    assert asyncio.run(_snapshot(scratch_erp.owner_url)) == before

    result = _statuses(evaluate(facts))
    for check in (
        "role.nosuperuser",
        "role.nobypassrls",
        "role.owns_nothing",
        "privilege.no_table_writes",
        "privilege.no_ddl",
        "probe.writes_refused",
    ):
        assert result[check] == "FAIL", check
    assert all(p.sqlstate is None for p in facts.probes)  # planned, never executed


def test_a_single_write_grant_is_detected(scratch_erp: ScratchErp) -> None:
    reader = urlsplit(scratch_erp.reader_url).username

    async def grant() -> None:
        conn = await asyncpg.connect(scratch_erp.owner_url)
        try:
            await conn.execute(f'GRANT INSERT ON stock_item TO "{reader}"')
        finally:
            await conn.close()

    asyncio.run(grant())
    before = asyncio.run(_snapshot(scratch_erp.owner_url))
    facts = asyncio.run(inspect(scratch_erp.reader_url, scratch_erp.database))
    assert asyncio.run(_snapshot(scratch_erp.owner_url)) == before

    result = _statuses(evaluate(facts))
    assert result["privilege.no_table_writes"] == "FAIL"
    assert result["probe.writes_refused"] == "FAIL"
    accepted = {(p.operation, p.relation) for p in facts.probes if p.sqlstate is None}
    assert accepted == {("INSERT", "public.stock_item")}


def test_wrong_database_and_agent_database_are_refused(scratch_erp: ScratchErp) -> None:
    with pytest.raises(InspectionRefused, match="expected 'erp_prod'"):
        asyncio.run(inspect(scratch_erp.reader_url, "erp_prod"))

    agent_url = _plain(os.environ["TEST_DATABASE_URL"])
    agent_db = urlsplit(agent_url).path.lstrip("/")
    with pytest.raises(InspectionRefused, match="Agent DB"):
        asyncio.run(inspect(agent_url, agent_db))
