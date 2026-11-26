"""Phase 10: ERP boundary without a database: SQL guard, settings isolation, production
policy, factory, figure grounding (UNIT VERIFIED)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from agentic_platform.business.adapters.postgres import (
    IDENTITY_QUERY,
    ErpQuery,
    ReadOnlyErpDatabase,
    check_read_only_sql,
    erp_database_settings,
    read_only_engine,
)
from agentic_platform.business.factory import build_erp_port
from agentic_platform.business.grounding import numbers_in, render_results, ungrounded_numbers
from agentic_platform.business.ports import ErpSafetyViolationError
from agentic_platform.core.config import AppSettings, AuthSettings, DatabaseSettings, ErpSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.core.production import check_configuration
from agentic_platform.services.clock import ClockService

# --- statement guard ----------------------------------------------------------------------
from tests.erp_contract.fixtures import TEST_CONTRACT, contract_dict


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM customer WHERE tenant = :t",
        "with x as (select 1 as a) select a from x",
        "SELECT current_setting('transaction_read_only')",
        IDENTITY_QUERY.sql,
    ],
)
def test_plain_reads_pass(sql: str) -> None:
    check_read_only_sql(sql)
    ErpQuery("test.read", sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customer VALUES (1)",
        "UPDATE stock SET qty = 0",
        "DELETE FROM invoice",
        "TRUNCATE invoice",
        "DROP TABLE invoice",
        "CREATE TABLE x (a int)",
        "ALTER TABLE invoice ADD COLUMN x int",
        "GRANT SELECT ON invoice TO public",
        "SELECT 1; DELETE FROM invoice",
        "SELECT 1 -- comment",
        "SELECT /* x */ 1",
        "WITH d AS (DELETE FROM invoice RETURNING *) SELECT * FROM d",
        "SELECT * INTO backup FROM invoice",
        "SELECT * FROM invoice FOR UPDATE",
        "SELECT * FROM invoice FOR SHARE",
        "SELECT nextval('invoice_seq')",
        "SELECT set_config('app.tenant', 'x', false)",
        "SELECT pg_advisory_lock(1)",
        "SELECT dblink_exec('x', 'DELETE FROM y')",
        "SELECT pg_sleep(10)",
        "COPY invoice TO '/tmp/x'",
        "SET ROLE erp_owner",
        "CALL do_something()",
        "  ",
    ],
)
def test_anything_but_a_plain_read_is_refused(sql: str) -> None:
    with pytest.raises(ErpSafetyViolationError):
        check_read_only_sql(sql)
    with pytest.raises(ErpSafetyViolationError):
        ErpQuery("test.bad", sql)


def test_a_forged_query_is_refused_again_at_execution() -> None:
    query = ErpQuery("test.read", "SELECT 1")
    object.__setattr__(query, "sql", "DELETE FROM invoice")  # bypasses __post_init__
    db = ReadOnlyErpDatabase(ErpSettings(adapter="postgres",
                                         database_url="postgresql://ro:x@127.0.0.1:1/erp",
                                         expected_database="erp"))  # fmt: skip
    import asyncio

    with pytest.raises(ErpSafetyViolationError):
        asyncio.run(db._execute(query, {}, max_rows=1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        query.sql = "SELECT 2"  # type: ignore[misc]


# --- connection settings ------------------------------------------------------------------


def test_agent_db_environment_never_leaks_into_the_erp_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {"DATABASE_ECHO_SQL": "true", "DATABASE_STATEMENT_TIMEOUT_MS": "0",
                        "DATABASE_POOL_SIZE": "50", "DATABASE_SSL_MODE": "disable",
                        "DATABASE_APPLICATION_NAME": "agentic_platform"}.items():  # fmt: skip
        monkeypatch.setenv(name, value)
    erp = ErpSettings(adapter="postgres", database_url="postgresql://ro:x@erp.internal/erp",
                      expected_database="erp", ssl_mode="verify-full")  # fmt: skip
    db = erp_database_settings(erp)
    assert (db.echo_sql, db.statement_timeout_ms, db.pool_size) == (False, 8000, 2)
    assert (db.ssl_mode, db.application_name) == ("verify-full", "agentic_erp_reader")
    assert db.migration_url is None


def test_every_erp_session_is_read_only_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_engine(url: str, **options: object) -> object:
        captured.update(options, url=url)
        return object()

    monkeypatch.setattr("agentic_platform.business.adapters.postgres.create_async_engine",
                        fake_engine)  # fmt: skip
    erp = ErpSettings(adapter="postgres", database_url="postgresql://ro:x@127.0.0.1:1/erp",
                      expected_database="erp")  # fmt: skip
    read_only_engine(erp_database_settings(erp))
    server = captured["connect_args"]["server_settings"]  # type: ignore[index]
    assert server["default_transaction_read_only"] == "on"
    assert server["statement_timeout"] == "8000"
    assert server["application_name"] == "agentic_erp_reader"
    assert captured["echo"] is False


@pytest.mark.parametrize(
    ("erp", "message"),
    [
        (ErpSettings(adapter="postgres", expected_database="erp"), "ERP_DATABASE_URL"),
        (ErpSettings(adapter="postgres", database_url="postgresql://ro:x@h/erp"),
         "ERP_EXPECTED_DATABASE"),
        (ErpSettings(adapter="postgres", database_url="postgresql://ro:x@db:5432/agentic",
                     expected_database="agentic"), "Agent DB"),
    ],
)  # fmt: skip
def test_misconfigured_erp_connection_is_refused(erp: ErpSettings, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        ReadOnlyErpDatabase(erp, agent_database_urls=["postgresql+asyncpg://app:y@DB/agentic"])


def test_erp_settings_are_separate_from_the_agent_db() -> None:
    """Alembic only ever sees DATABASE_*: the migration code never reads ERP settings."""
    from pathlib import Path

    from agentic_platform.db import migrations

    assert {n for n in DatabaseSettings.model_fields if "url" in n} == {"url", "migration_url"}
    folder = Path(migrations.__file__).parent
    for path in (folder / "__init__.py", folder / "env.py"):  # env.py runs only under Alembic
        source = path.read_text(encoding="utf-8")
        assert "ErpSettings" not in source and "settings.erp" not in source
        assert "ERP_DATABASE_URL" not in source


# --- factory and production policy ----------------------------------------------------------


def test_the_fake_adapter_no_longer_exists() -> None:
    """2026-10-30 (owner decision): the fake TEST-data adapter was removed. Selecting it is
    a configuration error in every environment, not only production."""
    with pytest.raises(ValueError, match="adapter"):
        ErpSettings(adapter="fake")  # type: ignore[arg-type]
    with pytest.raises(ImportError):
        __import__("agentic_platform.business.adapters.fake")


def test_none_builds_nothing() -> None:
    assert build_erp_port(AppSettings(env="test"), ClockService()) is None


def _codes(erp: ErpSettings, database_url: str = "postgresql://app:p@db/agentic") -> set[str]:
    settings = AppSettings(env="production", erp=erp, auth=AuthSettings(),
                           database=DatabaseSettings(url=database_url))  # fmt: skip
    return {i.code for i in check_configuration(settings, role="api").issues}


def test_production_policy_for_the_postgres_adapter(tmp_path: Path) -> None:
    assert {"ERP_URL_MISSING", "ERP_EXPECTED_DATABASE_MISSING"} <= _codes(
        ErpSettings(adapter="postgres")
    )
    same = ErpSettings(adapter="postgres", database_url="postgresql://ro:x@db:5432/agentic",
                       expected_database="agentic")  # fmt: skip
    assert "ERP_URL_IS_AGENT_DB" in _codes(same)
    # Phase 14 (ADR-063): production also needs a VERIFIED ERP contract (Q1) whose database
    # and time zone match. Without one the configuration is refused (new assertion); with
    # a complete TEST contract the original expectation (no ERP_* issue) holds.
    no_contract = ErpSettings(adapter="postgres", database_url="postgresql://ro:x@erp.internal/erp",
                              expected_database="erp", ssl_mode="verify-full")  # fmt: skip
    assert "ERP_CONTRACT_MISSING" in _codes(no_contract)
    contract = contract_dict(database={**TEST_CONTRACT["database"], "name": "erp"})
    path = tmp_path / "erp_contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    good = no_contract.model_copy(update={"contract_path": str(path),
                                          "business_timezone": "Asia/Kolkata"})  # fmt: skip
    assert not {c for c in _codes(good) if c.startswith("ERP_")}
    weak = good.model_copy(update={"ssl_mode": None})
    assert "ERP_TLS_WEAK" in _codes(weak)
    assert not {c for c in _codes(ErpSettings()) if c.startswith("ERP_")}  # none: nothing


# --- grounding ----------------------------------------------------------------------------


def test_numbers_are_normalised() -> None:
    assert numbers_in("₹45,000.00 and 4,50,000 and 45000") == {"45000", "450000"}
    assert numbers_in("TEST-INV-001 on 2026-10-01") == {"2026", "10", "1"}
    assert ungrounded_numbers("owes ₹50,000", {"45000"}) == ["50000"]
    assert ungrounded_numbers("owes ₹45,000", {"45000"}) == []


def test_rendering_uses_only_the_results() -> None:
    text = render_results([
        {"capability": "customer_outstanding", "status": "SUCCESS",
         "data": {"customer": {"ref": "C1", "display_name": "Rahul Gupta (TEST)"},
                  "outstanding": {"amount": "45000.00", "currency": "INR"}},
         "source": {"test_data": True}},
        {"capability": "daily_sales", "status": "UNAVAILABLE", "reason": "ERP_TIMEOUT"},
        {"capability": "x", "status": "AMBIGUOUS",
         "candidates": [{"ref": "a", "display_name": "A"}, {"ref": "b", "display_name": "B"}]},
    ])  # fmt: skip
    assert "45000.00 INR" in text and "TEST data" in text
    assert "could not be retrieved" in text
    assert "A, B" in text
