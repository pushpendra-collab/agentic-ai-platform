"""Customer counts (2026-11-25, owner approval in the project chat).

`business.customer_count` answers "mere paas kitne customers hain?" (master data, cash-sale
placeholders excluded) and, with a period or day, "aaj kitne customer aaye?" (different
customers with a posted, non-counter invoice in it; counter-sale bills counted apart).
Runs against the InvenLogic TEST replica with fresh companies, through the real readers,
the real contract mapping and the service's checks.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from agentic_platform.business.composition import compose, render_context
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.business.models import CustomerCountData, CustomersBilledData
from agentic_platform.business.results import BusinessStatus, Reason
from agentic_platform.tools.business import CustomerCountInput, tool_call_for
from tests.business_helpers import TODAY, business_principal, make_service, need_erp, run

YESTERDAY = date(2026, 9, 30)


class _Company:
    """One fresh TEST company with a known mix of customers and bills."""

    def __init__(self) -> None:
        need_erp()
        from tests.erp_db import ErpCompany

        co = ErpCompany()
        asha = co.customer("Asha Count (TEST)")
        bharat = co.customer("Bharat Count (TEST)")
        chetan = co.customer("Chetan Count (TEST)")
        co.customer("Old Shop Count (TEST)", active=False)
        walk_in = co.customer("Counter Sale (TEST)", cash_sale=True)
        # today: Asha twice, Bharat once, two counter sales, one draft (not counted)
        co.invoice(asha, "100", on=TODAY)
        co.invoice(asha, "200", on=TODAY)
        co.invoice(bharat, "300", on=TODAY)
        co.invoice(walk_in, "50", on=TODAY, counter=True, paid="50")
        co.invoice(walk_in, "60", on=TODAY, counter=True, paid="60")
        co.invoice(walk_in, "70", on=TODAY)  # placeholder billed without the counter flag
        co.invoice(chetan, "999", on=TODAY, status="DRAFT")
        # yesterday and earlier last month: Chetan only
        co.invoice(chetan, "400", on=YESTERDAY)
        co.invoice(chetan, "500", on=date(2026, 9, 10))
        self.tenant = co.tenant
        # Another company with its own customer and bill on the same day (isolation).
        other = ErpCompany()
        other.invoice(other.customer("Other Count (TEST)"), "700", on=TODAY)
        self.other_tenant = other.tenant


_CACHE: dict[str, _Company] = {}


def company() -> _Company:
    if "c" not in _CACHE:
        _CACHE["c"] = _Company()
    return _CACHE["c"]


# Each test builds a fresh adapter, so every read also runs the security gate of both
# databases it touches (sales and master data); a long-lived server pays that once.
TIMEOUT = 15.0


def count(**kwargs: Any) -> Any:
    service, _ = make_service(timeout=TIMEOUT)
    who = kwargs.pop("who", None) or business_principal(company().tenant)
    return run(service.customer_count(who, **kwargs))


# --- registered customers ------------------------------------------------------------------


def test_customers_are_counted_active_and_inactive_without_cash_sale_placeholders() -> None:
    result = count()
    assert result.status is BusinessStatus.SUCCESS
    assert isinstance(result.data, CustomerCountData)
    assert (result.data.active_count, result.data.inactive_count) == (3, 1)


def test_each_company_sees_only_its_own_customers() -> None:
    result = count(who=business_principal(company().other_tenant))
    assert result.status is BusinessStatus.SUCCESS
    assert (result.data.active_count, result.data.inactive_count) == (1, 0)


def test_a_user_without_customer_permission_gets_nothing() -> None:
    service, port = make_service(timeout=TIMEOUT)
    member = business_principal(company().tenant, roles=("member",))
    result = run(service.customer_count(member))
    assert (result.status, result.reason) == (
        BusinessStatus.UNAUTHORIZED,
        Reason.PERMISSION_MISSING,
    )
    assert port.calls == []


# --- customers billed in a period ----------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected", "start"),
    [
        ({"period": "today"}, (2, 3, 3), TODAY),
        ({"period": "yesterday"}, (1, 1, 0), YESTERDAY),
        ({"period": "last_month"}, (1, 2, 0), date(2026, 9, 1)),
        ({"day": date(2026, 9, 10)}, (1, 1, 0), date(2026, 9, 10)),
        ({"day": date(2026, 9, 11)}, (0, 0, 0), date(2026, 9, 11)),  # zero is a count
    ],
)
def test_customers_billed_counts_different_named_customers_and_counter_bills_apart(
    kwargs: dict[str, Any], expected: tuple[int, int, int], start: date
) -> None:
    from agentic_platform.business.dates import DatePeriod

    if "period" in kwargs:
        kwargs["period"] = DatePeriod(kwargs["period"])
    result = count(**kwargs)
    assert result.status is BusinessStatus.SUCCESS
    data = result.data
    assert isinstance(data, CustomersBilledData)
    assert (data.customer_count, data.invoice_count, data.counter_sale_count) == expected
    assert data.date_range.start == start


def test_a_future_day_is_refused() -> None:
    result = count(day=date(2030, 1, 1))
    assert (result.status, result.reason) == (BusinessStatus.VALIDATION_ERROR, Reason.FUTURE_DATE)


# --- tool input and typed intent -----------------------------------------------------------


def test_the_tool_takes_a_period_or_a_day_but_never_both_or_identity() -> None:
    assert CustomerCountInput.model_validate({}).period is None
    assert CustomerCountInput.model_validate({"period": "Current Month"}).period == "this_month"
    with pytest.raises(ValueError):
        CustomerCountInput.model_validate({"period": "today", "day": "2026-10-01"})
    with pytest.raises(ValueError):
        CustomerCountInput.model_validate({"tenant_id": "other"})


@pytest.mark.parametrize(
    ("intent", "args"),
    [
        ({"capability": "customer_count"}, {}),
        ({"capability": "customer_count", "period": "today"}, {"period": "today"}),
        ({"capability": "customer_count", "explicit_date": "2026-09-10"}, {"day": "2026-09-10"}),
    ],
)
def test_the_typed_intent_compiles_into_one_call(
    intent: dict[str, Any], args: dict[str, Any]
) -> None:
    call = tool_call_for(BusinessQueryIntent.model_validate(intent))
    assert call is not None
    assert (call[0], json.loads(call[1])) == ("business.customer_count", args)


# --- the answer ----------------------------------------------------------------------------


def test_the_deterministic_answer_names_what_was_counted() -> None:
    from agentic_platform.business.dates import DatePeriod

    billed = count(period=DatePeriod.TODAY).model_dump(mode="json")
    assert render_context(compose([billed])) == (
        "Customers billed on 2026-10-01: 2. Invoices to these customers: 3. "
        "Counter-sale bills (no customer name): 3."
    )
    registered = count().model_dump(mode="json")
    assert render_context(compose([registered])) == "Active customers: 3. Inactive customers: 1."
