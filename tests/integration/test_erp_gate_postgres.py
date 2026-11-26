"""Phase 14: the ERP security gate and tenant binding on a real PostgreSQL (throw-away).

No ERP exists for this repository (Q1). A throw-away database with the TEST schema of
`tests/erp_contract/fixtures.py` and a throw-away read-only role stand in for it; both are
dropped afterwards. Nothing here touches `inventoryai-postgres` or any real ERP.

Shown: a correct read-only role passes the gate with the TEST contract; a write grant,
an owned relation, a schema/type/version mismatch and missing RLS each refuse the
connection; column isolation forces the principal's tenant; RLS isolation binds it per
transaction; the readiness CLI's live path (inspector + gate) passes end to end.

Needs TEST_DATABASE_URL (a role that may CREATE DATABASE / ROLE).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg  # type: ignore[import-untyped]
import pytest
from scripts import erp_readiness

from agentic_platform.business.adapters.postgres import ReadOnlyErpDatabase
from agentic_platform.business.adapters.sql_guard import ErpQuery
from agentic_platform.business.erp_contract import ErpContract
from agentic_platform.business.ports import ErpSafetyViolationError
from agentic_platform.core.config import ErpSettings
from tests.erp_contract.fixtures import TEST_CONTRACT, TEST_SCHEMA_DDL, contract_dict

pytestmark = pytest.mark.postgres

CUSTOMERS_BY_TENANT = ErpQuery(
    "test.customers",
    "SELECT customer_ref FROM erp_test.customer WHERE tenant_code = :tenant ORDER BY 1",
)
CUSTOMERS_RLS = ErpQuery(
    "test.customers_rls", "SELECT customer_ref FROM erp_test.customer ORDER BY 1"
)


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
    reader: str
    owner_url: str
    reader_url: str
    major: int

    def contract(self, **changes: Any) -> dict[str, Any]:
        database = {**TEST_CONTRACT["database"], "name": self.database,
                    "postgres_major_version": self.major}  # fmt: skip
        return contract_dict(database=database, **changes)


async def _sql(url: str, sql: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture
def scratch() -> Iterator[Scratch]:
    owner = os.environ.get("TEST_DATABASE_URL")
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = uuid.uuid4().hex[:10]
    database, reader = f"agentic_p14_erp_{suffix}", f"agentic_p14_reader_{suffix}"

    async def create() -> int:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
            await admin.execute(f"CREATE ROLE \"{reader}\" LOGIN PASSWORD '{suffix}' "
                                "NOSUPERUSER NOBYPASSRLS")  # fmt: skip
            major = int(await admin.fetchval("SHOW server_version_num")) // 10000
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip("the test role may not create databases or roles")
        finally:
            await admin.close()
        await _sql(_url(owner, database), TEST_SCHEMA_DDL + f"""
            INSERT INTO erp_test.customer VALUES
                ('acme', 'C-ACME-1', 'Acme Customer (TEST)', '000', 'a@test'),
                ('globex', 'C-GLOBEX-1', 'Globex Customer (TEST)', '111', 'g@test');
            GRANT CONNECT ON DATABASE "{database}" TO "{reader}";
            GRANT USAGE ON SCHEMA erp_test TO "{reader}";
            GRANT SELECT ON ALL TABLES IN SCHEMA erp_test TO "{reader}";
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        """)  # fmt: skip
        await _sql(_plain(owner), f'ALTER ROLE "{reader}" SET default_transaction_read_only = on')
        return major

    async def drop() -> None:
        admin = await asyncpg.connect(_plain(owner))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            await admin.execute(f'DROP ROLE IF EXISTS "{reader}"')
        finally:
            await admin.close()

    major = asyncio.run(create())
    try:
        yield Scratch(database, reader, _url(owner, database),
                      _url(owner, database, reader, suffix), major)  # fmt: skip
    finally:
        asyncio.run(drop())


def _db(s: Scratch, contract: dict[str, Any] | None) -> ReadOnlyErpDatabase:
    settings = ErpSettings(adapter="postgres", database_url=s.reader_url,
                           expected_database=s.database)  # fmt: skip
    model = ErpContract.model_validate(contract) if contract is not None else None
    return ReadOnlyErpDatabase(settings, contract=model)


def _verify(s: Scratch, contract: dict[str, Any] | None) -> str | None:
    async def body() -> str | None:
        db = _db(s, contract)
        try:
            await db.verify()
            return None
        except ErpSafetyViolationError as exc:
            return str(exc)
        finally:
            await db.dispose()

    return asyncio.run(body())


def _fetch(s: Scratch, contract: dict[str, Any], query: ErpQuery, params: dict[str, Any],
           tenant: str) -> list[str]:  # fmt: skip
    async def body() -> list[str]:
        db = _db(s, contract)
        try:
            rows = await db.fetch(query, params, tenant_id=tenant)
            return [r["customer_ref"] for r in rows]
        finally:
            await db.dispose()

    return asyncio.run(body())


def test_a_read_only_role_passes_the_gate_with_the_contract(scratch: Scratch) -> None:
    assert _verify(scratch, None) is None  # identity + effective privileges
    assert _verify(scratch, scratch.contract()) is None  # + schema, version, isolation


def test_a_write_grant_refuses_the_connection(scratch: Scratch) -> None:
    asyncio.run(_sql(scratch.owner_url, f'GRANT INSERT ON erp_test.stock TO "{scratch.reader}"'))
    problem = _verify(scratch, None)
    assert problem is not None and "write privileges" in problem


def test_an_owned_relation_refuses_the_connection(scratch: Scratch) -> None:
    asyncio.run(_sql(scratch.owner_url, f'GRANT CREATE ON SCHEMA erp_test TO "{scratch.reader}"'))
    asyncio.run(
        _sql(scratch.reader_url, "BEGIN READ WRITE; CREATE TABLE erp_test.mine (x int); COMMIT")
    )
    asyncio.run(
        _sql(scratch.owner_url, f'REVOKE CREATE ON SCHEMA erp_test FROM "{scratch.reader}"')
    )
    problem = _verify(scratch, None)
    assert problem is not None and "owns relations" in problem


def test_schema_mismatches_refuse_the_connection(scratch: Scratch) -> None:
    capabilities = json.loads(json.dumps(TEST_CONTRACT["capabilities"]))
    for column in capabilities["daily_sales"]["columns"]:
        if column["column"] == "posted_total":
            column["data_type"] = "numeric(18,4)"  # the catalog says numeric(14,2)
    capabilities["daily_sales"]["columns"].append(
        {"relation": "erp_test.daily_sales", "column": "net_total", "data_type": "numeric(14,2)",
         "role": "field", "record_field": "total"}
    )  # fmt: skip
    problem = _verify(scratch, scratch.contract(capabilities=capabilities))
    assert problem is not None
    assert "posted_total: type numeric(14,2), contract numeric(18,4)" in problem
    assert "net_total: column not found" in problem
    wrong_version = scratch.contract()
    wrong_version["database"]["postgres_major_version"] = scratch.major - 1
    problem = _verify(scratch, wrong_version)
    assert problem is not None and f"PostgreSQL {scratch.major}" in problem


def test_column_isolation_returns_only_the_principals_tenant(scratch: Scratch) -> None:
    contract = scratch.contract()
    # The caller's own `tenant` value is overwritten by the principal's tenant.
    assert _fetch(scratch, contract, CUSTOMERS_BY_TENANT, {"tenant": "globex"}, "acme") == [
        "C-ACME-1"
    ]
    assert _fetch(scratch, contract, CUSTOMERS_BY_TENANT, {}, "globex") == ["C-GLOBEX-1"]
    with pytest.raises(ErpSafetyViolationError, match="no tenant filter"):
        _fetch(scratch, contract, CUSTOMERS_RLS, {}, "acme")


RLS = {**TEST_CONTRACT["tenant_isolation"], "mechanism": "rls_session_setting", "column": None,
       "setting": "erp.tenant_code"}  # fmt: skip


def test_rls_isolation_must_exist_and_then_binds_the_tenant(scratch: Scratch) -> None:
    contract = scratch.contract(tenant_isolation=RLS)
    problem = _verify(scratch, contract)
    assert problem is not None and "row-level security is not enabled" in problem
    tables = ("customer", "invoice", "daily_sales", "warehouse", "product", "stock")
    asyncio.run(_sql(scratch.owner_url, "".join(
        f"ALTER TABLE erp_test.{t} ENABLE ROW LEVEL SECURITY;"
        f"CREATE POLICY tenant_only ON erp_test.{t} "
        f"USING (tenant_code = current_setting('erp.tenant_code', true));"
        for t in tables
    )))  # fmt: skip
    assert _verify(scratch, contract) is None
    # No tenant filter in the query at all: the RLS binding alone isolates the tenant.
    assert _fetch(scratch, contract, CUSTOMERS_RLS, {}, "acme") == ["C-ACME-1"]
    assert _fetch(scratch, contract, CUSTOMERS_RLS, {}, "globex") == ["C-GLOBEX-1"]


def test_readiness_live_path_end_to_end(
    scratch: Scratch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(scratch.contract()), encoding="utf-8")
    # The throw-away server has no TLS; the contract asks for `require`.
    env = {"ERP_INSPECT_DATABASE_URL": scratch.reader_url, "ERP_SSL_MODE": "disable"}
    code = erp_readiness.main(["--contract", str(path), "--live", "--json"], environ=env)
    payload = json.loads(capsys.readouterr().out)
    assert code == erp_readiness.EXIT_READY, payload
    assert payload["state"] == "READY_FOR_ACTIVATION" and payload["problems"] == []
    assert {"check": "adapter.gate", "status": "PASS"} in payload["live"]
    assert not any(c["status"] == "FAIL" for c in payload["live"])
    assert scratch.reader_url.split("@")[0].split(":")[-1] not in json.dumps(payload)  # password

    asyncio.run(_sql(scratch.owner_url, f'GRANT DELETE ON erp_test.invoice TO "{scratch.reader}"'))
    code = erp_readiness.main(["--contract", str(path), "--live", "--json"], environ=env)
    payload = json.loads(capsys.readouterr().out)
    assert code == erp_readiness.EXIT_FAILED
    assert "security inspection failed: stopped before any read" in payload["problems"]
