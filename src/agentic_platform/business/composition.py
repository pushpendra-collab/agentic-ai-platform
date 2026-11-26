"""Business result composition (Phase 11, ADR-060).

    BusinessResult (service, per tool call)
        ─► compose()  ─► BusinessResponseContext   (typed, deterministic, no LLM)
        ─► generate_response (LLM phrases it)  ─► verify_business_response (grounding)
        ─► render_context() when the draft is rejected

Composition only *selects and normalises*. It re-validates each SUCCESS payload into its
typed `*Data` model, then:

* picks the authoritative figures: the requested metric plus the capability's defaults
  (`metrics.measure`, which never computes), or a `MetricGap` saying why one is missing
  (e.g. RULE_NOT_VERIFIED);
* normalises records (identity, name, amount, quantity, status, dates) and applies the
  intent's filters to flags the service already computed (e.g. `overdue`);
* states the resolved date range, the data source (`test_data`), warnings, and for
  NOT_FOUND / AMBIGUOUS / failures a fixed, safe message and clarification.

The context holds no tenant ids, credentials, SQL or adapter internals: only what the
`BusinessResult`s already showed the LLM in Phase 10, re-shaped.

Phase 16 (ADR-065): a sales comparison (`SalesComparisonData`) becomes two
`sales_period` records (current and same period last year), the comparison figures, a
gap for every figure that could not be computed (no records, zero baseline, mixed
currency) and a fixed basis notice: same dates one year earlier, sales as recorded, not
profit, internal data only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agentic_platform.business.dates import BusinessDateRange
from agentic_platform.business.intent import BusinessQueryIntent, InvoiceFilter
from agentic_platform.business.metrics import (
    BILLED_METRICS,
    COMPARISON_METRICS,
    DEFAULT_METRICS,
    METRIC_CAPABILITY,
    BusinessMetric,
    MetricGap,
    MetricValue,
    measure,
)
from agentic_platform.business.models import (
    CustomerCountData,
    CustomerOutstandingData,
    CustomersBilledData,
    DailySalesData,
    LowStockData,
    OpenInvoicesData,
    ProductStockData,
    SalesComparisonData,
    SalesPeriodData,
    SalesPeriodSummary,
    WarehouseStockData,
)
from agentic_platform.business.results import DataSource, Entity, Reason

# Records rendered in the deterministic fallback (the context itself keeps all of them).
MAX_RENDERED_RECORDS = 10

# Questions about facts the ERP does not record (2026-11-25). Keyed by the reason the
# business domain gives in `DomainResult.metadata["not_recorded"]`.
NOT_RECORDED_MESSAGES: dict[str, str] = {
    "new_registrations": (
        "The ERP data this assistant reads does not record when a customer or user was "
        "registered, so new registrations can't be counted. You can ask how many customers "
        "you have, or how many customers were billed today or in a period."
    ),
}

READ_ONLY_NOTICE = (
    "I can only read business data. Creating or changing purchase orders, invoices, "
    "payments, stock or messages is not available in this assistant."
)

_DATA_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "customer_outstanding": TypeAdapter(CustomerOutstandingData),
    "customer_open_invoices": TypeAdapter(OpenInvoicesData),
    "daily_sales": TypeAdapter(DailySalesData | SalesPeriodData | SalesComparisonData),
    "product_stock": TypeAdapter(ProductStockData),
    "warehouse_stock": TypeAdapter(WarehouseStockData),
    "low_stock_products": TypeAdapter(LowStockData),
    "customer_count": TypeAdapter(CustomerCountData | CustomersBilledData),
}

RecordKind = Literal["invoice", "sales_day", "sales_period", "stock", "low_stock"]


class RecordView(BaseModel):
    """One normalised business record. Values are text exactly as the service gave them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RecordKind
    ref: str
    name: str
    amount: str | None = None
    currency: str | None = None
    quantity: str | None = None
    unit: str | None = None
    status: str | None = None
    record_date: date | None = None
    due_date: date | None = None
    details: dict[str, str] = Field(default_factory=dict)


class Clarification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: str | None
    question: str
    # Display names only: no references, amounts or other fields of the candidates.
    options: list[str] = Field(default_factory=list)


class ResultView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    status: str
    reason: str | None = None
    entity: str | None = None
    subject: str | None = None  # e.g. the customer, product or warehouse display name
    figures: list[MetricValue] = Field(default_factory=list)
    gaps: list[MetricGap] = Field(default_factory=list)
    records: list[RecordView] = Field(default_factory=list)
    records_filter: str | None = None
    date_range: BusinessDateRange | None = None
    source: DataSource | None = None
    warnings: list[str] = Field(default_factory=list)
    # Deterministic sentence for this result (used verbatim for non-SUCCESS statuses).
    message: str
    clarification: Clarification | None = None
    # Typed derivation of the answer: which deterministic steps produced it.
    steps: list[str] = Field(default_factory=list)


class BusinessResponseContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    results: list[ResultView] = Field(default_factory=list)
    requested_metric: BusinessMetric | None = None
    read_only_notice: str | None = None
    test_data: bool = False

    @property
    def needs_clarification(self) -> bool:
        return any(r.clarification is not None for r in self.results)

    def to_prompt(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, exclude_defaults=False)


# --- formatting --------------------------------------------------------------------------


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def format_amount(value: str, currency: str | None) -> str:
    """₹ with Indian grouping for INR ("₹1,25,000"); "USD 1,234.50" otherwise. Integral
    amounts drop ".00". Pure formatting: the figure is unchanged."""
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return value
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    text = format(amount.quantize(Decimal(1)) if amount == amount.to_integral() else amount, "f")
    whole, _, fraction = text.partition(".")
    if currency == "INR":
        grouped = _group_indian(whole)
        return f"{sign}₹{grouped}{'.' + fraction if fraction else ''}"
    grouped = f"{int(whole):,}"
    body = f"{grouped}{'.' + fraction if fraction else ''}"
    return f"{sign}{currency} {body}" if currency else f"{sign}{body}"


def format_quantity(value: str, unit: str | None) -> str:
    try:
        number = Decimal(value)
        text = format(number.normalize(), "f") if number == number.to_integral() else value
    except InvalidOperation:
        text = value
    return f"{text} {unit}" if unit else text


def format_figure(figure: MetricValue) -> str:
    if figure.kind == "money":
        return format_amount(figure.value, figure.currency)
    if figure.kind == "quantity":
        return format_quantity(figure.value, figure.unit)
    if figure.kind == "percent":
        return f"{figure.value}%"
    return figure.value


# --- status messages ------------------------------------------------------------------------

_ENTITY_WORD = {
    Entity.CUSTOMER: "customer",
    Entity.PRODUCT: "product",
    Entity.WAREHOUSE: "warehouse",
    Entity.INVOICE: "open invoice",
}


def _asked_name(entity: str | None, intent: BusinessQueryIntent | None) -> str | None:
    if intent is None:
        return None
    ref = {
        Entity.CUSTOMER: intent.customer_reference,
        Entity.PRODUCT: intent.product_reference,
        Entity.WAREHOUSE: intent.warehouse_reference,
    }.get(entity or "")
    if entity == Entity.INVOICE:
        return intent.invoice_number
    return ref.name if ref is not None else None


def _not_found(entity: str | None, intent: BusinessQueryIntent | None) -> str:
    if entity == Entity.SALES:
        return "No sales record was found for the requested day or period."
    if entity == Entity.OUTSTANDING:
        return "No outstanding balance record was found for this customer."
    word = _ENTITY_WORD.get(entity or "")
    if word is None:
        return "No matching record was found."
    name = _asked_name(entity, intent)
    if entity == Entity.INVOICE:
        return f"No {word} numbered '{name}' was found." if name else f"No {word} was found."
    if name:
        return f"No {word} matching '{name}' was found. Please check the name."
    return f"No matching {word} was found. Please check the name."


def _ambiguous(entity: str | None, options: list[str], intent: BusinessQueryIntent | None) -> str:
    word = _ENTITY_WORD.get(entity or "", "record")
    name = _asked_name(entity, intent)
    match = f"match '{name}'" if name else "match"
    listed = f": {', '.join(options)}" if options else ""
    return (
        f"Several {word}s {match}{listed}. Which one do you mean? "
        f"You can also give the {word} ID or another identifying detail."
    )


def _status_message(
    status: str, reason: str | None, entity: str | None, intent: BusinessQueryIntent | None
) -> str:
    if status == "NOT_FOUND":
        return _not_found(entity, intent)
    if status == "UNAVAILABLE":
        if reason in (Reason.RULE_NOT_VERIFIED, Reason.ERP_NOT_CONFIGURED):
            return "This information is not available in the assistant yet."
        return "The current business data could not be retrieved right now. Please try again later."
    if status == "UNAUTHORIZED":
        return "You do not have access to this information."
    if status == "VALIDATION_ERROR":
        if reason == Reason.FUTURE_DATE:
            return "Sales cannot be reported for a date in the future."
        if reason == Reason.PERIOD_REQUIRED:
            return "Please name the period to compare, for example this month or last year."
        return "The question could not be answered as asked."
    if status == "DEPENDENCY_ERROR":
        return "The business data could not be read correctly, so no figure is given."
    return "The business data could not be retrieved."


# --- per-capability views ---------------------------------------------------------------------


def _wanted_metrics(
    capability: str, intent: BusinessQueryIntent | None, data: BaseModel | None = None
) -> list[BusinessMetric]:
    wanted: list[BusinessMetric] = []
    asked = intent.requested_metric if intent is not None else None
    if asked is not None and METRIC_CAPABILITY[asked] == capability:
        wanted.append(asked)
    if isinstance(data, SalesComparisonData):
        defaults = COMPARISON_METRICS
    elif isinstance(data, CustomersBilledData):
        defaults = BILLED_METRICS
    else:
        defaults = DEFAULT_METRICS.get(capability, ())
    if capability == "customer_open_invoices" and wants_overdue(intent):
        # "Overdue bills dikhao": the overdue figures belong next to the open ones.
        defaults = (*defaults, BusinessMetric.OVERDUE_INVOICE_COUNT, BusinessMetric.OVERDUE_AMOUNT)
    for metric in defaults:
        if metric not in wanted:
            wanted.append(metric)
    return wanted


def wants_overdue(intent: BusinessQueryIntent | None) -> bool:
    return intent is not None and (
        InvoiceFilter.OVERDUE in intent.filters
        or intent.requested_metric
        in (BusinessMetric.OVERDUE_AMOUNT, BusinessMetric.OVERDUE_INVOICE_COUNT)
    )


def _invoice_records(d: OpenInvoicesData) -> list[RecordView]:
    records: list[RecordView] = []
    for line in d.invoices:
        details: dict[str, str] = {}
        if line.customer is not None:
            details["customer"] = line.customer.display_name
        if line.total is not None:
            details["invoice_total"] = str(line.total.amount)
        if line.paid is not None:
            details["paid"] = str(line.paid.amount)
        status = None if line.overdue is None else ("overdue" if line.overdue else "open")
        records.append(
            RecordView(
                kind="invoice",
                ref=line.invoice.ref,
                name=line.invoice.display_name,
                amount=str(line.balance.amount),
                currency=line.balance.currency,
                status=status,
                record_date=line.issued_on,
                due_date=line.due_on,
                details=details,
            )
        )
    return records


def _period_record(side: SalesPeriodSummary, which: str) -> RecordView:
    span = side.date_range
    return RecordView(
        kind="sales_period",
        ref=f"{span.start.isoformat()}..{span.last_day.isoformat()}",
        name=which,
        amount=str(side.total.amount) if side.total is not None else None,
        currency=side.total.currency if side.total is not None else None,
        status=None if side.days_with_sales else "no_sales_record",
        record_date=span.start,
        details={
            "last_day": span.last_day.isoformat(),
            "days": str(span.days),
            "days_with_sales": str(side.days_with_sales),
            "document_count": str(side.document_count),
        },
    )


def _success_view(
    capability: str,
    data: BaseModel,
    intent: BusinessQueryIntent | None,
) -> dict[str, Any]:
    """Fields of a SUCCESS ResultView (everything except status/source/message)."""
    view: dict[str, Any] = {"warnings": [], "steps": [], "records": []}
    warnings: list[str] = view["warnings"]
    if isinstance(data, CustomerOutstandingData):
        view["subject"] = data.customer.display_name
        view["steps"] = ["resolve_customer", "read_outstanding"]
    elif isinstance(data, OpenInvoicesData):
        view["subject"] = data.customer.display_name if data.customer else None
        steps = ["resolve_customer"] if data.customer else []
        steps += ["read_open_invoices"]
        records = _invoice_records(data)
        if wants_overdue(intent):
            if data.overdue_count is not None:
                records = [r for r in records if r.status == "overdue"]
                view["records_filter"] = "overdue"
                steps.append("filter_overdue_by_rule")
            else:
                warnings.append("OVERDUE_NOT_DETERMINED")
        steps.append("aggregate_in_service")
        view["records"], view["steps"] = records, steps
        if data.truncated:
            warnings.append("LIST_TRUNCATED")
        if data.rules_not_verified:
            warnings.append("RULES_NOT_VERIFIED")
    elif isinstance(data, DailySalesData):
        view["date_range"] = data.date_range
        view["steps"] = ["resolve_business_day", "read_daily_sales"]
    elif isinstance(data, SalesPeriodData):
        view["date_range"] = data.date_range
        view["records"] = [
            RecordView(
                kind="sales_day",
                ref=day.business_date.isoformat(),
                name=day.business_date.isoformat(),
                amount=str(day.total.amount),
                currency=day.total.currency,
                record_date=day.business_date,
                details={"document_count": str(day.document_count)},
            )
            for day in data.days
        ]
        view["steps"] = ["resolve_period", "read_sales_by_day", "aggregate_in_service"]
        if data.days_with_sales < data.date_range.days:
            warnings.append("DAYS_WITHOUT_SALES_RECORD")
    elif isinstance(data, SalesComparisonData):
        view["date_range"] = data.current.date_range
        view["records"] = [
            _period_record(data.current, "current"),
            _period_record(data.baseline, "same_period_last_year"),
        ]
        view["steps"] = [
            "resolve_period",
            "resolve_same_period_last_year",
            "read_sales_by_day",
            "read_sales_by_day",
            "compare_in_service",
        ]
        warnings.append("COMPARISON_BASIS")
        warnings += list(data.gaps)
        if data.day_counts_differ:
            warnings.append("DAY_COUNTS_DIFFER")
    elif isinstance(data, ProductStockData):
        view["subject"] = data.product.display_name
        view["records"] = [
            RecordView(
                kind="stock",
                ref=line.warehouse.ref,
                name=line.warehouse.display_name,
                quantity=str(line.quantity),
                unit=data.unit,
            )
            for line in data.lines
        ]
        view["steps"] = ["resolve_product"]
        if data.warehouse_filter is not None:
            view["steps"].append("resolve_warehouse_in_scope")
            view["records_filter"] = data.warehouse_filter.display_name
        view["steps"] += ["read_stock", "filter_warehouse_scope", "aggregate_in_service"]
        if data.scope_limited:
            warnings.append("WAREHOUSE_SCOPE_LIMITED")
    elif isinstance(data, WarehouseStockData):
        view["subject"] = data.warehouse.display_name
        view["records"] = [
            RecordView(
                kind="stock",
                ref=line.product.ref,
                name=line.product.display_name,
                quantity=str(line.quantity),
                unit=line.unit,
            )
            for line in data.lines
        ]
        view["steps"] = ["resolve_warehouse_in_scope", "read_stock"]
        if data.truncated:
            warnings.append("LIST_TRUNCATED")
    elif isinstance(data, LowStockData):
        view["subject"] = data.product_filter.display_name if data.product_filter else None
        view["records"] = [
            RecordView(
                kind="low_stock",
                ref=f"{item.product.ref}@{item.warehouse.ref}",
                name=item.product.display_name,
                quantity=str(item.quantity),
                unit=item.unit,
                status="low",
                details={
                    "warehouse": item.warehouse.display_name,
                    "low_stock_level": str(item.threshold),
                },
            )
            for item in data.items
        ]
        view["steps"] = (["resolve_product"] if data.product_filter else []) + [
            "read_low_stock",
            "filter_warehouse_scope",
        ]
        if data.truncated:
            warnings.append("LIST_TRUNCATED")
        if data.scope_limited:
            warnings.append("WAREHOUSE_SCOPE_LIMITED")
    elif isinstance(data, CustomerCountData):
        view["steps"] = ["read_customer_count"]
    elif isinstance(data, CustomersBilledData):
        view["date_range"] = data.date_range
        view["steps"] = ["resolve_period", "read_customers_billed"]

    figures: list[MetricValue] = []
    gaps: list[MetricGap] = []
    comparison = isinstance(data, SalesComparisonData)
    for metric in _wanted_metrics(capability, intent, data):
        outcome = measure(metric, data)
        if isinstance(outcome, MetricValue):
            figures.append(outcome)
        elif comparison or (intent is not None and intent.requested_metric is metric):
            # Only the asked-for figure's absence is worth a word, except in a
            # comparison, where every missing figure explains why it is not reliable.
            gaps.append(outcome)
    view["figures"], view["gaps"] = figures, gaps
    return view


def _view(result: Mapping[str, Any], intent: BusinessQueryIntent | None) -> ResultView:
    capability = str(result.get("capability", ""))
    status = str(result.get("status", ""))
    reason = result.get("reason")
    entity = result.get("entity")
    source = result.get("source")
    if status == "SUCCESS" and isinstance(result.get("data"), Mapping):
        adapter = _DATA_ADAPTERS.get(capability)
        try:
            if adapter is None:
                raise ValueError("unknown capability")
            data = adapter.validate_python(result["data"])
            parsed_source = DataSource.model_validate(source)
        except (ValidationError, ValueError):
            return ResultView(
                capability=capability,
                status="DEPENDENCY_ERROR",
                reason=Reason.MALFORMED_RESPONSE,
                message=_status_message("DEPENDENCY_ERROR", None, None, intent),
            )
        fields = _success_view(capability, data, intent)
        if parsed_source.test_data:
            fields["warnings"].insert(0, "TEST_DATA")
        view = ResultView(
            capability=capability,
            status=status,
            source=parsed_source,
            message="",
            **fields,
        )
        return view.model_copy(update={"message": _success_sentence(view)})
    if status == "AMBIGUOUS":
        options = [
            str(c.get("display_name"))
            for c in result.get("candidates", [])
            if isinstance(c, Mapping) and c.get("display_name")
        ]
        message = _ambiguous(entity, options, intent)
        return ResultView(
            capability=capability,
            status=status,
            reason=reason,
            entity=entity,
            message=message,
            clarification=Clarification(entity=entity, question=message, options=options),
        )
    warnings = ["RETRYABLE"] if result.get("retryable") else []
    return ResultView(
        capability=capability,
        status=status,
        reason=reason,
        entity=entity,
        warnings=warnings,
        message=_status_message(status, reason, entity, intent),
    )


def compose(
    results: Iterable[Mapping[str, Any]], intent: BusinessQueryIntent | None = None
) -> BusinessResponseContext:
    views = [_view(r, intent) for r in results]
    return BusinessResponseContext(
        results=views,
        requested_metric=intent.requested_metric if intent else None,
        read_only_notice=(
            READ_ONLY_NOTICE if intent is not None and intent.write_requested else None
        ),
        test_data=any(v.source is not None and v.source.test_data for v in views),
    )


# --- deterministic rendering ---------------------------------------------------------------


_WARNING_TEXT = {
    "LIST_TRUNCATED": "The list is incomplete; totals are not given for a partial list.",
    "WAREHOUSE_SCOPE_LIMITED": "Only the warehouses you can see are included.",
    "OVERDUE_NOT_DETERMINED": "Which of these invoices are overdue cannot be determined yet.",
    "DAYS_WITHOUT_SALES_RECORD": "Days without a sales record are not included.",
    "COMPARISON_BASIS": "Compared with the same dates one year earlier. Sales are the totals "
    "recorded in the business data (not profit), internal company data only.",
    "BASELINE_NO_RECORDS": "There is no sales record for the same period last year (no "
    "history, or no sales), so no change is calculated.",
    "CURRENT_NO_RECORDS": "There is no sales record for the current period, so no change is "
    "calculated.",
    "ZERO_BASELINE": "Sales in the same period last year were zero, so no percentage is given.",
    "MIXED_CURRENCY": "The amounts are in different currencies, so no change is calculated.",
    "DAY_COUNTS_DIFFER": "The two periods have a different number of days (29 February).",
}

_GAP_TEXT = {
    "RULE_NOT_VERIFIED": "is not available: the business rule behind it is not verified yet",
    "INCOMPLETE_LIST": "cannot be given from an incomplete list",
    "MIXED_CURRENCY": "cannot be given across different currencies",
    "NOT_PROVIDED": "is not provided by the business data",
    "NOT_APPLICABLE": "needs a specific product or record to be named",
    "WRONG_CAPABILITY": "is not part of this answer",
    "NO_RECORDS": "is not available: a compared period has no sales record",
    "ZERO_BASELINE": "is not available: the comparison total is zero",
}


def _range_text(span: BusinessDateRange | None) -> str:
    if span is None:
        return ""
    if span.days == 1:
        return f" on {span.start.isoformat()}"
    return f" from {span.start.isoformat()} to {span.last_day.isoformat()}"


def _record_text(record: RecordView) -> str:
    if record.kind == "invoice":
        parts = [f"{record.name}: {format_amount(record.amount or '', record.currency)}"]
        if "customer" in record.details:
            parts.append(f"({record.details['customer']})")
        if record.due_date is not None:
            parts.append(f"due {record.due_date.isoformat()}")
        if record.status == "overdue":
            parts.append("(overdue)")
        return " ".join(parts)
    if record.kind == "sales_day":
        return f"{record.name}: {format_amount(record.amount or '', record.currency)}"
    if record.kind == "sales_period":
        label = "Same period last year" if record.name == "same_period_last_year" else "Current"
        span = record.ref.replace("..", " to ")
        if record.amount is None:
            return f"{label} ({span}): no sales record"
        documents = record.details.get("document_count", "")
        return f"{label} ({span}): {format_amount(record.amount, record.currency)}, " + (
            f"{documents} document(s)"
        )
    if record.kind == "low_stock":
        return (
            f"{record.name} in {record.details.get('warehouse', '')}: "
            f"{format_quantity(record.quantity or '', record.unit)} "
            f"(low-stock level {record.details.get('low_stock_level', '')})"
        )
    return f"{record.name}: {format_quantity(record.quantity or '', record.unit)}"


def _comparison_sentence(view: ResultView) -> str:
    """Phase 16: the comparison in one deterministic sentence (existing figures only)."""
    figures = {f.metric: format_figure(f) for f in view.figures}
    span = _range_text(view.date_range).strip()
    current = figures.get(BusinessMetric.TOTAL_SALES, "no sales record")
    baseline = figures.get(BusinessMetric.BASELINE_TOTAL_SALES, "no sales record")
    parts = [f"Total sales {span}: {current}", f"same period last year: {baseline}"]
    change = figures.get(BusinessMetric.SALES_CHANGE_AMOUNT)
    if change is not None:
        percent = figures.get(BusinessMetric.SALES_CHANGE_PERCENT)
        parts.append(f"change: {change}" + (f" ({percent})" if percent else ""))
    return "; ".join(parts) + "."


def _success_sentence(view: ResultView) -> str:
    if any(r.kind == "sales_period" for r in view.records):
        return _comparison_sentence(view)
    subject = f" for {view.subject}" if view.subject else ""
    figures = {f.metric: f for f in view.figures}
    flag = figures.get(BusinessMetric.IS_LOW_STOCK)
    if flag is not None:
        if flag.value == "yes":
            return f"{view.subject} is below its low-stock level."
        return f"{view.subject} is not below its low-stock level in the warehouses you can see."
    span = _range_text(view.date_range)
    shown = [
        f"{f.label}{subject}{span}: {format_figure(f)}" if i == 0 else
        f"{f.label}: {format_figure(f)}"
        for i, f in enumerate(view.figures)
    ]  # fmt: skip
    return ". ".join(shown) + "." if shown else f"The data{subject} was retrieved."


def _render_view(view: ResultView) -> str:
    if view.status != "SUCCESS":
        return view.message
    parts = [view.message]
    for gap in view.gaps:
        parts.append(f"{gap.metric.value.replace('_', ' ').capitalize()} {_GAP_TEXT[gap.reason]}.")
    if view.records:
        listed = view.records[:MAX_RENDERED_RECORDS]
        heading = "Overdue invoices" if view.records_filter == "overdue" else "Details"
        parts.append(f"{heading}: " + "; ".join(_record_text(r) for r in listed) + ".")
    elif view.records_filter == "overdue":
        parts.append("None of the open invoices is overdue.")
    parts += [_WARNING_TEXT[w] for w in view.warnings if w in _WARNING_TEXT]
    if "TEST_DATA" in view.warnings:
        parts.append("(TEST data, not real company data.)")
    return " ".join(parts)


def render_context(context: BusinessResponseContext) -> str:
    """Plain text built from the context alone (no LLM)."""
    parts = [_render_view(v) for v in context.results]
    if context.read_only_notice:
        parts.append(context.read_only_notice)
    return " ".join(p for p in parts if p) or "The business data could not be retrieved."


def first_clarification(context: BusinessResponseContext) -> Clarification | None:
    return next((r.clarification for r in context.results if r.clarification), None)
