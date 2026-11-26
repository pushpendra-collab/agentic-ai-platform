"""Typed contracts of the business read layer (Phase 10, ADR-059).

Two families, both strict (`extra="forbid"`, frozen):

* **Records** (`*Record`): what an ERP adapter returns through `ErpReadPort`. Every
  record states the tenant it belongs to, so the service can verify that an adapter
  never hands one tenant another tenant's data. The service re-validates every record
  (a malformed record becomes DEPENDENCY_ERROR, never a figure).
* **Data** (`*Data`): what the service returns inside a SUCCESS `BusinessResult`, and
  what tools show the LLM. No tenant ids, no ERP internals.

These shapes are the platform's contract, NOT a description of the ERP schema: no ERP
table or column is known (Q1). A live adapter maps the real schema onto them once the
ERP owner has confirmed each mapping and business rule (docs/SCHEMA_INTEGRATION.md).
References (`ref`) are opaque identifiers chosen by the adapter.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_platform.business.dates import BusinessDateRange

REF_PATTERN = r"^[A-Za-z0-9_.:\-]{1,64}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityRef(_Strict):
    """A customer, product, warehouse or invoice as the user can recognise it."""

    ref: str = Field(pattern=REF_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)


class Money(_Strict):
    amount: Decimal
    currency: str = Field(pattern=CURRENCY_PATTERN)


# --- records (adapter → service) -------------------------------------------------------


class _Record(_Strict):
    tenant_id: str = Field(min_length=1, max_length=128)


class CustomerRecord(_Record):
    ref: str = Field(pattern=REF_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)


class OutstandingRecord(_Record):
    customer_ref: str = Field(pattern=REF_PATTERN)
    amount: Decimal
    currency: str = Field(pattern=CURRENCY_PATTERN)
    as_of: datetime


class InvoiceRecord(_Record):
    ref: str = Field(pattern=REF_PATTERN)
    number: str = Field(min_length=1, max_length=64)
    customer_ref: str = Field(pattern=REF_PATTERN)
    balance: Decimal
    currency: str = Field(pattern=CURRENCY_PATTERN)
    issued_on: date | None = None
    due_on: date | None = None
    # Phase 11, optional: the adapter fills them when its mapping provides them.
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    total: Decimal | None = None  # gross invoice amount
    paid: Decimal | None = None  # amount already settled against it


class SalesRecord(_Record):
    business_date: date
    total: Decimal
    currency: str = Field(pattern=CURRENCY_PATTERN)
    document_count: int = Field(ge=0)


class ProductRecord(_Record):
    ref: str = Field(pattern=REF_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=32)


class WarehouseRecord(_Record):
    ref: str = Field(pattern=REF_PATTERN)
    display_name: str = Field(min_length=1, max_length=200)


class StockRecord(_Record):
    product_ref: str = Field(pattern=REF_PATTERN)
    product_name: str = Field(min_length=1, max_length=200)
    warehouse_ref: str = Field(pattern=REF_PATTERN)
    warehouse_name: str = Field(min_length=1, max_length=200)
    quantity: Decimal
    unit: str | None = Field(default=None, max_length=32)


class CustomerCountRecord(_Record):
    """How many customers the tenant has (cash-sale placeholders excluded)."""

    active_count: int = Field(ge=0)
    inactive_count: int = Field(ge=0)


class CustomersBilledRecord(_Record):
    """Billing activity of `start` <= day < `end`: different named customers with a
    posted invoice, those invoices, and counter (cash) sale bills, which name nobody."""

    start: date
    end: date
    customer_count: int = Field(ge=0)
    invoice_count: int = Field(ge=0)
    counter_sale_count: int = Field(ge=0)


class LowStockRecord(StockRecord):
    # The level below which the ERP considers the item low. Where it comes from is an ERP
    # rule the owner must confirm; the platform never invents a threshold.
    threshold: Decimal


# --- data (service → tool → LLM) -------------------------------------------------------


class CustomerOutstandingData(_Strict):
    customer: EntityRef
    outstanding: Money
    as_of: datetime


class InvoiceLine(_Strict):
    invoice: EntityRef
    balance: Money
    issued_on: date | None = None
    due_on: date | None = None
    # Phase 11 (all optional; None = not provided or rule not verified).
    customer: EntityRef | None = None
    total: Money | None = None
    paid: Money | None = None
    overdue: bool | None = None


class OpenInvoicesData(_Strict):
    # None: open invoices of every customer of the tenant (Phase 11).
    customer: EntityRef | None
    invoices: list[InvoiceLine]
    invoice_count: int
    # Sum of the listed balances, computed by the service (never by the LLM). Absent
    # when the list was truncated: a partial sum is not a total.
    total_balance: Money | None
    truncated: bool
    # Phase 11: derived figures, computed by the service only when every input is
    # present and the rules they need hold (rules.py); otherwise None.
    invoice_number: str | None = None  # the number the list was filtered to
    as_of_date: date | None = None  # business date used for "overdue"
    overdue_count: int | None = None
    overdue_balance: Money | None = None
    invoice_total: Money | None = None
    paid_total: Money | None = None
    # BusinessRule values that blocked a derived figure.
    rules_not_verified: list[str] = Field(default_factory=list)


class DailySalesData(_Strict):
    business_date: date
    # The configured business time zone that resolved "today"; None for an explicit date.
    timezone: str | None
    total: Money
    document_count: int
    # Phase 11: the resolved day as a range (None for Phase 10-style explicit dates).
    date_range: BusinessDateRange | None = None


class SalesDayLine(_Strict):
    business_date: date
    total: Money
    document_count: int


class SalesPeriodData(_Strict):
    """Sales of a multi-day period (Phase 11): the sum of the days the ERP reported."""

    date_range: BusinessDateRange
    # None when the days carry different currencies (no total across currencies).
    total: Money | None
    document_count: int
    days: list[SalesDayLine]
    # Days of the range with a sales record; the others had none.
    days_with_sales: int


class SalesPeriodSummary(_Strict):
    """One side of a sales comparison (Phase 16): totals only, no day list."""

    date_range: BusinessDateRange
    # None when no day of the range has a sales record, or the days carry different
    # currencies (then `currencies` says so). Never zero for "no records".
    total: Money | None
    document_count: int
    days_with_sales: int
    currencies: list[str] = Field(default_factory=list)


ComparisonGap = Literal[
    "CURRENT_NO_RECORDS",  # the current period has no sales record at all
    "BASELINE_NO_RECORDS",  # the comparison period has none (no history, or no sales)
    "ZERO_BASELINE",  # the comparison total is zero: no percentage
    "MIXED_CURRENCY",  # the two periods (or the days of one) differ in currency
]


class SalesComparisonData(_Strict):
    """Sales of a period against the same period last year (Phase 16, ADR-065).

    Every figure is computed by the service from the ERP's day records with Decimal
    arithmetic. "Sales" is the ERP's sales total as the adapter's mapping defines it
    (taxes, discounts, returns and cancellations are the mapping's rules): it is never
    profit, and it is internal company data only, never market performance. A period
    without any record has no total (`None`), never zero: missing history and a period
    without sales cannot be told apart, so no change is computed against it.
    """

    basis: Literal["same_period_last_year"] = "same_period_last_year"
    current: SalesPeriodSummary
    baseline: SalesPeriodSummary
    # current.total - baseline.total, when both totals exist in one currency.
    change_amount: Money | None
    # change_amount / baseline.total * 100, rounded half-up to one decimal place; None
    # when the change cannot be computed or the baseline is zero.
    change_percent: Decimal | None
    direction: Literal["increase", "decrease", "unchanged"] | None
    # current.document_count - baseline.document_count, when both periods have records.
    document_count_change: int | None
    # Why a figure above is None (empty when every figure was computed).
    gaps: list[ComparisonGap] = Field(default_factory=list)
    # True when the two ranges hold a different number of days (29 February).
    day_counts_differ: bool = False


class StockLine(_Strict):
    warehouse: EntityRef
    quantity: Decimal


class ProductStockData(_Strict):
    product: EntityRef
    unit: str | None
    lines: list[StockLine]
    # Sum over the warehouses the caller may see (and only those).
    total_quantity: Decimal
    warehouse_count: int
    # True when the caller's warehouse scope is limited (the total is not company-wide).
    scope_limited: bool
    # Phase 11: the one warehouse the lines were narrowed to, if asked.
    warehouse_filter: EntityRef | None = None


class ProductLine(_Strict):
    product: EntityRef
    quantity: Decimal
    unit: str | None


class WarehouseStockData(_Strict):
    warehouse: EntityRef
    lines: list[ProductLine]
    product_count: int
    truncated: bool


class CustomerCountData(_Strict):
    """How many customers the company has (master data; cash-sale placeholders, which
    stand for walk-in buyers, are not customers)."""

    active_count: int
    inactive_count: int


class CustomersBilledData(_Strict):
    """How many different customers were billed in a period ("aaj kitne customer
    aaye?"): customers with a posted invoice in the range. Counter (cash) sales name no
    customer, so they are counted as bills, never as customers."""

    date_range: BusinessDateRange
    customer_count: int
    invoice_count: int
    counter_sale_count: int


class LowStockLine(_Strict):
    product: EntityRef
    warehouse: EntityRef
    quantity: Decimal
    threshold: Decimal
    unit: str | None


class LowStockData(_Strict):
    items: list[LowStockLine]
    item_count: int
    truncated: bool
    scope_limited: bool
    # Phase 11: the one product the list was narrowed to ("is X low?").
    product_filter: EntityRef | None = None
