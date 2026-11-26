"""Seed one demo company on the InvenLogic TEST replica (docker/erp-test), for chatbot demos.

    $env:TEST_ERP_ADMIN_URL="postgresql://erp_test_admin:erp_test_admin_dev@127.0.0.1:5434/postgres"
    $env:TEST_ERP_READER_URL="postgresql://agentic_erp_reader:erp_reader_dev@127.0.0.1:5434"
    .venv\\Scripts\\python -m scripts.seed_erp_demo
    .venv\\Scripts\\python -m scripts.seed_erp_demo --company-id 900000002 --customers 200

TEST data only. Company (= platform tenant) id 900000001: 100 customers "... (TEST)" with
invoices, opening bills, receipts, advances and credit notes over the last ~18 months,
daily counter sales since the start of last financial year, 25 products in 4 warehouses
with stock and reorder rules (some low).

Safety: before any write it refuses unless the admin URL is the replica's admin
(`erp_test_admin`) on 127.0.0.1/localhost, and the server has the replica's databases,
its read-only role and `invenlogic_company_id()` in each database. It never touches any
other database.

Idempotent and deterministic: every row of company 900000001 in the seeded tables is
deleted first (in one transaction per database with the inserts), and the data comes from
`random.Random(20261030)` with dates relative to today in Asia/Kolkata, so reruns on the
same day give the same figures (only the row ids differ).

Afterwards it reads the data back through the agent's real read path (the InvenLogic
readers as `agentic_erp_reader`, the `BusinessReadService`, the real clock) and prints a
summary and example questions for the chatbot.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]

COMPANY_ID = 900000001
BRANCH_ID = 1
SEED = 20261030
TZ = "Asia/Kolkata"
ADMIN_ENV, READER_ENV = "TEST_ERP_ADMIN_URL", "TEST_ERP_READER_URL"
DEFAULT_ADMIN_URL = "postgresql://erp_test_admin:erp_test_admin_dev@127.0.0.1:5434/postgres"
DEFAULT_READER_URL = "postgresql://agentic_erp_reader:erp_reader_dev@127.0.0.1:5434"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
ADMIN_USER, READER_ROLE = "erp_test_admin", "agentic_erp_reader"
DATABASES = ("master_data_db", "sales_db", "inventory_db")

# table -> database; tables whose id the seed assigns (from the table's own sequence)
DB_OF = {
    "uom": "master_data_db",
    "customer": "master_data_db",
    "product": "master_data_db",
    "warehouse": "master_data_db",
    "sales_invoice": "sales_db",
    "sales_opening_bill": "sales_db",
    "sales_receipt": "sales_db",
    "sales_credit_note": "sales_db",
    "receivable_allocation": "sales_db",
    "stock_balance": "inventory_db",
    "reorder_rule": "inventory_db",
}
OWN_ID = (
    "uom",
    "customer",
    "product",
    "warehouse",
    "sales_invoice",
    "sales_opening_bill",
    "sales_receipt",
    "sales_credit_note",
)
FKS: dict[str, dict[str, str]] = {
    "product": {"uom_id": "uom"},
    "sales_invoice": {"customer_id": "customer"},
    "sales_opening_bill": {"customer_id": "customer"},
    "sales_receipt": {"customer_id": "customer"},
    "sales_credit_note": {"customer_id": "customer"},
    "receivable_allocation": {
        "sales_receipt_id": "sales_receipt",
        "sales_credit_note_id": "sales_credit_note",
        "sales_invoice_id": "sales_invoice",
        "sales_opening_bill_id": "sales_opening_bill",
    },
    "stock_balance": {"warehouse_id": "warehouse", "product_id": "product"},
    "reorder_rule": {"warehouse_id": "warehouse", "product_id": "product"},
}
# Delete order per database (children first).
DELETE_ORDER = {
    "sales_db": (
        "receivable_allocation",
        "sales_receipt",
        "sales_credit_note",
        "sales_invoice",
        "sales_opening_bill",
    ),
    "inventory_db": ("reorder_rule", "stock_balance"),
    "master_data_db": ("product", "uom", "customer", "warehouse"),
}
INSERT_ORDER = {
    "master_data_db": ("uom", "customer", "product", "warehouse"),
    "sales_db": (
        "sales_invoice",
        "sales_opening_bill",
        "sales_receipt",
        "sales_credit_note",
        "receivable_allocation",
    ),
    "inventory_db": ("stock_balance", "reorder_rule"),
}

PAISA = Decimal("0.01")


class Refused(Exception):
    pass


# --- safety guard --------------------------------------------------------------------------


def _safe(text: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def check_urls(admin_url: str, reader_url: str) -> None:
    admin = urlsplit(admin_url)
    if admin.scheme not in ("postgresql", "postgres"):
        raise Refused(f"{ADMIN_ENV} is not a postgresql:// URL")
    if admin.hostname not in LOCAL_HOSTS:
        raise Refused(f"{ADMIN_ENV} host {admin.hostname!r} is not 127.0.0.1/localhost")
    if admin.username != ADMIN_USER:
        raise Refused(f"{ADMIN_ENV} user {admin.username!r} is not {ADMIN_USER!r}")
    reader = urlsplit(reader_url)
    if reader.hostname not in LOCAL_HOSTS or reader.username != READER_ROLE:
        raise Refused(f"{READER_ENV} must be {READER_ROLE!r} on 127.0.0.1/localhost")


async def connect(admin_url: str, database: str) -> asyncpg.Connection:
    base = (
        admin_url.rsplit("/", 1)[0]
        if urlsplit(admin_url).path not in ("", "/")
        else (admin_url.rstrip("/"))
    )
    try:
        return await asyncpg.connect(f"{base}/{database}", timeout=10)
    except Exception as exc:  # any failure is a refusal
        raise Refused(
            f"cannot connect to {database} on the TEST replica "
            f"({type(exc).__name__}: {_safe(str(exc), urlsplit(admin_url).password)})"
        ) from exc


async def guard(admin_url: str) -> None:
    """Refuse unless this server really is the InvenLogic TEST replica."""
    conn = await connect(admin_url, urlsplit(admin_url).path.lstrip("/") or "postgres")
    try:
        names = {r[0] for r in await conn.fetch("SELECT datname FROM pg_database")}
        missing = [d for d in DATABASES if d not in names]
        if missing:
            raise Refused(f"not the TEST replica: database(s) {', '.join(missing)} missing")
        if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", READER_ROLE):
            raise Refused(f"not the TEST replica: role {READER_ROLE} missing")
    finally:
        await conn.close()
    for database in DATABASES:
        conn = await connect(admin_url, database)
        try:
            found = await conn.fetchval(
                "SELECT to_regprocedure('public.invenlogic_company_id()') IS NOT NULL"
            )
        finally:
            await conn.close()
        if not found:
            raise Refused(f"not the TEST replica: invenlogic_company_id() missing in {database}")


# --- the plan (rows with local ids, mapped to real ids at insert time) --------------------


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(PAISA)


def fiscal_year(day: date) -> int:
    return day.year if day.month >= 4 else day.year - 1


@dataclass
class Plan:
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    numbers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # expected figures, computed from the plan, to compare with what the readers report
    due: dict[int, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    customers: dict[int, dict[str, Any]] = field(default_factory=dict)

    def add(self, table: str, **values: Any) -> int:
        rows = self.rows[table]
        local = len(rows) + 1
        if table in OWN_ID:
            values = {"id": local, **values}
        rows.append(values)
        return local

    def number(self, prefix: str, day: date, width: int = 5) -> str:
        fy = fiscal_year(day)
        key = f"{prefix}{fy}"
        self.numbers[key] += 1
        return f"{prefix}/{fy % 100:02d}{(fy + 1) % 100:02d}/{self.numbers[key]:0{width}d}"

    # master data
    def customer(
        self, name: str, short: str | None, *, active: bool = True, cash_sale: bool = False
    ) -> int:
        ref = self.add(
            "customer",
            ledger_name=name,
            display_name=name,
            short_name=short,
            active=active,
            party_id=1,
            cash_sale=cash_sale,
        )
        self.customers[ref] = {"name": name, "short": short, "active": active}
        return ref

    def uom(self, code: str) -> int:
        return self.add("uom", code=code, name=code)

    def product(self, name: str, sku: str, uom: int) -> int:
        return self.add("product", sku=sku, name=name, uom_id=uom)

    def warehouse(self, name: str, code: str) -> int:
        return self.add("warehouse", branch_id=BRANCH_ID, code=code, name=name)

    # sales
    def invoice(
        self,
        customer: int,
        total: Decimal,
        *,
        on: date,
        due: date | None,
        status: str = "POSTED",
        counter: bool = False,
    ) -> int:
        number = None if status == "DRAFT" else self.number("CS" if counter else "SI", on)
        ref = self.add(
            "sales_invoice",
            branch_id=BRANCH_ID,
            invoice_number=number,
            fiscal_year=fiscal_year(on),
            invoice_date=on,
            mode="CREDIT",
            customer_id=customer,
            counter_sale=counter,
            credit_days=None if due is None else (due - on).days,
            due_date=due,
            sub_total=total,
            taxable_total=total,
            grand_total=total,
            status=status,
        )
        if status == "POSTED":
            self.due[customer] += total
        return ref

    def opening_bill(
        self, customer: int, amount: Decimal, *, on: date, due: date, number: str
    ) -> int:
        self.due[customer] += amount
        return self.add(
            "sales_opening_bill",
            branch_id=BRANCH_ID,
            customer_id=customer,
            customer_name=self.customers[customer]["name"],
            bill_number=number,
            bill_date=on,
            due_date=due,
            amount=amount,
            status="POSTED",
            posting_date=on,
        )

    def receipt(
        self,
        customer: int,
        amount: Decimal,
        *,
        on: date,
        unallocated: Decimal = Decimal(0),
        counter: bool = False,
    ) -> int:
        self.due[customer] -= unallocated
        return self.add(
            "sales_receipt",
            branch_id=BRANCH_ID,
            receipt_number=self.number("RC", on, 6),
            fiscal_year=fiscal_year(on),
            receipt_date=on,
            customer_id=customer,
            mode="CASH",
            amount=amount,
            settlement_value=amount,
            allocated_amount=amount - unallocated,
            unallocated_amount=unallocated,
            status="POSTED",
            counter_sale=counter,
        )

    def credit_note(self, customer: int, amount: Decimal, *, on: date, unapplied: Decimal) -> int:
        self.due[customer] -= unapplied
        return self.add(
            "sales_credit_note",
            branch_id=BRANCH_ID,
            credit_note_number=self.number("CN", on),
            fiscal_year=fiscal_year(on),
            note_date=on,
            customer_id=customer,
            kind="VALUE",
            reason="DISCOUNT",
            status="POSTED",
            taxable_total=amount,
            grand_total=amount,
            applied_amount=amount - unapplied,
            unapplied_amount=unapplied,
        )

    def allocate(
        self,
        amount: Decimal,
        *,
        receipt: int | None = None,
        credit_note: int | None = None,
        invoice: int | None = None,
        opening_bill: int | None = None,
        customer: int,
        undone_on: date | None = None,
    ) -> None:
        if undone_on is None:
            self.due[customer] -= amount
        self.add(
            "receivable_allocation",
            branch_id=BRANCH_ID,
            sales_receipt_id=receipt,
            sales_credit_note_id=credit_note,
            sales_invoice_id=invoice,
            sales_opening_bill_id=opening_bill,
            amount=amount,
            allocated_by=1,
            undone_by=None if undone_on is None else 1,
            undone_at=None
            if undone_on is None
            else datetime(undone_on.year, undone_on.month, undone_on.day, 12, tzinfo=ZoneInfo(TZ)),
        )

    def settle(
        self,
        customer: int,
        amount: Decimal,
        on: date,
        *,
        invoice: int | None = None,
        opening_bill: int | None = None,
        counter: bool = False,
    ) -> None:
        """A receipt fully allocated to one bill (as ErpCompany._settle / pay do)."""
        receipt = self.receipt(customer, amount, on=on, counter=counter)
        self.allocate(
            amount, receipt=receipt, invoice=invoice, opening_bill=opening_bill, customer=customer
        )

    # inventory
    def stock(self, product: int, warehouse: int, quantity: Decimal) -> None:
        self.add(
            "stock_balance",
            branch_id=BRANCH_ID,
            warehouse_id=warehouse,
            product_id=product,
            quantity_on_hand=quantity,
        )

    def reorder(self, product: int, warehouse: int, min_qty: Decimal, max_qty: Decimal) -> None:
        self.add(
            "reorder_rule",
            branch_id=BRANCH_ID,
            warehouse_id=warehouse,
            product_id=product,
            min_qty=min_qty,
            max_qty=max_qty,
        )


# --- the demo data -------------------------------------------------------------------------

PEOPLE = """
Rahul Sharma|Rahul Verma|Priya Patel|Priya Nair|Amit Kumar|Suresh Reddy|Suresh Menon|Neha Gupta|
Neha Joshi|Vikram Singh|Anjali Desai|Rohan Mehta|Kavita Iyer|Arjun Malhotra|Pooja Agarwal|
Sanjay Bhatt|Deepak Chauhan|Meera Krishnan|Karan Kapoor|Sunita Yadav|Manoj Tiwari|Rekha Pillai|
Aakash Jain|Divya Rao|Harish Pandey|Swati Kulkarni|Nikhil Bansal|Shalini Mishra|Gaurav Saxena|
Lakshmi Subramanian|Vivek Chaturvedi|Anita Dubey|Rajesh Choudhary|Farhan Qureshi|Imran Sheikh|
Zoya Khan|Gurpreet Kaur|Harpreet Gill|Simran Dhillon|Jaspreet Sandhu|Mohit Arora|Tanvi Shetty|
Siddharth Ghosh|Ananya Banerjee|Sourav Das|Moumita Chatterjee|Bhavesh Shah|Hetal Parikh|
Jignesh Trivedi|Kiran Hegde|Prakash Gowda|Venkatesh Naidu|Sravani Reddy|Ramesh Iyengar|
Usha Nambiar|Thomas Mathew|Joseph Fernandes|Maria DSouza|Ashok Patil|Snehal Deshmukh|
Omkar Jadhav|Pankaj Sinha|Ritu Srivastava|Abhishek Thakur|Nisha Rawat|Yogesh Negi|
Sameer Kulkarni|Alok Mohanty|Sasmita Panda|Bikash Barua|Dipika Bora|Arvind Rathore|
Komal Solanki|Hitesh Chawla|Payal Sethi|Varun Ahuja|Ishita Bhalla|Kunal Oberoi|Ritesh Goyal|
Tarun Bajaj
"""
SHOPS = """
Amit Kumar Traders|Sharma Mobile Point|Shree Ganesh Electronics|Balaji Telecom|Om Sai Mobiles|
New India Mobile Store|Krishna Communications|Jai Mata Di Electronics|Galaxy Mobile Hub|
City Phone Care|Mahalaxmi Enterprises|Royal Gadgets|Star Mobile Gallery|Patel Brothers Electronics|
Kumar Accessories|Sri Venkateswara Agencies|Metro Mobile World|Deccan Digital|
Modern Electronics|Annapurna Distributors
"""
INACTIVE = {"Sanjay Bhatt", "Tarun Bajaj"}

PRODUCTS = [  # (name, sku, unit, kind)
    ("iPhone 15 128GB", "TST-MOB-001", "pcs", "phone"),
    ("iPhone 15 Pro 256GB", "TST-MOB-002", "pcs", "phone"),
    ("Samsung Galaxy S24", "TST-MOB-003", "pcs", "phone"),
    ("Samsung Galaxy A15", "TST-MOB-004", "pcs", "phone"),
    ("Redmi Note 13 Pro", "TST-MOB-005", "pcs", "phone"),
    ("Redmi 13C", "TST-MOB-006", "pcs", "phone"),
    ("Vivo Y28", "TST-MOB-007", "pcs", "phone"),
    ("Oppo A79", "TST-MOB-008", "pcs", "phone"),
    ("OnePlus Nord CE4", "TST-MOB-009", "pcs", "phone"),
    ("Realme Narzo 70", "TST-MOB-010", "pcs", "phone"),
    ("USB-C Cable 1m", "TST-ACC-001", "pcs", "acc"),
    ("Fast Charger 25W", "TST-ACC-002", "pcs", "acc"),
    ("Tempered Glass Screen Guard", "TST-ACC-003", "box", "box"),
    ("Silicone Back Cover", "TST-ACC-004", "box", "box"),
    ("Wired Earphones", "TST-ACC-005", "pcs", "acc"),
    ("Bluetooth Neckband", "TST-ACC-006", "pcs", "acc"),
    ("Power Bank 10000mAh", "TST-ACC-007", "pcs", "acc"),
    ("Memory Card 64GB", "TST-ACC-008", "pcs", "acc"),
    ("OTG Adapter", "TST-ACC-009", "box", "box"),
    ("Car Charger Dual USB", "TST-ACC-010", "pcs", "acc"),
    ("Smart Watch Fit", "TST-ELE-001", "pcs", "phone"),
    ("Bluetooth Speaker Mini", "TST-ELE-002", "pcs", "acc"),
    ("Smart TV 43 inch", "TST-ELE-003", "pcs", "phone"),
    ("Wi-Fi Router Dual Band", "TST-ELE-004", "pcs", "acc"),
    ("LED Bulb 9W", "TST-ELE-005", "box", "box"),
]
WAREHOUSES = [
    ("Main Godown (TEST)", "MAIN"),
    ("Shop Counter (TEST)", "SHOP"),
    ("Delhi Branch (TEST)", "DEL"),
    ("Pune Godown (TEST)", "PUNE"),
]
LOW_STOCK_ITEMS = 8


def _names(block: str) -> list[str]:
    return [n.strip() for n in block.replace("\n", "").split("|") if n.strip()]


SHOP_SUFFIXES = ("Electronics", "Telecom", "Mobile Zone", "Gadget House", "Communications")


def extra_customers(count: int) -> list[tuple[str, bool]]:
    """`count` more (name, is_shop) pairs beyond the 100 fixed ones, deterministic and
    unique: new first/last-name pairs from PEOPLE, and shop names from their surnames
    (one in five is a shop, as in the fixed list). Its own RNG, so the default 100-customer
    company stays exactly as before."""
    rng = random.Random(SEED + 1)
    taken = {n.casefold() for n in _names(PEOPLE) + _names(SHOPS)}
    people = [n.split() for n in _names(PEOPLE)]
    firsts = sorted({p[0] for p in people})
    lasts = sorted({p[-1] for p in people})
    out: list[tuple[str, bool]] = []
    while len(out) < count:
        shop = len(out) % 5 == 4
        if shop:
            name = f"{rng.choice(lasts)} {rng.choice(SHOP_SUFFIXES)}"
        else:
            name = f"{rng.choice(firsts)} {rng.choice(lasts)}"
        if name.casefold() not in taken:
            taken.add(name.casefold())
            out.append((name, shop))
    return out


def _short(name: str, shop: bool, rng: random.Random) -> str | None:
    if rng.random() < 0.15:
        return None
    words = name.split()
    if shop:
        return "".join(w[0] for w in words).upper()
    return f"{words[0]} {words[-1][0]}"


def bill_amount(rng: random.Random) -> Decimal:
    tier = rng.random()
    if tier < 0.45:
        rupees = rng.randint(8, 60) * 100
    elif tier < 0.85:
        rupees = rng.randint(60, 400) * 100
    else:
        rupees = rng.randint(400, 1500) * 100
    paise = Decimal(rng.randint(1, 99)) / 100 if rng.random() < 0.3 else Decimal(0)
    return money(Decimal(rupees) + paise)


def counter_amount(rng: random.Random) -> Decimal:
    accessory = rng.random() < 0.7
    # accessories Rs 149 .. 2,499; phones and electronics Rs 7,999 .. 89,999
    rupees = rng.randint(15, 250) * 10 - 1 if accessory else rng.randint(8, 90) * 1000 - 1
    return money(rupees) + (Decimal("0.50") if rng.random() < 0.15 else Decimal(0))


def build(today: date, n_customers: int = 100) -> tuple[Plan, dict[str, Any]]:
    rng = random.Random(SEED)
    plan = Plan()
    info: dict[str, Any] = {"today": today}
    fy_start = date(fiscal_year(today), 4, 1)
    last_fy_start = date(fiscal_year(today) - 1, 4, 1)

    # customers: the 100 fixed names, then generated ones (every 50th extra one inactive)
    people, shops = _names(PEOPLE), _names(SHOPS)
    assert len(people) + len(shops) == 100, len(people) + len(shops)
    named = [(n, False) for n in people] + [(n, True) for n in shops]
    extra = extra_customers(max(0, n_customers - 100))
    inactive = INACTIVE | {n for i, (n, _) in enumerate(extra) if i % 50 == 25}
    customers: list[int] = []
    for name, shop in (named + extra)[:n_customers]:
        customers.append(
            plan.customer(f"{name} (TEST)", _short(name, shop, rng), active=name not in inactive)
        )
    counter = plan.customer("Counter Sale (TEST)", "Counter", cash_sale=True)
    info["counter"] = counter

    # customer invoices over the last ~18 months, with payments
    open_invoices: list[tuple[int, int, Decimal, date]] = []  # (customer, invoice, balance, on)
    for customer in customers:
        for _ in range(rng.randint(0, 6)):
            on = today - timedelta(days=rng.randint(0, 540))
            credit = rng.choice((15, 30, 30, 45))
            due = on + timedelta(days=credit)
            total = bill_amount(rng)
            inv = plan.invoice(customer, total, on=on, due=due)
            age, r = (today - on).days, rng.random()
            full, part = (
                (0.85, 0.95) if age > 180 else (0.55, 0.75) if due < today else (0.25, 0.45)
            )
            if r < full:
                paid_on = min(today, on + timedelta(days=rng.randint(3, credit + 20)))
                plan.settle(customer, total, paid_on, invoice=inv)
                continue
            balance = total
            if r < part:
                for _ in range(rng.randint(1, 2)):
                    part_amount = money(
                        max(500, int(balance * Decimal(rng.uniform(0.2, 0.5))) // 500 * 500)
                    )
                    if part_amount >= balance:
                        break
                    paid_on = min(today, on + timedelta(days=rng.randint(2, credit + 10)))
                    plan.settle(customer, part_amount, paid_on, invoice=inv)
                    balance -= part_amount
            open_invoices.append((customer, inv, balance, on))

    active = [c for c in customers if plan.customers[c]["active"]]
    # cancelled and draft invoices: never sales, never open
    for _ in range(6):
        on = today - timedelta(days=rng.randint(1, 400))
        plan.invoice(
            rng.choice(active),
            bill_amount(rng),
            on=on,
            due=on + timedelta(days=30),
            status="CANCELLED",
        )
    for _ in range(4):
        plan.invoice(
            rng.choice(active),
            bill_amount(rng),
            on=today - timedelta(days=rng.randint(0, 20)),
            due=None,
            status="DRAFT",
        )

    # opening bills on the first day of this financial year (some part or fully paid)
    for i, customer in enumerate(rng.sample(active, 12), start=1):
        amount = bill_amount(rng)
        ob = plan.opening_bill(
            customer,
            amount,
            on=fy_start,
            due=fy_start + timedelta(days=30),
            number=f"OB-TEST-{i:03d}",
        )
        r = rng.random()
        if r < 0.25:
            plan.settle(
                customer, amount, fy_start + timedelta(days=rng.randint(10, 90)), opening_bill=ob
            )
        elif r < 0.6:
            plan.settle(
                customer,
                money(amount // 2 // 100 * 100),
                fy_start + timedelta(days=rng.randint(10, 120)),
                opening_bill=ob,
            )

    # advances: receipts not (fully) allocated
    for customer in rng.sample(active, 4):
        amount = money(rng.choice((5000, 10000, 15000, 25000)))
        plan.receipt(
            customer, amount, on=today - timedelta(days=rng.randint(1, 60)), unallocated=amount
        )
    big = [o for o in open_invoices if o[2] >= 20000 and plan.customers[o[0]]["active"]]
    customer, inv, balance, on = rng.choice(big)
    receipt = plan.receipt(
        customer, money(20000), on=today - timedelta(days=5), unallocated=money(5000)
    )
    plan.allocate(money(15000), receipt=receipt, invoice=inv, customer=customer)
    # a payment allocated and then undone: the receipt is unallocated again (an advance)
    customer, inv, balance, on = rng.choice([o for o in big if o[1] != inv])
    receipt = plan.receipt(
        customer, money(5000), on=today - timedelta(days=20), unallocated=money(5000)
    )
    plan.allocate(
        money(5000),
        receipt=receipt,
        invoice=inv,
        customer=customer,
        undone_on=today - timedelta(days=18),
    )

    # credit notes: four unapplied, one partly applied to an open invoice
    for customer in rng.sample(active, 4):
        amount = money(rng.randint(5, 50) * 100)
        plan.credit_note(
            customer, amount, on=today - timedelta(days=rng.randint(1, 90)), unapplied=amount
        )
    customer, inv, balance, on = rng.choice([o for o in big if o[1] != inv])
    note = plan.credit_note(
        customer, money(3000), on=today - timedelta(days=10), unapplied=money(1000)
    )
    plan.allocate(money(2000), credit_note=note, invoice=inv, customer=customer)

    # daily counter sales since the start of last financial year, fully paid
    day = last_fy_start
    while day <= today:
        if day >= today - timedelta(days=1) or rng.random() > 0.08:
            count = rng.randint(3, 9) + (1 if day.weekday() >= 5 else 0)
            count += 1 if day >= fy_start else 0  # this year sells a little more
            count += 2 if day.month in (10, 11) else 0  # festive season
            for _ in range(count):
                amount = counter_amount(rng)
                inv = plan.invoice(counter, amount, on=day, due=day, counter=True)
                plan.settle(counter, amount, day, invoice=inv, counter=True)
        day += timedelta(days=1)

    # products, warehouses, stock, reorder rules
    uoms = {code: plan.uom(code) for code in ("pcs", "box")}
    products = [(plan.product(f"{n} (TEST)", sku, uoms[u]), kind) for n, sku, u, kind in PRODUCTS]
    warehouses = [plan.warehouse(n, c) for n, c in WAREHOUSES]
    stock: dict[tuple[int, int], Decimal] = {}
    for product, kind in products:
        for warehouse in warehouses:
            r = rng.random()
            if r < 0.15:
                continue  # no stock row
            high = {"phone": 40, "acc": 500, "box": 60}[kind]
            stock[(product, warehouse)] = Decimal(0 if r < 0.25 else rng.randint(1, high))
    rules: list[tuple[int, int, Decimal]] = []
    for product, kind in products:
        base = {"phone": 5, "acc": 25, "box": 8}[kind]
        for warehouse in rng.sample(warehouses, rng.choice((1, 1, 2))):
            rules.append((product, warehouse, Decimal(base * rng.randint(1, 3))))
    low = set(rng.sample(range(len(rules)), LOW_STOCK_ITEMS))
    for i, (product, warehouse, min_qty) in enumerate(rules):
        if i in low:  # at or below min_qty (one exactly at it, some zero)
            qty = min_qty if i == min(low) else Decimal(rng.randint(0, int(min_qty) - 1))
            stock[(product, warehouse)] = qty
        else:
            current = stock.get((product, warehouse), Decimal(0))
            if current <= min_qty:
                stock[(product, warehouse)] = min_qty + rng.randint(1, int(min_qty) * 3)
        plan.reorder(product, warehouse, min_qty, min_qty * 4)
    for (product, warehouse), qty in stock.items():
        plan.stock(product, warehouse, Decimal(qty))
    info["low_stock"] = LOW_STOCK_ITEMS
    info["products"] = {p: n for (p, _), (n, *_) in zip(products, PRODUCTS, strict=True)}
    return plan, info


# --- writing -------------------------------------------------------------------------------


async def write(admin_url: str, plan: Plan) -> dict[str, list[int]]:
    """Delete the company's rows, then insert the plan (one transaction per database)."""
    conns = {db: await connect(admin_url, db) for db in DATABASES}
    try:
        ids: dict[str, list[int]] = {}
        for table in OWN_ID:
            n = len(plan.rows[table])
            ids[table] = [
                r[0]
                for r in await conns[DB_OF[table]].fetch(
                    "SELECT nextval(pg_get_serial_sequence($1, 'id')) FROM generate_series(1, $2)",
                    f"public.{table}",
                    n,
                )
            ]
        # No foreign key crosses databases, so each database is replaced on its own.
        for db in ("master_data_db", "sales_db", "inventory_db"):
            conn = conns[db]
            async with conn.transaction():
                for table in DELETE_ORDER[db]:
                    await conn.execute(
                        f"DELETE FROM public.{table} WHERE company_id = $1", COMPANY_ID
                    )
                for table in INSERT_ORDER[db]:
                    await _copy(conn, table, plan.rows[table], ids)
        return ids
    finally:
        for conn in conns.values():
            await conn.close()


async def _copy(
    conn: asyncpg.Connection, table: str, rows: list[dict[str, Any]], ids: dict[str, list[int]]
) -> None:
    if not rows:
        return
    columns = list(rows[0])
    fks = FKS.get(table, {})
    records = []
    for row in rows:
        if list(row) != columns:
            raise RuntimeError(f"{table}: rows with different columns")
        values = []
        for column in columns:
            value = row[column]
            if column == "id":
                value = ids[table][value - 1]
            elif column in fks and value is not None:
                value = ids[fks[column]][value - 1]
            values.append(value)
        records.append((COMPANY_ID, 1, 1, *values))
    await conn.copy_records_to_table(
        table,
        schema_name="public",
        records=records,
        columns=["company_id", "created_by", "updated_by", *columns],
    )


async def counts(admin_url: str) -> dict[str, int]:
    """Row counts of the demo company (read as admin, after the seed)."""
    queries = {
        "customers_active": ("master_data_db", "customer", "active AND NOT cash_sale"),
        "customers_inactive": ("master_data_db", "customer", "NOT active"),
        "products": ("master_data_db", "product", "true"),
        "warehouses": ("master_data_db", "warehouse", "true"),
        "invoices_posted_credit": (
            "sales_db",
            "sales_invoice",
            "status = 'POSTED' AND NOT counter_sale",
        ),
        "invoices_counter": ("sales_db", "sales_invoice", "counter_sale"),
        "invoices_cancelled": ("sales_db", "sales_invoice", "status = 'CANCELLED'"),
        "invoices_draft": ("sales_db", "sales_invoice", "status = 'DRAFT'"),
        "opening_bills": ("sales_db", "sales_opening_bill", "true"),
        "receipts": ("sales_db", "sales_receipt", "true"),
        "advances": ("sales_db", "sales_receipt", "unallocated_amount > 0"),
        "credit_notes": ("sales_db", "sales_credit_note", "true"),
        "allocations": ("sales_db", "receivable_allocation", "true"),
        "stock_rows": ("inventory_db", "stock_balance", "true"),
        "reorder_rules": ("inventory_db", "reorder_rule", "true"),
    }
    result: dict[str, int] = {}
    conns = {db: await connect(admin_url, db) for db in DATABASES}
    try:
        for key, (db, table, where) in queries.items():
            result[key] = await conns[db].fetchval(
                f"SELECT count(*) FROM public.{table} WHERE company_id = $1 AND ({where})",
                COMPANY_ID,
            )
    finally:
        for conn in conns.values():
            await conn.close()
    return result


# --- reading back through the agent's read path --------------------------------------------


def _unique(plan: Plan, query: str) -> bool:
    q = query.casefold()
    hits = [
        c
        for c in plan.customers.values()
        if c["active"]
        and (q in c["name"].casefold() or (c["short"] and q in c["short"].casefold()))
    ]
    return len(hits) == 1


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _inr(amount: Decimal | None) -> str:
    if amount is None:
        return "-"
    sign = "-" if amount < 0 else ""
    rupees, paise = f"{abs(amount):.2f}".split(".")
    head, tail = rupees[:-3], rupees[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}Rs {','.join([*groups, tail])}.{paise}"


async def read_back(plan: Plan, info: dict[str, Any], table_counts: dict[str, int]) -> int:
    from tests.erp_db import erp_adapter, replica_contract

    from agentic_platform.business.capabilities import BusinessPermission as BP
    from agentic_platform.business.dates import DatePeriod
    from agentic_platform.business.service import BusinessReadService
    from agentic_platform.core.config import ErpSettings
    from agentic_platform.security import IdentityResolver, RoleCatalog
    from agentic_platform.services.clock import ClockService

    roles = {"owner": [BP.CUSTOMER_READ, BP.SALES_READ, BP.INVENTORY_READ, BP.WAREHOUSE_ALL]}
    principal = IdentityResolver(RoleCatalog(roles)).resolve(
        user_id="demo-owner", tenant_id=str(COMPANY_ID), roles=("owner",), auth_method="token"
    )
    settings = ErpSettings(
        adapter="postgres", business_timezone=TZ, query_timeout_seconds=15, max_rows=200
    )
    service = BusinessReadService(
        erp_adapter(replica_contract()), settings=settings, clock=ClockService()
    )
    problems = 0

    def ok(result: Any, what: str) -> Any:
        nonlocal problems
        if result.status != "SUCCESS":
            problems += 1
            print(f"  !! {what}: {result.status} {result.reason}")
            return None
        return result.data

    try:
        print("\nInvenLogic TEST replica demo company (TEST data only)")
        print(f"  Tenant / company id : {COMPANY_ID}")
        print(f"  Today (Asia/Kolkata): {info['today']}")
        c = table_counts
        print(
            f"  Customers           : {c['customers_active'] + c['customers_inactive']} "
            f"({c['customers_inactive']} inactive) + 1 'Counter Sale (TEST)'"
        )
        print(
            f"  Invoices            : {c['invoices_posted_credit']} posted credit, "
            f"{c['invoices_counter']} counter sales, {c['invoices_cancelled']} cancelled, "
            f"{c['invoices_draft']} draft"
        )
        print(
            f"  Opening bills       : {c['opening_bills']}; receipts {c['receipts']} "
            f"({c['advances']} with an advance); credit notes {c['credit_notes']}; "
            f"allocations {c['allocations']}"
        )
        print(
            f"  Products/warehouses : {c['products']} products, {c['warehouses']} warehouses,"
            f" {c['stock_rows']} stock rows, {c['reorder_rules']} reorder rules"
        )

        print("\nRead back through the agent (agentic_erp_reader, BusinessReadService):")
        for label, period in (
            ("Today's sales", DatePeriod.TODAY),
            ("Yesterday's sales", DatePeriod.YESTERDAY),
            ("This week's sales", DatePeriod.THIS_WEEK),
            ("This month's sales", DatePeriod.THIS_MONTH),
            ("This FY's sales", DatePeriod.THIS_YEAR),
        ):
            data = ok(await service.daily_sales(principal, period=period), label)
            if data is not None:
                print(f"  {label:<20}: {_inr(data.total.amount)} ({data.document_count} invoices)")
        data = ok(
            await service.daily_sales(
                principal, period=DatePeriod.THIS_YEAR, compare_with="same_period_last_year"
            ),
            "comparison",
        )
        if data is not None:
            print(
                f"  FY vs last FY       : {_inr(data.current.total and data.current.total.amount)}"
                f" vs {_inr(data.baseline.total and data.baseline.total.amount)}"
                f" ({data.direction} {data.change_percent}%)"
            )
        data = ok(await service.low_stock_products(principal), "low stock")
        low_example = "Redmi Note 13 Pro ka stock kitna hai?"
        if data is not None and data.items:
            item = data.items[0]
            product = (item.product.display_name or "").removesuffix(" (TEST)")
            place = (item.warehouse.display_name or "").removesuffix(" (TEST)")
            low_example = f"{product} ka stock {place} mein kitna hai?"
        if data is not None:
            print(f"  Low-stock items     : {data.item_count}")
            for line in data.items:
                print(
                    f"    - {line.product.display_name} @ {line.warehouse.display_name}: "
                    f"{_qty(line.quantity)} {line.unit or ''} (min {_qty(line.threshold)})"
                )
            if data.item_count != info["low_stock"]:
                problems += 1
                print(f"  !! expected {info['low_stock']} low-stock items")

        # five example customers: the three largest balances, one settled, one in credit
        candidates = [
            (ref, c["name"].removesuffix(" (TEST)"), plan.due[ref])
            for ref, c in plan.customers.items()
            if c["active"] and ref != info["counter"]
        ]
        candidates = [x for x in candidates if _unique(plan, x[1])]
        by_due = sorted(candidates, key=lambda x: (-x[2], x[0]))
        picks = by_due[:3] + [x for x in by_due if x[2] == 0][:1] + by_due[-1:]
        print("\n  Example customers (outstanding via business.customer_outstanding):")
        for _, name, expected in picks:
            data = ok(await service.customer_outstanding(principal, customer_name=name), name)
            if data is not None:
                got = data.outstanding.amount
                flag = "" if got == expected else f"  !! expected {_inr(expected)}"
                problems += 1 if flag else 0
                print(f"    - {name:<28} {_inr(got):>16}{flag}")
        top = picks[0][1]
        data = ok(await service.customer_open_invoices(principal, customer_name=top), top)
        if data is not None:
            print(
                f"  {top}: {data.invoice_count} pending bills, {data.overdue_count} overdue "
                f"({_inr(data.overdue_balance and data.overdue_balance.amount)})"
            )
        for name in ("Rahul", "Amit Kumar", "Priya"):
            result = await service.customer_outstanding(principal, customer_name=name)
            print(
                f"  '{name}' -> {result.status}: "
                f"{', '.join(c.display_name or c.ref for c in result.candidates)}"
            )
        result = await service.product_stock(principal, product_name="iPhone 15")
        print(
            f"  'iPhone 15' (product) -> {result.status}: "
            f"{', '.join(c.display_name or c.ref for c in result.candidates)}"
        )
        result = await service.product_stock(principal, product_name="Samsung Galaxy S24")
        if (data := ok(result, "product stock")) is not None:
            print(
                f"  Samsung Galaxy S24 stock: {_qty(data.total_quantity)} {data.unit} in "
                f"{data.warehouse_count} warehouses"
            )
    finally:
        await service.aclose()

    print(f"\nTry these in the chatbot (tenant {COMPANY_ID}):")
    for question in (
        f"{picks[0][1]} ka outstanding kitna hai?",
        f"{picks[1][1]} ke pending bills dikhao",
        f"{picks[0][1]} ke overdue bills kaunse hain?",
        "Rahul ka outstanding batao  (ambiguous: Rahul Sharma / Rahul Verma)",
        "Priya ke pending bills dikhao  (ambiguous: Priya Patel / Priya Nair)",
        "Amit Kumar ka balance kitna hai?  (ambiguous: Amit Kumar / Amit Kumar Traders)",
        "Aaj ki sales batao",
        "Kal ki sales kitni thi?",
        "Is hafte ki sales",
        "Is mahine ki sales kitni hui?",
        "Pichhle mahine ki sales batao",
        "Pichhle saal se is saal ki sales compare karo",
        "Samsung Galaxy S24 ka stock kitna hai?",
        low_example,
        "Low stock products kaunse hain?",
        "Shop Counter mein kya stock hai?",
        "iPhone 15 ka stock?  (ambiguous product: iPhone 15 128GB / iPhone 15 Pro 256GB)",
    ):
        print(f"  - {question}")
    return problems


# --- main ----------------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> int:
    global COMPANY_ID
    parser = argparse.ArgumentParser(prog="scripts.seed_erp_demo")
    parser.add_argument(
        "--company-id",
        type=int,
        default=COMPANY_ID,
        help=f"TEST company id (900000000-999999999; default {COMPANY_ID}).",
    )
    parser.add_argument(
        "--customers", type=int, default=100, help="Number of customers, 100-1000 (default 100)."
    )
    args = parser.parse_args(argv)
    if not 900_000_000 <= args.company_id <= 999_999_999:
        parser.error("--company-id must be a TEST id between 900000000 and 999999999")
    if not 100 <= args.customers <= 1000:
        parser.error("--customers must be between 100 and 1000")
    COMPANY_ID = args.company_id
    # The platform's own warnings (e.g. an AMBIGUOUS read) are not part of this summary.
    logging.getLogger().addHandler(logging.NullHandler())
    admin_url = os.environ.get(ADMIN_ENV) or DEFAULT_ADMIN_URL
    reader_url = os.environ.setdefault(READER_ENV, DEFAULT_READER_URL)
    os.environ.setdefault(ADMIN_ENV, admin_url)
    try:
        check_urls(admin_url, reader_url)
        await guard(admin_url)
    except Refused as exc:
        print(f"REFUSED (nothing written): {exc}")
        return 2
    today = datetime.now(ZoneInfo(TZ)).date()
    plan, info = build(today, args.customers)
    started = datetime.now()
    await write(admin_url, plan)
    seconds = (datetime.now() - started).total_seconds()
    rows = sum(len(r) for r in plan.rows.values())
    print(f"Seeded company {COMPANY_ID} on the TEST replica: {rows} rows in {seconds:.1f} s")
    problems = await read_back(plan, info, await counts(admin_url))
    if problems:
        print(f"\n{problems} read-back problem(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
