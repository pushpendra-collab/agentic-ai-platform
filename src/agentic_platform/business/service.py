"""Business read service: the only way business data reaches a tool (Phase 10, ADR-059).

    tool ─► BusinessReadService ─► ErpReadPort ─► adapter ─► ERP (read only)

Every public method takes the authenticated `Principal` and returns a `BusinessResult`;
none raises. In order, each read:

1. re-checks the capability's permission on the principal (the tool registry checked it
   already; this layer does not trust its callers) → UNAUTHORIZED;
2. builds the `BusinessContext` from the principal only (tenant, user, warehouse scope);
3. runs under one deadline (`ERP_QUERY_TIMEOUT_SECONDS`) → UNAVAILABLE / ERP_TIMEOUT;
4. resolves names to exactly one record → NOT_FOUND / AMBIGUOUS (candidates);
5. re-validates every record the adapter returned → DEPENDENCY_ERROR / MALFORMED_RESPONSE,
   and checks it belongs to the caller's tenant → DEPENDENCY_ERROR / TENANT_MISMATCH
   (the whole answer is discarded, nothing partial is returned);
6. filters stock by warehouse scope again (records outside it are invisible);
7. computes totals in code (never the LLM) and returns SUCCESS with its data source.
   Derived figures (overdue, period sales, weeks; Phase 11) are computed only when the
   business rules they need hold (`rules.py`); otherwise they are left out and named
   in `rules_not_verified`, or the read is UNAVAILABLE / RULE_NOT_VERIFIED;
8. logs and audits the outcome as metadata only: capability, status, reason, adapter,
   duration. Never names, amounts, quantities or refs.

There is no method that writes, and nothing here builds SQL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from agentic_platform.business.capabilities import (
    CUSTOMER_COUNT,
    CUSTOMER_OPEN_INVOICES,
    CUSTOMER_OUTSTANDING,
    DAILY_SALES,
    LOW_STOCK,
    PRODUCT_STOCK,
    WAREHOUSE_STOCK,
    Capability,
)
from agentic_platform.business.context import (
    BusinessContext,
    PermissionScopeResolver,
    WarehouseScopeResolver,
)
from agentic_platform.business.dates import (
    SINGLE_DAY_PERIODS,
    WEEK_PERIODS,
    YEAR_PERIODS,
    BusinessDateRange,
    ComparisonBasis,
    DatePeriod,
    TimezoneSource,
    resolve_period,
    same_period_last_year,
    single_day,
)
from agentic_platform.business.models import (
    ComparisonGap,
    CustomerCountData,
    CustomerCountRecord,
    CustomerOutstandingData,
    CustomerRecord,
    CustomersBilledData,
    CustomersBilledRecord,
    DailySalesData,
    EntityRef,
    InvoiceLine,
    InvoiceRecord,
    LowStockData,
    LowStockLine,
    LowStockRecord,
    Money,
    OpenInvoicesData,
    OutstandingRecord,
    ProductLine,
    ProductRecord,
    ProductStockData,
    SalesComparisonData,
    SalesDayLine,
    SalesPeriodData,
    SalesPeriodSummary,
    SalesRecord,
    StockLine,
    StockRecord,
    WarehouseRecord,
    WarehouseStockData,
)
from agentic_platform.business.ports import (
    ErpGateState,
    ErpMalformedError,
    ErpNotConfiguredError,
    ErpReadPort,
    ErpRuleNotVerifiedError,
    ErpSafetyViolationError,
    ErpTimeoutError,
    ErpUnavailableError,
)
from agentic_platform.business.results import (
    RETRYABLE_REASONS,
    BusinessResult,
    BusinessStatus,
    DataSource,
    Entity,
    Reason,
)
from agentic_platform.business.rules import BusinessRule
from agentic_platform.core.config import ErpSettings
from agentic_platform.core.logging import current_log_context, get_logger
from agentic_platform.observability.deadline import strict_deadline
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit
from agentic_platform.observability.retry import erp_read_policy, retry_async
from agentic_platform.observability.taxonomy import (
    ErrorCategory,
    classify_business,
    classify_exception,
)
from agentic_platform.security.identity import Principal
from agentic_platform.services.audit import AuditEvent
from agentic_platform.services.clock import ClockService, UnknownTimezoneError

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)

# Most candidates shown for an ambiguous name.
MAX_CANDIDATES = 10

Day = date | Literal["today"]
SalesData = DailySalesData | SalesPeriodData | SalesComparisonData
CustomerCountResult = CustomerCountData | CustomersBilledData

_ENTITY_OF: dict[type[BaseModel], str] = {
    CustomerRecord: Entity.CUSTOMER,
    ProductRecord: Entity.PRODUCT,
    WarehouseRecord: Entity.WAREHOUSE,
}


class Auditor(Protocol):
    async def record(self, event: AuditEvent) -> int: ...


class _Early(Exception):  # control flow: a deterministic non-SUCCESS outcome
    def __init__(
        self,
        status: BusinessStatus,
        reason: str,
        candidates: Sequence[EntityRef] = (),
        entity: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.status, self.reason, self.candidates = status, reason, list(candidates)
        self.entity = entity


class _Rejected(Exception):  # the adapter's answer is unusable
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BusinessReadService:
    def __init__(
        self,
        port: ErpReadPort,
        *,
        settings: ErpSettings,
        clock: ClockService | None = None,
        auditor: Auditor | None = None,
        scope_resolver: WarehouseScopeResolver | None = None,
    ) -> None:
        self._port = port
        self._settings = settings
        self._clock = clock or ClockService()
        self._auditor = auditor
        self._scopes = scope_resolver or PermissionScopeResolver()
        # Phase 13 (ADR-062): an unreachable ERP is retried, within the read's deadline.
        self._retry = erp_read_policy(
            settings.read_max_attempts, settings.read_retry_backoff_seconds
        )

    @property
    def adapter_name(self) -> str:
        return self._port.name

    @property
    def test_data(self) -> bool:
        return self._port.test_data

    def gate_state(self) -> ErpGateState | None:
        """Phase 15: the live adapter's security-gate state (None: the adapter has no
        gate, for example a test double). Never connects."""
        state: Callable[[], ErpGateState] | None = getattr(self._port, "gate_state", None)
        return state() if state is not None else None

    async def aclose(self) -> None:
        """Phase 15: release the adapter's connections (process shutdown)."""
        close: Callable[[], Awaitable[None]] | None = getattr(self._port, "aclose", None)
        if close is not None:
            await close()

    # --- capabilities ------------------------------------------------------------------

    async def customer_outstanding(
        self,
        principal: Principal,
        *,
        customer_name: str | None = None,
        customer_ref: str | None = None,
    ) -> BusinessResult[CustomerOutstandingData]:
        async def body(ctx: BusinessContext) -> CustomerOutstandingData:
            customer = await self._customer(ctx, customer_name, customer_ref)
            raw = await self._port.customer_outstanding(ctx, customer.ref)
            if raw is None:
                raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH)
            record = self._record(OutstandingRecord, raw, ctx)
            if record.customer_ref != customer.ref:
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            return CustomerOutstandingData(
                customer=_ref(customer),
                outstanding=Money(amount=record.amount, currency=record.currency),
                as_of=record.as_of,
            )

        return await self._run(CUSTOMER_OUTSTANDING, principal, body)

    async def customer_open_invoices(
        self,
        principal: Principal,
        *,
        customer_name: str | None = None,
        customer_ref: str | None = None,
        invoice_number: str | None = None,
    ) -> BusinessResult[OpenInvoicesData]:
        """Open invoices of one customer, or of every customer of the tenant when no
        customer is named (Phase 11); narrowed to one invoice by `invoice_number`."""

        async def body(ctx: BusinessContext) -> OpenInvoicesData:
            customer: CustomerRecord | None = None
            if customer_name is not None or customer_ref is not None:
                customer = await self._customer(ctx, customer_name, customer_ref)
            limit = self._settings.max_rows
            raw = await self._port.open_invoices(
                ctx, customer.ref if customer else None, limit + 1, invoice_number
            )
            records = self._records(InvoiceRecord, raw, ctx)
            if customer is not None and any(r.customer_ref != customer.ref for r in records):
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            if invoice_number is not None:
                if any(r.number.casefold() != invoice_number.casefold() for r in records):
                    raise _Rejected(Reason.MALFORMED_RESPONSE)
                if not records:
                    raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=Entity.INVOICE)
            truncated = len(records) > limit
            shown = records[:limit]
            currencies = {r.currency for r in shown}
            single = currencies.pop() if len(currencies) == 1 else None
            complete = not truncated and single is not None

            # Overdue: needs the business date and the overdue rule (rules.py).
            gaps = self._rule_gaps(BusinessRule.BUSINESS_TIMEZONE, BusinessRule.OVERDUE_BY_DUE_DATE)
            as_of = None if gaps else self._today()[0]

            def overdue(r: InvoiceRecord) -> bool | None:
                return None if as_of is None or r.due_on is None else r.due_on < as_of

            flags = [overdue(r) for r in shown]
            overdue_known = as_of is not None and complete and None not in flags
            overdue_rows = [r for r, flag in zip(shown, flags, strict=True) if flag]

            def summed(values: list[Decimal | None]) -> Money | None:
                if not complete or single is None or any(v is None for v in values):
                    return None
                return Money(amount=sum((v for v in values if v is not None), Decimal(0)),
                             currency=single)  # fmt: skip

            return OpenInvoicesData(
                customer=_ref(customer) if customer else None,
                invoices=[
                    InvoiceLine(
                        invoice=EntityRef(ref=r.ref, display_name=r.number),
                        balance=Money(amount=r.balance, currency=r.currency),
                        issued_on=r.issued_on,
                        due_on=r.due_on,
                        customer=None if customer is not None else _customer_of(r),
                        total=_money(r.total, r.currency),
                        paid=_money(r.paid, r.currency),
                        overdue=flag,
                    )
                    for r, flag in zip(shown, flags, strict=True)
                ],
                invoice_count=len(shown),
                total_balance=summed([r.balance for r in shown]),
                truncated=truncated,
                invoice_number=invoice_number,
                as_of_date=as_of,
                overdue_count=len(overdue_rows) if overdue_known else None,
                overdue_balance=(
                    summed([r.balance for r in overdue_rows]) if overdue_known else None
                ),
                invoice_total=summed([r.total for r in shown]),
                paid_total=summed([r.paid for r in shown]),
                rules_not_verified=gaps,
            )

        return await self._run(CUSTOMER_OPEN_INVOICES, principal, body)

    async def daily_sales(
        self,
        principal: Principal,
        *,
        day: Day = "today",
        period: DatePeriod | None = None,
        compare_with: ComparisonBasis | None = None,
    ) -> BusinessResult[SalesData]:
        """Sales of one business day (`day`, or `period` today/yesterday) or of a period
        (Phase 11: this/last week, this/last month; Phase 16: this/last year), resolved
        from the clock, never from the model. Phase 16: with `compare_with`, the period
        against the same period last year (a relative `period` is required)."""

        async def body(ctx: BusinessContext) -> SalesData:
            if compare_with is not None:
                if period is None:
                    raise _Early(BusinessStatus.VALIDATION_ERROR, Reason.PERIOD_REQUIRED)
                return await self._sales_comparison(ctx, period)
            if period is not None and period not in SINGLE_DAY_PERIODS:
                return await self._sales_period(ctx, period)
            timezone: str | None
            if period is DatePeriod.YESTERDAY:
                today, timezone = self._today()
                business_date = today - timedelta(days=1)
            elif period is DatePeriod.TODAY:
                business_date, timezone = self._today()
            else:
                business_date, timezone = self._business_date(day)
            raw = await self._port.daily_sales(ctx, business_date)
            if raw is None:
                raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=Entity.SALES)
            record = self._record(SalesRecord, raw, ctx)
            if record.business_date != business_date:
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            return DailySalesData(
                business_date=business_date,
                timezone=timezone,
                total=Money(amount=record.total, currency=record.currency),
                document_count=record.document_count,
                date_range=single_day(
                    business_date,
                    timezone=timezone,
                    source=self._timezone_source if timezone else None,
                    period=period or (DatePeriod.TODAY if day == "today" else None),
                ),
            )

        return await self._run(DAILY_SALES, principal, body)

    def _period_span(self, period: DatePeriod, *extra: BusinessRule) -> BusinessDateRange:
        """Resolve a multi-day (or, for comparisons, any) period after checking the rules
        it needs; UNAVAILABLE / RULE_NOT_VERIFIED otherwise."""
        needed = [BusinessRule.BUSINESS_TIMEZONE, BusinessRule.SALES_PERIOD_IS_SUM_OF_DAYS]
        if period in WEEK_PERIODS:
            needed.append(BusinessRule.WEEK_STARTS_MONDAY)
        year_start_month = 1
        if period in YEAR_PERIODS:
            # Exactly one year rule must hold: calendar year or Indian financial year.
            calendar = not self._rule_gaps(BusinessRule.YEAR_IS_CALENDAR_YEAR)
            financial = not self._rule_gaps(BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL)
            if calendar == financial:
                raise _Early(BusinessStatus.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)
            year_start_month = 4 if financial else 1
        if self._rule_gaps(*needed, *extra):
            raise _Early(BusinessStatus.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)
        today, timezone = self._today()
        return resolve_period(
            period, today=today, timezone=timezone, source=self._timezone_source,
            year_start_month=year_start_month,
        )  # fmt: skip

    async def _sales_days(self, ctx: BusinessContext, span: BusinessDateRange) -> list[SalesRecord]:
        """The validated day records of `span`, sorted (at most one per day, all inside)."""
        raw = await self._port.sales_by_day(ctx, span.start, span.end, span.days + 1)
        records = sorted(self._records(SalesRecord, raw, ctx), key=lambda r: r.business_date)
        dates = [r.business_date for r in records]
        if len(records) > span.days or len(set(dates)) != len(dates):
            raise _Rejected(Reason.MALFORMED_RESPONSE)
        if not all(span.contains(d) for d in dates):
            raise _Rejected(Reason.MALFORMED_RESPONSE)
        return records

    async def _sales_comparison(
        self, ctx: BusinessContext, period: DatePeriod
    ) -> SalesComparisonData:
        """Phase 16 (ADR-065): `period` against the same calendar dates one year earlier.
        Both sides are read with the same port method and the same validation; every
        figure is Decimal arithmetic here, never the model's."""
        span = self._period_span(period, BusinessRule.SAME_PERIOD_LAST_YEAR_BY_DATE)
        earlier = same_period_last_year(span)
        current = _summary(span, await self._sales_days(ctx, span))
        baseline = _summary(earlier, await self._sales_days(ctx, earlier))
        if current.days_with_sales == 0 and baseline.days_with_sales == 0:
            raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=Entity.SALES)
        return _compare(current, baseline)

    async def _sales_period(self, ctx: BusinessContext, period: DatePeriod) -> SalesPeriodData:
        span = self._period_span(period)
        records = await self._sales_days(ctx, span)
        if not records:
            raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=Entity.SALES)
        currencies = {r.currency for r in records}
        return SalesPeriodData(
            date_range=span,
            total=Money(amount=sum((r.total for r in records), Decimal(0)),
                        currency=currencies.pop())
            if len(currencies) == 1
            else None,
            document_count=sum(r.document_count for r in records),
            days=[
                SalesDayLine(
                    business_date=r.business_date,
                    total=Money(amount=r.total, currency=r.currency),
                    document_count=r.document_count,
                )
                for r in records
            ],
            days_with_sales=len(records),
        )  # fmt: skip

    async def product_stock(
        self,
        principal: Principal,
        *,
        product_name: str | None = None,
        product_ref: str | None = None,
        warehouse_name: str | None = None,
        warehouse_ref: str | None = None,
    ) -> BusinessResult[ProductStockData]:
        """Stock of one product per visible warehouse; narrowed to one warehouse when one
        is named (Phase 11). A warehouse outside the caller's scope is NOT_FOUND."""

        async def body(ctx: BusinessContext) -> ProductStockData:
            if ctx.warehouse_scope.empty:
                raise _Early(BusinessStatus.UNAUTHORIZED, Reason.WAREHOUSE_SCOPE_EMPTY)
            product = await self._product(ctx, product_name, product_ref)
            warehouse: WarehouseRecord | None = None
            if warehouse_name is not None or warehouse_ref is not None:
                warehouse = await self._warehouse(ctx, warehouse_name, warehouse_ref)
            limit = self._settings.max_rows
            raw = await self._port.product_stock(ctx, product.ref, limit + 1)
            records = self._records(StockRecord, raw, ctx)
            if any(r.product_ref != product.ref for r in records):
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            if len(records) > limit:  # a partial per-product total would be wrong
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            visible = [
                r
                for r in records
                if ctx.warehouse_scope.allows(r.warehouse_ref)
                and (warehouse is None or r.warehouse_ref == warehouse.ref)
            ]
            return ProductStockData(
                product=_ref(product),
                unit=product.unit,
                lines=[
                    StockLine(
                        warehouse=EntityRef(ref=r.warehouse_ref, display_name=r.warehouse_name),
                        quantity=r.quantity,
                    )
                    for r in visible
                ],
                total_quantity=sum((r.quantity for r in visible), Decimal(0)),
                warehouse_count=len(visible),
                scope_limited=ctx.warehouse_scope.limited,
                warehouse_filter=_ref(warehouse) if warehouse else None,
            )

        return await self._run(PRODUCT_STOCK, principal, body)

    async def warehouse_stock(
        self,
        principal: Principal,
        *,
        warehouse_name: str | None = None,
        warehouse_ref: str | None = None,
    ) -> BusinessResult[WarehouseStockData]:
        async def body(ctx: BusinessContext) -> WarehouseStockData:
            if ctx.warehouse_scope.empty:
                raise _Early(BusinessStatus.UNAUTHORIZED, Reason.WAREHOUSE_SCOPE_EMPTY)
            warehouse = await self._warehouse(ctx, warehouse_name, warehouse_ref)
            limit = self._settings.max_rows
            raw = await self._port.warehouse_stock(ctx, warehouse.ref, limit + 1)
            records = self._records(StockRecord, raw, ctx)
            if any(r.warehouse_ref != warehouse.ref for r in records):
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            shown = records[:limit]
            return WarehouseStockData(
                warehouse=_ref(warehouse),
                lines=[
                    ProductLine(
                        product=EntityRef(ref=r.product_ref, display_name=r.product_name),
                        quantity=r.quantity,
                        unit=r.unit,
                    )
                    for r in shown
                ],
                product_count=len(shown),
                truncated=len(records) > limit,
            )

        return await self._run(WAREHOUSE_STOCK, principal, body)

    async def customer_count(
        self,
        principal: Principal,
        *,
        period: DatePeriod | None = None,
        day: date | None = None,
    ) -> BusinessResult[CustomerCountResult]:
        """How many customers the company has; with `period` or `day`, how many different
        customers were billed in it (2026-11-25). Counts only, computed by the ERP query
        and re-validated here."""

        async def body(ctx: BusinessContext) -> CustomerCountResult:
            if period is None and day is None:
                raw = await self._port.customer_count(ctx)
                if raw is None:
                    raise _Rejected(Reason.MALFORMED_RESPONSE)
                record = self._record(CustomerCountRecord, raw, ctx)
                return CustomerCountData(
                    active_count=record.active_count, inactive_count=record.inactive_count
                )
            span = self._count_span(period, day)
            raw = await self._port.customers_billed(ctx, span.start, span.end)
            if raw is None:
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            billed = self._record(CustomersBilledRecord, raw, ctx)
            if (billed.start, billed.end) != (span.start, span.end):
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            return CustomersBilledData(
                date_range=span,
                customer_count=billed.customer_count,
                invoice_count=billed.invoice_count,
                counter_sale_count=billed.counter_sale_count,
            )

        return await self._run(CUSTOMER_COUNT, principal, body)

    def _count_span(self, period: DatePeriod | None, day: date | None) -> BusinessDateRange:
        """The range of a customers-billed count: one day (today, yesterday or a date the
        user named) or a multi-day period (rule-checked like sales periods)."""
        if period is not None and period not in SINGLE_DAY_PERIODS:
            return self._period_span(period)
        timezone: str | None
        if period is not None:
            today, timezone = self._today()
            business_date = today - timedelta(days=1) if period is DatePeriod.YESTERDAY else today
        else:
            assert day is not None
            business_date, timezone = self._business_date(day)
        return single_day(
            business_date,
            timezone=timezone,
            source=self._timezone_source if timezone else None,
            period=period,
        )

    async def low_stock_products(
        self,
        principal: Principal,
        *,
        limit: int = 50,
        product_name: str | None = None,
        product_ref: str | None = None,
    ) -> BusinessResult[LowStockData]:
        """Items below the ERP's low-stock level in visible warehouses; narrowed to one
        product when one is named ("is X low?", Phase 11)."""

        async def body(ctx: BusinessContext) -> LowStockData:
            if ctx.warehouse_scope.empty:
                raise _Early(BusinessStatus.UNAUTHORIZED, Reason.WAREHOUSE_SCOPE_EMPTY)
            product: ProductRecord | None = None
            if product_name is not None or product_ref is not None:
                product = await self._product(ctx, product_name, product_ref)
            wanted = max(1, min(limit, self._settings.max_rows))
            raw = await self._port.low_stock(ctx, wanted + 1, product.ref if product else None)
            records = self._records(LowStockRecord, raw, ctx)
            if product is not None and any(r.product_ref != product.ref for r in records):
                raise _Rejected(Reason.MALFORMED_RESPONSE)
            visible = [r for r in records if ctx.warehouse_scope.allows(r.warehouse_ref)]
            shown = visible[:wanted]
            return LowStockData(
                items=[
                    LowStockLine(
                        product=EntityRef(ref=r.product_ref, display_name=r.product_name),
                        warehouse=EntityRef(ref=r.warehouse_ref, display_name=r.warehouse_name),
                        quantity=r.quantity,
                        threshold=r.threshold,
                        unit=r.unit,
                    )
                    for r in shown
                ],
                item_count=len(shown),
                truncated=len(records) > wanted,
                scope_limited=ctx.warehouse_scope.limited,
                product_filter=_ref(product) if product else None,
            )

        return await self._run(LOW_STOCK, principal, body)

    # --- resolution ----------------------------------------------------------------------

    async def _customer(
        self, ctx: BusinessContext, name: str | None, ref: str | None
    ) -> CustomerRecord:
        return await self._resolve(
            ctx,
            CustomerRecord,
            name,
            ref,
            lambda n: self._port.find_customers(ctx, n, MAX_CANDIDATES + 1),
            lambda r: self._port.get_customer(ctx, r),
        )

    async def _product(
        self, ctx: BusinessContext, name: str | None, ref: str | None
    ) -> ProductRecord:
        return await self._resolve(
            ctx,
            ProductRecord,
            name,
            ref,
            lambda n: self._port.find_products(ctx, n, MAX_CANDIDATES + 1),
            lambda r: self._port.get_product(ctx, r),
        )

    async def _warehouse(
        self, ctx: BusinessContext, name: str | None, ref: str | None
    ) -> WarehouseRecord:
        found = await self._resolve(
            ctx,
            WarehouseRecord,
            name,
            ref,
            lambda n: self._port.find_warehouses(ctx, n, MAX_CANDIDATES + 1),
            lambda r: self._port.get_warehouse(ctx, r),
            visible=lambda w: ctx.warehouse_scope.allows(w.ref),
        )
        return found

    async def _resolve(
        self,
        ctx: BusinessContext,
        model: type[R],
        name: str | None,
        ref: str | None,
        find: Callable[[str], Awaitable[Sequence[object]]],
        get: Callable[[str], Awaitable[object | None]],
        visible: Callable[[R], bool] = lambda _: True,
    ) -> R:
        """Exactly one record, or NOT_FOUND / AMBIGUOUS. Records the caller may not see
        (`visible`) are treated exactly like records that do not exist."""
        entity = _ENTITY_OF.get(model)
        if ref is not None:
            raw = await get(ref)
            record = None if raw is None else self._record(model, raw, ctx)
            if record is None or getattr(record, "ref", None) != ref or not visible(record):
                raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=entity)
            return record
        if name is None or not name.strip():
            raise _Early(BusinessStatus.VALIDATION_ERROR, Reason.NO_MATCH)
        matches = [r for r in self._records(model, await find(name.strip()), ctx) if visible(r)]
        if not matches:
            raise _Early(BusinessStatus.NOT_FOUND, Reason.NO_MATCH, entity=entity)
        if len(matches) > 1:
            raise _Early(
                BusinessStatus.AMBIGUOUS,
                Reason.SEVERAL_MATCHES,
                [_ref_of(m) for m in matches[:MAX_CANDIDATES]],
                entity=entity,
            )
        return matches[0]

    def _today(self) -> tuple[date, str]:
        """Today's business date and its zone, or UNAVAILABLE / RULE_NOT_VERIFIED."""
        timezone = self._settings.business_timezone
        if timezone is None:
            # The business day boundary is an ERP rule nobody has confirmed (Q1).
            raise _Early(BusinessStatus.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)
        try:
            return self._clock.now(timezone).date(), timezone
        except UnknownTimezoneError as exc:
            raise _Early(BusinessStatus.UNAVAILABLE, Reason.RULE_NOT_VERIFIED) from exc

    @property
    def _timezone_source(self) -> TimezoneSource:
        # With an adapter serving TEST data the zone is a TEST zone, never a claim about the ERP.
        return "TEST" if self._port.test_data else "ERP_BUSINESS_TIMEZONE"

    def _rule_gaps(self, *rules: BusinessRule) -> list[str]:
        """The rules among `rules` that do not hold (empty: all hold)."""
        gaps: list[str] = []
        for rule in rules:
            if rule is BusinessRule.BUSINESS_TIMEZONE:
                holds = self._settings.business_timezone is not None
            else:
                holds = rule.value in self._port.verified_rules
            if not holds:
                gaps.append(rule.value)
        return gaps

    def _business_date(self, day: Day) -> tuple[date, str | None]:
        if day == "today":
            return self._today()
        # Explicit date: refuse the future (latest date anywhere on Earth is UTC+14).
        latest = (self._clock.now("UTC") + timedelta(hours=14)).date()
        if day > latest:
            raise _Early(BusinessStatus.VALIDATION_ERROR, Reason.FUTURE_DATE)
        return day, None

    # --- record checks ------------------------------------------------------------------

    @staticmethod
    def _record(model: type[R], value: object, ctx: BusinessContext) -> R:
        """Re-validate one adapter record and check its tenant."""
        if isinstance(value, BaseModel):
            payload: Any = value.model_dump()
        elif isinstance(value, dict):
            payload = value
        else:
            raise _Rejected(Reason.MALFORMED_RESPONSE)
        try:
            record = model.model_validate(payload)
        except ValidationError as exc:
            raise _Rejected(Reason.MALFORMED_RESPONSE) from exc
        if getattr(record, "tenant_id", None) != ctx.tenant_id:
            raise _Rejected(Reason.TENANT_MISMATCH)
        return record

    def _records(self, model: type[R], values: object, ctx: BusinessContext) -> list[R]:
        if not isinstance(values, list | tuple):
            raise _Rejected(Reason.MALFORMED_RESPONSE)
        return [self._record(model, v, ctx) for v in values]

    # --- execution -----------------------------------------------------------------------

    async def _run(
        self,
        capability: Capability,
        principal: Principal,
        body: Callable[[BusinessContext], Awaitable[T]],
    ) -> BusinessResult[T]:
        started = time.perf_counter()
        status: BusinessStatus
        reason: str | None = None
        data: T | None = None
        candidates: list[EntityRef] = []
        entity: str | None = None
        attempts = 0
        if not principal.has(capability.permission):
            status, reason = BusinessStatus.UNAUTHORIZED, Reason.PERMISSION_MISSING
        else:
            ctx = BusinessContext.for_principal(principal, self._scopes)
            seconds = self._settings.query_timeout_seconds

            async def attempt() -> T:
                nonlocal attempts
                attempts += 1
                return await body(ctx)

            try:
                # Phase 13: one strict deadline for all attempts (a read that swallowed its
                # cancellation cannot report SUCCESS after the deadline).
                deadline_at = asyncio.get_running_loop().time() + seconds
                async with strict_deadline(seconds, scope="business_read"):
                    fetched = await retry_async(
                        "business_read",
                        attempt,
                        self._retry,
                        classify=_classify_erp_error,
                        deadline=deadline_at,
                    )
                # Only after the deadline block: data fetched after the deadline fired is
                # discarded (strict_deadline raised), never reported.
                data, status = fetched, BusinessStatus.SUCCESS
            except _Early as early:
                status, reason, candidates = early.status, early.reason, early.candidates
                entity = early.entity
            except _Rejected as rejected:
                status, reason = BusinessStatus.DEPENDENCY_ERROR, rejected.reason
                log = logger.error if reason == Reason.TENANT_MISMATCH else logger.warning
                log(
                    "business.read.rejected",
                    extra={"fields": {"capability": capability.name, "reason": reason}},
                )
            except (TimeoutError, ErpTimeoutError):
                status, reason = BusinessStatus.UNAVAILABLE, Reason.ERP_TIMEOUT
            except ErpNotConfiguredError:
                status, reason = BusinessStatus.UNAVAILABLE, Reason.ERP_NOT_CONFIGURED
            except ErpUnavailableError:
                status, reason = BusinessStatus.UNAVAILABLE, Reason.ERP_UNAVAILABLE
            except ErpRuleNotVerifiedError:
                status, reason = BusinessStatus.UNAVAILABLE, Reason.RULE_NOT_VERIFIED
            except ErpMalformedError as exc:
                status, reason = BusinessStatus.DEPENDENCY_ERROR, Reason.MALFORMED_RESPONSE
                logger.warning(
                    "business.read.rejected",
                    extra={"fields": {"capability": capability.name, "reason": reason,
                                      "error": str(exc)}},
                )  # fmt: skip
            except ErpSafetyViolationError as exc:
                status, reason = BusinessStatus.DEPENDENCY_ERROR, "ERP_SAFETY_VIOLATION"
                logger.error(
                    "business.read.safety_violation",
                    extra={"fields": {"capability": capability.name, "error": type(exc).__name__}},
                )
            except Exception:
                status, reason = BusinessStatus.DEPENDENCY_ERROR, Reason.INTERNAL_ERROR
                logger.exception(
                    "business.read.crashed", extra={"fields": {"capability": capability.name}}
                )
        result: BusinessResult[T] = BusinessResult(
            capability=capability.name,
            status=status,
            reason=reason,
            retryable=reason in RETRYABLE_REASONS,
            data=data,
            candidates=candidates,
            entity=entity,
            source=DataSource(
                adapter=self._port.name,
                retrieved_at=self._clock.now("UTC"),
                test_data=self._port.test_data,
            )
            if status is BusinessStatus.SUCCESS
            else None,
        )
        await self._observe(capability, principal, result, started, attempts)
        return result

    async def _observe(
        self,
        capability: Capability,
        principal: Principal,
        result: BusinessResult[Any],
        started: float,
        attempts: int = 1,
    ) -> None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        fields = {
            "capability": capability.name,
            "status": result.status.value,
            "reason": result.reason,
            "adapter": self._port.name,
            "duration_ms": duration_ms,
        }
        logger.info("business.read.completed", extra={"fields": fields})
        category = classify_business(result.status.value, result.reason)
        emit(
            EventName.BUSINESS_READ,
            stage=Stage.BUSINESS,
            status="ok" if category in (None, ErrorCategory.NOT_FOUND) else "error",
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error_category=category,
            error_code=result.reason,
            retryable=result.retryable,
            attempt=attempts,
            attributes={
                "capability": capability.name,
                "result_status": result.status.value,
                "reason": result.reason,
                "adapter": self._port.name,
                "test_data": self._port.test_data,
            },
        )
        if self._auditor is None:
            return
        context = current_log_context()
        audit_status: Literal["ok", "error", "denied"] = (
            "denied"
            if result.status is BusinessStatus.UNAUTHORIZED
            else "error"
            if result.status in (BusinessStatus.UNAVAILABLE, BusinessStatus.DEPENDENCY_ERROR)
            else "ok"
        )
        try:
            await self._auditor.record(
                AuditEvent(
                    action="business.read",
                    status=audit_status,
                    actor_type="agent",
                    actor_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    error_code=result.reason,
                    request_id=context.get("request_id"),
                    thread_id=context.get("thread_id"),
                    # Metadata only: no names, amounts, quantities or refs.
                    details={**fields, "test_data": self._port.test_data},
                )
            )
        except Exception:
            logger.error(
                "business.audit.failed",
                exc_info=True,
                extra={"fields": {"capability": capability.name}},
            )


def _classify_erp_error(exc: BaseException) -> ErrorCategory:
    """ERP port errors for the retry policy: only an unreachable ERP is transient."""
    if isinstance(exc, ErpUnavailableError):
        return ErrorCategory.DEPENDENCY_UNAVAILABLE
    if isinstance(exc, ErpTimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(
        exc,
        ErpNotConfiguredError | ErpRuleNotVerifiedError | ErpSafetyViolationError
        | ErpMalformedError,
    ):
        return ErrorCategory.NON_RETRYABLE_DEPENDENCY
    return classify_exception(exc)


def _ref(record: CustomerRecord | ProductRecord | WarehouseRecord) -> EntityRef:
    return EntityRef(ref=record.ref, display_name=record.display_name)


def _money(amount: Decimal | None, currency: str) -> Money | None:
    return None if amount is None else Money(amount=amount, currency=currency)


def _customer_of(record: InvoiceRecord) -> EntityRef:
    name = record.customer_name or record.customer_ref
    return EntityRef(ref=record.customer_ref, display_name=name)


def _ref_of(record: BaseModel) -> EntityRef:
    """Candidates of any resolvable record type (customer, product, warehouse)."""
    fields = record.model_dump(include={"ref", "display_name"})
    return EntityRef.model_validate(fields)


_PERCENT_STEP = Decimal("0.1")


def _summary(span: BusinessDateRange, records: Sequence[SalesRecord]) -> SalesPeriodSummary:
    """One comparison side (Phase 16). No records: no total (never a zero)."""
    currencies = sorted({r.currency for r in records})
    total = (
        Money(amount=sum((r.total for r in records), Decimal(0)), currency=currencies[0])
        if len(currencies) == 1
        else None
    )
    return SalesPeriodSummary(
        date_range=span,
        total=total,
        document_count=sum(r.document_count for r in records),
        days_with_sales=len(records),
        currencies=currencies,
    )


def _compare(current: SalesPeriodSummary, baseline: SalesPeriodSummary) -> SalesComparisonData:
    """Deterministic change figures (Phase 16). A figure that cannot be computed honestly
    is None and its reason is listed in `gaps`; nothing is divided by zero."""
    gaps: list[ComparisonGap] = []
    if current.days_with_sales == 0:
        gaps.append("CURRENT_NO_RECORDS")
    if baseline.days_with_sales == 0:
        gaps.append("BASELINE_NO_RECORDS")
    both = not gaps
    if both and (current.total is None or baseline.total is None
                 or current.total.currency != baseline.total.currency):  # fmt: skip
        gaps.append("MIXED_CURRENCY")
    change: Money | None = None
    percent: Decimal | None = None
    direction: Literal["increase", "decrease", "unchanged"] | None = None
    if not gaps and current.total is not None and baseline.total is not None:
        delta = current.total.amount - baseline.total.amount
        change = Money(amount=delta, currency=current.total.currency)
        direction = "increase" if delta > 0 else "decrease" if delta < 0 else "unchanged"
        if baseline.total.amount == 0:
            gaps.append("ZERO_BASELINE")
        else:
            percent = (delta / baseline.total.amount * 100).quantize(
                _PERCENT_STEP, rounding=ROUND_HALF_UP
            )
    return SalesComparisonData(
        current=current,
        baseline=baseline,
        change_amount=change,
        change_percent=percent,
        direction=direction,
        document_count_change=current.document_count - baseline.document_count if both else None,
        gaps=gaps,
        day_counts_differ=current.date_range.days != baseline.date_range.days,
    )


__all__ = ["BusinessReadService", "Day"]
