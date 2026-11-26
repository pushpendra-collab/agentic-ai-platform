"""Phase 10: business tools through the real ToolRegistry, on the real ERP read path
(InvenLogic readers, TEST replica; tests/erp_db.py). The fake ERP was removed on 2026-10-30.
"""

from __future__ import annotations

import json

import pytest

from agentic_platform.core.config import ErpSettings
from agentic_platform.core.errors import ToolRegistrationError
from agentic_platform.security import Principal
from agentic_platform.tools import ToolKind, ToolRegistry, ToolResult
from agentic_platform.tools.base import Tool, ToolInput, ToolOutput, ToolSpec
from agentic_platform.tools.business import (
    BUSINESS_DOMAIN,
    business_tools,
    register_business_tools,
)
from agentic_platform.tools.system import SYSTEM_DOMAIN
from tests.business_helpers import ErpFaults, business_principal, make_service, run, std

TOOLS = (
    "business.customer_outstanding",
    "business.customer_open_invoices",
    "business.daily_sales",
    "business.product_stock",
    "business.warehouse_stock",
    "business.low_stock_products",
    "business.customer_count",  # 2026-11-25
)
# Arguments the model must never be able to set (identity, scope, permissions, SQL).
FORBIDDEN_ARGS = ("tenant", "user", "scope", "permission", "role", "sql", "query", "table",
                  "column", "where", "warehouses", "statement")  # fmt: skip


def registry(**kwargs: object) -> tuple[ToolRegistry, ErpFaults]:
    service, adapter = make_service(**kwargs)  # type: ignore[arg-type]
    reg = ToolRegistry()
    register_business_tools(reg, service, ErpSettings(adapter="postgres"))
    return reg, adapter


def call(
    reg: ToolRegistry, name: str, args: dict[str, object] | str, who: Principal | None = None,
    domains: tuple[str, ...] = (BUSINESS_DOMAIN,),
) -> ToolResult:  # fmt: skip
    text = args if isinstance(args, str) else json.dumps(args)
    return run(reg.execute(name, text, allowed_domains=domains,
                           principal=who or business_principal()))  # fmt: skip


def test_catalogue_is_seven_read_tools_in_the_business_domain() -> None:
    reg, _ = registry()
    specs = reg.specs([BUSINESS_DOMAIN])
    assert tuple(s.name for s in specs) == TOOLS
    assert all(s.kind is ToolKind.READ and s.domain == BUSINESS_DOMAIN for s in specs)
    assert all(not reg.needs_approval(s) for s in specs)  # reads, no HITL needed
    assert all(s.required_permission.startswith("business.") for s in specs)


def test_no_tool_accepts_identity_scope_or_sql_arguments() -> None:
    reg, _ = registry()
    for spec in reg.specs([BUSINESS_DOMAIN]):
        schema = spec.input_model.model_json_schema()
        assert schema.get("additionalProperties") is False, spec.name
        for prop in schema.get("properties", {}):
            # Phase 11 added exactly two typed, non-identity arguments: a period token
            # (resolved by the service from the clock) and an invoice number to search.
            if prop.endswith(("_name", "_ref")) or prop in ("day", "limit", "period",
                                                            "invoice_number"):  # fmt: skip
                continue
            # Phase 16 added one more: a closed comparison token on daily_sales only.
            if prop == "compare_with" and spec.name == "business.daily_sales":
                options = schema["properties"][prop]["anyOf"]
                assert {"const": "same_period_last_year", "type": "string"} in options
                assert all(o.get("type") in ("string", "null") and
                           ("const" in o or o["type"] == "null") for o in options)  # fmt: skip
                continue
            pytest.fail(f"{spec.name} has an unexpected argument {prop!r}")
        assert not [p for p in schema.get("properties", {})
                    if any(word in p for word in FORBIDDEN_ARGS)]  # fmt: skip


@pytest.mark.parametrize(
    "args",
    [
        {"customer_name": "Rahul", "tenant_id": "globex"},
        {"customer_name": "Rahul", "user_id": "boss"},
        {"customer_name": "Rahul", "sql": "SELECT * FROM invoices"},
        {"customer_name": "Rahul", "table": "customers"},
        {"customer_name": "Rahul", "permissions": ["business.customer.read"]},
    ],
)
def test_smuggled_arguments_are_refused(args: dict[str, object]) -> None:
    reg, adapter = registry()
    result = call(reg, "business.customer_outstanding", args)
    assert (result.status, result.error_code) == ("error", "TOOL_INVALID_INPUT")
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("business.customer_outstanding", {}),  # neither name nor ref
        (
            "business.customer_outstanding",
            {"customer_name": "Rahul", "customer_ref": "1001"},
        ),  # both
        ("business.customer_outstanding", {"customer_name": "R"}),  # too short
        ("business.customer_outstanding", {"customer_ref": "x'; DROP TABLE x; --"}),
        ("business.daily_sales", {"day": "yesterday"}),
        ("business.daily_sales", {"day": "2026-13-45"}),
        ("business.low_stock_products", {"limit": 0}),
        ("business.low_stock_products", {"limit": 10_000}),
        ("business.product_stock", "not json"),
        ("business.product_stock", "[1, 2]"),
    ],
)
def test_invalid_input_is_refused_before_the_erp(name: str, args: dict[str, object] | str) -> None:
    reg, adapter = registry()
    result = call(reg, name, args)
    assert (result.status, result.error_code) == ("error", "TOOL_INVALID_INPUT")
    assert adapter.calls == []


def test_success_is_a_structured_deterministic_result() -> None:
    reg, _ = registry()
    result = call(reg, "business.customer_outstanding", {"customer_name": "Rahul Gupta"})
    assert result.status == "ok" and result.output is not None
    business = result.output["result"]
    assert business["status"] == "SUCCESS"
    assert business["data"]["outstanding"] == {"amount": "45000.00", "currency": "INR"}
    assert business["data"]["customer"]["ref"] == std()["rahul"]
    assert (business["source"]["adapter"], business["source"]["test_data"]) == ("postgres", False)
    assert "tenant_id" not in json.dumps(result.output)  # no tenant ids reach the model


def test_non_success_is_still_a_completed_tool_call() -> None:
    reg, _ = registry()
    result = call(reg, "business.customer_outstanding", {"customer_name": "Amit Sharma"})
    assert result.status == "ok" and result.output is not None
    assert result.output["result"]["status"] == "AMBIGUOUS"
    assert result.output["result"]["data"] is None


def test_permission_is_checked_by_the_registry() -> None:
    reg, adapter = registry()
    member_only = business_principal(roles=("member",))
    result = call(reg, "business.daily_sales", {"day": "today"}, who=member_only)
    assert (result.status, result.error_code) == ("error", "PERMISSION_DENIED")
    assert adapter.calls == []
    # ... and the model is never shown tools the user may not run
    assert reg.specs([BUSINESS_DOMAIN], principal=member_only) == []
    sales = business_principal(roles=("member", "sales_viewer"))
    assert {s.name for s in reg.specs([BUSINESS_DOMAIN], principal=sales)} == {
        "business.customer_outstanding", "business.customer_open_invoices",
        "business.daily_sales", "business.customer_count"}  # fmt: skip


def test_business_tools_do_not_run_outside_their_domain() -> None:
    reg, adapter = registry()
    result = call(reg, "business.daily_sales", {}, domains=(SYSTEM_DOMAIN,))
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_ALLOWED")
    assert adapter.calls == []


@pytest.mark.parametrize("name", ["business.run_sql", "business.create_purchase_order",
                                  "business.update_stock", "sql.execute"])  # fmt: skip
def test_unregistered_tools_cannot_be_called(name: str) -> None:
    reg, _ = registry()
    result = call(reg, name, {})
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_FOUND")


def test_a_write_tool_cannot_be_registered_as_a_business_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = make_service()
    tools = business_tools(service, ErpSettings(adapter="postgres"))
    writer = Tool(
        spec=ToolSpec(name="business.create_purchase_order", description="x",
                      domain=BUSINESS_DOMAIN, kind=ToolKind.WRITE, input_model=ToolInput,
                      output_model=ToolOutput),
        handler=tools[0].handler,
    )  # fmt: skip
    monkeypatch.setattr("agentic_platform.tools.business.business_tools",
                        lambda *_: [*tools, writer])  # fmt: skip
    with pytest.raises(ToolRegistrationError, match="must be a READ"):
        register_business_tools(ToolRegistry(), service, ErpSettings(adapter="postgres"))


def test_adapter_timeout_is_reported_not_raised() -> None:
    reg, _ = registry(failure="hang", timeout=0.05)
    result = call(reg, "business.product_stock", {"product_name": "iPhone 15"})
    assert result.status == "ok" and result.output is not None
    assert result.output["result"]["status"] == "UNAVAILABLE"
    assert result.output["result"]["reason"] == "ERP_TIMEOUT"
