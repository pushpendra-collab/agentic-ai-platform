"""The same contract suite against the REAL ERP. Skipped until Q1 is closed.

It runs only when ALL of these are set (OPERATIONS_RUNBOOK § 19):

* `ERP_CONTRACT_TEST_CONTRACT`: the VERIFIED ERP contract (JSON, no secrets), with
  `reader_set` and `databases` (InvenLogic: docs/erp/invenlogic_contract.json completed);
* `ERP_CONTRACT_TEST_FIXTURES`: the owner-supplied expectations for dedicated TEST
  records in the ERP (JSON with the `ContractFixtures` fields, tests/erp_contract/fixtures.py);
* `ERP_DATABASE_URLS`: the read-only credentials (JSON key → URL), loaded from the secret
  store for this run only;
* `python -m scripts.erp_readiness --contract ... --live` exited 0 in the same session.

Every statement is a read in a read-only transaction as the read-only role; nothing is
written to the ERP. Without these inputs every test here is skipped, naming what is missing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from agentic_platform.business.erp_contract import Verification, load_contract
from agentic_platform.business.service import BusinessReadService
from agentic_platform.core.config import ErpSettings
from agentic_platform.security.identity import Principal
from agentic_platform.services.clock import ClockService
from tests.business_helpers import ErpFaults, business_principal
from tests.erp_contract.fixtures import fixtures_from_json
from tests.erp_contract.suite import SUITE, ContractTarget, Failure, Scope
from tests.erp_contract.test_invenlogic_contract import LIMITED_USER, _Scopes
from tests.erp_db import erp_adapter

REQUIRED = ("ERP_CONTRACT_TEST_CONTRACT", "ERP_CONTRACT_TEST_FIXTURES", "ERP_DATABASE_URLS")


def _missing() -> list[str]:
    return [name for name in REQUIRED if not os.environ.get(name)]


class RealErpTarget:
    def __init__(self) -> None:
        self.contract = load_contract(os.environ["ERP_CONTRACT_TEST_CONTRACT"])
        if self.contract.status is not Verification.VERIFIED or not self.contract.databases:
            pytest.skip("real ERP contract suite: the contract is not VERIFIED multi-database")
        assert self.contract.invoice_statuses is not None and self.contract.time is not None
        self.statuses = self.contract.invoice_statuses
        raw = json.loads(Path(os.environ["ERP_CONTRACT_TEST_FIXTURES"]).read_text("utf-8"))
        self.fixtures = fixtures_from_json(raw)
        tls = self.contract.access.tls_mode if self.contract.access else "verify-full"
        self.settings = ErpSettings(
            adapter="postgres",
            database_urls=os.environ["ERP_DATABASE_URLS"],
            ssl_mode=os.environ.get("ERP_SSL_MODE") or tls,
            business_timezone=self.contract.time.business_timezone,
            read_retry_backoff_seconds=0.0,
        )

    def _service(self, port: object, now: datetime | None) -> BusinessReadService:
        f = self.fixtures
        return BusinessReadService(
            port,  # type: ignore[arg-type]
            settings=self.settings,
            clock=ClockService(now_utc=lambda: now or f.now),
            scope_resolver=_Scopes(f.warehouse_ref),
        )

    def service(self, *, now: datetime | None = None) -> BusinessReadService:
        return self._service(erp_adapter(self.contract, self.settings), now)

    def failing_service(self, failure: Failure) -> BusinessReadService:
        mode = {"connection": "unavailable"}.get(failure, failure)
        port = ErpFaults(erp_adapter(self.contract, self.settings), failure=mode)  # type: ignore[arg-type]
        return self._service(port, None)

    def principal(self, *, tenant: str | None = None, scope: Scope = "all") -> Principal:
        tenant_id = tenant or self.fixtures.tenant
        if scope == "limited":
            return business_principal(
                tenant_id, LIMITED_USER, ("member", "sales_viewer", "stock_viewer_limited")
            )
        return business_principal(tenant_id)


@pytest.mark.parametrize("check", SUITE, ids=[c.__name__ for c in SUITE])
def test_real_erp_honours_the_contract(check: Callable[[ContractTarget], None]) -> None:
    missing = _missing()
    if missing:
        pytest.skip(f"real ERP contract suite: Q1 inputs missing ({', '.join(missing)})")
    check(RealErpTarget())
