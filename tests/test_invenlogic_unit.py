"""InvenLogic integration (2026-10-30): pure checks that need no database.

* every column the reader SQL touches is mapped in the contract (so the security gate,
  which checks mapped columns against the live catalog, covers every column read);
* every reader statement filters its own `company_id` with the tenant;
* the contract file is valid, names only the three read databases and never `auth_db`;
* multi-database settings, the production policy and the factory refuse mismatches;
* the Indian financial year (April-March) periods.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from agentic_platform.business.adapters.invenlogic import (
    DATABASE_KEYS,
    QUERIES,
    READERS,
    like_pattern,
)
from agentic_platform.business.capabilities import CAPABILITIES
from agentic_platform.business.dates import DatePeriod, period_bounds, year_start
from agentic_platform.business.erp_contract import (
    ErpContract,
    load_contract,
    missing_inputs,
    validate_contract,
)
from agentic_platform.business.factory import build_erp_port
from agentic_platform.business.rules import BusinessRule
from agentic_platform.core.config import AppSettings, DatabaseSettings, ErpSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.core.production import check_configuration
from agentic_platform.services.clock import ClockService

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "erp" / "invenlogic_contract.json"

# Table aliases used by the reader SQL, per database key.
ALIASES = {
    "master_data": {"c": "customer", "p": "product", "u": "uom", "w": "warehouse"},
    "sales": {
        "i": "sales_invoice",
        "b": "sales_opening_bill",
        "r": "sales_receipt",
        "n": "sales_credit_note",
        "a": "receivable_allocation",
    },
    "inventory": {"b": "stock_balance", "r": "reorder_rule"},
}


def _contract() -> ErpContract:
    return load_contract(CONTRACT)


def _mapped_columns(contract: ErpContract) -> set[tuple[str | None, str, str]]:
    """(database key, table, column) of every mapped column."""
    database_of = {
        (r.database, r.qualified) for m in contract.capabilities.values() for r in m.sources
    }
    out: set[tuple[str | None, str, str]] = set()
    for mapping in contract.capabilities.values():
        for column in mapping.columns:
            for key, qualified in database_of:
                if qualified == column.relation and any(
                    s.qualified == qualified and s.database == key for s in mapping.sources
                ):
                    out.add((key, qualified.split(".")[1], column.column))
    return out


def test_every_column_the_readers_touch_is_mapped_in_the_contract() -> None:
    mapped = _mapped_columns(_contract())
    unmapped: set[tuple[str, str, str]] = set()
    for key, query in QUERIES:
        for alias, column in re.findall(r"\b([a-z])\.([a-z_]+)\b", query.sql):
            if alias == "d":  # the derived table of UNKNOWN_STATUSES (statuses checked above)
                continue
            table = ALIASES[key].get(alias)
            assert table is not None, (query.name, alias)
            if (key, table, column) not in mapped:
                unmapped.add((key, table, column))
    assert not unmapped, (
        f"columns read but not in the contract (the gate would not check them): {sorted(unmapped)}"
    )


def test_every_reader_statement_filters_the_tenant_itself() -> None:
    for _, query in QUERIES:
        assert "company_id = CAST(CAST(:tenant AS text) AS bigint)" in query.sql, query.name


def test_the_readers_cover_exactly_the_six_capabilities() -> None:
    assert {r.capability for r in READERS} == {c.name for c in CAPABILITIES}


def test_the_contract_file_is_valid_and_reads_only_three_databases() -> None:
    contract = _contract()
    assert validate_contract(contract) == []
    assert contract.reader_set == "invenlogic_v1" and contract.currency == "INR"
    keys = {r.database for r in contract.relations()}
    assert keys == set(DATABASE_KEYS)
    text = CONTRACT.read_text(encoding="utf-8")
    assert "auth_db" not in text and "password" not in text.lower()
    # Only the items the owner must still supply are missing (no business rule is).
    assert [m.item for m in missing_inputs(contract)] == ["A", "B", "C", "D", "E", "F", "G", "T"]
    assert BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL in contract.rules
    assert BusinessRule.YEAR_IS_CALENDAR_YEAR not in contract.rules


def test_a_contract_confirming_both_year_rules_is_refused() -> None:
    raw = json.loads(CONTRACT.read_text(encoding="utf-8"))
    raw["rules"]["year_is_calendar_year"] = raw["rules"]["year_is_financial_year_april"]
    problems = validate_contract(ErpContract.model_validate(raw))
    assert any("calendar year or the financial year" in p for p in problems)


def test_a_database_key_must_be_declared() -> None:
    raw = json.loads(CONTRACT.read_text(encoding="utf-8"))
    raw["databases"] = {
        "sales": {"host": "erp.internal", "port": 5432, "name": "sales_db",
                  "postgres_major_version": 18, "schema_version": "V36"},
    }  # fmt: skip
    problems = validate_contract(ErpContract.model_validate(raw))
    assert any("names no contract database" in p for p in problems)


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern(" 50%_off\\ ") == "%50\\%\\_off\\\\%"


# --- settings, production policy, factory ------------------------------------------------------

URLS = {
    "master_data": "postgresql://ro:Real-Pw-1@erp.internal:5432/master_data_db",
    "sales": "postgresql://ro:Real-Pw-1@erp.internal:5432/sales_db",
    "inventory": "postgresql://ro:Real-Pw-1@erp.internal:5432/inventory_db",
}


def test_database_urls_are_parsed_and_never_echoed() -> None:
    settings = ErpSettings(adapter="postgres", database_urls=json.dumps(URLS))
    parsed = settings.database_url_map()
    assert set(parsed) == set(URLS)
    assert parsed["sales"].get_secret_value().startswith("postgresql+asyncpg://")
    with pytest.raises(ValueError) as caught:
        ErpSettings(database_urls="not-json postgresql://ro:Secret-9@h/x")
    assert "Secret-9" not in str(caught.value)
    with pytest.raises(ValueError):
        ErpSettings(database_urls=json.dumps({"Bad Key": "postgresql://h/x"}))


def test_production_refuses_an_erp_url_that_is_the_agent_db(tmp_path: Path) -> None:
    urls = dict(URLS, sales="postgresql://ro:Real-Pw-1@db:5432/agentic")
    settings = AppSettings(
        env="production",
        database=DatabaseSettings(url="postgresql://app:Real-Pw-2@db:5432/agentic"),
        erp=ErpSettings(adapter="postgres", database_urls=json.dumps(urls)),
    )
    report = check_configuration(settings, role="api", environ={})
    issues = {(i.code, i.setting) for i in report.errors}
    assert ("ERP_URL_IS_AGENT_DB", "ERP_DATABASE_URLS[sales]") in issues
    assert "ERP_EXPECTED_DATABASE_MISSING" not in {code for code, _ in issues}


def test_production_requires_the_contracts_databases(tmp_path: Path) -> None:
    raw = json.loads(CONTRACT.read_text(encoding="utf-8"))
    raw["databases"] = {
        key: {"host": "erp.internal", "port": 5432, "name": f"{key}_db",
              "postgres_major_version": 18, "schema_version": "V1"}
        for key in DATABASE_KEYS
    }  # fmt: skip
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    two = {k: v for k, v in URLS.items() if k != "inventory"}
    settings = AppSettings(
        env="production",
        erp=ErpSettings(adapter="postgres", database_urls=json.dumps(two),
                        contract_path=str(path), business_timezone="Asia/Kolkata"),
    )  # fmt: skip
    codes = {i.code for i in check_configuration(settings, role="api", environ={}).errors}
    assert "ERP_CONTRACT_DATABASES_MISMATCH" in codes
    with pytest.raises(ConfigurationError, match="ERP_DATABASE_URLS must name exactly"):
        build_erp_port(AppSettings(env="test", erp=settings.erp), ClockService())


def test_the_factory_builds_one_gated_connection_per_database(tmp_path: Path) -> None:
    raw = json.loads(CONTRACT.read_text(encoding="utf-8"))
    raw["databases"] = {
        key: {"host": "erp.internal", "port": 5432, "name": f"{key}_db",
              "postgres_major_version": 18, "schema_version": "V1"}
        for key in DATABASE_KEYS
    }  # fmt: skip
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    erp = ErpSettings(adapter="postgres", database_urls=json.dumps(URLS), contract_path=str(path))
    port = build_erp_port(AppSettings(env="test", erp=erp), ClockService())
    assert port is not None and port.name == "postgres" and not port.test_data
    assert set(port.databases) == set(DATABASE_KEYS)  # type: ignore[attr-defined]
    # The contract is DRAFT: no reader is registered, nothing is answered, nothing connects.
    assert port.gate_state() == "unverified"  # type: ignore[attr-defined]


# --- the Indian financial year ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "start"),
    [(date(2026, 10, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 4, 1)),
     (date(2026, 3, 31), date(2025, 4, 1)), (date(2027, 1, 15), date(2026, 4, 1))],
)  # fmt: skip
def test_financial_year_start(today: date, start: date) -> None:
    assert year_start(today, 4) == start
    assert year_start(today) == date(today.year, 1, 1)


def test_financial_year_periods() -> None:
    today = date(2026, 10, 1)
    assert period_bounds(DatePeriod.THIS_YEAR, today, 4) == (date(2026, 4, 1), date(2026, 10, 2))
    assert period_bounds(DatePeriod.LAST_YEAR, today, 4) == (date(2025, 4, 1), date(2026, 4, 1))
    assert period_bounds(DatePeriod.THIS_YEAR, today) == (date(2026, 1, 1), date(2026, 10, 2))
