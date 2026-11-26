"""InvenLogic readers on the TEST replica (docker/erp-test): the safety properties.

* RLS: the read-only role sees no row without the tenant setting, and only its own
  company's rows with it, whatever a query asks for;
* the security gate refuses a role that is not read-only and a contract that disagrees
  with the live catalog;
* a status outside the contract's closed list fails the read (MALFORMED_RESPONSE);
* the outstanding rule: opening bills count, advances and unapplied credit notes reduce
  it, undone allocations do not count as paid;
* a tenant id that is not an InvenLogic company id is refused.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]
import pytest

from agentic_platform.business.context import ALL_WAREHOUSES, BusinessContext
from agentic_platform.business.ports import ErpSafetyViolationError
from agentic_platform.business.results import BusinessStatus, Reason
from tests.business_helpers import business_principal, make_service
from tests.erp_db import (
    ErpCompany,
    erp_adapter,
    erp_settings,
    reader_urls,
    replica_contract,
    requires_erp,
)

pytestmark = requires_erp
S = BusinessStatus


def _ctx(tenant: str) -> BusinessContext:
    return BusinessContext(tenant_id=tenant, user_id="u", warehouse_scope=ALL_WAREHOUSES)


async def _reader_rows(database: str, sql: str, setting: str | None) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(reader_urls()[database])
    try:
        async with conn.transaction(readonly=True):
            if setting is not None:
                await conn.execute("SELECT set_config('invenlogic.company_id', $1, true)", setting)
            return list(await conn.fetch(sql))
    finally:
        await conn.close()


def test_rls_hides_every_row_without_and_other_companies_with_the_setting() -> None:
    a, b = ErpCompany(), ErpCompany()
    a.customer("RLS A (TEST)")
    b.customer("RLS B (TEST)")
    sql = "SELECT company_id FROM customer"  # deliberately without a company filter
    assert asyncio.run(_reader_rows("master_data", sql, None)) == []
    seen = {r["company_id"] for r in asyncio.run(_reader_rows("master_data", sql, a.tenant))}
    assert seen == {a.company_id}


def test_the_reader_role_cannot_write() -> None:
    c = ErpCompany()

    async def write(read_write: bool) -> None:
        conn = await asyncpg.connect(reader_urls()["master_data"])
        try:
            if read_write:
                await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
            await conn.execute(
                "INSERT INTO uom (company_id, created_by, updated_by, code, name)"
                " VALUES ($1, 1, 1, 'x', 'x')",
                c.company_id,
            )
        finally:
            await conn.close()

    # Two layers: the role's sessions are read-only by default
    # (default_transaction_read_only=on) ...
    with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        asyncio.run(write(read_write=False))
    # ... and a session that turns that off still has no write privilege.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        asyncio.run(write(read_write=True))


def test_the_gate_refuses_a_superuser_connection() -> None:
    admin = os.environ["TEST_ERP_ADMIN_URL"].rsplit("/", 1)[0]
    urls = {k: f"{admin}/{v.rsplit('/', 1)[1]}" for k, v in reader_urls().items()}
    adapter = erp_adapter(settings=erp_settings(database_urls=json.dumps(urls)))
    with pytest.raises(ErpSafetyViolationError, match="superuser"):
        asyncio.run(adapter.find_customers(_ctx(ErpCompany().tenant), "x", 1))
    assert adapter.gate_state() == "refused"


def test_the_gate_refuses_a_contract_that_disagrees_with_the_catalog() -> None:
    raw = replica_contract().model_dump(mode="json")
    columns = raw["capabilities"]["daily_sales"]["columns"]
    next(c for c in columns if c["column"] == "grand_total")["data_type"] = "numeric(14,2)"
    contract = replica_contract(capabilities=raw["capabilities"])
    adapter = erp_adapter(contract)
    with pytest.raises(ErpSafetyViolationError, match="grand_total"):
        asyncio.run(adapter.daily_sales(_ctx(ErpCompany().tenant), date(2026, 10, 1)))


def test_a_draft_capability_has_no_reader() -> None:
    raw = replica_contract().model_dump(mode="json")
    raw["capabilities"]["daily_sales"]["status"] = "DRAFT"
    service, _ = make_service(adapter=None)
    from tests.business_helpers import ErpFaults

    port = ErpFaults(erp_adapter(replica_contract(capabilities=raw["capabilities"])))
    service, _ = make_service(adapter=port)
    c = ErpCompany()
    c.sales_day(date(2026, 10, 1), "100.00", 1)
    result = asyncio.run(service.daily_sales(business_principal(c.tenant)))
    assert (result.status, result.reason) == (S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)


def test_an_unknown_status_fails_the_read_instead_of_being_guessed() -> None:
    c = ErpCompany()
    rahul = c.customer("Status Probe (TEST)")
    bill = c.invoice(rahul, "100.00", on=date(2026, 9, 1), due=date(2026, 9, 30))
    c.sales_day(date(2026, 10, 1), "50.00", 1)
    service, _ = make_service()
    who = business_principal(c.tenant)
    assert asyncio.run(service.daily_sales(who)).status is S.SUCCESS
    c.set_invoice_status(bill, "PARTLY_POSTED")  # a value the contract does not list
    for result in (
        asyncio.run(service.customer_outstanding(who, customer_ref=rahul)),
        asyncio.run(service.customer_open_invoices(who, customer_ref=rahul)),
    ):
        assert (result.status, result.reason) == (S.DEPENDENCY_ERROR, Reason.MALFORMED_RESPONSE)
        assert result.data is None
    # Sales of a day without the odd invoice are still answered; a range holding it is not.
    assert asyncio.run(service.daily_sales(who)).status is S.SUCCESS
    week = asyncio.run(service.daily_sales(who, day=date(2026, 9, 1)))
    assert (week.status, week.reason) == (S.DEPENDENCY_ERROR, Reason.MALFORMED_RESPONSE)


def test_outstanding_rule_opening_bills_advances_credit_notes_and_undone_payments() -> None:
    c = ErpCompany()
    who = c.customer("Rule Probe (TEST)")
    inv = c.invoice(who, "10000.00", on=date(2026, 9, 1), due=date(2026, 9, 30), paid="4000.00")
    c.undone_payment(who, inv, "3000.00", date(2026, 9, 5))  # undone: not paid
    c.opening_bill(who, "2500.00", on=date(2026, 4, 1), due=date(2026, 4, 30),
                   number="OB-1", paid="500.00")  # fmt: skip
    c.receipt(who, "700.00", on=date(2026, 9, 10), unallocated="700.00")  # an advance
    c.credit_note(who, "300.00", on=date(2026, 9, 12), unapplied="300.00")
    c.invoice(who, "999.00", on=date(2026, 9, 2), status="CANCELLED")
    c.invoice(who, "888.00", on=date(2026, 9, 3), status="DRAFT")
    service, _ = make_service()
    principal = business_principal(c.tenant)
    out = asyncio.run(service.customer_outstanding(principal, customer_ref=who))
    assert out.status is S.SUCCESS and out.data is not None
    # (10000 - 4000) + (2500 - 500) - 700 - 300 = 7000
    assert out.data.outstanding.amount == Decimal("7000.00")
    bills = asyncio.run(service.customer_open_invoices(principal, customer_ref=who))
    assert bills.data is not None
    assert {(i.invoice.display_name, i.balance.amount) for i in bills.data.invoices} == {
        (inv and "ID-" + inv[3:], Decimal("6000.00")), ("OB-1", Decimal("2000.00"))}  # fmt: skip


def test_a_tenant_that_is_not_a_company_id_is_refused() -> None:
    service, _ = make_service()
    result = asyncio.run(
        service.customer_outstanding(business_principal("acme"), customer_name="Rahul")
    )
    assert (result.status, result.reason) == (S.DEPENDENCY_ERROR, "ERP_SAFETY_VIOLATION")
