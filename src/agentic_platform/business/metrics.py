"""Deterministic business metrics (Phase 11, ADR-060).

A metric is one named figure that a capability's typed result already contains. The
service computes every figure. This module only *selects* one from a SUCCESS result and
says why when it cannot (`MetricGap`). It performs no arithmetic of its own, and the LLM
performs none at all.

| Metric                  | Capability             | Taken from                         |
|-------------------------|------------------------|------------------------------------|
| outstanding_amount      | customer_outstanding   | `outstanding`                      |
| open_invoice_total      | customer_open_invoices | `total_balance`                    |
| open_invoice_count      | customer_open_invoices | `invoice_count`                    |
| overdue_amount          | customer_open_invoices | `overdue_balance` (rule-gated)     |
| overdue_invoice_count   | customer_open_invoices | `overdue_count` (rule-gated)       |
| invoice_total           | customer_open_invoices | `invoice_total` (gross)            |
| paid_amount             | customer_open_invoices | `paid_total`                       |
| total_sales             | daily_sales            | `total`                            |
| sales_document_count    | daily_sales            | `document_count`                   |
| stock_quantity          | product_stock          | `total_quantity` (visible scope)   |
| warehouse_product_count | warehouse_stock        | `product_count`                    |
| low_stock_item_count    | low_stock_products     | `item_count`                       |
| is_low_stock            | low_stock_products     | items of the one named product     |
| baseline_total_sales    | daily_sales            | comparison `baseline.total` (P16)  |
| baseline_document_count | daily_sales            | comparison `baseline` documents    |
| sales_change_amount     | daily_sales            | comparison `change_amount`         |
| sales_change_percent    | daily_sales            | comparison `change_percent`        |
| active_customer_count   | customer_count         | `active_count` (2026-11-25)        |
| inactive_customer_count | customer_count         | `inactive_count`                   |
| billed_customer_count   | customer_count         | billed `customer_count` (period)   |
| billed_invoice_count    | customer_count         | billed `invoice_count`             |
| counter_sale_count      | customer_count         | billed `counter_sale_count`        |

On a Phase 16 comparison (`SalesComparisonData`), `total_sales` and
`sales_document_count` are the current period's figures.

No capability provides a payment history, a customer's purchase history, product-level
sales or a count of login users. Questions about those are unsupported until the ERP
mapping adds a capability (Q1).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentic_platform.business.models import (
    CustomerCountData,
    CustomerOutstandingData,
    CustomersBilledData,
    DailySalesData,
    LowStockData,
    Money,
    OpenInvoicesData,
    ProductStockData,
    SalesComparisonData,
    SalesPeriodData,
    SalesPeriodSummary,
    WarehouseStockData,
)
from agentic_platform.business.rules import BusinessRule


class BusinessMetric(StrEnum):
    OUTSTANDING_AMOUNT = "outstanding_amount"
    OPEN_INVOICE_TOTAL = "open_invoice_total"
    OPEN_INVOICE_COUNT = "open_invoice_count"
    OVERDUE_AMOUNT = "overdue_amount"
    OVERDUE_INVOICE_COUNT = "overdue_invoice_count"
    INVOICE_TOTAL = "invoice_total"
    PAID_AMOUNT = "paid_amount"
    TOTAL_SALES = "total_sales"
    SALES_DOCUMENT_COUNT = "sales_document_count"
    STOCK_QUANTITY = "stock_quantity"
    WAREHOUSE_PRODUCT_COUNT = "warehouse_product_count"
    LOW_STOCK_ITEM_COUNT = "low_stock_item_count"
    IS_LOW_STOCK = "is_low_stock"
    # Phase 16: sales against the same period last year.
    BASELINE_TOTAL_SALES = "baseline_total_sales"
    BASELINE_DOCUMENT_COUNT = "baseline_document_count"
    SALES_CHANGE_AMOUNT = "sales_change_amount"
    SALES_CHANGE_PERCENT = "sales_change_percent"
    # 2026-11-25: customer counts.
    ACTIVE_CUSTOMER_COUNT = "active_customer_count"
    INACTIVE_CUSTOMER_COUNT = "inactive_customer_count"
    BILLED_CUSTOMER_COUNT = "billed_customer_count"
    BILLED_INVOICE_COUNT = "billed_invoice_count"
    COUNTER_SALE_COUNT = "counter_sale_count"


M = BusinessMetric

METRIC_CAPABILITY: dict[BusinessMetric, str] = {
    M.OUTSTANDING_AMOUNT: "customer_outstanding",
    M.OPEN_INVOICE_TOTAL: "customer_open_invoices",
    M.OPEN_INVOICE_COUNT: "customer_open_invoices",
    M.OVERDUE_AMOUNT: "customer_open_invoices",
    M.OVERDUE_INVOICE_COUNT: "customer_open_invoices",
    M.INVOICE_TOTAL: "customer_open_invoices",
    M.PAID_AMOUNT: "customer_open_invoices",
    M.TOTAL_SALES: "daily_sales",
    M.SALES_DOCUMENT_COUNT: "daily_sales",
    M.STOCK_QUANTITY: "product_stock",
    M.WAREHOUSE_PRODUCT_COUNT: "warehouse_stock",
    M.LOW_STOCK_ITEM_COUNT: "low_stock_products",
    M.IS_LOW_STOCK: "low_stock_products",
    M.BASELINE_TOTAL_SALES: "daily_sales",
    M.BASELINE_DOCUMENT_COUNT: "daily_sales",
    M.SALES_CHANGE_AMOUNT: "daily_sales",
    M.SALES_CHANGE_PERCENT: "daily_sales",
    M.ACTIVE_CUSTOMER_COUNT: "customer_count",
    M.INACTIVE_CUSTOMER_COUNT: "customer_count",
    M.BILLED_CUSTOMER_COUNT: "customer_count",
    M.BILLED_INVOICE_COUNT: "customer_count",
    M.COUNTER_SALE_COUNT: "customer_count",
}

# Figures shown for a capability when the question names no metric.
DEFAULT_METRICS: dict[str, tuple[BusinessMetric, ...]] = {
    "customer_outstanding": (M.OUTSTANDING_AMOUNT,),
    "customer_open_invoices": (M.OPEN_INVOICE_TOTAL, M.OPEN_INVOICE_COUNT),
    "daily_sales": (M.TOTAL_SALES, M.SALES_DOCUMENT_COUNT),
    "product_stock": (M.STOCK_QUANTITY,),
    "warehouse_stock": (M.WAREHOUSE_PRODUCT_COUNT,),
    "low_stock_products": (M.LOW_STOCK_ITEM_COUNT,),
    "customer_count": (M.ACTIVE_CUSTOMER_COUNT, M.INACTIVE_CUSTOMER_COUNT),
}

# Figures shown for customers billed in a period when the question names no metric.
BILLED_METRICS: tuple[BusinessMetric, ...] = (
    M.BILLED_CUSTOMER_COUNT,
    M.BILLED_INVOICE_COUNT,
    M.COUNTER_SALE_COUNT,
)

# Phase 16: figures shown for a sales comparison when the question names no metric.
COMPARISON_METRICS: tuple[BusinessMetric, ...] = (
    M.TOTAL_SALES,
    M.BASELINE_TOTAL_SALES,
    M.SALES_CHANGE_AMOUNT,
    M.SALES_CHANGE_PERCENT,
    M.SALES_DOCUMENT_COUNT,
    M.BASELINE_DOCUMENT_COUNT,
)

LABELS: dict[BusinessMetric, str] = {
    M.OUTSTANDING_AMOUNT: "Outstanding",
    M.OPEN_INVOICE_TOTAL: "Total pending (open invoices)",
    M.OPEN_INVOICE_COUNT: "Open invoices",
    M.OVERDUE_AMOUNT: "Overdue amount",
    M.OVERDUE_INVOICE_COUNT: "Overdue invoices",
    M.INVOICE_TOTAL: "Invoice total (gross)",
    M.PAID_AMOUNT: "Already paid",
    M.TOTAL_SALES: "Total sales",
    M.SALES_DOCUMENT_COUNT: "Sales documents",
    M.STOCK_QUANTITY: "Stock",
    M.WAREHOUSE_PRODUCT_COUNT: "Products in stock list",
    M.LOW_STOCK_ITEM_COUNT: "Low-stock items",
    M.IS_LOW_STOCK: "Low stock",
    M.BASELINE_TOTAL_SALES: "Total sales, same period last year",
    M.BASELINE_DOCUMENT_COUNT: "Sales documents, same period last year",
    M.SALES_CHANGE_AMOUNT: "Change in sales",
    M.SALES_CHANGE_PERCENT: "Change in sales (%)",
    M.ACTIVE_CUSTOMER_COUNT: "Active customers",
    M.INACTIVE_CUSTOMER_COUNT: "Inactive customers",
    M.BILLED_CUSTOMER_COUNT: "Customers billed",
    M.BILLED_INVOICE_COUNT: "Invoices to these customers",
    M.COUNTER_SALE_COUNT: "Counter-sale bills (no customer name)",
}


class GapReason:
    RULE_NOT_VERIFIED = "RULE_NOT_VERIFIED"  # a rule the figure needs does not hold
    INCOMPLETE_LIST = "INCOMPLETE_LIST"  # the list was truncated: no total from a part
    MIXED_CURRENCY = "MIXED_CURRENCY"  # no total across currencies
    NOT_PROVIDED = "NOT_PROVIDED"  # the ERP mapping does not provide the input
    NOT_APPLICABLE = "NOT_APPLICABLE"  # the question did not name what it needs
    WRONG_CAPABILITY = "WRONG_CAPABILITY"  # the metric belongs to another capability
    # Phase 16 (comparisons):
    NO_RECORDS = "NO_RECORDS"  # a compared period has no sales record at all
    ZERO_BASELINE = "ZERO_BASELINE"  # no percentage against a zero total


FigureKind = Literal["money", "quantity", "count", "flag", "percent"]


class MetricValue(BaseModel):
    """One authoritative figure, as text exactly as the service produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: BusinessMetric
    label: str
    kind: FigureKind
    value: str
    currency: str | None = None
    unit: str | None = None


class MetricGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: BusinessMetric
    reason: str
    rules: list[str] = []


Outcome = MetricValue | MetricGap


def _money(metric: BusinessMetric, money: Money) -> MetricValue:
    return MetricValue(
        metric=metric,
        label=LABELS[metric],
        kind="money",
        value=str(money.amount),
        currency=money.currency,
    )


def _count(metric: BusinessMetric, value: int) -> MetricValue:
    return MetricValue(metric=metric, label=LABELS[metric], kind="count", value=str(value))


def _gap(metric: BusinessMetric, reason: str, rules: list[str] | None = None) -> MetricGap:
    return MetricGap(metric=metric, reason=reason, rules=rules or [])


_OVERDUE_RULES = {BusinessRule.BUSINESS_TIMEZONE.value, BusinessRule.OVERDUE_BY_DUE_DATE.value}


def _invoices(metric: BusinessMetric, d: OpenInvoicesData) -> Outcome:
    def money_or_gap(money: Money | None, rule_gated: bool = False) -> Outcome:
        if money is not None:
            return _money(metric, money)
        blocking = [r for r in d.rules_not_verified if r in _OVERDUE_RULES] if rule_gated else []
        if blocking:
            return _gap(metric, GapReason.RULE_NOT_VERIFIED, blocking)
        if d.truncated:
            return _gap(metric, GapReason.INCOMPLETE_LIST)
        if len({line.balance.currency for line in d.invoices}) > 1:
            return _gap(metric, GapReason.MIXED_CURRENCY)
        return _gap(metric, GapReason.NOT_PROVIDED)

    if metric is M.OPEN_INVOICE_TOTAL:
        return money_or_gap(d.total_balance)
    if metric is M.OPEN_INVOICE_COUNT:
        return _count(metric, d.invoice_count)
    if metric is M.OVERDUE_AMOUNT:
        return money_or_gap(d.overdue_balance, rule_gated=True)
    if metric is M.OVERDUE_INVOICE_COUNT:
        if d.overdue_count is not None:
            return _count(metric, d.overdue_count)
        blocking = [r for r in d.rules_not_verified if r in _OVERDUE_RULES]
        if blocking:
            return _gap(metric, GapReason.RULE_NOT_VERIFIED, blocking)
        return _gap(metric, GapReason.INCOMPLETE_LIST if d.truncated else GapReason.NOT_PROVIDED)
    if metric is M.INVOICE_TOTAL:
        return money_or_gap(d.invoice_total)
    if metric is M.PAID_AMOUNT:
        return money_or_gap(d.paid_total)
    return _gap(metric, GapReason.WRONG_CAPABILITY)


def _comparison(metric: BusinessMetric, d: SalesComparisonData) -> Outcome:
    """Phase 16: select a comparison figure; a None figure becomes a gap with its reason
    (the service already decided which figures can be given)."""

    def side_gap(side: SalesPeriodSummary) -> MetricGap:
        if side.days_with_sales == 0:
            return _gap(metric, GapReason.NO_RECORDS)
        return _gap(metric, GapReason.MIXED_CURRENCY)

    def change_gap() -> MetricGap:
        if "CURRENT_NO_RECORDS" in d.gaps or "BASELINE_NO_RECORDS" in d.gaps:
            return _gap(metric, GapReason.NO_RECORDS)
        if "MIXED_CURRENCY" in d.gaps:
            return _gap(metric, GapReason.MIXED_CURRENCY)
        return _gap(metric, GapReason.ZERO_BASELINE)

    if metric in (M.TOTAL_SALES, M.BASELINE_TOTAL_SALES):
        side = d.current if metric is M.TOTAL_SALES else d.baseline
        return _money(metric, side.total) if side.total is not None else side_gap(side)
    if metric is M.SALES_DOCUMENT_COUNT:
        return _count(metric, d.current.document_count)
    if metric is M.BASELINE_DOCUMENT_COUNT:
        return _count(metric, d.baseline.document_count)
    if metric is M.SALES_CHANGE_AMOUNT:
        if d.change_amount is None:
            return change_gap()
        return _money(metric, d.change_amount)
    if metric is M.SALES_CHANGE_PERCENT:
        if d.change_percent is None:
            return change_gap()
        return MetricValue(
            metric=metric, label=LABELS[metric], kind="percent", value=str(d.change_percent)
        )
    return _gap(metric, GapReason.WRONG_CAPABILITY)


def _sales(metric: BusinessMetric, d: DailySalesData | SalesPeriodData) -> Outcome:
    if metric is M.TOTAL_SALES:
        if d.total is None:
            return _gap(metric, GapReason.MIXED_CURRENCY)
        return _money(metric, d.total)
    if metric is M.SALES_DOCUMENT_COUNT:
        return _count(metric, d.document_count)
    return _gap(metric, GapReason.WRONG_CAPABILITY)


def _low_stock(metric: BusinessMetric, d: LowStockData) -> Outcome:
    if metric is M.LOW_STOCK_ITEM_COUNT:
        return _count(metric, d.item_count)
    if metric is M.IS_LOW_STOCK:
        if d.product_filter is None:
            return _gap(metric, GapReason.NOT_APPLICABLE)
        if d.items:
            return MetricValue(metric=metric, label=LABELS[metric], kind="flag", value="yes")
        if d.truncated:  # "not low" cannot be concluded from a partial list
            return _gap(metric, GapReason.INCOMPLETE_LIST)
        return MetricValue(metric=metric, label=LABELS[metric], kind="flag", value="no")
    return _gap(metric, GapReason.WRONG_CAPABILITY)


def measure(metric: BusinessMetric, data: BaseModel) -> Outcome:
    """Select `metric` from one capability's SUCCESS data (typed). Never computes."""
    if isinstance(data, CustomerOutstandingData):
        if metric is M.OUTSTANDING_AMOUNT:
            return _money(metric, data.outstanding)
        return _gap(metric, GapReason.WRONG_CAPABILITY)
    if isinstance(data, OpenInvoicesData):
        return _invoices(metric, data)
    if isinstance(data, DailySalesData | SalesPeriodData):
        return _sales(metric, data)
    if isinstance(data, SalesComparisonData):
        return _comparison(metric, data)
    if isinstance(data, ProductStockData):
        if metric is M.STOCK_QUANTITY:
            return MetricValue(
                metric=metric,
                label=LABELS[metric],
                kind="quantity",
                value=str(data.total_quantity),
                unit=data.unit,
            )
        return _gap(metric, GapReason.WRONG_CAPABILITY)
    if isinstance(data, WarehouseStockData):
        if metric is M.WAREHOUSE_PRODUCT_COUNT:
            if data.truncated:
                return _gap(metric, GapReason.INCOMPLETE_LIST)
            return _count(metric, data.product_count)
        return _gap(metric, GapReason.WRONG_CAPABILITY)
    if isinstance(data, LowStockData):
        return _low_stock(metric, data)
    if isinstance(data, CustomerCountData):
        if metric is M.ACTIVE_CUSTOMER_COUNT:
            return _count(metric, data.active_count)
        if metric is M.INACTIVE_CUSTOMER_COUNT:
            return _count(metric, data.inactive_count)
        if metric in BILLED_METRICS:
            return _gap(metric, GapReason.NOT_APPLICABLE)  # needs a period
        return _gap(metric, GapReason.WRONG_CAPABILITY)
    if isinstance(data, CustomersBilledData):
        billed = {
            M.BILLED_CUSTOMER_COUNT: data.customer_count,
            M.BILLED_INVOICE_COUNT: data.invoice_count,
            M.COUNTER_SALE_COUNT: data.counter_sale_count,
        }
        if metric in billed:
            return _count(metric, billed[metric])
        if metric in (M.ACTIVE_CUSTOMER_COUNT, M.INACTIVE_CUSTOMER_COUNT):
            return _gap(metric, GapReason.NOT_APPLICABLE)  # asked with a period
        return _gap(metric, GapReason.WRONG_CAPABILITY)
    return _gap(metric, GapReason.WRONG_CAPABILITY)
