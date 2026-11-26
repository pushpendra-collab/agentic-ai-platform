"""The ERP read port: the only interface an ERP adapter implements (Phase 10, ADR-059).

    tool → BusinessReadService → ErpReadPort → adapter (postgres + readers) → ERP

Rules for every adapter:

* **Read only.** The port has no method that creates, changes or deletes anything, and
  an adapter must not add one. Writes belong to a later approval-gated action phase.
* **No SQL from outside.** Arguments are typed values (names to search, opaque refs,
  dates, limits) produced by validated tool inputs; an adapter never receives a table
  name, a column name or a SQL fragment, and never executes text it was given.
* **Tenant from the context.** `ctx.tenant_id` comes from the authenticated principal.
  Every returned record states its `tenant_id`; the service discards the whole answer
  if any record belongs to another tenant.
* **Scope.** Stock reads receive the caller's `WarehouseScope`; adapters should filter
  by it and the service filters again.
* **Errors.** Raise the `Erp*Error` classes below; anything else is treated as an
  internal error. Messages must not contain data, SQL, hosts or credentials.
* **Limits.** Return at most `limit` rows where a limit is given. The service asks for
  `limit + 1` to detect truncation.
* **Rules (Phase 11).** `verified_rules` names the `BusinessRule`s the adapter's mapping
  vouches for. A derived figure (overdue, period sales, weeks) is computed only when its
  rule is listed. An adapter without a confirmed mapping lists none.
* **Lifecycle (Phase 15).** Optional, not part of the protocol: `aclose()` releases the
  adapter's connections at process shutdown; `gate_state()` reports, without connecting,
  whether the live adapter has passed its security gate (readiness shows it).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal, Protocol, runtime_checkable

from agentic_platform.business.context import BusinessContext
from agentic_platform.business.models import (
    CustomerCountRecord,
    CustomerRecord,
    CustomersBilledRecord,
    InvoiceRecord,
    LowStockRecord,
    OutstandingRecord,
    ProductRecord,
    SalesRecord,
    StockRecord,
    WarehouseRecord,
)

# Phase 15: a live adapter's security gate (ADR-063), as readiness reports it.
#   unverified  no VERIFIED contract capability: nothing can be answered (RULE_NOT_VERIFIED)
#   pending     not connected yet: the gate runs before the first business read
#   verified    the gate passed on this process's connection
#   refused     the gate refused the connection: every business read fails closed
ErpGateState = Literal["unverified", "pending", "verified", "refused"]


class ErpError(Exception):
    """Base of adapter errors. `str(exc)` is logged; never put data or secrets in it."""


class ErpNotConfiguredError(ErpError):
    """No ERP connection is configured (UNAVAILABLE / ERP_NOT_CONFIGURED)."""


class ErpUnavailableError(ErpError):
    """Unreachable, refused, overloaded (UNAVAILABLE / ERP_UNAVAILABLE, retryable)."""


class ErpTimeoutError(ErpError):
    """A statement or connection timed out (UNAVAILABLE / ERP_TIMEOUT, retryable)."""


class ErpRuleNotVerifiedError(ErpError):
    """The capability needs an ERP mapping or rule that has not been verified with the
    ERP owner (UNAVAILABLE / RULE_NOT_VERIFIED). The honest answer until Q1 is closed."""


class ErpMalformedError(ErpError):
    """The ERP answered something the contract does not allow: a status value outside
    the contract's closed list, a row without its required name (DEPENDENCY_ERROR /
    MALFORMED_RESPONSE). The answer is discarded, never mapped by guess."""


class ErpSafetyViolationError(ErpError):
    """The ERP refused something the read-only boundary should never have sent (a write,
    a permission error), or the connection is not the expected read-only ERP database.
    Logged as an error; the caller sees DEPENDENCY_ERROR."""


@runtime_checkable
class ErpReadPort(Protocol):
    @property
    def name(self) -> str:
        """Adapter name recorded as the data source (`postgres`)."""
        ...

    @property
    def test_data(self) -> bool:
        """True only for adapters serving test data."""
        ...

    @property
    def verified_rules(self) -> frozenset[str]:
        """`BusinessRule` values this adapter's mapping vouches for (Phase 11)."""
        ...

    async def find_customers(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[CustomerRecord]: ...

    async def get_customer(self, ctx: BusinessContext, ref: str) -> CustomerRecord | None: ...

    async def customer_outstanding(
        self, ctx: BusinessContext, customer_ref: str
    ) -> OutstandingRecord | None: ...

    async def open_invoices(
        self,
        ctx: BusinessContext,
        customer_ref: str | None,
        limit: int,
        invoice_number: str | None = None,
    ) -> Sequence[InvoiceRecord]:
        """Open invoices of one customer, or of every customer when `customer_ref` is
        None; only the one numbered `invoice_number` when given (Phase 11)."""
        ...

    async def daily_sales(
        self, ctx: BusinessContext, business_date: date
    ) -> SalesRecord | None: ...

    async def sales_by_day(
        self, ctx: BusinessContext, start: date, end: date, limit: int
    ) -> Sequence[SalesRecord]:
        """One record per business day with sales, start <= day < end (Phase 11)."""
        ...

    async def find_products(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[ProductRecord]: ...

    async def get_product(self, ctx: BusinessContext, ref: str) -> ProductRecord | None: ...

    async def find_warehouses(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[WarehouseRecord]: ...

    async def get_warehouse(self, ctx: BusinessContext, ref: str) -> WarehouseRecord | None: ...

    async def product_stock(
        self, ctx: BusinessContext, product_ref: str, limit: int
    ) -> Sequence[StockRecord]: ...

    async def warehouse_stock(
        self, ctx: BusinessContext, warehouse_ref: str, limit: int
    ) -> Sequence[StockRecord]: ...

    async def low_stock(
        self, ctx: BusinessContext, limit: int, product_ref: str | None = None
    ) -> Sequence[LowStockRecord]: ...

    async def customer_count(self, ctx: BusinessContext) -> CustomerCountRecord | None:
        """How many customers the tenant has (2026-11-25)."""
        ...

    async def customers_billed(
        self, ctx: BusinessContext, start: date, end: date
    ) -> CustomersBilledRecord | None:
        """Customers billed in start <= day < end (2026-11-25)."""
        ...
