"""Typed business query intent (Phase 11, ADR-060).

The planner (`understand_request`) describes a business question as a
`BusinessQueryIntent`: which capability, which entities the user named, which period
token, which metric, which filters. It is LLM output, so it is untrusted:

* it has no field for a tenant, user, warehouse scope, permission, table, column or SQL,
  and unknown fields are refused. Natural language is never an authorization mechanism:
  naming "Warehouse B" asks for Warehouse B, and the service still decides whether the
  caller may see it (out of scope = NOT_FOUND);
* it carries no dates the model computed: relative periods are `DatePeriod` tokens that
  the service resolves from the clock;
* it selects; it never supplies figures.

Uses: when the planner gave no explicit business tool call, the handler compiles the
intent into exactly one typed call (`tools/business.py: tool_call_for`); composition
picks the requested metric and filters; logs record `safe_log_fields()` only (no names).
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_platform.business.dates import ComparisonBasis, DatePeriod, normalise_period
from agentic_platform.business.metrics import BusinessMetric
from agentic_platform.business.models import REF_PATTERN

CapabilityName = Literal[
    "customer_outstanding",
    "customer_open_invoices",
    "daily_sales",
    "product_stock",
    "warehouse_stock",
    "low_stock_products",
    "customer_count",
]

BusinessEntity = Literal["customer", "invoice", "sales", "product", "warehouse", "stock"]


class BusinessOperation(StrEnum):
    LOOKUP = "lookup"  # one figure or record
    LIST = "list"  # several records
    TOTAL = "total"  # an aggregate figure
    COUNT = "count"  # how many
    CHECK = "check"  # yes/no ("is X low?")


class InvoiceFilter(StrEnum):
    OPEN = "open"
    OVERDUE = "overdue"


class EntityReference(BaseModel):
    """How the user named a customer, product or warehouse: their words, or an exact
    reference from an earlier result in this conversation. A reference never widens
    access: the service resolves it inside the caller's tenant and scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, description="The name exactly as the user wrote it.")
    ref: str | None = Field(
        default=None,
        description="Exact reference from an earlier result or the conversation focus; "
        "never invented.",
    )

    @field_validator("name", "ref", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("name")
    @classmethod
    def _name_length(cls, value: str | None) -> str | None:
        if value is not None and not 2 <= len(value) <= 100:
            raise ValueError("a name has 2 to 100 characters")
        return value

    @field_validator("ref")
    @classmethod
    def _ref_shape(cls, value: str | None) -> str | None:

        if value is not None and not re.fullmatch(REF_PATTERN, value):
            raise ValueError("not a reference")
        return value

    @model_validator(mode="after")
    def _something(self) -> EntityReference:
        if self.name is None and self.ref is None:
            raise ValueError("a reference needs a name or a ref")
        return self

    def tool_arguments(self, prefix: str) -> dict[str, str]:
        """`{prefix}_ref` when a ref is known (it is exact), else `{prefix}_name`."""
        if self.ref is not None:
            return {f"{prefix}_ref": self.ref}
        assert self.name is not None
        return {f"{prefix}_name": self.name}


class BusinessQueryIntent(BaseModel):
    """What a business question asks, as typed data. Untrusted LLM output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: CapabilityName | None = Field(
        default=None, description="The listed business capability that answers it."
    )
    operation: BusinessOperation = BusinessOperation.LOOKUP
    entity: BusinessEntity | None = Field(default=None, description="What the question is about.")
    customer_reference: EntityReference | None = None
    product_reference: EntityReference | None = None
    warehouse_reference: EntityReference | None = None
    invoice_number: str | None = Field(
        default=None, description="An invoice number exactly as the user wrote it."
    )
    period: DatePeriod | None = Field(
        default=None,
        description="Relative period token (today, yesterday, this_week, last_week, "
        "this_month, last_month, this_year, last_year). Never a computed date.",
    )
    comparison: ComparisonBasis | None = Field(
        default=None,
        description="Phase 16: 'same_period_last_year' when the user compares the period "
        "with the same period last year. Needs `period`.",
    )
    explicit_date: date | None = Field(
        default=None, description="A calendar date the user wrote explicitly (YYYY-MM-DD)."
    )
    requested_metric: BusinessMetric | None = None
    filters: list[InvoiceFilter] = Field(default_factory=list)
    follow_up: bool = Field(
        default=False, description="True when it refers to the previous business answer."
    )
    write_requested: bool = Field(
        default=False,
        description="True when the user asks to create, change, send or delete anything.",
    )
    ambiguity: str | None = Field(
        default=None, description="Why the question may be ambiguous, if it may be."
    )
    confidence: Literal["high", "medium", "low"] = "high"
    explanation: str | None = Field(default=None, description="One short reason for the choice.")

    @field_validator("period", mode="before")
    @classmethod
    def _period(cls, value: object) -> object:
        return normalise_period(value)

    @field_validator("invoice_number", "ambiguity", "explanation", mode="before")
    @classmethod
    def _trim(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()[:300]
            return value or None
        return value

    @field_validator("filters", mode="before")
    @classmethod
    def _dedupe(cls, value: object) -> object:
        if isinstance(value, list):
            return list(dict.fromkeys(value))
        return value

    def safe_log_fields(self) -> dict[str, Any]:
        """Metadata for logs and audit: never names, refs, numbers or dates."""
        return {
            "capability": self.capability,
            "operation": self.operation.value,
            "entity": self.entity,
            "metric": self.requested_metric.value if self.requested_metric else None,
            "period": self.period.value if self.period else None,
            "has_explicit_date": self.explicit_date is not None,
            "comparison": self.comparison,
            "has_customer": self.customer_reference is not None,
            "has_product": self.product_reference is not None,
            "has_warehouse": self.warehouse_reference is not None,
            "has_invoice_number": self.invoice_number is not None,
            "filters": [f.value for f in self.filters],
            "follow_up": self.follow_up,
            "write_requested": self.write_requested,
            "confidence": self.confidence,
        }
