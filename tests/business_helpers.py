"""Shared fixtures for the business-layer tests: the real ERP read path on the InvenLogic
TEST replica (tests/erp_db.py). The fake ERP was removed on 2026-10-30.

`std()` / `ext()` are TEST companies on the replica with the scenarios these tests were
written for (Rahul Gupta's overdue and part-paid bills, an ambiguous "Amit Sharma",
Godown 1-2 stock and low stock; `ext()` adds Rahul Verma and Godown 3). Refs are the
replica's ids (`std()["rahul"]`, `std()["inv1"]`, ...).

`ErpFaults` wraps the real adapter for the failure tests: it counts calls and can make a
call fail the way a broken ERP or driver would (unreachable, timeout, hang, malformed rows,
another tenant's rows, ...). It holds no business data of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from datetime import date
from typing import Any, Literal, TypeVar, cast

import pytest

from agentic_platform.business.capabilities import BusinessPermission as BP
from agentic_platform.business.context import BusinessContext, WarehouseScopeResolver
from agentic_platform.business.ports import (
    ErpNotConfiguredError,
    ErpReadPort,
    ErpRuleNotVerifiedError,
    ErpTimeoutError,
    ErpUnavailableError,
)
from agentic_platform.business.service import BusinessReadService
from agentic_platform.core.config import ErpSettings
from agentic_platform.security import IdentityResolver, Principal
from agentic_platform.security.permissions import DEFAULT_ROLES, RoleCatalog
from agentic_platform.services.audit import AuditEvent
from agentic_platform.services.clock import ClockService
from tests.conftest import FIXED_NOW
from tests.erp_db import (
    StandardData,
    erp_adapter,
    erp_available,
    other_company,
    replica_contract,
    standard_company,
)

T = TypeVar("T")

# FIXED_NOW is 2026-10-01 06:30 UTC = 12:00 in Asia/Kolkata: "today" is 2026-10-01.
TODAY = date(2026, 10, 1)
TZ = "Asia/Kolkata"

ROLES: dict[str, list[str]] = {
    **{name: sorted(perms) for name, perms in DEFAULT_ROLES.items()},
    "sales_viewer": [BP.CUSTOMER_READ, BP.SALES_READ],
    "stock_viewer": [BP.INVENTORY_READ, BP.WAREHOUSE_ALL],
    "stock_viewer_limited": [BP.INVENTORY_READ],  # no warehouse scope of its own
}
ALL_BUSINESS = ("member", "sales_viewer", "stock_viewer")

# The calendar-year rule set: the tests of the year logic written for calendar years pass
# it explicitly; the InvenLogic contract itself confirms the financial year.
_CACHE: dict[str, StandardData] = {}


def need_erp() -> None:
    if not erp_available():
        pytest.skip("InvenLogic TEST replica not configured (tests/erp_db.py)")


def std() -> StandardData:
    """The standard TEST company (Godown 1-2, no Rahul Verma), created once per run."""
    need_erp()
    if "std" not in _CACHE:
        _CACHE["std"] = standard_company(TODAY, extended=False)
    return _CACHE["std"]


def ext() -> StandardData:
    """The extended TEST company (adds Rahul Verma and Godown 3), created once per run."""
    need_erp()
    if "ext" not in _CACHE:
        _CACHE["ext"] = standard_company(TODAY, extended=True)
    return _CACHE["ext"]


def globex() -> StandardData:
    """A second company whose only customer exists nowhere else."""
    need_erp()
    if "globex" not in _CACHE:
        _CACHE["globex"] = other_company()
    return _CACHE["globex"]


def business_principal(
    tenant_id: str | None = None, user_id: str = "asha", roles: tuple[str, ...] = ALL_BUSINESS
) -> Principal:
    """A principal of `tenant_id` (default: the standard TEST company's id)."""
    return IdentityResolver(RoleCatalog(ROLES)).resolve(
        user_id=user_id, tenant_id=tenant_id or std().tenant, roles=roles, auth_method="token"
    )


Failure = Literal[
    "unavailable",  # ErpUnavailableError
    "timeout",  # ErpTimeoutError (the adapter's own statement timeout)
    "hang",  # never answers (the service deadline must fire)
    "malformed",  # records with a required field missing
    "tenant_leak",  # records that claim another tenant
    "rule_not_verified",  # ErpRuleNotVerifiedError
    "not_configured",  # ErpNotConfiguredError
    "crash",  # an unexpected exception
    "swallow_cancel",  # ignores the deadline's cancellation once, then answers (Phase 13)
    "swallow_cancel_always",  # ignores every cancellation (only an outer deadline ends it)
]

_METHODS = (
    "find_customers", "get_customer", "customer_outstanding", "open_invoices", "daily_sales",
    "sales_by_day", "find_products", "get_product", "find_warehouses", "get_warehouse",
    "product_stock", "warehouse_stock", "low_stock", "customer_count", "customers_billed",
)  # fmt: skip


class ErpFaults:
    """The real ERP port with call counting and injectable failures (no data of its own)."""

    def __init__(
        self,
        inner: ErpReadPort,
        *,
        failure: Failure | None = None,
        rules: frozenset[str] | None = None,
    ) -> None:
        self._inner = inner
        self.failure = failure
        self.verified_rules = inner.verified_rules if rules is None else rules
        self.calls: list[str] = []
        # The next N calls fail with ErpUnavailableError, then calls succeed.
        self.transient_failures = 0
        self._swallowed = False
        for method in _METHODS:
            setattr(self, method, self._wrap(method))

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def test_data(self) -> bool:
        return self._inner.test_data

    def gate_state(self) -> Any:
        return getattr(self._inner, "gate_state", lambda: None)()

    async def aclose(self) -> None:
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()

    def _wrap(self, method: str) -> Any:
        async def call(ctx: BusinessContext, *args: Any, **kwargs: Any) -> Any:
            if self._swallows():
                # The real read runs alongside the swallowed wait, so the answer is ready
                # when the wait returns: real I/O started only after the deadline would race
                # the tool's own deadline (query timeout + 2 s) on a busy host.
                fetch = asyncio.ensure_future(getattr(self._inner, method)(ctx, *args, **kwargs))
                try:
                    await self._enter(method)
                    return self._out(await fetch)
                finally:
                    fetch.cancel()  # no-op once it has finished
            await self._enter(method)
            return self._out(await getattr(self._inner, method)(ctx, *args, **kwargs))

        return call

    def _swallows(self) -> bool:
        return self.failure == "swallow_cancel_always" or (
            self.failure == "swallow_cancel" and not self._swallowed
        )

    async def _enter(self, method: str) -> None:
        self.calls.append(method)
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise ErpUnavailableError("test: ERP briefly unreachable")
        if self.failure == "unavailable":
            raise ErpUnavailableError("test: ERP unreachable")
        if self.failure == "timeout":
            raise ErpTimeoutError("test: statement timeout")
        if self.failure == "rule_not_verified":
            raise ErpRuleNotVerifiedError(f"test: {method} not mapped")
        if self.failure == "not_configured":
            raise ErpNotConfiguredError("test: not configured")
        if self.failure == "crash":
            raise RuntimeError("test: unexpected adapter bug")
        if self.failure == "hang":
            await asyncio.Event().wait()  # until the service deadline cancels it
        if self.failure == "swallow_cancel_always" or (
            self.failure == "swallow_cancel" and not self._swallowed
        ):
            # A badly behaved driver: catches the cancellation and answers anyway.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()
            self._swallowed = True

    def _out(self, value: Any) -> Any:
        if value is None:
            return None
        rows = value if isinstance(value, list | tuple) else [value]
        rows = [dict(r if isinstance(r, dict) else r.model_dump()) for r in rows]
        if self.failure == "malformed":
            # Drop the first required field after the tenant: never a valid record.
            rows = [dict(list(r.items())[:1] + list(r.items())[2:]) for r in rows] or [
                {"unexpected": "shape"}
            ]
        if self.failure == "tenant_leak":
            rows = [{**r, "tenant_id": "999999999"} for r in rows]
        return rows if isinstance(value, list | tuple) else rows[0]


def erp_port(failure: Failure | None = None, rules: frozenset[str] | None = None) -> ErpFaults:
    need_erp()
    return ErpFaults(erp_adapter(), failure=failure, rules=rules)


class RecordingAuditor:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> int:
        self.events.append(event)
        return len(self.events)


def make_service(
    *,
    failure: Failure | None = None,
    adapter: ErpFaults | None = None,
    timezone: str | None = TZ,
    timeout: float = 5.0,
    max_rows: int = 200,
    auditor: RecordingAuditor | None = None,
    scopes: WarehouseScopeResolver | None = None,
    rules: frozenset[str] | None = None,
) -> tuple[BusinessReadService, ErpFaults]:
    port = adapter or erp_port(failure, rules)
    settings = ErpSettings(
        adapter="postgres", business_timezone=timezone, query_timeout_seconds=timeout,
        max_rows=max_rows,
    )  # fmt: skip
    service = BusinessReadService(
        cast(ErpReadPort, port), settings=settings, clock=ClockService(now_utc=lambda: FIXED_NOW),
        auditor=auditor, scope_resolver=scopes,
    )  # fmt: skip
    return service, port


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


__all__ = [
    "ALL_BUSINESS",
    "ROLES",
    "TODAY",
    "TZ",
    "ErpFaults",
    "Failure",
    "RecordingAuditor",
    "business_principal",
    "erp_port",
    "ext",
    "globex",
    "make_service",
    "need_erp",
    "replica_contract",
    "run",
    "std",
]
