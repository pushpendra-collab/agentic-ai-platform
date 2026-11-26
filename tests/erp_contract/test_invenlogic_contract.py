"""The ERP adapter contract suite against the InvenLogic readers on the TEST replica.

Real SQL through the read-only role, the security gate and RLS (docker/erp-test,
tests/erp_db.py). Replaces the fake-ERP run (removed 2026-10-30). Failures are real where
they can be: "unavailable" and "connection" point the adapter at a closed port; a statement
timeout and malformed rows are injected by the `ErpFaults` wrapper (no data of its own).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import cast

import pytest

from agentic_platform.business.capabilities import BusinessPermission
from agentic_platform.business.context import (
    ALL_WAREHOUSES,
    NO_WAREHOUSES,
    ScopeKind,
    WarehouseScope,
)
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.business.service import BusinessReadService
from agentic_platform.security.identity import Principal
from agentic_platform.services.clock import ClockService
from tests.business_helpers import ErpFaults, business_principal
from tests.business_helpers import Failure as FaultFailure
from tests.erp_contract.fixtures import ContractFixtures, replica_fixtures
from tests.erp_contract.suite import SUITE, ContractTarget, Failure, Scope
from tests.erp_db import DATABASES, erp_adapter, erp_settings, replica_contract, requires_erp

pytestmark = requires_erp

LIMITED_USER = "limited_user"
CLOSED_PORT = "postgresql://agentic_erp_reader:x@127.0.0.1:1"


class _Scopes:
    """All warehouses with `business.warehouse.all`; the limited user sees one warehouse."""

    def __init__(self, listed: str) -> None:
        self._listed = listed

    def resolve(self, principal: Principal) -> WarehouseScope:
        if principal.has(BusinessPermission.WAREHOUSE_ALL):
            return ALL_WAREHOUSES
        if principal.user_id == LIMITED_USER:
            return WarehouseScope(kind=ScopeKind.LISTED, warehouse_refs=frozenset({self._listed}))
        return NO_WAREHOUSES


class InvenLogicTarget:
    _fixtures: ContractFixtures | None = None

    def __init__(self) -> None:
        if InvenLogicTarget._fixtures is None:
            InvenLogicTarget._fixtures = replica_fixtures()
        self.fixtures = InvenLogicTarget._fixtures
        contract = replica_contract()
        assert contract.invoice_statuses is not None
        self.contract = contract
        self.statuses = contract.invoice_statuses

    def _service(self, port: ErpReadPort, now: datetime | None) -> BusinessReadService:
        f = self.fixtures
        settings = erp_settings(
            business_timezone=f.timezone, query_timeout_seconds=5.0,
            read_retry_backoff_seconds=0.0,
        )  # fmt: skip
        return BusinessReadService(
            port,
            settings=settings,
            clock=ClockService(now_utc=lambda: now or f.now),
            scope_resolver=_Scopes(f.warehouse_ref),
        )

    def service(self, *, now: datetime | None = None) -> BusinessReadService:
        return self._service(erp_adapter(self.contract), now)

    def failing_service(self, failure: Failure) -> BusinessReadService:
        if failure in ("unavailable", "connection"):
            # A real connection failure: every database URL points at a closed port.
            closed = {key: f"{CLOSED_PORT}/{name}" for key, name in DATABASES.items()}
            settings = erp_settings(database_urls=json.dumps(closed), connect_timeout_seconds=2)
            return self._service(erp_adapter(self.contract, settings), None)
        # Only "timeout" and "malformed" remain here; both are `ErpFaults` failures.
        faults = ErpFaults(erp_adapter(self.contract), failure=cast(FaultFailure, failure))
        return self._service(cast(ErpReadPort, faults), None)

    def principal(self, *, tenant: str | None = None, scope: Scope = "all") -> Principal:
        tenant_id = tenant or self.fixtures.tenant
        if scope == "limited":
            return business_principal(
                tenant_id, LIMITED_USER, ("member", "sales_viewer", "stock_viewer_limited")
            )
        return business_principal(tenant_id)


@pytest.mark.parametrize("check", SUITE, ids=[c.__name__ for c in SUITE])
def test_invenlogic_honours_the_contract(check: Callable[[ContractTarget], None]) -> None:
    check(InvenLogicTarget())


def test_the_suite_covers_the_brief() -> None:
    names = {c.__name__ for c in SUITE}
    assert len(SUITE) == 18 and len(names) == 18
