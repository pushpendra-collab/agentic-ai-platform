"""The InvenLogic TEST replica for business-layer tests (replaces the fake ERP, 2026-10-30).

Every business test runs the real read path: `BusinessReadService` → `PostgresErpAdapter`
→ the InvenLogic readers (`adapters/invenlogic.py`) → the read-only role → a PostgreSQL 18
replica of the ERP's tables (docker/erp-test). There is no in-memory ERP any more.

    docker compose -f docker/erp-test/compose.yml up -d
    $env:TEST_ERP_ADMIN_URL="postgresql://erp_test_admin:erp_test_admin_dev@127.0.0.1:5434/postgres"
    $env:TEST_ERP_READER_URL="postgresql://agentic_erp_reader:erp_reader_dev@127.0.0.1:5434"

Without both variables every test that needs the replica is skipped (`requires_erp`).

Isolation: each `ErpCompany` is a new InvenLogic company id, so tests never see each
other's rows (the readers filter company_id and RLS is forced). Rows are inserted by the
replica's admin (a superuser, which bypasses RLS); the agent only ever connects as
`agentic_erp_reader`. All names end with "(TEST)" or are test-local; nothing is real.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import threading
import time
from collections.abc import Coroutine, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

import asyncpg  # type: ignore[import-untyped]
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from agentic_platform.business.adapters.invenlogic import register_invenlogic_readers
from agentic_platform.business.adapters.postgres import (
    PostgresErpAdapter,
    ReadOnlyErpDatabase,
    erp_database_settings,
)
from agentic_platform.business.erp_contract import ErpContract
from agentic_platform.core.config import ErpSettings
from agentic_platform.db.engine import engine_options

T = TypeVar("T")

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILE = ROOT / "docs" / "erp" / "invenlogic_contract.json"
DATABASES = {"master_data": "master_data_db", "sales": "sales_db", "inventory": "inventory_db"}
SCHEMA_VERSIONS = {"master_data": "V31", "sales": "V36", "inventory": "V24"}
ADMIN_ENV, READER_ENV = "TEST_ERP_ADMIN_URL", "TEST_ERP_READER_URL"

TEST_CONFIRMATION = {
    "confirmed_by": "test suite",
    "confirmed_on": "2026-10-30",
    "reference": "docker/erp-test (TEST replica, never the real ERP)",
}


def erp_available() -> bool:
    return bool(os.environ.get(ADMIN_ENV) and os.environ.get(READER_ENV))


requires_erp = pytest.mark.skipif(
    not erp_available(),
    reason=f"InvenLogic TEST replica not configured ({ADMIN_ENV}, {READER_ENV}); "
    "docker compose -f docker/erp-test/compose.yml up -d",
)


# --- a private event loop for seeding (works inside and outside running loops) -----------


class _Runner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        thread.start()
        self._connections: dict[str, asyncpg.Connection] = {}

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=60)

    async def connection(self, database: str) -> asyncpg.Connection:
        conn = self._connections.get(database)
        if conn is None or conn.is_closed():
            base = os.environ[ADMIN_ENV].rsplit("/", 1)[0]
            conn = await asyncpg.connect(f"{base}/{database}")
            self._connections[database] = conn
        return conn


_RUNNER: _Runner | None = None
_LOCK = threading.Lock()


def _runner() -> _Runner:
    global _RUNNER
    with _LOCK:
        if _RUNNER is None:
            _RUNNER = _Runner()
        return _RUNNER


def admin_execute(database: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    """Run one statement on the TEST replica as its admin (seeding and test setup only)."""
    runner = _runner()

    async def go() -> list[asyncpg.Record]:
        conn = await runner.connection(database)
        return list(await conn.fetch(sql, *args))

    return runner.run(go())


_COMPANY_IDS = itertools.count(int(time.time() * 1000) % 10**9 * 1000)


# --- contract and adapter ---------------------------------------------------------------------


def replica_contract(**changes: Any) -> ErpContract:
    """The real InvenLogic contract (docs/erp/invenlogic_contract.json) completed for the
    TEST replica: its databases, the test role, VERIFIED. The mappings and rules are the
    real contract's, so the replica tests exercise exactly what production would run."""
    raw = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    raw["status"] = "VERIFIED"
    raw["ownership"] = {
        "erp_owner": "test suite", "application_owner": "test suite",
        "is_intended_erp": True, "confirmation": TEST_CONFIRMATION,
    }  # fmt: skip
    raw["databases"] = {
        key: {"host": "localhost", "port": 5434, "name": name, "postgres_major_version": 18,
              "schema_version": SCHEMA_VERSIONS[key]}
        for key, name in DATABASES.items()
    }  # fmt: skip
    raw["access"] = {
        "read_only_role": "agentic_erp_reader", "delivery": "secret_store", "tls_mode": "require",
    }  # fmt: skip
    for mapping in raw["capabilities"].values():
        mapping["status"] = "VERIFIED"
    raw.update(changes)
    return ErpContract.model_validate(raw)


def reader_urls() -> dict[str, str]:
    base = os.environ[READER_ENV].rstrip("/")
    return {key: f"{base}/{name}" for key, name in DATABASES.items()}


def erp_settings(**overrides: Any) -> ErpSettings:
    values: dict[str, Any] = {
        "adapter": "postgres",
        "database_urls": json.dumps(reader_urls()),
        "ssl_mode": "disable",
        "business_timezone": "Asia/Kolkata",
    }
    values.update(overrides)
    return ErpSettings(**values)


def _engine(settings: ErpSettings, url: SecretStr) -> AsyncEngine:
    """The production engine options with NullPool: every test runs its own event loop."""
    db = erp_database_settings(settings, url)
    options = engine_options(db)
    for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping"):
        options.pop(key)
    options["connect_args"]["server_settings"]["default_transaction_read_only"] = "on"
    return create_async_engine(db.runtime_url(), poolclass=NullPool, **options)


def erp_adapter(
    contract: ErpContract | None = None, settings: ErpSettings | None = None
) -> PostgresErpAdapter:
    """The live adapter on the TEST replica, with the readers its contract activates."""
    contract = contract or replica_contract()
    settings = settings or erp_settings()
    urls = settings.database_url_map()
    databases = {
        key: ReadOnlyErpDatabase(
            settings,
            contract=contract,
            database_key=key,
            url=urls[key],
            engine=_engine(settings, urls[key]),
        )
        for key in DATABASES
    }
    adapter = PostgresErpAdapter(databases, contract)
    register_invenlogic_readers(adapter, contract)
    return adapter


# --- seeding ----------------------------------------------------------------------------------


@dataclass
class ErpCompany:
    """One InvenLogic company on the TEST replica. Methods insert rows and return the refs
    the readers will report (customers/products/warehouses: the id; bills: `si:` / `ob:`)."""

    company_id: int = field(default_factory=lambda: next(_COMPANY_IDS))
    branch_id: int = 1
    _uom: dict[str, int] = field(default_factory=dict)
    _numbers: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    _counter_customer: str | None = None

    @property
    def tenant(self) -> str:
        return str(self.company_id)

    def _one(self, database: str, sql: str, *args: Any) -> Any:
        return admin_execute(database, sql, *args)[0][0]

    # master data
    def customer(
        self, name: str, *, short_name: str | None = None, active: bool = True,
        cash_sale: bool = False,
    ) -> str:  # fmt: skip
        return str(self._one(
            "master_data_db",
            "INSERT INTO customer (company_id, created_by, updated_by, ledger_name, display_name,"
            " short_name, active, party_id, cash_sale)"
            " VALUES ($1, 1, 1, $2, $2, $3, $4, 1, $5) RETURNING id",
            self.company_id, name, short_name, active, cash_sale,
        ))  # fmt: skip

    def uom(self, code: str) -> int:
        if code not in self._uom:
            self._uom[code] = self._one(
                "master_data_db",
                "INSERT INTO uom (company_id, created_by, updated_by, code, name)"
                " VALUES ($1, 1, 1, $2, $2) RETURNING id",
                self.company_id, code,
            )  # fmt: skip
        return self._uom[code]

    def product(self, name: str, *, sku: str | None = None, unit: str = "pcs") -> str:
        return str(self._one(
            "master_data_db",
            "INSERT INTO product (company_id, created_by, updated_by, sku, name, uom_id)"
            " VALUES ($1, 1, 1, $2, $3, $4) RETURNING id",
            self.company_id, sku or f"SKU-{next(iter(self._numbers))}-{name}"[:255], name,
            self.uom(unit),
        ))  # fmt: skip

    def warehouse(self, name: str, *, code: str | None = None) -> str:
        return str(self._one(
            "master_data_db",
            "INSERT INTO warehouse (company_id, branch_id, created_by, updated_by, code, name)"
            " VALUES ($1, $2, 1, 1, $3, $4) RETURNING id",
            self.company_id, self.branch_id, code or f"W{next(iter(self._numbers))}", name,
        ))  # fmt: skip

    # sales
    def invoice(
        self, customer: str, total: str | Decimal, *, on: date, due: date | None = None,
        number: str | None = None, status: str = "POSTED", paid: str | Decimal = "0",
        counter: bool = False,
    ) -> str:  # fmt: skip
        invoice_id = self._one(
            "sales_db",
            "INSERT INTO sales_invoice (company_id, branch_id, created_by, updated_by,"
            " invoice_number, invoice_date, mode, customer_id, due_date, grand_total, status,"
            " counter_sale) VALUES ($1, $2, 1, 1, $3, $4, 'CREDIT', $5, $6, $7, $8, $9)"
            " RETURNING id",
            self.company_id, self.branch_id, number, on, int(customer), due,
            Decimal(total), status, counter,
        )  # fmt: skip
        if Decimal(paid) > 0:
            self._settle(customer, Decimal(paid), on, invoice=invoice_id)
        return f"si:{invoice_id}"

    def opening_bill(
        self, customer: str, amount: str | Decimal, *, on: date, due: date, number: str,
        status: str = "POSTED", paid: str | Decimal = "0",
    ) -> str:  # fmt: skip
        bill_id = self._one(
            "sales_db",
            "INSERT INTO sales_opening_bill (company_id, branch_id, created_by, updated_by,"
            " customer_id, bill_number, bill_date, due_date, amount, status)"
            " VALUES ($1, $2, 1, 1, $3, $4, $5, $6, $7, $8) RETURNING id",
            self.company_id, self.branch_id, int(customer), number, on, due, Decimal(amount),
            status,
        )  # fmt: skip
        if Decimal(paid) > 0:
            self._settle(customer, Decimal(paid), on, opening_bill=bill_id)
        return f"ob:{bill_id}"

    def receipt(
        self, customer: str, amount: str | Decimal, *, on: date,
        unallocated: str | Decimal = "0", status: str = "POSTED",
    ) -> int:  # fmt: skip
        amount = Decimal(amount)
        rest = Decimal(unallocated)
        return int(self._one(
            "sales_db",
            "INSERT INTO sales_receipt (company_id, branch_id, created_by, updated_by,"
            " receipt_date, customer_id, mode, amount, settlement_value, allocated_amount,"
            " unallocated_amount, status) VALUES ($1, $2, 1, 1, $3, $4, 'CASH', $5, $5,"
            " $6, $7, $8) RETURNING id",
            self.company_id, self.branch_id, on, int(customer), amount, amount - rest, rest,
            status,
        ))  # fmt: skip

    def credit_note(
        self, customer: str, amount: str | Decimal, *, on: date,
        unapplied: str | Decimal = "0", status: str = "POSTED",
    ) -> int:  # fmt: skip
        amount = Decimal(amount)
        return int(self._one(
            "sales_db",
            "INSERT INTO sales_credit_note (company_id, branch_id, created_by, updated_by,"
            " note_date, customer_id, kind, reason, status, grand_total, applied_amount,"
            " unapplied_amount) VALUES ($1, $2, 1, 1, $3, $4, 'VALUE', 'DISCOUNT', $5, $6,"
            " $7, $8) RETURNING id",
            self.company_id, self.branch_id, on, int(customer), status, amount,
            amount - Decimal(unapplied), Decimal(unapplied),
        ))  # fmt: skip

    def _settle(
        self, customer: str, amount: Decimal, on: date, *, invoice: int | None = None,
        opening_bill: int | None = None, undone: bool = False,
    ) -> None:  # fmt: skip
        receipt = self.receipt(customer, amount, on=on)
        admin_execute(
            "sales_db",
            "INSERT INTO receivable_allocation (company_id, branch_id, created_by, updated_by,"
            " sales_receipt_id, sales_invoice_id, sales_opening_bill_id, amount, allocated_by,"
            " undone_at) VALUES ($1, $2, 1, 1, $3, $4, $5, $6, 1, $7)",
            self.company_id, self.branch_id, receipt, invoice, opening_bill, amount,
            None if not undone else on,
        )  # fmt: skip

    def pay(self, customer: str, bill_ref: str, amount: str | Decimal, on: date) -> None:
        """A receipt allocated to an invoice (`si:`) or opening bill (`ob:`)."""
        kind, bill_id = bill_ref.split(":")
        self._settle(
            customer, Decimal(amount), on,
            invoice=int(bill_id) if kind == "si" else None,
            opening_bill=int(bill_id) if kind == "ob" else None,
        )  # fmt: skip

    def undone_payment(self, customer: str, invoice_ref: str, amount: str, on: date) -> None:
        """A payment that was allocated and then undone (it must not count as paid)."""
        self._settle(customer, Decimal(amount), on, invoice=int(invoice_ref[3:]), undone=True)

    def sales_day(self, on: date, total: str | Decimal, count: int) -> None:
        """`count` posted, fully paid counter invoices on `on` totalling `total`."""
        if self._counter_customer is None:
            self._counter_customer = self.customer("Counter Sale (TEST)")
        total = Decimal(total)
        each = (total / count).quantize(Decimal("0.01"))
        amounts = [each] * (count - 1) + [total - each * (count - 1)]
        for amount in amounts:
            self.invoice(self._counter_customer, amount, on=on, due=on, paid=amount)

    # inventory
    def stock(self, product: str, warehouse: str, quantity: str | Decimal) -> None:
        admin_execute(
            "inventory_db",
            "INSERT INTO stock_balance (company_id, branch_id, created_by, updated_by,"
            " warehouse_id, product_id, quantity_on_hand) VALUES ($1, $2, 1, 1, $3, $4, $5)"
            " ON CONFLICT (company_id, branch_id, warehouse_id, product_id)"
            " DO UPDATE SET quantity_on_hand = EXCLUDED.quantity_on_hand",
            self.company_id, self.branch_id, int(warehouse), int(product), Decimal(quantity),
        )  # fmt: skip

    def reorder(self, product: str, warehouse: str, min_qty: str | Decimal) -> None:
        admin_execute(
            "inventory_db",
            "INSERT INTO reorder_rule (company_id, branch_id, created_by, updated_by,"
            " warehouse_id, product_id, min_qty) VALUES ($1, $2, 1, 1, $3, $4, $5)",
            self.company_id, self.branch_id, int(warehouse), int(product), Decimal(min_qty),
        )  # fmt: skip

    def set_invoice_status(self, invoice_ref: str, status: str) -> None:
        admin_execute(
            "sales_db", "UPDATE sales_invoice SET status = $1 WHERE company_id = $2 AND id = $3",
            status, self.company_id, int(invoice_ref[3:]),
        )  # fmt: skip


# --- the standard TEST data set (the fake ERP's scenarios, on the real schema) ---------------


@dataclass
class StandardData:
    company: ErpCompany
    today: date
    refs: dict[str, str]

    @property
    def tenant(self) -> str:
        return self.company.tenant

    def __getitem__(self, key: str) -> str:
        return self.refs[key]


def standard_company(today: date, *, extended: bool = True) -> StandardData:
    """Customers Rahul Gupta / Amit Sharma / Amit Sharma Traders (and, extended, Rahul
    Verma), their bills, four sales days, three products in Godown 1-3 with stock and
    reorder rules: the scenarios the business tests were written for, as real rows."""
    c = ErpCompany()
    r: dict[str, str] = {}
    r["rahul"] = c.customer("Rahul Gupta (TEST)")
    r["amit"] = c.customer("Amit Sharma (TEST)")
    r["traders"] = c.customer("Amit Sharma Traders (TEST)")
    # Rahul Gupta: one overdue and unpaid, one part-paid and not yet due, one paid.
    r["inv1"] = c.invoice(r["rahul"], "30000.00", on=date(2026, 8, 1), due=date(2026, 8, 31),
                          number="TEST-INV-001")  # fmt: skip
    r["inv2"] = c.invoice(r["rahul"], "25000.00", on=date(2026, 8, 25), due=date(2026, 10, 15),
                          number="TEST-INV-002", paid="10000.00")  # fmt: skip
    r["inv3"] = c.invoice(r["rahul"], "20000.00", on=date(2026, 7, 1), due=date(2026, 7, 31),
                          number="TEST-INV-003", paid="20000.00")  # fmt: skip
    # A cancelled and a draft invoice: never open, never sales.
    r["inv_cancelled"] = c.invoice(r["rahul"], "5000.00", on=date(2026, 8, 5),
                                   due=date(2026, 9, 5), number="TEST-INV-005",
                                   status="CANCELLED")  # fmt: skip
    c.invoice(r["rahul"], "7000.00", on=date(2026, 8, 6), status="DRAFT")
    # Amit Sharma Traders: one overdue and unpaid.
    r["inv6"] = c.invoice(r["traders"], "8000.00", on=date(2026, 8, 20), due=date(2026, 9, 20),
                          number="TEST-INV-006")  # fmt: skip
    for day, total, count in (
        (today, "125000.00", 7), (today - timedelta(days=1), "80000.00", 5),
        (today - timedelta(days=3), "40000.00", 2), (today - timedelta(days=29), "20000.00", 1),
    ):  # fmt: skip
        c.sales_day(day, total, count)
    r["iphone"] = c.product("iPhone 15 (TEST)")
    r["galaxy"] = c.product("Galaxy S24 (TEST)")
    r["cable"] = c.product("USB-C Cable (TEST)")
    r["godown1"] = c.warehouse("Godown 1 (TEST)")
    r["godown2"] = c.warehouse("Godown 2 (TEST)")
    c.stock(r["iphone"], r["godown1"], "12")
    c.stock(r["iphone"], r["godown2"], "3")
    c.stock(r["galaxy"], r["godown2"], "0")
    c.stock(r["cable"], r["godown1"], "250")
    c.reorder(r["iphone"], r["godown2"], "5")
    c.reorder(r["galaxy"], r["godown2"], "2")
    c.reorder(r["cable"], r["godown1"], "20")  # not low: 250 > 20
    if extended:
        r["verma"] = c.customer("Rahul Verma (TEST)")
        r["inv4"] = c.invoice(r["verma"], "12000.00", on=date(2026, 8, 15),
                              due=date(2026, 9, 15), number="TEST-INV-004")  # fmt: skip
        r["godown3"] = c.warehouse("Godown 3 (TEST)")
        c.stock(r["galaxy"], r["godown3"], "6")
        c.stock(r["cable"], r["godown3"], "40")
    return StandardData(company=c, today=today, refs=r)


def other_company() -> StandardData:
    """A second company whose only customer exists nowhere else."""
    c = ErpCompany()
    refs = {"globex": c.customer("Globex Only Customer (TEST)")}
    c.opening_bill(refs["globex"], "99999.00", on=date(2026, 4, 1), due=date(2026, 4, 30),
                   number="TEST-OB-901")  # fmt: skip
    return StandardData(company=c, today=date(2026, 10, 1), refs=refs)


__all__ = [
    "ErpCompany",
    "StandardData",
    "admin_execute",
    "erp_adapter",
    "erp_available",
    "erp_settings",
    "other_company",
    "replica_contract",
    "requires_erp",
    "standard_company",
]
