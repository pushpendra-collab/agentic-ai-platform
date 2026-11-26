"""Fixtures of the ERP contract test suite (Phase 14). TEST data only.

* `TEST_CONTRACT`: a complete contract for a throw-away TEST schema (`erp_test`), used by
  the contract unit tests and by the gate's integration test on a throw-away PostgreSQL.
  It describes no real ERP: every name, rule and confirmation is invented for tests and
  marked TEST. A real contract comes from the ERP owner (Q1).
* `ContractFixtures` + `replica_fixtures()`: the records the suite expects. On the InvenLogic
  TEST replica (tests/erp_db.py) they are inserted as real rows; for the real ERP the owner
  supplies the same expectations for dedicated test records (`ERP_CONTRACT_TEST_FIXTURES`,
  JSON). The fake ERP was removed on 2026-10-30.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

_TEST_CONFIRMATION = {
    "confirmed_by": "TEST fixture (no ERP owner)",
    "confirmed_on": "2026-10-05",
    "reference": "TEST-ONLY",
}


def _column(
    relation: str, column: str, data_type: str, role: str, record_field: str | None = None
) -> dict[str, Any]:
    return {
        "relation": relation,
        "column": column,
        "data_type": data_type,
        "role": role,
        "record_field": record_field,
    }


def _mapping(
    capability: str,
    permission: str,
    sources: list[str],
    columns: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "sources": [
            {"schema_name": s.split(".")[0], "name": s.split(".")[1], "kind": "table"}
            for s in sources
        ],
        "columns": columns,
        "joins": extra.get("joins", []),
        "tenant_boundary": "tenant_code = principal tenant (TEST)",
        "warehouse_boundary": extra.get("warehouse_boundary", "not warehouse-scoped (TEST)"),
        "status_filter": extra.get("status_filter", []),
        "date_filter": extra.get("date_filter", "none (TEST)"),
        "timezone_behavior": "dates are business dates in the contract time zone (TEST)",
        "null_behavior": "a null amount is a malformed row (TEST)",
        "ambiguity_behavior": "several name matches: AMBIGUOUS with candidates (TEST)",
        "not_found_behavior": "no row: NOT_FOUND (TEST)",
        "unavailable_behavior": "connection/timeout: UNAVAILABLE (TEST)",
        "rules": extra.get("rules", []),
        "permission": permission,
        "status": "VERIFIED",
        "confirmation": _TEST_CONFIRMATION,
    }


C, INV, S, W, P, K = (
    "erp_test.customer",
    "erp_test.invoice",
    "erp_test.daily_sales",
    "erp_test.warehouse",
    "erp_test.product",
    "erp_test.stock",
)
_TENANT = [_column(r, "tenant_code", "text", "tenant") for r in (C, INV, S, W, P, K)]


def _tenant(relation: str) -> dict[str, Any]:
    return next(c for c in _TENANT if c["relation"] == relation)


def _customer_columns() -> list[dict[str, Any]]:
    return [
        _tenant(C),
        _column(C, "customer_ref", "text", "field", "ref"),
        _column(C, "display_name", "text", "field", "display_name"),
    ]


def _stock_columns() -> list[dict[str, Any]]:
    return [
        _tenant(K),
        _tenant(P),
        _tenant(W),
        _column(K, "product_ref", "text", "field", "product_ref"),
        _column(P, "display_name", "text", "field", "product_name"),
        _column(K, "warehouse_code", "text", "warehouse"),
        _column(W, "warehouse_code", "text", "field", "warehouse_ref"),
        _column(W, "display_name", "text", "field", "warehouse_name"),
        _column(K, "quantity_available", "numeric(14,3)", "field", "quantity"),
    ]


TEST_CONTRACT: dict[str, Any] = {
    "contract_version": "TEST-1",
    "status": "VERIFIED",
    "ownership": {
        "erp_owner": "TEST owner",
        "application_owner": "TEST application owner",
        "is_intended_erp": True,
        "confirmation": _TEST_CONFIRMATION,
    },
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "erp_contract_test",
        "postgres_major_version": 16,
        "schema_version": "TEST-1",
    },
    "access": {
        "read_only_role": "erp_reader_test",
        "delivery": "secret_store",
        "tls_mode": "require",
    },
    "tenant_isolation": {
        "mechanism": "column",
        "column": "tenant_code",
        "tenant_value": "platform_tenant_id",
        "confirmation": _TEST_CONFIRMATION,
    },
    "warehouse_isolation": {
        "mechanism": "column",
        "column": "warehouse_code",
        "ref_column": "warehouse_code",
        "out_of_scope": "not_found",
        "confirmation": _TEST_CONFIRMATION,
    },
    "time": {
        "erp_timestamp_storage": "timestamptz",
        "business_timezone": "Asia/Kolkata",
        "day_boundary": "midnight_local",
        "week_start": "monday",
        "month": "calendar",
        "confirmation": _TEST_CONFIRMATION,
    },
    "rules": {
        rule: {"definition": f"TEST definition of {rule}", "confirmation": _TEST_CONFIRMATION}
        for rule in (
            "business_ownership",
            "customer_identification",
            "sales_calculation",
            "outstanding_balance",
            "stock_calculation",
            "date_period",
            "invoice_status_semantics",
            "overdue_by_due_date",
        )
    },
    "invoice_statuses": {
        "open": ["OPEN"],
        "partially_paid": ["PART_PAID"],
        "paid": ["PAID"],
        "cancelled": ["CANCELLED"],
        "draft": ["DRAFT"],
    },
    "permission_expectations": {
        "platform_permissions_authoritative": True,
        "notes": "TEST",
        "confirmation": _TEST_CONFIRMATION,
    },
    "capabilities": {
        "customer_outstanding": _mapping(
            "customer_outstanding",
            "business.customer.read",
            [C, INV],
            [
                *_customer_columns(),
                _tenant(INV),
                _column(INV, "customer_ref", "text", "join"),
                _column(INV, "balance_amount", "numeric(14,2)", "field", "amount"),
                _column(INV, "currency", "text", "field", "currency"),
                _column(INV, "status", "text", "status"),
            ],
            joins=["invoice.customer_ref = customer.customer_ref within one tenant"],
            status_filter=["OPEN", "PART_PAID"],
            rules=["outstanding_balance"],
        ),
        "customer_open_invoices": _mapping(
            "customer_open_invoices",
            "business.customer.read",
            [C, INV],
            [
                *_customer_columns(),
                _tenant(INV),
                _column(INV, "invoice_ref", "text", "field", "ref"),
                _column(INV, "invoice_no", "text", "field", "number"),
                _column(INV, "customer_ref", "text", "field", "customer_ref"),
                _column(INV, "balance_amount", "numeric(14,2)", "field", "balance"),
                _column(INV, "currency", "text", "field", "currency"),
                _column(INV, "due_date", "date", "field", "due_on"),
                _column(INV, "status", "text", "status"),
            ],
            status_filter=["OPEN", "PART_PAID"],
            rules=["invoice_status_semantics"],
        ),
        "daily_sales": _mapping(
            "daily_sales",
            "business.sales.read",
            [S],
            [
                _tenant(S),
                _column(S, "business_date", "date", "field", "business_date"),
                _column(S, "posted_total", "numeric(14,2)", "field", "total"),
                _column(S, "currency", "text", "field", "currency"),
                _column(S, "document_count", "integer", "field", "document_count"),
            ],
            date_filter="business_date = the resolved business day (TEST)",
            rules=["sales_calculation", "date_period"],
        ),
        "product_stock": _mapping(
            "product_stock",
            "business.inventory.read",
            [K, P, W],
            _stock_columns(),
            warehouse_boundary="stock.warehouse_code within the principal's scope (TEST)",
            rules=["stock_calculation"],
        ),
        "warehouse_stock": _mapping(
            "warehouse_stock",
            "business.inventory.read",
            [K, P, W],
            _stock_columns(),
            warehouse_boundary="stock.warehouse_code within the principal's scope (TEST)",
            rules=["stock_calculation"],
        ),
        "low_stock_products": _mapping(
            "low_stock_products",
            "business.inventory.read",
            [K, P, W],
            [*_stock_columns(), _column(K, "reorder_level", "numeric(14,3)", "field", "threshold")],
            warehouse_boundary="stock.warehouse_code within the principal's scope (TEST)",
            rules=["stock_calculation"],
        ),
        "customer_count": _mapping(
            "customer_count",
            "business.customer.read",
            [C, S],
            [
                _tenant(C),
                _tenant(S),
                _column(C, "customer_ref", "text", "field", "active_count"),
                _column(S, "business_date", "date", "date"),
                _column(S, "document_count", "integer", "field", "invoice_count"),
            ],
            date_filter="customers billed: business_date in the resolved range (TEST)",
            rules=["customer_identification", "sales_calculation"],
        ),
    },
}

# DDL of the TEST schema the contract above describes (throw-away databases only).
TEST_SCHEMA_DDL = """
CREATE SCHEMA erp_test;
CREATE TABLE erp_test.customer (tenant_code text NOT NULL, customer_ref text NOT NULL,
    display_name text NOT NULL, phone text, email text);
CREATE TABLE erp_test.invoice (tenant_code text NOT NULL, invoice_ref text NOT NULL,
    invoice_no text NOT NULL, customer_ref text NOT NULL, balance_amount numeric(14,2),
    currency text NOT NULL, due_date date, status text NOT NULL);
CREATE TABLE erp_test.daily_sales (tenant_code text NOT NULL, business_date date NOT NULL,
    posted_total numeric(14,2) NOT NULL, currency text NOT NULL, document_count integer);
CREATE TABLE erp_test.warehouse (tenant_code text NOT NULL, warehouse_code text NOT NULL,
    display_name text NOT NULL);
CREATE TABLE erp_test.product (tenant_code text NOT NULL, product_ref text NOT NULL,
    display_name text NOT NULL);
CREATE TABLE erp_test.stock (tenant_code text NOT NULL, product_ref text NOT NULL,
    warehouse_code text NOT NULL, quantity_available numeric(14,3) NOT NULL,
    reorder_level numeric(14,3));
"""


def contract_dict(**changes: Any) -> dict[str, Any]:
    """A deep copy of TEST_CONTRACT with top-level keys replaced."""
    contract = copy.deepcopy(TEST_CONTRACT)
    contract.update(changes)
    return contract


# =============================================================================================
# the suite's expectations
# =============================================================================================


@dataclass(frozen=True)
class ContractFixtures:
    """What the contract suite expects of an adapter, in platform terms."""

    tenant: str
    other_tenant: str
    customer_name: str  # resolves to exactly one customer
    customer_ref: str
    outstanding: Decimal  # that customer's outstanding amount
    open_invoice_refs: frozenset[str]  # open invoices (cancelled and paid excluded)
    paid_invoice_ref: str
    cancelled_invoice_ref: str
    partial_invoice_ref: str
    partial_total: Decimal
    partial_paid: Decimal
    partial_balance: Decimal
    ambiguous_name: str  # matches several customers
    ambiguous_refs: frozenset[str]
    missing_name: str  # matches nobody
    empty_customer_ref: str  # a customer with no open invoice
    today: date  # business date at `now`
    now: datetime
    today_sales_total: Decimal
    today_sales_documents: int
    previous_day_sales_total: Decimal
    late_evening_utc: datetime  # still the previous business day in the contract zone
    just_after_midnight_utc: datetime  # already `today` in the contract zone
    warehouse_ref: str  # in the limited principal's scope
    other_warehouse_ref: str  # same tenant, outside that scope
    product_ref: str
    stock_in_warehouse: Decimal  # product quantity in `warehouse_ref`
    # Other tenant, same warehouse/product refs. None: refs are unique across tenants (the
    # InvenLogic ids are), so the other tenant must not find `warehouse_ref` at all.
    other_tenant_stock_same_ref: Decimal | None
    timezone: str


def replica_fixtures() -> ContractFixtures:
    """The suite's records as real rows of two fresh companies on the InvenLogic TEST
    replica (tests/erp_db.py), and the expectations that follow from them."""
    from tests.erp_db import ErpCompany  # needs the replica; imported only when used

    today = date(2026, 10, 1)
    main, other = ErpCompany(), ErpCompany()
    asha = main.customer("Asha Traders (TEST)")
    bharat = main.customer("Bharat Stores (TEST)")
    bharat_north = main.customer("Bharat Stores North (TEST)")
    empty = main.customer("Empty Ledger (TEST)")
    issued = date(2026, 9, 1)
    unpaid = main.invoice(asha, "1000.00", on=issued, due=date(2026, 9, 15), number="CT-INV-1")
    partial = main.invoice(asha, "5000.00", on=issued, due=date(2026, 10, 20),
                           number="CT-INV-2", paid="2000.00")  # fmt: skip
    paid = main.invoice(asha, "800.00", on=issued, due=date(2026, 9, 10), number="CT-INV-3",
                        paid="800.00")  # fmt: skip
    cancelled = main.invoice(asha, "700.00", on=issued, due=date(2026, 9, 20),
                             number="CT-INV-4", status="CANCELLED")  # fmt: skip
    main.sales_day(today, "12345.50", 3)
    main.sales_day(date(2026, 9, 30), "999.00", 1)
    widget = main.product("Contract Widget (TEST)")
    one, two = main.warehouse("Godown One (TEST)"), main.warehouse("Godown Two (TEST)")
    main.stock(widget, one, "7")
    main.stock(widget, two, "4")
    other.customer("Other Tenant Only (TEST)")
    other_widget = other.product("Contract Widget (TEST)")
    other.stock(other_widget, other.warehouse("Other Godown (TEST)"), "99")
    return ContractFixtures(
        tenant=main.tenant,
        other_tenant=other.tenant,
        customer_name="Asha Traders",
        customer_ref=asha,
        outstanding=Decimal("4000.00"),
        open_invoice_refs=frozenset({unpaid, partial}),
        paid_invoice_ref=paid,
        cancelled_invoice_ref=cancelled,
        partial_invoice_ref=partial,
        partial_total=Decimal("5000.00"),
        partial_paid=Decimal("2000.00"),
        partial_balance=Decimal("3000.00"),
        ambiguous_name="Bharat",
        ambiguous_refs=frozenset({bharat, bharat_north}),
        missing_name="Zed Nobody",
        empty_customer_ref=empty,
        today=today,
        now=datetime(2026, 10, 1, 6, 30, tzinfo=UTC),
        today_sales_total=Decimal("12345.50"),
        today_sales_documents=3,
        previous_day_sales_total=Decimal("999.00"),
        late_evening_utc=datetime(2026, 9, 30, 18, 0, tzinfo=UTC),  # 23:30 IST, 30 Sep
        just_after_midnight_utc=datetime(2026, 9, 30, 19, 0, tzinfo=UTC),  # 00:30 IST, 1 Oct
        warehouse_ref=one,
        other_warehouse_ref=two,
        product_ref=widget,
        stock_in_warehouse=Decimal("7"),
        other_tenant_stock_same_ref=None,
        timezone="Asia/Kolkata",
    )


def fixtures_from_json(raw: dict[str, Any]) -> ContractFixtures:
    """Owner-supplied expectations for the real ERP's dedicated test records
    (`ERP_CONTRACT_TEST_FIXTURES`): the `ContractFixtures` fields as JSON."""
    values: dict[str, Any] = dict(raw)
    for key in ("outstanding", "partial_total", "partial_paid", "partial_balance",
                "today_sales_total", "previous_day_sales_total", "stock_in_warehouse"):  # fmt: skip
        values[key] = Decimal(str(values[key]))
    if values.get("other_tenant_stock_same_ref") is not None:
        values["other_tenant_stock_same_ref"] = Decimal(str(values["other_tenant_stock_same_ref"]))
    for key in ("open_invoice_refs", "ambiguous_refs"):
        values[key] = frozenset(values[key])
    values["today"] = date.fromisoformat(values["today"])
    for key in ("now", "late_evening_utc", "just_after_midnight_utc"):
        values[key] = datetime.fromisoformat(values[key])
    values["today_sales_documents"] = int(values["today_sales_documents"])
    return ContractFixtures(**values)
