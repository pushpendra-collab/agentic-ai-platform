"""Phase 10: BusinessReadService on the real ERP read path (InvenLogic readers, TEST
replica; tests/erp_db.py). The fake ERP was removed on 2026-10-30.

Covers the deterministic result semantics, tenant isolation, warehouse scope, adapter
failures (unavailable, timeout, hang, malformed, tenant leak, unverified rule, crash),
permissions, and that audit/log records carry metadata only.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from agentic_platform.business.context import ScopeKind, WarehouseScope
from agentic_platform.business.results import BusinessResult, BusinessStatus, Reason
from agentic_platform.security import Principal
from tests.business_helpers import (
    TODAY,
    RecordingAuditor,
    business_principal,
    globex,
    make_service,
    run,
    std,
)

S = BusinessStatus


# --- SUCCESS: one test per reference question ----------------------------------------


def test_customer_outstanding() -> None:
    service, _ = make_service()
    result = run(service.customer_outstanding(business_principal(), customer_name="Rahul Gupta"))
    assert result.status is S.SUCCESS and result.reason is None
    assert result.data is not None
    assert result.data.customer.ref == std()["rahul"]
    assert result.data.outstanding.amount == Decimal("45000.00")
    assert result.data.outstanding.currency == "INR"
    assert result.source is not None
    assert (result.source.adapter, result.source.test_data) == ("postgres", False)


def test_customer_open_invoices_total_is_computed_by_code() -> None:
    service, _ = make_service()
    result = run(service.customer_open_invoices(business_principal(), customer_name="rahul"))
    assert result.status is S.SUCCESS and result.data is not None
    assert [i.invoice.display_name for i in result.data.invoices] == ["TEST-INV-001",
                                                                     "TEST-INV-002"]  # fmt: skip
    assert result.data.invoice_count == 2
    assert result.data.total_balance is not None
    assert result.data.total_balance.amount == Decimal("45000.00")
    assert not result.data.truncated


def test_open_invoices_truncated_has_no_total() -> None:
    service, _ = make_service(max_rows=1)
    result = run(service.customer_open_invoices(business_principal(), customer_name="Rahul"))
    assert result.status is S.SUCCESS and result.data is not None
    assert result.data.truncated and result.data.invoice_count == 1
    assert result.data.total_balance is None  # a partial sum is not a total


def test_daily_sales_today_uses_the_configured_business_timezone() -> None:
    service, adapter = make_service()
    result = run(service.daily_sales(business_principal()))
    assert result.status is S.SUCCESS and result.data is not None
    assert (result.data.business_date, result.data.timezone) == (TODAY, "Asia/Kolkata")
    assert result.data.total.amount == Decimal("125000.00")
    assert result.data.document_count == 7
    assert adapter.calls == ["daily_sales"]


def test_product_stock_sums_visible_warehouses() -> None:
    service, _ = make_service()
    result = run(service.product_stock(business_principal(), product_name="iPhone 15"))
    assert result.status is S.SUCCESS and result.data is not None
    assert result.data.total_quantity == Decimal("15")
    assert {line.warehouse.ref: line.quantity for line in result.data.lines} == {
        std()["godown1"]: Decimal("12"), std()["godown2"]: Decimal("3")}  # fmt: skip
    assert not result.data.scope_limited


def test_warehouse_stock() -> None:
    service, _ = make_service()
    result = run(service.warehouse_stock(business_principal(), warehouse_name="Godown 2"))
    assert result.status is S.SUCCESS and result.data is not None
    assert result.data.warehouse.ref == std()["godown2"]
    assert {p.product.ref for p in result.data.lines} == {std()["iphone"], std()["galaxy"]}


def test_low_stock_products() -> None:
    service, _ = make_service()
    result = run(service.low_stock_products(business_principal()))
    assert result.status is S.SUCCESS and result.data is not None
    assert [(i.product.ref, i.quantity, i.threshold) for i in result.data.items] == [
        (std()["iphone"], Decimal("3"), Decimal("5")),
        (std()["galaxy"], Decimal("0"), Decimal("2")),
    ]


# --- NOT_FOUND / AMBIGUOUS / VALIDATION_ERROR --------------------------------------------


def test_unknown_customer_is_not_found() -> None:
    service, _ = make_service()
    result = run(service.customer_outstanding(business_principal(), customer_name="Nobody"))
    assert (result.status, result.reason, result.data) == (S.NOT_FOUND, Reason.NO_MATCH, None)


def test_unknown_ref_is_not_found() -> None:
    service, _ = make_service()
    result = run(service.product_stock(business_principal(), product_ref="999999999999"))
    assert result.status is S.NOT_FOUND


def test_ambiguous_customer_lists_candidates_and_no_figure() -> None:
    service, adapter = make_service()
    result = run(service.customer_outstanding(business_principal(), customer_name="Amit Sharma"))
    assert result.status is S.AMBIGUOUS and result.data is None
    assert [c.ref for c in result.candidates] == [std()["amit"], std()["traders"]]
    assert "customer_outstanding" not in adapter.calls  # no balance was even read


def test_resolving_a_candidate_ref_succeeds() -> None:
    service, _ = make_service()
    result = run(service.customer_outstanding(business_principal(), customer_ref=std()["amit"]))
    assert result.status is S.SUCCESS and result.data is not None
    assert result.data.outstanding.amount == Decimal("0.00")


def test_ambiguous_warehouse() -> None:
    service, _ = make_service()
    result = run(service.warehouse_stock(business_principal(), warehouse_name="Godown"))
    assert result.status is S.AMBIGUOUS and len(result.candidates) == 2


def test_future_date_is_a_validation_error() -> None:
    service, adapter = make_service()
    result = run(service.daily_sales(business_principal(), day=TODAY + timedelta(days=5)))
    assert (result.status, result.reason) == (S.VALIDATION_ERROR, Reason.FUTURE_DATE)
    assert adapter.calls == []


def test_explicit_past_date_without_sales_is_not_found() -> None:
    service, _ = make_service()
    result = run(service.daily_sales(business_principal(), day=date(2026, 9, 1)))
    assert result.status is S.NOT_FOUND


def test_today_without_a_confirmed_timezone_is_rule_not_verified() -> None:
    service, adapter = make_service(timezone=None)
    result = run(service.daily_sales(business_principal()))
    assert (result.status, result.reason) == (S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)
    assert adapter.calls == []


# --- authorization and scope ------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "call"),
    [
        (("member",), "outstanding"),
        (("member", "stock_viewer"), "outstanding"),  # inventory does not grant customers
        (("member", "sales_viewer"), "stock"),
    ],
)
def test_missing_permission_is_unauthorized(roles: tuple[str, ...], call: str) -> None:
    service, adapter = make_service()
    who = business_principal(roles=roles)
    result: BusinessResult[object]  # type: ignore[type-var]
    if call == "outstanding":
        result = run(service.customer_outstanding(who, customer_name="Rahul"))  # type: ignore[assignment]
    else:
        result = run(service.product_stock(who, product_name="iPhone"))  # type: ignore[assignment]
    assert (result.status, result.reason) == (S.UNAUTHORIZED, Reason.PERMISSION_MISSING)
    assert adapter.calls == []  # refused before the ERP is touched


def test_stock_without_warehouse_scope_is_refused() -> None:
    service, adapter = make_service()
    who = business_principal(roles=("member", "stock_viewer_limited"))
    for result in (
        run(service.product_stock(who, product_name="iPhone")),
        run(service.warehouse_stock(who, warehouse_name="Godown 1")),
        run(service.low_stock_products(who)),
    ):
        assert (result.status, result.reason) == (S.UNAUTHORIZED, Reason.WAREHOUSE_SCOPE_EMPTY)
    assert adapter.calls == []


class OnlyGodown1:
    def resolve(self, principal: Principal) -> WarehouseScope:
        return WarehouseScope(kind=ScopeKind.LISTED, warehouse_refs=frozenset({std()["godown1"]}))


def test_listed_scope_limits_totals_and_hides_other_warehouses() -> None:
    service, _ = make_service(scopes=OnlyGodown1())
    who = business_principal(roles=("member", "stock_viewer_limited"))

    stock = run(service.product_stock(who, product_name="iPhone 15"))
    assert stock.status is S.SUCCESS and stock.data is not None
    assert stock.data.total_quantity == Decimal("12")  # Godown 2's 3 units are invisible
    assert stock.data.scope_limited

    hidden = run(service.warehouse_stock(who, warehouse_name="Godown 2"))
    assert hidden.status is S.NOT_FOUND  # same answer as a warehouse that does not exist
    by_ref = run(service.warehouse_stock(who, warehouse_ref=std()["godown2"]))
    assert by_ref.status is S.NOT_FOUND
    # "Godown" now matches one visible warehouse only: no ambiguity leaks the other one
    visible = run(service.warehouse_stock(who, warehouse_name="Godown"))
    assert visible.status is S.SUCCESS

    low = run(service.low_stock_products(who))
    assert low.status is S.SUCCESS and low.data is not None
    assert low.data.items == [] and low.data.scope_limited  # both low items are in Godown 2


# --- tenant isolation -------------------------------------------------------------------


def test_tenants_only_see_their_own_records() -> None:
    service, _ = make_service()
    acme = business_principal(std().tenant)
    other = business_principal(globex().tenant, user_id="gus")

    assert run(service.customer_outstanding(acme, customer_name="Globex Only")).status is (
        S.NOT_FOUND
    )
    cross = run(service.customer_outstanding(acme, customer_ref=globex()["globex"]))
    assert (cross.status, cross.data, cross.candidates) == (S.NOT_FOUND, None, [])

    own = run(service.customer_outstanding(other, customer_name="Globex Only"))
    assert own.status is S.SUCCESS and own.data is not None
    assert own.data.outstanding.amount == Decimal("99999.00")
    # ... and globex cannot see acme's customer under the same name or ref
    assert run(service.customer_outstanding(other, customer_name="Rahul")).status is S.NOT_FOUND
    assert (
        run(service.customer_outstanding(other, customer_ref=std()["rahul"])).status is S.NOT_FOUND
    )


def test_records_of_another_tenant_are_discarded(caplog: pytest.LogCaptureFixture) -> None:
    service, _ = make_service(failure="tenant_leak")
    with caplog.at_level(logging.ERROR, logger="agentic_platform"):
        result = run(service.customer_outstanding(business_principal(), customer_name="Rahul"))
    assert (result.status, result.reason) == (S.DEPENDENCY_ERROR, Reason.TENANT_MISMATCH)
    assert result.data is None and result.candidates == []
    assert any(r.getMessage() == "business.read.rejected" for r in caplog.records)


# --- adapter failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "status", "reason", "retryable"),
    [
        ("unavailable", S.UNAVAILABLE, Reason.ERP_UNAVAILABLE, True),
        ("timeout", S.UNAVAILABLE, Reason.ERP_TIMEOUT, True),
        ("not_configured", S.UNAVAILABLE, Reason.ERP_NOT_CONFIGURED, False),
        ("rule_not_verified", S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED, False),
        ("malformed", S.DEPENDENCY_ERROR, Reason.MALFORMED_RESPONSE, False),
        ("crash", S.DEPENDENCY_ERROR, Reason.INTERNAL_ERROR, False),
    ],
)
def test_adapter_failures_never_produce_figures(
    failure: str, status: BusinessStatus, reason: str, retryable: bool
) -> None:
    service, _ = make_service(failure=failure)  # type: ignore[arg-type]
    who = business_principal()
    for result in (
        run(service.customer_outstanding(who, customer_name="Rahul")),
        run(service.daily_sales(who)),
        run(service.product_stock(who, product_name="iPhone")),
        run(service.low_stock_products(who)),
    ):
        assert (result.status, result.reason, result.retryable) == (status, reason, retryable)
        assert result.data is None and result.source is None


def test_a_hung_erp_is_cut_off_by_the_service_deadline() -> None:
    service, _ = make_service(failure="hang", timeout=0.05)
    result = run(service.customer_outstanding(business_principal(), customer_name="Rahul"))
    assert (result.status, result.reason, result.retryable) == (
        S.UNAVAILABLE, Reason.ERP_TIMEOUT, True)  # fmt: skip


def test_result_invariants() -> None:
    with pytest.raises(ValueError, match="SUCCESS"):
        BusinessResult[object](capability="x", status=S.NOT_FOUND, data={"amount": 1})  # type: ignore[type-var]
    with pytest.raises(ValueError, match="SUCCESS"):
        BusinessResult[object](capability="x", status=S.SUCCESS)  # type: ignore[type-var]


# --- observability ------------------------------------------------------------------------


def test_audit_and_logs_hold_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    auditor = RecordingAuditor()
    service, _ = make_service(auditor=auditor)
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"):
        run(service.customer_outstanding(business_principal(), customer_name="Rahul Gupta"))
        run(service.customer_outstanding(business_principal(roles=("member",)),
                                         customer_name="Rahul Gupta"))  # fmt: skip

    ok, denied = auditor.events
    assert (ok.action, ok.status, ok.tenant_id, ok.actor_id) == (
        "business.read", "ok", std().tenant, "asha")  # fmt: skip
    assert ok.details["capability"] == "customer_outstanding"
    assert ok.details["status"] == "SUCCESS" and ok.details["test_data"] is False
    assert (denied.status, denied.error_code) == ("denied", Reason.PERMISSION_MISSING)

    text = " ".join(str(e.model_dump()) for e in auditor.events) + " ".join(
        f"{r.getMessage()} {getattr(r, 'fields', '')}" for r in caplog.records
    )
    for secret in ("Rahul", "45000", "INR"):
        assert secret not in text


def test_service_has_no_write_operation() -> None:
    from agentic_platform.business.ports import ErpReadPort
    from agentic_platform.business.service import BusinessReadService

    verbs = ("create", "update", "delete", "insert", "write", "save", "post", "adjust",
             "cancel", "pay", "execute", "sql", "raw")  # fmt: skip
    for cls in (BusinessReadService, ErpReadPort):
        names = [n for n in dir(cls) if not n.startswith("_")]
        assert not [n for n in names if any(v in n.lower() for v in verbs)], cls


def test_unused_principal_type_is_frozen() -> None:
    who: Principal = business_principal()
    with pytest.raises(ValueError):
        who.tenant_id = "globex"  # type: ignore[misc]
