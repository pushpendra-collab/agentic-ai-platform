"""The ERP adapter contract suite (Phase 14): one set of checks for every adapter.

Each check drives the real `BusinessReadService` over an adapter and asserts the
*structured* `BusinessResult` (status, reason, figures, refs), never wording. A
`ContractTarget` supplies the service and principals for one adapter:

* `tests/erp_contract/test_invenlogic_contract.py`: the InvenLogic readers on the TEST
  replica (docker/erp-test), real SQL through the read-only role;
* `tests/erp_contract/test_real_erp_contract.py`: the real ERP, only after Q1 (contract,
  owner-supplied fixtures, a passing security inspection).

The same checks, the same expectations: an adapter that passes is interchangeable for the
service. The expectations (`ContractFixtures`) name test records, not real customers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, TypeVar

from agentic_platform.business.erp_contract import InvoiceStatuses, UnexpectedStatusError
from agentic_platform.business.results import BusinessResult, BusinessStatus
from agentic_platform.business.service import BusinessReadService
from agentic_platform.security.identity import Principal
from tests.erp_contract.fixtures import ContractFixtures

T = TypeVar("T")
Failure = Literal["unavailable", "timeout", "connection", "malformed"]
Scope = Literal["all", "limited"]


class ContractTarget(Protocol):
    fixtures: ContractFixtures
    statuses: InvoiceStatuses

    def service(self, *, now: datetime | None = None) -> BusinessReadService: ...

    def failing_service(self, failure: Failure) -> BusinessReadService: ...

    def principal(self, *, tenant: str | None = None, scope: Scope = "all") -> Principal: ...


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _ok(result: BusinessResult[Any]) -> Any:
    assert result.status is BusinessStatus.SUCCESS, (result.status, result.reason)
    assert result.data is not None and result.source is not None
    return result.data


# =============================================================================================
# the 18 checks (brief § 14)
# =============================================================================================


def customer_outstanding(t: ContractTarget) -> None:
    f = t.fixtures
    data = _ok(run(t.service().customer_outstanding(t.principal(), customer_name=f.customer_name)))
    assert data.customer.ref == f.customer_ref
    assert data.outstanding.amount == f.outstanding


def todays_sales(t: ContractTarget) -> None:
    f = t.fixtures
    data = _ok(run(t.service(now=f.now).daily_sales(t.principal(), day="today")))
    assert data.business_date == f.today and data.timezone == f.timezone
    assert data.total.amount == f.today_sales_total
    assert data.document_count == f.today_sales_documents


def warehouse_stock(t: ContractTarget) -> None:
    f = t.fixtures
    data = _ok(run(t.service().warehouse_stock(t.principal(), warehouse_ref=f.warehouse_ref)))
    lines = {line.product.ref: line.quantity for line in data.lines}
    assert lines.get(f.product_ref) == f.stock_in_warehouse


def ambiguous_customer(t: ContractTarget) -> None:
    f = t.fixtures
    result = run(t.service().customer_outstanding(t.principal(), customer_name=f.ambiguous_name))
    assert result.status is BusinessStatus.AMBIGUOUS and result.data is None
    assert {c.ref for c in result.candidates} == f.ambiguous_refs


def missing_customer(t: ContractTarget) -> None:
    f = t.fixtures
    result = run(t.service().customer_outstanding(t.principal(), customer_name=f.missing_name))
    assert (result.status, result.entity) == (BusinessStatus.NOT_FOUND, "customer")
    assert result.data is None


def unavailable_erp(t: ContractTarget) -> None:
    f = t.fixtures
    result = run(
        t.failing_service("unavailable").customer_outstanding(
            t.principal(), customer_ref=f.customer_ref
        )
    )
    assert (result.status, result.reason, result.retryable) == (
        BusinessStatus.UNAVAILABLE,
        "ERP_UNAVAILABLE",
        True,
    )
    assert result.data is None and result.source is None


def unauthorized_tenant(t: ContractTarget) -> None:
    """Another tenant's principal naming this tenant's customer ref sees nothing."""
    f = t.fixtures
    intruder = t.principal(tenant=f.other_tenant)
    result = run(t.service().customer_outstanding(intruder, customer_ref=f.customer_ref))
    assert result.status is BusinessStatus.NOT_FOUND and result.data is None


def unauthorized_warehouse(t: ContractTarget) -> None:
    """Same tenant, allowed warehouse → data; same tenant, other warehouse → hidden
    (NOT_FOUND, the existing security model: results.py / ADR-059); another tenant with
    the same warehouse ref → only its own figures (or NOT_FOUND where refs are unique
    across tenants, as InvenLogic ids are)."""
    f = t.fixtures
    limited = t.principal(scope="limited")
    service = t.service()
    allowed = _ok(run(service.warehouse_stock(limited, warehouse_ref=f.warehouse_ref)))
    assert {line.product.ref for line in allowed.lines} == {f.product_ref}
    hidden = run(service.warehouse_stock(limited, warehouse_ref=f.other_warehouse_ref))
    assert hidden.status is BusinessStatus.NOT_FOUND and hidden.data is None
    other = t.principal(tenant=f.other_tenant)
    if f.other_tenant_stock_same_ref is None:
        # Refs are unique across tenants: another tenant cannot even find this warehouse.
        theirs = run(service.warehouse_stock(other, warehouse_ref=f.warehouse_ref))
        assert theirs.status is BusinessStatus.NOT_FOUND and theirs.data is None
        return
    theirs = _ok(run(service.warehouse_stock(other, warehouse_ref=f.warehouse_ref)))
    quantities = [line.quantity for line in theirs.lines]
    assert quantities == [f.other_tenant_stock_same_ref]
    assert f.stock_in_warehouse not in quantities


def invalid_date_range(t: ContractTarget) -> None:
    f = t.fixtures
    future = f.today + timedelta(days=3)
    result = run(t.service(now=f.now).daily_sales(t.principal(), day=future))
    assert (result.status, result.reason) == (BusinessStatus.VALIDATION_ERROR, "FUTURE_DATE")


def timezone_boundary(t: ContractTarget) -> None:
    f = t.fixtures
    after = _ok(run(t.service(now=f.just_after_midnight_utc).daily_sales(t.principal())))
    assert after.business_date == f.today  # still the previous UTC date
    before = _ok(run(t.service(now=f.late_evening_utc).daily_sales(t.principal())))
    assert before.business_date == f.today - timedelta(days=1)
    assert before.total.amount == f.previous_day_sales_total


def _open(t: ContractTarget, customer_ref: str) -> Any:
    return _ok(run(t.service().customer_open_invoices(t.principal(), customer_ref=customer_ref)))


def invoice_status_filtering(t: ContractTarget) -> None:
    f = t.fixtures
    refs = {line.invoice.ref for line in _open(t, f.customer_ref).invoices}
    assert refs == f.open_invoice_refs
    assert f.paid_invoice_ref not in refs


def cancelled_records(t: ContractTarget) -> None:
    f = t.fixtures
    data = _open(t, f.customer_ref)
    assert f.cancelled_invoice_ref not in {line.invoice.ref for line in data.invoices}
    assert data.total_balance is not None
    assert data.total_balance.amount == f.outstanding  # cancelled balance not counted


def partial_payments(t: ContractTarget) -> None:
    f = t.fixtures
    line = next(
        i for i in _open(t, f.customer_ref).invoices if i.invoice.ref == f.partial_invoice_ref
    )
    assert line.balance.amount == f.partial_balance
    assert line.total is not None and line.total.amount == f.partial_total
    assert line.paid is not None and line.paid.amount == f.partial_paid


def empty_result(t: ContractTarget) -> None:
    data = _open(t, t.fixtures.empty_customer_ref)
    assert data.invoice_count == 0 and data.invoices == []


def erp_timeout(t: ContractTarget) -> None:
    result = run(t.failing_service("timeout").daily_sales(t.principal()))
    assert (result.status, result.reason) == (BusinessStatus.UNAVAILABLE, "ERP_TIMEOUT")
    assert result.data is None


def connection_failure(t: ContractTarget) -> None:
    f = t.fixtures
    result = run(
        t.failing_service("connection").customer_open_invoices(
            t.principal(), customer_ref=f.customer_ref
        )
    )
    assert result.status is BusinessStatus.UNAVAILABLE and result.data is None
    assert result.reason in ("ERP_UNAVAILABLE", "ERP_TIMEOUT")


def malformed_data(t: ContractTarget) -> None:
    f = t.fixtures
    result = run(
        t.failing_service("malformed").customer_outstanding(
            t.principal(), customer_ref=f.customer_ref
        )
    )
    assert (result.status, result.reason) == (
        BusinessStatus.DEPENDENCY_ERROR,
        "MALFORMED_RESPONSE",
    )
    assert result.data is None


def unexpected_status(t: ContractTarget) -> None:
    """A status outside the contract's closed list is refused, never mapped by guess."""
    for value in t.statuses.all_values():
        assert t.statuses.group_of(value)
    for value in ("UNKNOWN", "open ", "Paid", ""):
        try:
            t.statuses.group_of(value)
        except UnexpectedStatusError:
            continue
        raise AssertionError(f"unexpected status {value!r} was accepted")


SUITE: tuple[Callable[[ContractTarget], None], ...] = (
    customer_outstanding,
    todays_sales,
    warehouse_stock,
    ambiguous_customer,
    missing_customer,
    unavailable_erp,
    unauthorized_tenant,
    unauthorized_warehouse,
    invalid_date_range,
    timezone_boundary,
    invoice_status_filtering,
    cancelled_records,
    partial_payments,
    empty_result,
    erp_timeout,
    connection_failure,
    malformed_data,
    unexpected_status,
)
