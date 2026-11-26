"""Phase 11: business query reasoning, unit and service level.

The service-level tests run the real read path (InvenLogic readers on the TEST replica,
tests/erp_db.py; the fake ERP was removed on 2026-10-30) on the extended TEST company
(`ext()`). Nothing here touches the real ERP or a real model. The test time zone
(`TEST_TIMEZONE`) is the configured ERP_BUSINESS_TIMEZONE of these tests.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_platform.business.composition import (
    BusinessResponseContext,
    compose,
    format_amount,
    render_context,
)
from agentic_platform.business.dates import (
    BusinessDateRange,
    DatePeriod,
    period_bounds,
    resolve_period,
)
from agentic_platform.business.focus import BusinessFocus, next_focus
from agentic_platform.business.grounding import (
    claims_action,
    figures_in,
    hedges,
    numbers_in,
    sourced_numbers,
    ungrounded_numbers,
)
from agentic_platform.business.intent import BusinessQueryIntent, EntityReference
from agentic_platform.business.metrics import (
    DEFAULT_METRICS,
    METRIC_CAPABILITY,
    BusinessMetric,
    GapReason,
    MetricGap,
    MetricValue,
    measure,
)
from agentic_platform.business.results import BusinessStatus as S
from agentic_platform.business.results import Entity, Reason
from agentic_platform.business.rules import BusinessRule
from agentic_platform.security import Principal
from agentic_platform.tools.business import tool_call_for
from tests.business_helpers import (
    TODAY,
    ErpFaults,
    business_principal,
    erp_port,
    ext,
    make_service,
    need_erp,
    run,
)
from tests.erp_db import ErpCompany

# The business time zone these tests configure (ERP_BUSINESS_TIMEZONE).
TEST_TIMEZONE = "Asia/Kolkata"


def who() -> Principal:
    """A user of the extended TEST company (Rahul Verma, Godown 3)."""
    return business_principal(ext().tenant)


def service(**kw: Any) -> Any:
    svc, _ = make_service(timezone=kw.pop("timezone", TEST_TIMEZONE), **kw)
    return svc


def dumped(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = result.model_dump(mode="json")
    return out


# =============================================================================================
# A. intent classification (typed, untrusted)
# =============================================================================================


def test_intent_is_typed_and_normalises_period_aliases() -> None:
    intent = BusinessQueryIntent.model_validate(
        {
            "capability": "daily_sales",
            "operation": "total",
            "entity": "sales",
            "period": "Current Month",
            "requested_metric": "total_sales",
        }
    )
    assert intent.period is DatePeriod.THIS_MONTH
    assert intent.requested_metric is BusinessMetric.TOTAL_SALES
    assert BusinessQueryIntent(period="previous_month").period is DatePeriod.LAST_MONTH  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "smuggled",
    [
        {"tenant_id": "globex"},
        {"user_id": "boss"},
        {"warehouse_scope": "all"},
        {"permissions": ["business.warehouse.all"]},
        {"sql": "SELECT 1"},
        {"table": "invoices"},
        {"column": "balance"},
    ],
)
def test_intent_has_no_identity_scope_or_sql_field(smuggled: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BusinessQueryIntent.model_validate({"capability": "daily_sales", **smuggled})
    with pytest.raises(ValidationError):
        EntityReference.model_validate({"name": "Rahul", **smuggled})


def test_intent_reference_shapes() -> None:
    assert EntityReference(name="  Rahul Gupta ").name == "Rahul Gupta"
    assert EntityReference(name="Rahul", ref="TEST_CUSTOMER_001").tool_arguments("customer") == {
        "customer_ref": "TEST_CUSTOMER_001"
    }
    for bad in ({}, {"name": "R"}, {"ref": "x'; DROP TABLE x; --"}):
        with pytest.raises(ValidationError):
            EntityReference.model_validate(bad)


def test_safe_log_fields_hold_no_names_or_numbers() -> None:
    intent = BusinessQueryIntent(
        capability="customer_open_invoices",
        customer_reference=EntityReference(name="Rahul Gupta"),
        invoice_number="TEST-INV-001",
        explicit_date=date(2026, 9, 1),
        requested_metric=BusinessMetric.OVERDUE_AMOUNT,
    )
    text = json.dumps(intent.safe_log_fields())
    assert "Rahul" not in text and "TEST-INV" not in text and "2026" not in text
    assert intent.safe_log_fields()["has_customer"] is True


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (
            dict(capability="customer_outstanding", customer_reference={"name": "Rahul Gupta"}),
            ("business.customer_outstanding", {"customer_name": "Rahul Gupta"}),
        ),
        (dict(capability="customer_open_invoices"), ("business.customer_open_invoices", {})),
        (
            dict(
                capability="customer_open_invoices",
                invoice_number="TEST-INV-001",
                customer_reference={"ref": "TEST_CUSTOMER_001"},
            ),
            (
                "business.customer_open_invoices",
                {"customer_ref": "TEST_CUSTOMER_001", "invoice_number": "TEST-INV-001"},
            ),
        ),
        (
            dict(capability="daily_sales", period="this_week"),
            ("business.daily_sales", {"period": "this_week"}),
        ),
        (
            dict(capability="daily_sales", explicit_date="2026-09-30"),
            ("business.daily_sales", {"day": "2026-09-30"}),
        ),
        (
            dict(
                capability="product_stock",
                product_reference={"name": "iPhone 15"},
                warehouse_reference={"name": "Godown 2"},
            ),
            ("business.product_stock", {"product_name": "iPhone 15", "warehouse_name": "Godown 2"}),
        ),
        (
            dict(capability="warehouse_stock", warehouse_reference={"name": "Godown 1"}),
            ("business.warehouse_stock", {"warehouse_name": "Godown 1"}),
        ),
        (
            dict(capability="low_stock_products", product_reference={"name": "iPhone 15"}),
            ("business.low_stock_products", {"product_name": "iPhone 15"}),
        ),
    ],
)
def test_intent_compiles_to_one_typed_call(
    intent: dict[str, Any], expected: tuple[str, dict[str, str]]
) -> None:
    compiled = tool_call_for(BusinessQueryIntent.model_validate(intent))
    assert compiled is not None
    assert (compiled[0], json.loads(compiled[1])) == expected


@pytest.mark.parametrize(
    "intent",
    [
        {},  # no capability
        {"capability": "customer_outstanding"},  # needs a customer
        {"capability": "warehouse_stock"},  # needs a warehouse
        {"capability": "low_stock_products", "write_requested": True},  # a write: never
    ],
)
def test_intents_that_do_not_compile(intent: dict[str, Any]) -> None:
    assert tool_call_for(BusinessQueryIntent.model_validate(intent)) is None


# =============================================================================================
# B. date range resolution (deterministic, clock-based)
# =============================================================================================

# TODAY = 2026-10-01, a Thursday.
PERIODS = [
    (DatePeriod.TODAY, date(2026, 10, 1), date(2026, 10, 2)),
    (DatePeriod.YESTERDAY, date(2026, 9, 30), date(2026, 10, 1)),
    (DatePeriod.THIS_WEEK, date(2026, 9, 28), date(2026, 10, 2)),
    (DatePeriod.LAST_WEEK, date(2026, 9, 21), date(2026, 9, 28)),
    (DatePeriod.THIS_MONTH, date(2026, 10, 1), date(2026, 10, 2)),
    (DatePeriod.LAST_MONTH, date(2026, 9, 1), date(2026, 10, 1)),
]


@pytest.mark.parametrize(("period", "start", "end"), PERIODS, ids=[p[0].value for p in PERIODS])
def test_period_bounds(period: DatePeriod, start: date, end: date) -> None:
    assert period_bounds(period, TODAY) == (start, end)
    span = resolve_period(period, today=TODAY, timezone=TEST_TIMEZONE, source="TEST")
    assert (span.start, span.end, span.last_day) == (start, end, end - timedelta(days=1))
    assert span.semantics == "start_inclusive_end_exclusive"
    assert span.contains(start) and not span.contains(end)


def test_month_and_year_boundaries() -> None:
    assert period_bounds(DatePeriod.LAST_MONTH, date(2026, 1, 15)) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )
    assert period_bounds(DatePeriod.LAST_MONTH, date(2026, 3, 31)) == (
        date(2026, 2, 1),
        date(2026, 3, 1),
    )
    assert period_bounds(DatePeriod.THIS_WEEK, date(2026, 10, 5)) == (  # a Monday
        date(2026, 10, 5),
        date(2026, 10, 6),
    )


def test_date_range_invariants() -> None:
    with pytest.raises(ValidationError):  # empty
        BusinessDateRange(
            start=TODAY, end=TODAY, last_day=TODAY, timezone=None, timezone_source=None
        )
    with pytest.raises(ValidationError):  # zone without a source
        BusinessDateRange(
            start=date(2026, 9, 30),
            end=TODAY,
            last_day=date(2026, 9, 30),
            timezone=TEST_TIMEZONE,
            timezone_source=None,
        )


def test_the_clock_and_the_time_zone_decide_today_not_the_model() -> None:
    # FIXED_NOW is 2026-10-01 06:30 UTC: still 2026-09-30 in Los Angeles.
    la = run(service(timezone="America/Los_Angeles").daily_sales(who(), period=DatePeriod.TODAY))
    kolkata = run(service().daily_sales(who(), period=DatePeriod.TODAY))
    assert la.data.business_date == date(2026, 9, 30)
    assert kolkata.data.business_date == TODAY
    assert kolkata.data.date_range.timezone_source == "ERP_BUSINESS_TIMEZONE"


def test_without_a_business_timezone_relative_dates_are_rule_not_verified() -> None:
    svc = service(timezone=None)
    for period in DatePeriod:
        result = run(svc.daily_sales(who(), period=period))
        assert (result.status, result.reason, result.data) == (
            S.UNAVAILABLE,
            Reason.RULE_NOT_VERIFIED,
            None,
        )
    explicit = run(svc.daily_sales(who(), day=date(2026, 9, 30)))  # no zone needed
    assert explicit.status is S.SUCCESS and explicit.data.date_range.timezone is None


def test_year_bounds_financial_and_calendar() -> None:
    assert period_bounds(DatePeriod.THIS_YEAR, TODAY, 4) == (date(2026, 4, 1), date(2026, 10, 2))
    assert period_bounds(DatePeriod.LAST_YEAR, TODAY, 4) == (date(2025, 4, 1), date(2026, 4, 1))
    assert period_bounds(DatePeriod.THIS_YEAR, date(2026, 3, 31), 4) == (
        date(2025, 4, 1), date(2026, 4, 1))  # fmt: skip
    assert period_bounds(DatePeriod.THIS_YEAR, TODAY) == (date(2026, 1, 1), date(2026, 10, 2))
    assert period_bounds(DatePeriod.LAST_YEAR, TODAY) == (date(2025, 1, 1), date(2026, 1, 1))


def _year_company() -> str:
    """A company with sales on each side of the financial and calendar year boundaries."""
    need_erp()  # skips without the InvenLogic TEST replica
    c = ErpCompany()
    for day, total in ((date(2024, 12, 31), "1"), (date(2025, 3, 31), "10"),
                       (date(2025, 4, 1), "100"), (date(2025, 12, 31), "1000"),
                       (date(2026, 1, 1), "10000"), (date(2026, 3, 31), "100000"),
                       (date(2026, 4, 1), "1000000")):  # fmt: skip
        c.sales_day(day, total, 1)
    return c.tenant


def _year_rules(*year_rules: BusinessRule) -> frozenset[str]:
    years = {
        BusinessRule.YEAR_IS_CALENDAR_YEAR.value,
        BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL.value,
    }
    return frozenset((erp_port().verified_rules - years) | {r.value for r in year_rules})


def test_the_contract_resolves_years_as_financial_years() -> None:
    # The InvenLogic contract confirms the Indian financial year (April to March).
    contract_rules = erp_port().verified_rules
    assert BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL.value in contract_rules
    assert BusinessRule.YEAR_IS_CALENDAR_YEAR.value not in contract_rules
    tenant = business_principal(_year_company())
    svc = service()
    this_year = run(svc.daily_sales(tenant, period=DatePeriod.THIS_YEAR))
    assert this_year.status is S.SUCCESS
    d = this_year.data
    assert (d.date_range.start, d.date_range.last_day) == (date(2026, 4, 1), TODAY)
    assert (d.total.amount, d.days_with_sales) == (Decimal("1000000.00"), 1)
    last = run(svc.daily_sales(tenant, period=DatePeriod.LAST_YEAR)).data
    assert (last.date_range.start, last.date_range.last_day) == (
        date(2025, 4, 1), date(2026, 3, 31))  # fmt: skip
    assert (last.total.amount, last.days_with_sales) == (Decimal("111100.00"), 4)


def test_calendar_years_when_that_rule_is_confirmed_instead() -> None:
    tenant = business_principal(_year_company())
    svc = service(rules=_year_rules(BusinessRule.YEAR_IS_CALENDAR_YEAR))
    this_year = run(svc.daily_sales(tenant, period=DatePeriod.THIS_YEAR)).data
    assert (this_year.date_range.start, this_year.date_range.last_day) == (date(2026, 1, 1), TODAY)
    assert this_year.total.amount == Decimal("1110000.00")
    last = run(svc.daily_sales(tenant, period=DatePeriod.LAST_YEAR)).data
    assert (last.date_range.start, last.date_range.last_day) == (
        date(2025, 1, 1), date(2025, 12, 31))  # fmt: skip
    assert last.total.amount == Decimal("1110.00")


@pytest.mark.parametrize(
    "year_rules",
    [(), (BusinessRule.YEAR_IS_CALENDAR_YEAR, BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL)],
    ids=["neither", "both"],
)
def test_year_needs_exactly_one_confirmed_year_rule(year_rules: tuple[BusinessRule, ...]) -> None:
    svc, port = make_service(rules=_year_rules(*year_rules))
    for period in (DatePeriod.THIS_YEAR, DatePeriod.LAST_YEAR):
        result = run(svc.daily_sales(who(), period=period))
        assert (result.status, result.reason, result.data) == (
            S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED, None)  # fmt: skip
    assert port.calls == []  # refused before the ERP
    month = run(svc.daily_sales(who(), period=DatePeriod.LAST_MONTH))
    assert month.status is S.SUCCESS  # only the year periods depend on the year rule


# =============================================================================================
# C/D. metrics and aggregation (service computes; rules gate derived figures)
# =============================================================================================


def test_every_metric_belongs_to_one_of_the_seven_capabilities() -> None:
    assert set(METRIC_CAPABILITY.values()) == set(DEFAULT_METRICS) and len(DEFAULT_METRICS) == 7
    assert set(METRIC_CAPABILITY) == set(BusinessMetric)


def test_open_invoices_aggregates_are_computed_by_the_service() -> None:
    d = run(service().customer_open_invoices(who(), customer_name="Rahul Gupta")).data
    assert [i.invoice.ref for i in d.invoices] == [ext()["inv1"], ext()["inv2"]]
    assert d.total_balance.amount == Decimal("45000.00")
    assert d.invoice_total.amount == Decimal("55000.00")  # gross of the two open ones
    assert d.paid_total.amount == Decimal("10000.00")
    assert (d.overdue_count, d.overdue_balance.amount) == (1, Decimal("30000.00"))
    assert d.as_of_date == TODAY and d.rules_not_verified == []
    assert [i.overdue for i in d.invoices] == [True, False]


def test_paid_invoices_are_not_open() -> None:
    d = run(service().customer_open_invoices(who(), customer_name="Rahul Gupta")).data
    assert ext()["inv3"] not in {i.invoice.ref for i in d.invoices}  # fully paid


def test_overdue_needs_its_rule_and_the_business_day() -> None:
    no_rule = run(
        service(rules=frozenset()).customer_open_invoices(who(), customer_name="Rahul Gupta")
    ).data
    assert no_rule.overdue_balance is None and no_rule.overdue_count is None
    assert no_rule.rules_not_verified == [BusinessRule.OVERDUE_BY_DUE_DATE.value]
    assert all(i.overdue is None for i in no_rule.invoices)
    assert no_rule.total_balance.amount == Decimal("45000.00")  # needs no extra rule
    no_zone = run(service(timezone=None).customer_open_invoices(who(), customer_name="Rahul Gupta"))
    assert no_zone.data.rules_not_verified == [BusinessRule.BUSINESS_TIMEZONE.value]
    gap = measure(BusinessMetric.OVERDUE_AMOUNT, no_rule)
    assert isinstance(gap, MetricGap) and gap.reason == GapReason.RULE_NOT_VERIFIED


def test_a_truncated_list_gives_no_total_or_overdue_figure() -> None:
    d = run(service(max_rows=1).customer_open_invoices(who(), customer_name="Rahul Gupta")).data
    assert d.truncated and d.total_balance is None and d.overdue_balance is None
    assert measure(BusinessMetric.OPEN_INVOICE_TOTAL, d) == MetricGap(
        metric=BusinessMetric.OPEN_INVOICE_TOTAL, reason=GapReason.INCOMPLETE_LIST
    )


def test_sales_period_is_the_sum_of_its_days() -> None:
    week = run(service().daily_sales(who(), period=DatePeriod.THIS_WEEK))
    assert week.status is S.SUCCESS
    d = week.data
    assert (d.date_range.start, d.date_range.last_day) == (date(2026, 9, 28), TODAY)
    assert [x.business_date for x in d.days] == [date(2026, 9, 28), date(2026, 9, 30), TODAY]
    assert (d.total.amount, d.document_count, d.days_with_sales) == (Decimal("245000.00"), 14, 3)
    month = run(service().daily_sales(who(), period=DatePeriod.LAST_MONTH)).data
    assert (month.total.amount, month.document_count) == (Decimal("140000.00"), 8)
    yesterday = run(service().daily_sales(who(), period=DatePeriod.YESTERDAY)).data
    assert (yesterday.business_date, yesterday.total.amount) == (
        date(2026, 9, 30),
        Decimal("80000.00"),
    )


def test_sales_period_rules() -> None:
    no_sum_svc, no_sum = make_service(rules=frozenset({BusinessRule.WEEK_STARTS_MONDAY.value}))
    result = run(no_sum_svc.daily_sales(who(), period=DatePeriod.LAST_MONTH))
    assert (result.status, result.reason) == (S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)
    no_week_svc, no_week = make_service(
        rules=frozenset({BusinessRule.SALES_PERIOD_IS_SUM_OF_DAYS.value})
    )
    assert run(no_week_svc.daily_sales(who(), period=DatePeriod.THIS_WEEK)).reason == (
        Reason.RULE_NOT_VERIFIED
    )
    assert run(no_week_svc.daily_sales(who(), period=DatePeriod.THIS_MONTH)).status is S.SUCCESS
    assert no_sum.calls == [] and no_week.calls == ["sales_by_day"]  # refused before the ERP


def test_sales_period_rejects_days_outside_the_range() -> None:
    port: ErpFaults = erp_port()
    real_sales_by_day = port.sales_by_day  # type: ignore[attr-defined]

    async def wrong_days(ctx: Any, start: date, end: date, limit: int) -> list[Any]:
        # A broken reader: answers real rows of the whole quarter, ignoring the range.
        rows: list[Any] = await real_sales_by_day(ctx, date(2026, 7, 1), end, 500)
        return rows

    port.sales_by_day = wrong_days  # type: ignore[attr-defined]
    result = run(service(adapter=port).daily_sales(who(), period=DatePeriod.THIS_MONTH))
    assert (result.status, result.reason) == (S.DEPENDENCY_ERROR, Reason.MALFORMED_RESPONSE)


def test_stock_in_one_warehouse_and_is_it_low() -> None:
    svc = service()
    one = run(svc.product_stock(who(), product_name="iPhone 15", warehouse_name="Godown 2")).data
    assert (one.total_quantity, one.warehouse_count) == (Decimal("3"), 1)
    assert one.warehouse_filter.ref == ext()["godown2"]
    low = run(svc.low_stock_products(who(), product_name="iPhone 15")).data
    assert measure(BusinessMetric.IS_LOW_STOCK, low).value == "yes"  # type: ignore[union-attr]
    ok = run(svc.low_stock_products(who(), product_name="USB-C")).data
    assert measure(BusinessMetric.IS_LOW_STOCK, ok).value == "no"  # type: ignore[union-attr]
    unnamed = run(svc.low_stock_products(who())).data
    assert measure(BusinessMetric.IS_LOW_STOCK, unnamed).reason == GapReason.NOT_APPLICABLE  # type: ignore[union-attr]


def test_metrics_only_select_figures() -> None:
    d = run(service().customer_outstanding(who(), customer_name="Rahul Gupta")).data
    value = measure(BusinessMetric.OUTSTANDING_AMOUNT, d)
    assert value == MetricValue(
        metric=BusinessMetric.OUTSTANDING_AMOUNT,
        label="Outstanding",
        kind="money",
        value="45000.00",
        currency="INR",
    )
    assert measure(BusinessMetric.TOTAL_SALES, d).reason == GapReason.WRONG_CAPABILITY  # type: ignore[union-attr]


# =============================================================================================
# E/F/G. multi-record results, ambiguity, not found
# =============================================================================================


def test_open_invoices_of_all_customers() -> None:
    d = run(service().customer_open_invoices(who())).data
    assert d.customer is None and d.invoice_count == 4
    assert {i.customer.display_name for i in d.invoices} == {
        "Rahul Gupta (TEST)",
        "Amit Sharma Traders (TEST)",
        "Rahul Verma (TEST)",
    }
    assert d.total_balance.amount == Decimal("65000.00")
    assert d.overdue_count == 3


def test_ambiguous_customer_is_safe() -> None:
    result = run(service().customer_outstanding(who(), customer_name="Rahul"))
    assert (result.status, result.entity, result.data) == (S.AMBIGUOUS, Entity.CUSTOMER, None)
    view = compose([dumped(result)], BusinessQueryIntent(customer_reference={"name": "Rahul"}))  # type: ignore[arg-type]
    clarification = view.results[0].clarification
    assert clarification is not None
    assert clarification.options == ["Rahul Gupta (TEST)", "Rahul Verma (TEST)"]
    text = render_context(view)
    assert "Several customers match 'Rahul'" in text and "customer ID" in text
    # no figure, no reference, nothing of another tenant
    assert not numbers_in(text) and "Globex" not in text
    assert ext()["rahul"] not in text and ext()["verma"] not in text


@pytest.mark.parametrize(
    ("call", "entity", "message"),
    [
        (
            lambda s: s.customer_outstanding(who(), customer_name="Nobody"),
            Entity.CUSTOMER,
            "No customer matching 'Nobody' was found.",
        ),
        (
            lambda s: s.customer_open_invoices(who(), invoice_number="TEST-INV-999"),
            Entity.INVOICE,
            "No open invoice numbered 'TEST-INV-999' was found.",
        ),
        (
            lambda s: s.product_stock(who(), product_name="Nokia"),
            Entity.PRODUCT,
            "No product matching 'Nokia' was found.",
        ),
        (
            lambda s: s.warehouse_stock(who(), warehouse_name="Godown 9"),
            Entity.WAREHOUSE,
            "No warehouse matching 'Godown 9' was found.",
        ),
        (
            lambda s: s.daily_sales(who(), day=date(2026, 9, 29)),
            Entity.SALES,
            "No sales record was found",
        ),
    ],
)
def test_not_found_is_standard_and_plain(call: Any, entity: str, message: str) -> None:
    result = run(call(service()))
    assert (result.status, result.reason, result.entity) == (S.NOT_FOUND, Reason.NO_MATCH, entity)
    intent = BusinessQueryIntent(
        customer_reference={"name": "Nobody"},
        product_reference={"name": "Nokia"},  # type: ignore[arg-type]
        warehouse_reference={"name": "Godown 9"},
        invoice_number="TEST-INV-999",  # type: ignore[arg-type]
    )
    text = render_context(compose([dumped(result)], intent))
    assert message in text
    assert not hedges(text)


def test_entity_only_on_not_found_or_ambiguous() -> None:
    from agentic_platform.business.results import BusinessResult

    with pytest.raises(ValidationError):
        BusinessResult(capability="x", status=S.UNAVAILABLE, entity="customer")


# =============================================================================================
# composition and rendering
# =============================================================================================


def test_composition_selects_requested_metric_and_filters_records() -> None:
    result = dumped(run(service().customer_open_invoices(who(), customer_name="Rahul Gupta")))
    intent = BusinessQueryIntent(
        capability="customer_open_invoices", requested_metric=BusinessMetric.OVERDUE_AMOUNT
    )
    context = compose([result], intent)
    view = context.results[0]
    assert [f.metric for f in view.figures] == [
        BusinessMetric.OVERDUE_AMOUNT,
        BusinessMetric.OPEN_INVOICE_TOTAL,
        BusinessMetric.OPEN_INVOICE_COUNT,
        BusinessMetric.OVERDUE_INVOICE_COUNT,  # 2026-11-25: shown with overdue questions
    ]
    assert view.figures[0].value == "30000.00"
    assert view.records_filter == "overdue" and [r.ref for r in view.records] == [ext()["inv1"]]
    assert view.steps == [
        "resolve_customer",
        "read_open_invoices",
        "filter_overdue_by_rule",
        "aggregate_in_service",
    ]
    # The real read path is not TEST data: no TEST_DATA warning, no TEST notice.
    assert "TEST_DATA" not in view.warnings and not context.test_data
    text = render_context(context)
    assert text.startswith("Overdue amount for Rahul Gupta (TEST): ₹30,000.")
    assert "(TEST data, not real company data.)" not in text


def test_a_rule_gap_is_stated_not_estimated() -> None:
    svc = service(rules=frozenset())
    result = dumped(run(svc.customer_open_invoices(who(), customer_name="Rahul Gupta")))
    intent = BusinessQueryIntent(requested_metric=BusinessMetric.OVERDUE_AMOUNT)
    view = compose([result], intent).results[0]
    assert view.gaps == [
        MetricGap(
            metric=BusinessMetric.OVERDUE_AMOUNT,
            reason=GapReason.RULE_NOT_VERIFIED,
            rules=[BusinessRule.OVERDUE_BY_DUE_DATE.value],
        )
    ]
    assert "OVERDUE_NOT_DETERMINED" in view.warnings
    assert "not verified yet" in render_context(compose([result], intent))


def test_context_holds_no_tenant_ids_or_internals() -> None:
    result = dumped(run(service().customer_open_invoices(who())))
    text = json.dumps(compose([result]).to_prompt())
    assert ext().tenant not in text and "tenant" not in text
    # The source names its adapter kind ("postgres"); nothing else of the database does.
    assert text.count('"adapter": "postgres"') == 1
    internals = text.replace('"adapter": "postgres"', "")
    assert "postgres" not in internals and "SELECT" not in internals
    for detail in ("sales_db", "master_data_db", "inventory_db", "agentic_erp_reader", "5434"):
        assert detail not in internals


def test_a_tampered_success_payload_is_not_rendered_as_figures() -> None:
    result = dumped(run(service().customer_outstanding(who(), customer_name="Rahul Gupta")))
    result["data"]["outstanding"]["amount"] = "not-a-number"
    view = compose([result]).results[0]
    assert (view.status, view.figures) == ("DEPENDENCY_ERROR", [])


def test_rendering_introduces_no_unsourced_figure() -> None:
    svc = service()
    results = [
        dumped(run(svc.customer_open_invoices(who()))),
        dumped(run(svc.daily_sales(who(), period=DatePeriod.THIS_WEEK))),
        dumped(run(svc.product_stock(who(), product_name="Galaxy"))),
        dumped(run(svc.low_stock_products(who()))),
        dumped(run(svc.warehouse_stock(who(), warehouse_name="Godown 3"))),
    ]
    text = render_context(compose(results))
    assert ungrounded_numbers(text, sourced_numbers(results, "")) == []


@pytest.mark.parametrize(
    ("amount", "currency", "text"),
    [
        ("45000.00", "INR", "₹45,000"),
        ("125000.00", "INR", "₹1,25,000"),
        ("12345678.50", "INR", "₹1,23,45,678.50"),
        ("999", "INR", "₹999"),
        ("-500.00", "INR", "-₹500"),
        ("1234.5", "USD", "USD 1,234.5"),
    ],
)
def test_amount_formatting(amount: str, currency: str, text: str) -> None:
    assert format_amount(amount, currency) == text
    assert figures_in(text) == numbers_in(amount.lstrip("-"))


def test_write_request_context_carries_the_read_only_notice() -> None:
    context = compose([], BusinessQueryIntent(write_requested=True))
    assert context.read_only_notice is not None
    assert "only read business data" in render_context(context)
    assert isinstance(context, BusinessResponseContext)


# =============================================================================================
# K. grounding of figure forms
# =============================================================================================


@pytest.mark.parametrize(
    "reply",
    [
        "45,000",
        "45000",
        "45k",
        "45K",
        "forty-five thousand",
        "forty five thousand",
        "₹45,000",
        "INR 45,000",
        "Rs. 45,000.00",
        "45 thousand",
        "45 hazaar",
        "0.45 lakh",
    ],
)
def test_every_form_of_45000_is_read_as_45000(reply: str) -> None:
    assert figures_in(f"Outstanding is {reply}.") == {"45000"}
    assert ungrounded_numbers(f"Outstanding is {reply}.", {"45000"}) == []
    assert ungrounded_numbers(f"Outstanding is {reply}.", {"50000"}) == ["45000"]


@pytest.mark.parametrize(
    "reply",
    ["50,000", "50k", "fifty thousand", "₹50,000", "INR 50,000", "0.5 lakh"],
)
def test_every_form_of_a_memory_figure_is_caught(reply: str) -> None:
    assert ungrounded_numbers(f"Sales are {reply}.", {"45000"}) == ["50000"]


def test_scaled_and_word_figures() -> None:
    assert figures_in("1.25 lakh") == {"125000"} == figures_in("one lakh twenty five thousand")
    assert figures_in("₹1,25,000") == {"125000"}
    assert figures_in("2 crore") == {"20000000"}
    assert figures_in("three hundred and twenty") == {"320"}
    # a lone small number word is not a figure: "which one?" must not trip the check
    assert figures_in("Which one do you mean?") == set()


def test_hedges_and_action_claims() -> None:
    assert hedges("Maybe the customer does not exist.")
    assert hedges("Shayad 45,000 hoga.") and hedges("I think it is 45k.")
    assert not hedges("Rahul Gupta ka outstanding ₹45,000 hai.")
    for claim in (
        "Maine purchase order bana diya hai.",
        "The PO has been created.",
        "I have placed the order for you.",
        "WhatsApp message sent to the supplier.",
    ):
        assert claims_action(claim), claim
    for safe in (
        "Invoice TEST-INV-001 was issued on 2026-08-01.",
        "Creating purchase orders is not available.",
        "I can only read business data.",
    ):
        assert not claims_action(safe), safe


# =============================================================================================
# focus (references only)
# =============================================================================================


def test_focus_keeps_references_never_figures() -> None:
    svc = service()
    first = dumped(run(svc.customer_open_invoices(who(), customer_name="Rahul Gupta")))
    focus = next_focus(None, [first], BusinessQueryIntent(period=DatePeriod.THIS_MONTH))
    assert focus.customer is not None and focus.customer.ref == ext()["rahul"]
    state = focus.to_state()
    text = json.dumps(state)
    for figure in ("45000", "30000", "15000", "INR", "2026"):
        assert figure not in text
    # a later stock answer adds a product and keeps the customer
    later = dumped(run(svc.product_stock(who(), product_name="iPhone 15")))
    after = next_focus(state, [later], None)
    assert (after.customer.ref, after.product.ref) == (ext()["rahul"], ext()["iphone"])  # type: ignore[union-attr]


def test_focus_records_ambiguous_candidates_and_drops_garbage() -> None:
    ambiguous = dumped(run(service().customer_outstanding(who(), customer_name="Rahul")))
    focus = next_focus(None, [ambiguous], None)
    assert [c.ref for c in focus.candidates] == [ext()["rahul"], ext()["verma"]]
    assert BusinessFocus.load({"customer": {"tenant_id": "globex"}}) == BusinessFocus()
