"""Business read tools (domain `business`, Phase 10, ADR-059).

    LLM plan ─► ToolRegistry (domain, permission, input schema, timeout, audit)
             ─► tool handler ─► BusinessReadService ─► ErpReadPort ─► adapter ─► ERP

Each tool is one typed read. Inputs are strict (`extra="forbid"`): a name to search or
an opaque reference from an earlier result, a date, a limit. There is no field for a
tenant, user, warehouse scope, permission, table, column or SQL, so the model cannot
supply one; identity comes from the authenticated principal bound to the call.

Every tool is `ToolKind.READ`, and `register_business_tools` refuses anything else. The
output is the service's `BusinessResult`: a deterministic status (SUCCESS, NOT_FOUND,
AMBIGUOUS, UNAVAILABLE, UNAUTHORIZED, VALIDATION_ERROR, DEPENDENCY_ERROR) plus data only
on SUCCESS. A non-SUCCESS result is still `ToolResult.status="ok"` (the tool worked;
the business answer is "not found", "ambiguous", ...).

Phase 11 (ADR-060) adds optional, typed narrowing to the same six tools: a relative
`period` token for sales (the service resolves it from the clock; the model never
computes a date), open invoices of all customers or of one invoice number, product
stock in one named warehouse (the caller's scope still decides visibility), and "is
this product low?". `tool_call_for` compiles a typed `BusinessQueryIntent` into one call.

Phase 16 (ADR-065) extends `business.daily_sales` instead of adding a tool: `this_year` /
`last_year` periods, and `compare_with="same_period_last_year"` for a deterministic
comparison of a relative period with the same dates one year earlier. Same capability,
permission and ERP read (`sales_by_day`), so the live adapter's gate is unchanged.

2026-11-25 (owner approval in the project chat): `business.customer_count`, how many
customers the company has, or with a period/day how many different customers were billed
in it ("aaj kitne customer aaye?"). Counts only. Login users are not business data and
no tool counts them.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from agentic_platform.business.capabilities import (
    CUSTOMER_COUNT,
    CUSTOMER_OPEN_INVOICES,
    CUSTOMER_OUTSTANDING,
    DAILY_SALES,
    LOW_STOCK,
    PRODUCT_STOCK,
    WAREHOUSE_STOCK,
)
from agentic_platform.business.dates import ComparisonBasis, DatePeriod, normalise_period
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.business.models import (
    REF_PATTERN,
    CustomerCountData,
    CustomerOutstandingData,
    CustomersBilledData,
    DailySalesData,
    LowStockData,
    OpenInvoicesData,
    ProductStockData,
    SalesComparisonData,
    SalesPeriodData,
    WarehouseStockData,
)
from agentic_platform.business.results import BusinessResult
from agentic_platform.business.service import BusinessReadService
from agentic_platform.core.config import ErpSettings
from agentic_platform.core.errors import ToolRegistrationError
from agentic_platform.security.context import require_principal
from agentic_platform.tools.base import Tool, ToolInput, ToolKind, ToolOutput, ToolSpec
from agentic_platform.tools.registry import ToolRegistry

BUSINESS_DOMAIN = "business"

T = TypeVar("T", bound=BaseModel)

_REF_HELP = "Exact reference copied from an earlier result's candidates. Never invent one."
INVOICE_NUMBER_PATTERN = r"^[A-Za-z0-9/_.\-]{1,64}$"

SalesData = DailySalesData | SalesPeriodData | SalesComparisonData
CustomerCountResult = CustomerCountData | CustomersBilledData


class BusinessToolOutput(ToolOutput, Generic[T]):
    result: BusinessResult[T]


def _exactly_one(name: str | None, ref: str | None, what: str) -> None:
    if (name is None) == (ref is None):
        raise ValueError(f"give exactly one of {what}_name or {what}_ref")


def _at_most_one(name: str | None, ref: str | None, what: str) -> None:
    if name is not None and ref is not None:
        raise ValueError(f"give at most one of {what}_name or {what}_ref")


class CustomerInput(ToolInput):
    customer_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Customer name as the user wrote it.",
    )
    customer_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)

    @model_validator(mode="after")
    def _one(self) -> CustomerInput:
        _exactly_one(self.customer_name, self.customer_ref, "customer")
        return self


class OpenInvoicesInput(ToolInput):
    customer_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Customer name as the user wrote it. Omit both customer fields for "
        "the open invoices of all customers.",
    )
    customer_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)
    invoice_number: str | None = Field(
        default=None,
        pattern=INVOICE_NUMBER_PATTERN,
        description="One invoice number exactly as the user wrote it.",
    )

    @model_validator(mode="after")
    def _one(self) -> OpenInvoicesInput:
        _at_most_one(self.customer_name, self.customer_ref, "customer")
        return self


class DailySalesInput(ToolInput):
    day: Literal["today"] | date = Field(
        default="today", description="'today' or an ISO date (YYYY-MM-DD) the user named."
    )
    period: DatePeriod | None = Field(
        default=None,
        description="Instead of `day`, a relative period: today, yesterday, this_week, "
        "last_week, this_month, last_month, this_year, last_year. Never compute a date "
        "yourself.",
    )
    compare_with: ComparisonBasis | None = Field(
        default=None,
        description="Phase 16: 'same_period_last_year' compares `period` with the same "
        "dates one year earlier. Needs `period` (without one, the answer asks for it).",
    )

    @field_validator("period", mode="before")
    @classmethod
    def _period(cls, value: object) -> object:
        return normalise_period(value)

    @model_validator(mode="after")
    def _day_or_period(self) -> DailySalesInput:
        if self.period is not None and "day" in self.model_fields_set:
            raise ValueError("give either day or period, not both")
        return self


class ProductInput(ToolInput):
    product_name: str | None = Field(
        default=None, min_length=2, max_length=100, description="Product name as the user wrote it."
    )
    product_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)
    warehouse_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Optional: only this warehouse/godown, as the user wrote it.",
    )
    warehouse_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)

    @model_validator(mode="after")
    def _one(self) -> ProductInput:
        _exactly_one(self.product_name, self.product_ref, "product")
        _at_most_one(self.warehouse_name, self.warehouse_ref, "warehouse")
        return self


class WarehouseInput(ToolInput):
    warehouse_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Warehouse/godown name as the user wrote it.",
    )
    warehouse_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)

    @model_validator(mode="after")
    def _one(self) -> WarehouseInput:
        _exactly_one(self.warehouse_name, self.warehouse_ref, "warehouse")
        return self


class LowStockInput(ToolInput):
    limit: int = Field(default=50, ge=1, le=100, description="Most items to list.")
    product_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Optional: only this product (is it low?), as the user wrote it.",
    )
    product_ref: str | None = Field(default=None, pattern=REF_PATTERN, description=_REF_HELP)

    @model_validator(mode="after")
    def _one(self) -> LowStockInput:
        _at_most_one(self.product_name, self.product_ref, "product")
        return self


class CustomerCountInput(ToolInput):
    period: DatePeriod | None = Field(
        default=None,
        description="Only for 'how many customers came/were billed': a relative period "
        "(today, yesterday, this_week, last_week, this_month, last_month, this_year, "
        "last_year). Omit it for 'how many customers do I have'.",
    )
    day: date | None = Field(
        default=None, description="Instead of `period`, an ISO date (YYYY-MM-DD) the user named."
    )

    @field_validator("period", mode="before")
    @classmethod
    def _period(cls, value: object) -> object:
        return normalise_period(value)

    @model_validator(mode="after")
    def _day_or_period(self) -> CustomerCountInput:
        if self.period is not None and self.day is not None:
            raise ValueError("give either day or period, not both")
        return self


_INPUTS: dict[str, type[ToolInput]] = {
    CUSTOMER_OUTSTANDING.name: CustomerInput,
    CUSTOMER_OPEN_INVOICES.name: OpenInvoicesInput,
    DAILY_SALES.name: DailySalesInput,
    PRODUCT_STOCK.name: ProductInput,
    WAREHOUSE_STOCK.name: WarehouseInput,
    LOW_STOCK.name: LowStockInput,
    CUSTOMER_COUNT.name: CustomerCountInput,
}


def tool_call_for(intent: BusinessQueryIntent) -> tuple[str, str] | None:
    """Compile a typed intent into one `(tool_name, arguments_json)` call, or None when
    the intent names no capability, asks for a write, or lacks what the capability needs
    (for example a customer). Arguments come only from typed intent fields and are
    validated against the tool's strict input model here."""
    if intent.capability is None or intent.write_requested:
        return None
    args: dict[str, Any] = {}
    if intent.capability in (CUSTOMER_OUTSTANDING.name, CUSTOMER_OPEN_INVOICES.name):
        if intent.customer_reference is not None:
            args |= intent.customer_reference.tool_arguments("customer")
        if intent.capability == CUSTOMER_OPEN_INVOICES.name and intent.invoice_number:
            args["invoice_number"] = intent.invoice_number
    elif intent.capability == DAILY_SALES.name:
        if intent.comparison is not None:
            # Without a period the service answers VALIDATION_ERROR / PERIOD_REQUIRED, a
            # deterministic "which period?", rather than no call at all.
            args["compare_with"] = intent.comparison
        if intent.period is not None:
            args["period"] = intent.period.value
        elif intent.explicit_date is not None and intent.comparison is None:
            args["day"] = intent.explicit_date.isoformat()
    elif intent.capability in (PRODUCT_STOCK.name, LOW_STOCK.name):
        if intent.product_reference is not None:
            args |= intent.product_reference.tool_arguments("product")
        if intent.capability == PRODUCT_STOCK.name and intent.warehouse_reference is not None:
            args |= intent.warehouse_reference.tool_arguments("warehouse")
    elif intent.capability == WAREHOUSE_STOCK.name and intent.warehouse_reference is not None:
        args |= intent.warehouse_reference.tool_arguments("warehouse")
    elif intent.capability == CUSTOMER_COUNT.name:
        if intent.period is not None:
            args["period"] = intent.period.value
        elif intent.explicit_date is not None:
            args["day"] = intent.explicit_date.isoformat()
    try:
        _INPUTS[intent.capability].model_validate(args)
    except ValidationError:
        return None
    return f"business.{intent.capability}", json.dumps(args)


def business_tools(service: BusinessReadService, settings: ErpSettings) -> list[Tool]:
    # The service has its own deadline; the registry's timeout is the outer backstop.
    timeout = settings.query_timeout_seconds + 2.0

    async def outstanding(i: CustomerInput) -> BusinessToolOutput[CustomerOutstandingData]:
        result = await service.customer_outstanding(
            require_principal(), customer_name=i.customer_name, customer_ref=i.customer_ref
        )
        return BusinessToolOutput[CustomerOutstandingData](result=result)

    async def open_invoices(i: OpenInvoicesInput) -> BusinessToolOutput[OpenInvoicesData]:
        result = await service.customer_open_invoices(
            require_principal(),
            customer_name=i.customer_name,
            customer_ref=i.customer_ref,
            invoice_number=i.invoice_number,
        )
        return BusinessToolOutput[OpenInvoicesData](result=result)

    async def daily_sales(i: DailySalesInput) -> BusinessToolOutput[SalesData]:
        result = await service.daily_sales(
            require_principal(), day=i.day, period=i.period, compare_with=i.compare_with
        )
        return BusinessToolOutput[SalesData](result=result)

    async def product_stock(i: ProductInput) -> BusinessToolOutput[ProductStockData]:
        result = await service.product_stock(
            require_principal(),
            product_name=i.product_name,
            product_ref=i.product_ref,
            warehouse_name=i.warehouse_name,
            warehouse_ref=i.warehouse_ref,
        )
        return BusinessToolOutput[ProductStockData](result=result)

    async def warehouse_stock(i: WarehouseInput) -> BusinessToolOutput[WarehouseStockData]:
        result = await service.warehouse_stock(
            require_principal(), warehouse_name=i.warehouse_name, warehouse_ref=i.warehouse_ref
        )
        return BusinessToolOutput[WarehouseStockData](result=result)

    async def low_stock(i: LowStockInput) -> BusinessToolOutput[LowStockData]:
        result = await service.low_stock_products(
            require_principal(),
            limit=i.limit,
            product_name=i.product_name,
            product_ref=i.product_ref,
        )
        return BusinessToolOutput[LowStockData](result=result)

    async def customer_count(i: CustomerCountInput) -> BusinessToolOutput[CustomerCountResult]:
        result = await service.customer_count(require_principal(), period=i.period, day=i.day)
        return BusinessToolOutput[CustomerCountResult](result=result)

    def spec(
        name: str,
        description: str,
        permission: str,
        input_model: type[ToolInput],
        output_model: type[ToolOutput],
    ) -> ToolSpec:
        return ToolSpec(
            name=f"business.{name}",
            description=description,
            domain=BUSINESS_DOMAIN,
            kind=ToolKind.READ,
            input_model=input_model,
            output_model=output_model,
            timeout_seconds=timeout,
            required_permission=permission,
            risk="low",
        )

    return [
        Tool(
            spec(
                CUSTOMER_OUTSTANDING.name,
                "Current outstanding amount (one total) of one customer, from the ERP. For a list "
                "of bills use customer_open_invoices.",
                CUSTOMER_OUTSTANDING.permission,
                CustomerInput,
                BusinessToolOutput[CustomerOutstandingData],
            ),
            outstanding,
        ),
        Tool(
            spec(
                CUSTOMER_OPEN_INVOICES.name,
                "Open (pending, unpaid) invoices with balances, due dates and overdue flags: "
                "of one customer, of all customers, or one invoice by number, from the ERP. "
                "Use for 'bills dikhao', 'pending/overdue bills'; no customer = all customers.",
                CUSTOMER_OPEN_INVOICES.permission,
                OpenInvoicesInput,
                BusinessToolOutput[OpenInvoicesData],
            ),
            open_invoices,
        ),
        Tool(
            spec(
                DAILY_SALES.name,
                "Total sales of one business day or of a relative period (this/last week, "
                "this/last month, this/last year), optionally compared with the same period "
                "last year, from the ERP. Sales totals, never profit.",
                DAILY_SALES.permission,
                DailySalesInput,
                BusinessToolOutput[SalesData],
            ),
            daily_sales,
        ),
        Tool(
            spec(
                PRODUCT_STOCK.name,
                "Stock of one product per warehouse the user may see, optionally in one "
                "named warehouse, from the ERP.",
                PRODUCT_STOCK.permission,
                ProductInput,
                BusinessToolOutput[ProductStockData],
            ),
            product_stock,
        ),
        Tool(
            spec(
                WAREHOUSE_STOCK.name,
                "Stock held in one warehouse (godown), from the ERP. Use for '<godown> mein kya "
                "stock hai'.",
                WAREHOUSE_STOCK.permission,
                WarehouseInput,
                BusinessToolOutput[WarehouseStockData],
            ),
            warehouse_stock,
        ),
        Tool(
            spec(
                LOW_STOCK.name,
                "Products whose stock is below the ERP's low-stock level, in the "
                "warehouses the user may see; optionally only one product (is it low?).",
                LOW_STOCK.permission,
                LowStockInput,
                BusinessToolOutput[LowStockData],
            ),
            low_stock,
        ),
        Tool(
            spec(
                CUSTOMER_COUNT.name,
                "How many customers the company has (active and inactive), from the ERP. "
                "With `period` or `day`: how many different customers were billed (came "
                "and bought) in it, plus counter-sale bills. Use for 'kitne customers "
                "hain', 'aaj kitne customer aaye'. Never counts login users.",
                CUSTOMER_COUNT.permission,
                CustomerCountInput,
                BusinessToolOutput[CustomerCountResult],
            ),
            customer_count,
        ),
    ]


def register_business_tools(
    registry: ToolRegistry, service: BusinessReadService, settings: ErpSettings
) -> None:
    for tool in business_tools(service, settings):
        # The business layer is a read foundation: a business write tool must never be
        # registered here (writes need their own approval-gated action phase).
        if tool.spec.kind is not ToolKind.READ or tool.spec.domain != BUSINESS_DOMAIN:
            raise ToolRegistrationError(f"Business tool '{tool.spec.name}' must be a READ.")
        registry.register(tool)
