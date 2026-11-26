"""Phase 14: ERP contract, security gate, adapter activation, tenant binding, configuration
and the readiness input gate. No database, no network, no ERP."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from scripts import erp_readiness

from agentic_platform.business.adapters.postgres import (
    METHOD_CAPABILITIES,
    PostgresErpAdapter,
    ReadOnlyErpDatabase,
)
from agentic_platform.business.adapters.sql_guard import ErpQuery
from agentic_platform.business.context import ALL_WAREHOUSES, BusinessContext
from agentic_platform.business.erp_contract import (
    ContractError,
    ErpContract,
    InvoiceStatuses,
    UnexpectedStatusError,
    Verification,
    load_contract,
    missing_inputs,
    validate_contract,
)
from agentic_platform.business.erp_gate import (
    evaluate_contract_schema,
    evaluate_privileges,
    gate_params,
)
from agentic_platform.business.factory import build_erp_port
from agentic_platform.business.ports import ErpRuleNotVerifiedError, ErpSafetyViolationError
from agentic_platform.core.config import AppSettings, AuthSettings, DatabaseSettings, ErpSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.core.production import check_configuration
from agentic_platform.services.clock import ClockService
from tests.erp_contract.fixtures import TEST_CONTRACT, contract_dict

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs" / "erp" / "erp_contract.template.json"
ERP_URL = "postgresql://ro:x@erp.internal:5432/erp_contract_test"


def contract(**changes: Any) -> ErpContract:
    return ErpContract.model_validate(contract_dict(**changes))


def write(tmp_path: Path, data: dict[str, Any], name: str = "contract.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def mapping_with(capability: str, **changes: Any) -> dict[str, Any]:
    capabilities: dict[str, Any] = json.loads(json.dumps(TEST_CONTRACT["capabilities"]))
    capabilities[capability].update(changes)
    return capabilities


# =============================================================================================
# the Q1 input gate
# =============================================================================================


def test_without_a_contract_every_q1_item_is_missing() -> None:
    assert [m.item for m in missing_inputs(None)] == list("ABCDEFGHIJKLMNOPQRST")
    assert len(missing_inputs(ErpContract(contract_version="0"))) == 20


def test_the_repository_template_is_an_empty_draft() -> None:
    template = load_contract(TEMPLATE)
    assert template.status is Verification.DRAFT
    assert len(missing_inputs(template)) == 20


def test_a_complete_test_contract_is_consistent() -> None:
    c = contract()
    assert validate_contract(c) == [] and missing_inputs(c) == []
    assert all(c.capability_verified(name) for name in c.capabilities)
    # only platform BusinessRules count as the adapter's verified rules
    assert c.verified_business_rules() == {"overdue_by_due_date"}
    assert len(c.relations()) == 6


def test_a_draft_contract_activates_nothing() -> None:
    c = contract(status="DRAFT")
    assert not any(c.capability_verified(name) for name in c.capabilities)
    assert c.verified_business_rules() == frozenset()


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        (
            {
                "capabilities": {
                    **TEST_CONTRACT["capabilities"],
                    "purchase_orders": {
                        **TEST_CONTRACT["capabilities"]["daily_sales"],
                        "capability": "purchase_orders",
                    },
                }
            },
            "unknown capability",
        ),
        (
            {"capabilities": mapping_with("daily_sales", permission="business.customer.read")},
            "public capability contract",
        ),
        (
            {"capabilities": mapping_with("customer_outstanding", rules=["credit_note_netting"])},
            "not confirmed",
        ),
        (
            {
                "capabilities": mapping_with(
                    "customer_open_invoices", status_filter=["OPEN", "ON_HOLD"]
                )
            },
            "unlisted statuses",
        ),
        (
            {"capabilities": mapping_with("daily_sales", confirmation=None)},
            "without the owner's confirmation",
        ),
        ({"ownership": None}, "Q1 items are missing: A, G"),
        (
            {"ownership": {**TEST_CONTRACT["ownership"], "is_intended_erp": False}},
            "did not confirm the intended ERP",
        ),
        (
            {
                "rules": {
                    k: v for k, v in TEST_CONTRACT["rules"].items() if k != "stock_calculation"
                }
            },
            "rule 'stock_calculation' is not defined",
        ),
        (
            {"tenant_isolation": {**TEST_CONTRACT["tenant_isolation"], "column": None}},
            "mechanism=column needs",
        ),
    ],
    ids=[
        "unknown_capability",
        "permission_change",
        "unconfirmed_rule",
        "unknown_status",
        "unconfirmed_mapping",
        "missing_owner",
        "not_intended_erp",
        "missing_rule",
        "tenant_column_missing",
    ],
)
def test_inconsistent_contracts_are_refused(changes: dict[str, Any], problem: str) -> None:
    problems = validate_contract(contract(**changes))
    assert any(problem in p for p in problems), problems


def test_data_minimization_refuses_columns_no_record_needs() -> None:
    columns = [
        *TEST_CONTRACT["capabilities"]["customer_outstanding"]["columns"],
        {
            "relation": "erp_test.customer",
            "column": "phone",
            "data_type": "text",
            "role": "field",
            "record_field": "phone",
        },
    ]
    problems = validate_contract(
        contract(capabilities=mapping_with("customer_outstanding", columns=columns))
    )
    assert any("data minimization" in p for p in problems)


def test_stock_needs_a_warehouse_column_and_tenant_isolation_a_tenant_column() -> None:
    stock = [
        c
        for c in TEST_CONTRACT["capabilities"]["warehouse_stock"]["columns"]
        if c["role"] not in ("warehouse", "tenant")
    ]
    problems = validate_contract(
        contract(capabilities=mapping_with("warehouse_stock", columns=stock))
    )
    assert any("without a warehouse column" in p for p in problems)
    assert any("no tenant column" in p for p in problems)


@pytest.mark.parametrize(
    "secret",
    [
        {"notes": "use postgresql://erp_reader:hunter2@erp:5432/erp"},
        {"notes": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
        {"notes": "password=hunter2"},
    ],
)
def test_a_contract_never_holds_credentials(secret: dict[str, str]) -> None:
    expectations = {**TEST_CONTRACT["permission_expectations"], **secret}
    problems = validate_contract(contract(permission_expectations=expectations))
    assert any("credential" in p for p in problems), problems


def test_identifiers_can_never_be_sql_fragments(tmp_path: Path) -> None:
    columns = [
        {
            "relation": "erp_test.customer",
            "column": "name; DROP TABLE x",
            "data_type": "text",
            "role": "field",
            "record_field": "display_name",
        }
    ]
    data = contract_dict(capabilities=mapping_with("customer_outstanding", columns=columns))
    with pytest.raises(ContractError) as raised:
        load_contract(write(tmp_path, data))
    assert "DROP" not in str(raised.value)  # input values are never echoed


def test_time_zone_must_be_iana(tmp_path: Path) -> None:
    data = contract_dict(time={**TEST_CONTRACT["time"], "business_timezone": "IST"})
    with pytest.raises(ContractError, match="time"):
        load_contract(write(tmp_path, data))


def test_load_contract_errors_are_safe(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="not found"):
        load_contract(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractError, match="not readable JSON"):
        load_contract(bad)
    secret = contract_dict(
        permission_expectations={
            **TEST_CONTRACT["permission_expectations"],
            "notes": "password=hunter2",
        }
    )
    with pytest.raises(ContractError, match="inconsistent"):
        load_contract(write(tmp_path, secret, "secret.json"))


def test_invoice_statuses_are_a_closed_list() -> None:
    statuses = InvoiceStatuses.model_validate(TEST_CONTRACT["invoice_statuses"])
    assert statuses.group_of("PART_PAID") == "partially_paid"
    assert statuses.counts_as_open() == {"OPEN", "PART_PAID"}
    with pytest.raises(UnexpectedStatusError):
        statuses.group_of("REFUNDED")


# =============================================================================================
# the security gate (pure evaluation)
# =============================================================================================

CLEAN = {
    "create_db": False,
    "create_role": False,
    "replication": False,
    "server_version_num": 160004,
    "database_create": False,
    "create_schemas": 0,
    "writable_relations": 0,
    "owned_relations": 0,
    "elevated_memberships": 0,
}


def test_a_clean_read_only_role_passes_the_privilege_gate() -> None:
    assert evaluate_privileges(CLEAN) == []


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("create_db", True, "CREATEDB"),
        ("create_role", True, "CREATEROLE"),
        ("replication", True, "REPLICATION"),
        ("database_create", True, "CREATE in the database"),
        ("create_schemas", 1, "CREATE in a schema"),
        ("writable_relations", 3, "write privileges"),
        ("owned_relations", 1, "owns relations"),
        ("elevated_memberships", 1, "elevated role"),
    ],
)
def test_every_privilege_problem_refuses(field: str, value: Any, problem: str) -> None:
    problems = evaluate_privileges({**CLEAN, field: value})
    assert len(problems) == 1 and problem in problems[0]
    assert evaluate_privileges(None) == ["no role row for the current user"]


def _catalog(c: ErpContract) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for mapping in c.capabilities.values():
        for column in mapping.columns:
            rows[(column.relation, column.column)] = {
                "relation": column.relation,
                "relkind": "r",
                "rls": False,
                "readable": True,
                "column_name": column.column,
                "data_type": column.data_type,
            }
    return list(rows.values())


def test_a_matching_catalog_passes_the_contract_gate() -> None:
    c = contract()
    assert evaluate_contract_schema(c, CLEAN, _catalog(c), []) == []


def test_catalog_mismatches_refuse() -> None:
    c = contract()
    rows = _catalog(c)
    changed = [
        {**r, "data_type": "double precision"} if r["column_name"] == "posted_total" else r
        for r in rows
        if r["relation"] != "erp_test.warehouse" and r["column_name"] != "status"
    ]
    changed = [
        {**r, "readable": False} if r["relation"] == "erp_test.product" else r for r in changed
    ]
    problems = evaluate_contract_schema(c, {**CLEAN, "server_version_num": 150002}, changed, [])
    text = "\n".join(problems)
    assert "PostgreSQL 15, contract says 16" in text
    assert "erp_test.warehouse: not found" in text
    assert "erp_test.invoice.status: column not found" in text
    assert "posted_total: type double precision, contract numeric(14,2)" in text
    assert "erp_test.product: no SELECT privilege" in text


def test_rls_tenant_isolation_must_be_provable() -> None:
    isolation = {
        "mechanism": "rls_session_setting",
        "setting": "erp.tenant_code",
        "tenant_value": "platform_tenant_id",
        "confirmation": TEST_CONTRACT["tenant_isolation"]["confirmation"],
    }
    c = contract(tenant_isolation=isolation)
    rows = _catalog(c)
    problems = evaluate_contract_schema(c, CLEAN, rows, [])
    assert any("row-level security is not enabled" in p for p in problems)
    assert any("no RLS policy reads erp.tenant_code" in p for p in problems)
    secured = [{**r, "rls": True} for r in rows]
    policies = [
        {"relation": r.qualified, "policies": 1, "tenant_policies": 1} for r in c.relations()
    ]
    assert evaluate_contract_schema(c, CLEAN, secured, policies) == []
    assert gate_params(c)["policies"]["setting_pattern"] == "%current_setting('erp.tenant_code'%"


def test_tenant_and_warehouse_isolation_cannot_be_skipped() -> None:
    c = contract(tenant_isolation=None, warehouse_isolation=None)
    problems = evaluate_contract_schema(c, CLEAN, _catalog(c), [])
    assert "tenant isolation is not defined: cannot be proven" in problems
    assert "warehouse isolation is not defined: cannot be proven" in problems


def test_gate_queries_are_guarded_reads() -> None:
    from agentic_platform.business import erp_gate

    for query in (erp_gate.PRIVILEGES_QUERY, erp_gate.SCHEMA_QUERY, erp_gate.POLICIES_QUERY):
        assert isinstance(query, ErpQuery)  # constructed only if the guard accepted it
        assert "CREATE" not in query.sql and "INSERT" not in query.sql  # bound, not inlined


# =============================================================================================
# adapter activation and tenant binding
# =============================================================================================


class _NoDatabase:
    """Fails the test if anything tries to reach the ERP."""

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the ERP was contacted")


CTX = BusinessContext(tenant_id="acme", user_id="asha", warehouse_scope=ALL_WAREHOUSES)
CALLS: dict[str, tuple[Any, ...]] = {
    "find_customers": (CTX, "Rahul", 5),
    "get_customer": (CTX, "R1"),
    "customer_outstanding": (CTX, "R1"),
    "open_invoices": (CTX, "R1", 5),
    "daily_sales": (CTX, __import__("datetime").date(2026, 10, 1)),
    "sales_by_day": (
        CTX,
        __import__("datetime").date(2026, 9, 1),
        __import__("datetime").date(2026, 10, 1),
        31,
    ),
    "find_products": (CTX, "x", 5),
    "get_product": (CTX, "P1"),
    "find_warehouses": (CTX, "x", 5),
    "get_warehouse": (CTX, "W1"),
    "product_stock": (CTX, "P1", 5),
    "warehouse_stock": (CTX, "W1", 5),
    "low_stock": (CTX, 5),
    "customer_count": (CTX,),
    "customers_billed": (CTX, date(2026, 10, 1), date(2026, 10, 2)),
}


@pytest.mark.parametrize("method", sorted(METHOD_CAPABILITIES))
def test_without_a_reader_every_capability_is_rule_not_verified(method: str) -> None:
    adapter = PostgresErpAdapter(_NoDatabase(), contract())  # type: ignore[arg-type]
    with pytest.raises(ErpRuleNotVerifiedError):
        asyncio.run(getattr(adapter, method)(*CALLS[method]))


class _SalesReader:
    capability = "daily_sales"

    async def daily_sales(self, ctx: BusinessContext, business_date: Any) -> str:
        return f"sales:{ctx.tenant_id}"


def test_a_reader_needs_a_verified_mapping() -> None:
    for c in (
        None,
        contract(status="DRAFT"),
        contract(capabilities=mapping_with("daily_sales", status="DRAFT")),
    ):
        with pytest.raises(ConfigurationError, match="reader refused"):
            PostgresErpAdapter(_NoDatabase(), c).register_reader(_SalesReader())  # type: ignore[arg-type]
    adapter = PostgresErpAdapter(_NoDatabase(), contract())  # type: ignore[arg-type]
    adapter.register_reader(_SalesReader())
    result: object = asyncio.run(adapter.daily_sales(*CALLS["daily_sales"]))
    assert result == "sales:acme"  # the registered reader answered
    with pytest.raises(ErpRuleNotVerifiedError):  # other capabilities stay closed
        asyncio.run(adapter.customer_outstanding(*CALLS["customer_outstanding"]))
    assert adapter.verified_rules == {"overdue_by_due_date"}
    assert PostgresErpAdapter(_NoDatabase()).verified_rules == frozenset()  # type: ignore[arg-type]


def _database(c: ErpContract | None, expected: str = "erp_contract_test") -> ReadOnlyErpDatabase:
    return ReadOnlyErpDatabase(
        ErpSettings(adapter="postgres", database_url=ERP_URL, expected_database=expected),
        contract=c,
    )


def _capture(db: ReadOnlyErpDatabase) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def verify() -> None:
        return None

    async def execute(query: Any, params: Any, **kwargs: Any) -> list[dict[str, Any]]:
        seen.append({"params": dict(params), **kwargs})
        return []

    db.verify = verify  # type: ignore[method-assign]
    db._execute = execute  # type: ignore[method-assign]
    return seen


TENANT_QUERY = ErpQuery(
    "test.q", "SELECT 1 AS one FROM erp_test.customer WHERE tenant_code = :tenant"
)


def test_business_reads_are_refused_without_tenant_isolation() -> None:
    db = _database(None)
    _capture(db)
    with pytest.raises(ErpSafetyViolationError, match="No verified tenant isolation"):
        asyncio.run(db.fetch(TENANT_QUERY, {}, tenant_id="acme"))


def test_column_isolation_forces_the_principals_tenant() -> None:
    db = _database(contract())
    seen = _capture(db)
    asyncio.run(db.fetch(TENANT_QUERY, {"tenant": "globex"}, tenant_id="acme"))
    assert seen[0]["params"]["tenant"] == "acme"  # a caller value never wins
    untenanted = ErpQuery("test.all", "SELECT 1 AS one FROM erp_test.customer")
    with pytest.raises(ErpSafetyViolationError, match="no tenant filter"):
        asyncio.run(db.fetch(untenanted, {}, tenant_id="acme"))
    with pytest.raises(ErpSafetyViolationError, match="principal's tenant"):
        asyncio.run(db.fetch(TENANT_QUERY, {}, tenant_id=""))


def test_rls_isolation_binds_the_setting_for_the_transaction() -> None:
    isolation = {
        **TEST_CONTRACT["tenant_isolation"],
        "mechanism": "rls_session_setting",
        "column": None,
        "setting": "erp.tenant_code",
    }
    db = _database(contract(tenant_isolation=isolation))
    seen = _capture(db)
    asyncio.run(db.fetch(ErpQuery("test.rls", "SELECT 1 AS one"), {}, tenant_id="acme"))
    assert seen[0]["tenant_setting"] == ("erp.tenant_code", "acme")


def test_the_contract_and_expected_database_must_agree() -> None:
    with pytest.raises(ConfigurationError, match="differs from the ERP contract"):
        _database(contract(), expected="some_other_db")


# =============================================================================================
# configuration: factory and production policy
# =============================================================================================


def _settings(env: str = "test", **erp: Any) -> AppSettings:
    return AppSettings(
        env=env,
        auth=AuthSettings(),
        database=DatabaseSettings(url="postgresql://app:p@db/agentic"),
        erp=ErpSettings(
            adapter="postgres",
            database_url=ERP_URL,
            expected_database="erp_contract_test",
            ssl_mode="verify-full",
            **erp,
        ),
    )


def test_the_factory_loads_a_configured_contract(tmp_path: Path) -> None:
    port = build_erp_port(
        _settings(contract_path=str(write(tmp_path, TEST_CONTRACT))), ClockService()
    )
    assert isinstance(port, PostgresErpAdapter) and port.verified_rules == {"overdue_by_due_date"}
    assert build_erp_port(_settings(), ClockService()).verified_rules == frozenset()  # type: ignore[union-attr]
    with pytest.raises(ConfigurationError, match="not found"):
        build_erp_port(_settings(contract_path=str(tmp_path / "absent.json")), ClockService())


def _codes(settings: AppSettings) -> set[str]:
    return {i.code for i in check_configuration(settings, role="api", environ={}).issues}


def test_production_refuses_the_live_adapter_without_a_verified_contract(tmp_path: Path) -> None:
    assert "ERP_CONTRACT_MISSING" in _codes(_settings("production"))
    draft = write(tmp_path, contract_dict(status="DRAFT"), "draft.json")
    assert "ERP_CONTRACT_NOT_VERIFIED" in _codes(
        _settings("production", contract_path=str(draft), business_timezone="Asia/Kolkata")
    )
    invalid = write(tmp_path, {"contract_version": 1}, "invalid.json")
    assert "ERP_CONTRACT_INVALID" in _codes(_settings("production", contract_path=str(invalid)))
    good = write(tmp_path, TEST_CONTRACT)
    assert "ERP_TIMEZONE_MISMATCH" in _codes(
        _settings("production", contract_path=str(good), business_timezone="UTC")
    )
    clean = _codes(
        _settings("production", contract_path=str(good), business_timezone="Asia/Kolkata")
    )
    assert not {c for c in clean if c.startswith("ERP_CONTRACT") or c == "ERP_TIMEZONE_MISMATCH"}


def test_development_default_stays_safe() -> None:
    assert ErpSettings().adapter == "none" and ErpSettings().contract_path is None
    assert build_erp_port(AppSettings(env="development"), ClockService()) is None


# =============================================================================================
# the readiness CLI (offline gate; the live path is covered on a throw-away PostgreSQL)
# =============================================================================================


def test_readiness_without_a_contract_is_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert erp_readiness.main([], environ={}) == erp_readiness.EXIT_BLOCKED
    out = capsys.readouterr().out
    assert "ERP READINESS: BLOCKED" in out and "T  schema/version information" in out


def test_readiness_states(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    draft = write(tmp_path, contract_dict(status="DRAFT"), "draft.json")
    assert erp_readiness.main(["--contract", str(draft)], environ={}) == 4
    assert "READY_FOR_DISCOVERY" in capsys.readouterr().out
    good = write(tmp_path, TEST_CONTRACT)
    assert erp_readiness.main(["--contract", str(good), "--json"], environ={}) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload["state"], payload["missing"]) == ("READY_FOR_ACTIVATION", [])
    secret = write(
        tmp_path,
        contract_dict(
            permission_expectations={
                **TEST_CONTRACT["permission_expectations"],
                "notes": "password=hunter2",
            }
        ),
        "s.json",
    )
    assert erp_readiness.main(["--contract", str(secret)], environ={}) == 3
    out = capsys.readouterr().out
    assert "credential" in out and "hunter2" not in out


def test_readiness_live_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = str(write(tmp_path, TEST_CONTRACT))
    assert erp_readiness.main(["--live"], environ={}) == erp_readiness.EXIT_BLOCKED
    assert erp_readiness.main(["--contract", good, "--live"], environ={}) == (
        erp_readiness.EXIT_REFUSED
    )
    agent = "postgresql://app:p@db:5432/agentic"
    env = {"ERP_INSPECT_DATABASE_URL": agent, "DATABASE_URL": agent}
    assert erp_readiness.main(["--contract", good, "--live", "--json"], environ=env) == 2
    out = capsys.readouterr().out
    assert "Agent DB" in out and "app:p" not in out
