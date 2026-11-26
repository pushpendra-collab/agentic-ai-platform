"""Capability readers for the InvenLogic ERP (reader set `invenlogic_v1`, 2026-10-30).

InvenLogic keeps one PostgreSQL database per service (docs/erp/INVENLOGIC_MAPPING.md).
The readers use three of them, by contract database key:

    master_data  master_data_db  customer, product, warehouse, uom
    sales        sales_db        sales_invoice, sales_opening_bill, sales_receipt,
                                 sales_credit_note, receivable_allocation
    inventory    inventory_db    stock_balance, reorder_rule

`auth_db`, `purchase_db` and `accounting_db` are never read. A foreign key never crosses
databases (customer_id in sales_db is a plain id), so a reader that needs a name reads
the rows in one database and the names in `master_data` with a second query, then joins
them here. A row whose name is missing makes the whole answer MALFORMED (never a guess).

Safety (on top of `ReadOnlyErpDatabase`: read-only role, read-only transaction, the
statement guard, the security gate, RLS on every table):

* every statement is an `ErpQuery` constant below; arguments are bound, never formatted;
* every statement filters `company_id` with the principal's tenant itself, so a mistake
  in the ERP's RLS still cannot leak another company's rows. The platform tenant id IS the
  InvenLogic `company_id` (decimal digits); any other tenant id is refused;
* statuses: every document status read is checked against the contract's closed list
  (`invoice_statuses`, shared by invoices, opening bills, receipts and credit notes). A
  value outside it fails the read (`ErpMalformedError`); it is never mapped by guess.

The business rules the SQL applies are the owner-approved defaults of 2026-10-30 (contract
`rules`; docs/erp/INVENLOGIC_MAPPING.md § 3):

* sales of a day = sum of `grand_total` (GST inclusive) of posted invoices dated that day;
  drafts, discarded and cancelled invoices are excluded; credit notes are NOT deducted;
* an invoice's paid amount = its live allocations (`receivable_allocation`, not undone);
  balance = grand_total - paid; it is open while the balance is above zero;
* opening bills (balances brought forward) are pending bills too (`sales_opening_bill`);
* customer outstanding = open invoice balances + open opening-bill balances - unallocated
  receipts (advances) - unapplied credit notes, of posted documents;
* stock = `stock_balance.quantity_on_hand` per warehouse (all batches, expired included);
* low stock = an active `reorder_rule` whose warehouse holds at most `min_qty`;
* customers (2026-11-25) = `customer` rows that are not cash-sale placeholders
  (`cash_sale`), counted active and inactive; customers billed in a period = different
  `customer_id`s of posted invoices dated in it, without counter sales (`counter_sale`)
  and without cash-sale placeholder customers: those are walk-in bills, counted as bills.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from agentic_platform.business.adapters.postgres import (
    ErpQuery,
    PostgresErpAdapter,
    ReadOnlyErpDatabase,
)
from agentic_platform.business.context import BusinessContext, ScopeKind
from agentic_platform.business.erp_contract import ErpContract, UnexpectedStatusError
from agentic_platform.business.ports import ErpMalformedError, ErpSafetyViolationError
from agentic_platform.core.errors import ConfigurationError

READER_SET = "invenlogic_v1"
MASTER, SALES, INVENTORY = "master_data", "sales", "inventory"
DATABASE_KEYS = (MASTER, SALES, INVENTORY)

_DIGITS = re.compile(r"^[1-9][0-9]{0,17}$")
_INVOICE_REF = re.compile(r"^si:([1-9][0-9]{0,17})$")
_OPENING_REF = re.compile(r"^ob:([1-9][0-9]{0,17})$")

_TENANT = "company_id = CAST(CAST(:tenant AS text) AS bigint)"


# --- master_data ---------------------------------------------------------------------------

FIND_CUSTOMERS = ErpQuery(
    "invenlogic.find_customers",
    "SELECT c.id::text AS ref, c.company_id::text AS tenant_id,"
    " left(coalesce(nullif(btrim(c.display_name), ''), c.ledger_name), 200) AS display_name"
    " FROM public.customer c WHERE c." + _TENANT + " AND c.active"
    " AND (c.ledger_name ILIKE :pattern ESCAPE '\\' OR c.display_name ILIKE :pattern ESCAPE '\\'"
    " OR c.short_name ILIKE :pattern ESCAPE '\\')"
    " ORDER BY display_name, c.id LIMIT :limit",
)
GET_CUSTOMERS = ErpQuery(
    "invenlogic.get_customers",
    "SELECT c.id::text AS ref, c.company_id::text AS tenant_id,"
    " left(coalesce(nullif(btrim(c.display_name), ''), c.ledger_name), 200) AS display_name"
    " FROM public.customer c WHERE c." + _TENANT + " AND c.id = ANY (CAST(:ids AS bigint[]))",
)
FIND_PRODUCTS = ErpQuery(
    "invenlogic.find_products",
    "SELECT p.id::text AS ref, p.company_id::text AS tenant_id, left(p.name, 200) AS display_name,"
    " left(u.code, 32) AS unit"
    " FROM public.product p LEFT JOIN public.uom u"
    " ON u.id = p.uom_id AND u.company_id = p.company_id"
    " WHERE p." + _TENANT + " AND p.active"
    " AND (p.name ILIKE :pattern ESCAPE '\\' OR p.sku ILIKE :pattern ESCAPE '\\')"
    " ORDER BY p.name, p.id LIMIT :limit",
)
GET_PRODUCTS = ErpQuery(
    "invenlogic.get_products",
    "SELECT p.id::text AS ref, p.company_id::text AS tenant_id, left(p.name, 200) AS display_name,"
    " left(u.code, 32) AS unit"
    " FROM public.product p LEFT JOIN public.uom u"
    " ON u.id = p.uom_id AND u.company_id = p.company_id"
    " WHERE p." + _TENANT + " AND p.id = ANY (CAST(:ids AS bigint[]))",
)
FIND_WAREHOUSES = ErpQuery(
    "invenlogic.find_warehouses",
    "SELECT w.id::text AS ref, w.company_id::text AS tenant_id, left(w.name, 200) AS display_name"
    " FROM public.warehouse w WHERE w." + _TENANT + " AND w.active"
    " AND (w.name ILIKE :pattern ESCAPE '\\' OR w.code ILIKE :pattern ESCAPE '\\')"
    " ORDER BY w.name, w.id LIMIT :limit",
)
GET_WAREHOUSES = ErpQuery(
    "invenlogic.get_warehouses",
    "SELECT w.id::text AS ref, w.company_id::text AS tenant_id, left(w.name, 200) AS display_name"
    " FROM public.warehouse w WHERE w." + _TENANT + " AND w.id = ANY (CAST(:ids AS bigint[]))",
)

# Customers of the company, without cash-sale placeholders (walk-in buyers). Counts only.
CUSTOMER_COUNT = ErpQuery(
    "invenlogic.customer_count",
    "SELECT c.company_id::text AS tenant_id,"
    " count(*) FILTER (WHERE c.active)::int AS active_count,"
    " count(*) FILTER (WHERE NOT c.active)::int AS inactive_count"
    " FROM public.customer c WHERE c." + _TENANT + " AND NOT c.cash_sale"
    " GROUP BY c.company_id",
)

# --- sales ---------------------------------------------------------------------------------

# Status values of the documents a read relies on, outside the contract's closed list.
UNKNOWN_STATUSES = ErpQuery(
    "invenlogic.unknown_statuses",
    "SELECT d.status FROM ("
    " SELECT i.status FROM public.sales_invoice i WHERE i." + _TENANT
    + " AND (CAST(:customer AS bigint) IS NULL OR i.customer_id = CAST(:customer AS bigint))"
    " AND (CAST(:day_from AS date) IS NULL OR i.invoice_date >= CAST(:day_from AS date))"
    " AND (CAST(:day_to AS date) IS NULL OR i.invoice_date < CAST(:day_to AS date))"
    " UNION ALL SELECT b.status FROM public.sales_opening_bill b WHERE b." + _TENANT
    + " AND CAST(:with_balances AS boolean)"
    " AND (CAST(:customer AS bigint) IS NULL OR b.customer_id = CAST(:customer AS bigint))"
    " UNION ALL SELECT r.status FROM public.sales_receipt r WHERE r." + _TENANT
    + " AND CAST(:with_balances AS boolean)"
    " AND (CAST(:customer AS bigint) IS NULL OR r.customer_id = CAST(:customer AS bigint))"
    " UNION ALL SELECT n.status FROM public.sales_credit_note n WHERE n." + _TENANT
    + " AND CAST(:with_balances AS boolean)"
    " AND (CAST(:customer AS bigint) IS NULL OR n.customer_id = CAST(:customer AS bigint))"
    ") d WHERE NOT (d.status = ANY (CAST(:known AS text[]))) LIMIT 1",
)

# Open pending bills: posted invoices and opening bills with a balance above zero.
# Payments are summed once per bill for the whole company (one pass over the allocations,
# then a hash join), not with a subquery per bill: `receivable_allocation` has no index on
# the bill columns, so a per-bill subquery scanned the table once per invoice (2026-11-25:
# 4-6 s for 200 customers, past the ERP timeout under load). Same rows, same amounts.
OPEN_BILLS = ErpQuery(
    "invenlogic.open_bills",
    "WITH live AS (SELECT a.sales_invoice_id, a.sales_opening_bill_id, a.amount"
    " FROM public.receivable_allocation a WHERE a." + _TENANT + " AND a.undone_at IS NULL),"
    " invoice_paid AS (SELECT lv.sales_invoice_id AS id, sum(lv.amount) AS paid FROM live lv"
    " WHERE lv.sales_invoice_id IS NOT NULL GROUP BY lv.sales_invoice_id),"
    " bill_paid AS (SELECT lv.sales_opening_bill_id AS id, sum(lv.amount) AS paid FROM live lv"
    " WHERE lv.sales_opening_bill_id IS NOT NULL GROUP BY lv.sales_opening_bill_id)"
    " SELECT * FROM ("
    " SELECT 'si:' || i.id::text AS ref, i.company_id::text AS tenant_id,"
    " coalesce(i.invoice_number, 'ID-' || i.id::text) AS number,"
    " i.customer_id::text AS customer_ref, i.grand_total AS total,"
    " coalesce(ip.paid, 0) AS paid,"
    " i.invoice_date AS issued_on, i.due_date AS due_on"
    " FROM public.sales_invoice i LEFT JOIN invoice_paid ip ON ip.id = i.id"
    " WHERE i." + _TENANT
    + " AND i.status = ANY (CAST(:posted AS text[]))"
    " AND (CAST(:customer AS bigint) IS NULL OR i.customer_id = CAST(:customer AS bigint))"
    " AND (CAST(:number AS text) IS NULL OR lower(i.invoice_number) = lower(CAST(:number AS text)))"
    " UNION ALL"
    " SELECT 'ob:' || b.id::text, b.company_id::text, b.bill_number,"
    " b.customer_id::text, b.amount,"
    " coalesce(bp.paid, 0),"
    " b.bill_date, b.due_date"
    " FROM public.sales_opening_bill b LEFT JOIN bill_paid bp ON bp.id = b.id"
    " WHERE b." + _TENANT
    + " AND b.status = ANY (CAST(:posted AS text[]))"
    " AND (CAST(:customer AS bigint) IS NULL OR b.customer_id = CAST(:customer AS bigint))"
    " AND (CAST(:number AS text) IS NULL OR lower(b.bill_number) = lower(CAST(:number AS text)))"
    ") bills WHERE bills.total - bills.paid > 0"
    " ORDER BY bills.due_on NULLS LAST, bills.issued_on, bills.ref LIMIT :limit",
)

OUTSTANDING = ErpQuery(
    "invenlogic.outstanding",
    "SELECT"
    " (SELECT coalesce(sum(i.grand_total - coalesce((SELECT sum(a.amount)"
    "   FROM public.receivable_allocation a WHERE a.company_id = i.company_id"
    "   AND a.sales_invoice_id = i.id AND a.undone_at IS NULL), 0)), 0)"
    "  FROM public.sales_invoice i WHERE i." + _TENANT
    + "  AND i.customer_id = CAST(:customer AS bigint)"
    "  AND i.status = ANY (CAST(:posted AS text[]))) AS invoices_due,"
    " (SELECT coalesce(sum(b.amount - coalesce((SELECT sum(a.amount)"
    "   FROM public.receivable_allocation a WHERE a.company_id = b.company_id"
    "   AND a.sales_opening_bill_id = b.id AND a.undone_at IS NULL), 0)), 0)"
    "  FROM public.sales_opening_bill b WHERE b." + _TENANT
    + "  AND b.customer_id = CAST(:customer AS bigint)"
    "  AND b.status = ANY (CAST(:posted AS text[]))) AS opening_due,"
    " (SELECT coalesce(sum(r.unallocated_amount), 0) FROM public.sales_receipt r"
    "  WHERE r." + _TENANT + " AND r.customer_id = CAST(:customer AS bigint)"
    "  AND r.status = ANY (CAST(:posted AS text[]))) AS advances,"
    " (SELECT coalesce(sum(n.unapplied_amount), 0) FROM public.sales_credit_note n"
    "  WHERE n." + _TENANT + " AND n.customer_id = CAST(:customer AS bigint)"
    "  AND n.status = ANY (CAST(:posted AS text[]))) AS credits,"
    " now() AS as_of",
)

SALES_BY_DAY = ErpQuery(
    "invenlogic.sales_by_day",
    "SELECT i.invoice_date AS business_date, i.company_id::text AS tenant_id,"
    " sum(i.grand_total) AS total, count(*)::int AS document_count"
    " FROM public.sales_invoice i WHERE i." + _TENANT
    + " AND i.status = ANY (CAST(:posted AS text[]))"
    " AND i.invoice_date >= CAST(:day_from AS date) AND i.invoice_date < CAST(:day_to AS date)"
    " GROUP BY i.invoice_date, i.company_id ORDER BY i.invoice_date LIMIT :limit",
)

# Posted invoices of [day_from, day_to) per customer and counter flag (ids and counts
# only); the cash-sale placeholders among those customers come from master data.
CUSTOMERS_BILLED = ErpQuery(
    "invenlogic.customers_billed",
    "SELECT i.company_id::text AS tenant_id, i.customer_id::text AS customer_ref,"
    " i.counter_sale AS counter, count(*)::int AS invoices"
    " FROM public.sales_invoice i WHERE i." + _TENANT
    + " AND i.status = ANY (CAST(:posted AS text[]))"
    " AND i.invoice_date >= CAST(:day_from AS date) AND i.invoice_date < CAST(:day_to AS date)"
    " GROUP BY i.company_id, i.customer_id, i.counter_sale",
)
CASH_SALE_CUSTOMERS = ErpQuery(
    "invenlogic.cash_sale_customers",
    "SELECT c.id::text AS ref FROM public.customer c WHERE c." + _TENANT
    + " AND c.cash_sale AND c.id = ANY (CAST(:ids AS bigint[]))",
)

# --- inventory -----------------------------------------------------------------------------

_SCOPE = (
    "(CAST(:all_warehouses AS boolean) OR b.warehouse_id = ANY (CAST(:warehouses AS bigint[])))"
)

STOCK = ErpQuery(
    "invenlogic.stock",
    "SELECT b.company_id::text AS tenant_id, b.product_id::text AS product_ref,"
    " b.warehouse_id::text AS warehouse_ref, sum(b.quantity_on_hand) AS quantity"
    " FROM public.stock_balance b WHERE b." + _TENANT + " AND " + _SCOPE
    + " AND (CAST(:product AS bigint) IS NULL OR b.product_id = CAST(:product AS bigint))"
    " AND (CAST(:warehouse AS bigint) IS NULL OR b.warehouse_id = CAST(:warehouse AS bigint))"
    " GROUP BY b.company_id, b.product_id, b.warehouse_id"
    " ORDER BY b.warehouse_id, b.product_id LIMIT :limit",
)

LOW_STOCK = ErpQuery(
    "invenlogic.low_stock",
    "SELECT r.company_id::text AS tenant_id, r.product_id::text AS product_ref,"
    " r.warehouse_id::text AS warehouse_ref, r.min_qty AS threshold,"
    " coalesce((SELECT sum(b.quantity_on_hand) FROM public.stock_balance b"
    "  WHERE b.company_id = r.company_id AND b.warehouse_id = r.warehouse_id"
    "  AND b.product_id = r.product_id), 0) AS quantity"
    " FROM public.reorder_rule r WHERE r." + _TENANT + " AND r.active"
    " AND (CAST(:all_warehouses AS boolean)"
    " OR r.warehouse_id = ANY (CAST(:warehouses AS bigint[])))"
    " AND (CAST(:product AS bigint) IS NULL OR r.product_id = CAST(:product AS bigint))"
    " AND coalesce((SELECT sum(b.quantity_on_hand) FROM public.stock_balance b"
    "  WHERE b.company_id = r.company_id AND b.warehouse_id = r.warehouse_id"
    "  AND b.product_id = r.product_id), 0) <= r.min_qty"
    " ORDER BY r.warehouse_id, r.product_id LIMIT :limit",
)

QUERIES: tuple[tuple[str, ErpQuery], ...] = (
    (MASTER, FIND_CUSTOMERS), (MASTER, GET_CUSTOMERS), (MASTER, FIND_PRODUCTS),
    (MASTER, GET_PRODUCTS), (MASTER, FIND_WAREHOUSES), (MASTER, GET_WAREHOUSES),
    (SALES, UNKNOWN_STATUSES), (SALES, OPEN_BILLS), (SALES, OUTSTANDING),
    (SALES, SALES_BY_DAY), (INVENTORY, STOCK), (INVENTORY, LOW_STOCK),
    (MASTER, CUSTOMER_COUNT), (SALES, CUSTOMERS_BILLED), (MASTER, CASH_SALE_CUSTOMERS),
)  # fmt: skip


# --- helpers -------------------------------------------------------------------------------


def like_pattern(name: str) -> str:
    """A case-insensitive substring pattern for ILIKE with the ESCAPE character '\\'."""
    escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _id(ref: str | None) -> int | None:
    """An InvenLogic id from an opaque ref, or None when the ref is not one."""
    if ref is None or not _DIGITS.match(ref):
        return None
    return int(ref)


class InvenLogicCore:
    """The shared read code; one per adapter. Capability readers below delegate to it."""

    def __init__(
        self, databases: Mapping[str | None, ReadOnlyErpDatabase], contract: ErpContract
    ) -> None:
        missing = [key for key in DATABASE_KEYS if key not in databases]
        if missing:
            raise ConfigurationError(f"InvenLogic readers need the databases {missing}.")
        if contract.invoice_statuses is None or contract.currency is None:
            raise ConfigurationError("InvenLogic readers need invoice_statuses and currency.")
        self._db = databases
        self._statuses = contract.invoice_statuses
        self._currency = contract.currency
        excluded = {*self._statuses.cancelled, *self._statuses.draft}
        self._posted = sorted(self._statuses.all_values() - excluded)
        self._known = sorted(self._statuses.all_values())

    # --- plumbing ---------------------------------------------------------------------

    @staticmethod
    def tenant(ctx: BusinessContext) -> str:
        if not _DIGITS.match(ctx.tenant_id):
            # The platform tenant id must be the InvenLogic company id (docs § 2).
            raise ErpSafetyViolationError("platform tenant is not an InvenLogic company id")
        return ctx.tenant_id

    async def fetch(
        self, key: str, query: ErpQuery, ctx: BusinessContext, params: Mapping[str, Any],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        tenant = self.tenant(ctx)
        rows = await self._db[key].fetch(
            query, {**params, "tenant": tenant}, max_rows=limit, tenant_id=tenant
        )
        return rows

    async def check_statuses(
        self, ctx: BusinessContext, *, customer: int | None = None,
        day_from: date | None = None, day_to: date | None = None, with_balances: bool = False,
    ) -> None:  # fmt: skip
        """Refuse the read if a document it relies on has a status outside the contract."""
        rows = await self.fetch(SALES, UNKNOWN_STATUSES, ctx, {
            "customer": customer, "day_from": day_from, "day_to": day_to,
            "with_balances": with_balances, "known": self._known,
        }, limit=1)  # fmt: skip
        for row in rows:
            try:
                self._statuses.group_of(str(row["status"]))
            except UnexpectedStatusError as exc:
                raise ErpMalformedError("document status outside the ERP contract") from exc

    def scope(self, ctx: BusinessContext) -> dict[str, Any]:
        scope = ctx.warehouse_scope
        ids = [int(r) for r in scope.warehouse_refs if _DIGITS.match(r)]
        return {"all_warehouses": scope.kind is ScopeKind.ALL, "warehouses": ids}

    async def names(
        self, ctx: BusinessContext, query: ErpQuery, ids: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        wanted = sorted({int(i) for i in ids if _DIGITS.match(i)})
        if not wanted:
            return {}
        rows = await self.fetch(MASTER, query, ctx, {"ids": wanted}, limit=len(wanted))
        return {row["ref"]: row for row in rows}

    @staticmethod
    def named(row: Mapping[str, Any], names: Mapping[str, Mapping[str, Any]], key: str) -> Any:
        found = names.get(row[key])
        if found is None:
            # Stock or a bill whose product / warehouse / customer does not exist for this
            # company in master data: the answer cannot be trusted.
            raise ErpMalformedError(f"no master data row for {key}")
        return found

    # --- master data ------------------------------------------------------------------

    async def find(self, ctx: BusinessContext, query: ErpQuery, name: str, limit: int) -> list[dict[str, Any]]:  # noqa: E501
        if not name.strip():
            return []
        return await self.fetch(
            MASTER, query, ctx, {"pattern": like_pattern(name), "limit": limit}, limit=limit
        )

    async def get(self, ctx: BusinessContext, query: ErpQuery, ref: str) -> dict[str, Any] | None:
        if _id(ref) is None:
            return None
        return (await self.names(ctx, query, [ref])).get(ref)

    # --- sales ------------------------------------------------------------------------

    async def outstanding(self, ctx: BusinessContext, customer_ref: str) -> dict[str, Any] | None:
        customer = _id(customer_ref)
        if customer is None:
            return None
        await self.check_statuses(ctx, customer=customer, with_balances=True)
        rows = await self.fetch(
            SALES, OUTSTANDING, ctx, {"customer": customer, "posted": self._posted}, limit=1
        )
        row = rows[0]
        amount = (
            Decimal(row["invoices_due"]) + Decimal(row["opening_due"])
            - Decimal(row["advances"]) - Decimal(row["credits"])
        )  # fmt: skip
        return {
            "tenant_id": self.tenant(ctx), "customer_ref": customer_ref, "amount": amount,
            "currency": self._currency, "as_of": row["as_of"],
        }  # fmt: skip

    async def open_bills(
        self, ctx: BusinessContext, customer_ref: str | None, limit: int,
        invoice_number: str | None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        customer = _id(customer_ref)
        if customer_ref is not None and customer is None:
            return []
        await self.check_statuses(ctx, customer=customer, with_balances=True)
        rows = await self.fetch(SALES, OPEN_BILLS, ctx, {
            "customer": customer, "posted": self._posted, "number": invoice_number,
            "limit": limit,
        }, limit=limit)  # fmt: skip
        names = await self.names(ctx, GET_CUSTOMERS, (r["customer_ref"] for r in rows))
        bills = []
        for row in rows:
            name = self.named(row, names, "customer_ref")["display_name"]
            total, paid = Decimal(row["total"]), Decimal(row["paid"])
            bills.append({
                "tenant_id": row["tenant_id"], "ref": row["ref"], "number": row["number"],
                "customer_ref": row["customer_ref"], "customer_name": name,
                "balance": total - paid, "total": total, "paid": paid,
                "currency": self._currency, "issued_on": row["issued_on"],
                "due_on": row["due_on"],
            })  # fmt: skip
        return bills

    async def sales_by_day(
        self, ctx: BusinessContext, start: date, end: date, limit: int
    ) -> list[dict[str, Any]]:
        await self.check_statuses(ctx, day_from=start, day_to=end)
        rows = await self.fetch(SALES, SALES_BY_DAY, ctx, {
            "posted": self._posted, "day_from": start, "day_to": end, "limit": limit,
        }, limit=limit)  # fmt: skip
        return [
            {"tenant_id": r["tenant_id"], "business_date": r["business_date"],
             "total": Decimal(r["total"]), "currency": self._currency,
             "document_count": int(r["document_count"])}
            for r in rows
        ]  # fmt: skip

    async def customers_billed(
        self, ctx: BusinessContext, start: date, end: date
    ) -> dict[str, Any]:
        await self.check_statuses(ctx, day_from=start, day_to=end)
        rows = await self.fetch(SALES, CUSTOMERS_BILLED, ctx, {
            "posted": self._posted, "day_from": start, "day_to": end,
        })  # fmt: skip
        ids = sorted({int(r["customer_ref"]) for r in rows if _DIGITS.match(r["customer_ref"])})
        cash: set[str] = set()
        if ids:
            found = await self.fetch(MASTER, CASH_SALE_CUSTOMERS, ctx, {"ids": ids})
            cash = {r["ref"] for r in found}
        customers: set[str] = set()
        invoices = counter = 0
        for row in rows:
            if row["counter"] or row["customer_ref"] in cash:
                counter += int(row["invoices"])  # walk-in sale: a bill, never a customer
            else:
                customers.add(row["customer_ref"])
                invoices += int(row["invoices"])
        return {
            "tenant_id": self.tenant(ctx), "start": start, "end": end,
            "customer_count": len(customers), "invoice_count": invoices,
            "counter_sale_count": counter,
        }  # fmt: skip

    # --- master data counts -----------------------------------------------------------

    async def customer_count(self, ctx: BusinessContext) -> dict[str, Any]:
        rows = await self.fetch(MASTER, CUSTOMER_COUNT, ctx, {}, limit=1)
        row = rows[0] if rows else {}
        return {
            "tenant_id": self.tenant(ctx),
            "active_count": int(row.get("active_count", 0)),
            "inactive_count": int(row.get("inactive_count", 0)),
        }

    # --- inventory --------------------------------------------------------------------

    async def stock(
        self, ctx: BusinessContext, limit: int, *, product: str | None = None,
        warehouse: str | None = None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        product_id, warehouse_id = _id(product), _id(warehouse)
        if (product is not None and product_id is None) or (
            warehouse is not None and warehouse_id is None
        ):
            return []
        rows = await self.fetch(INVENTORY, STOCK, ctx, {
            **self.scope(ctx), "product": product_id, "warehouse": warehouse_id,
            "limit": limit,
        }, limit=limit)  # fmt: skip
        return await self._with_names(ctx, rows)

    async def low_stock(
        self, ctx: BusinessContext, limit: int, product: str | None
    ) -> list[dict[str, Any]]:
        product_id = _id(product)
        if product is not None and product_id is None:
            return []
        rows = await self.fetch(INVENTORY, LOW_STOCK, ctx, {
            **self.scope(ctx), "product": product_id, "limit": limit,
        }, limit=limit)  # fmt: skip
        return await self._with_names(ctx, rows, low=True)

    async def _with_names(
        self, ctx: BusinessContext, rows: Sequence[Mapping[str, Any]], low: bool = False
    ) -> list[dict[str, Any]]:
        products = await self.names(ctx, GET_PRODUCTS, (r["product_ref"] for r in rows))
        warehouses = await self.names(ctx, GET_WAREHOUSES, (r["warehouse_ref"] for r in rows))
        out = []
        for row in rows:
            product = self.named(row, products, "product_ref")
            warehouse = self.named(row, warehouses, "warehouse_ref")
            record = {
                "tenant_id": row["tenant_id"], "product_ref": row["product_ref"],
                "product_name": product["display_name"], "warehouse_ref": row["warehouse_ref"],
                "warehouse_name": warehouse["display_name"],
                "quantity": Decimal(row["quantity"]), "unit": product["unit"],
            }  # fmt: skip
            if low:
                record["threshold"] = Decimal(row["threshold"])
            out.append(record)
        return out


# --- capability readers (one per capability; registered only when VERIFIED) -------------------


class _Reader:
    capability = ""

    def __init__(self, core: InvenLogicCore) -> None:
        self._core = core


class _Customers(_Reader):
    async def find_customers(self, ctx: BusinessContext, name: str, limit: int) -> list[Any]:
        return await self._core.find(ctx, FIND_CUSTOMERS, name, limit)

    async def get_customer(self, ctx: BusinessContext, ref: str) -> Any:
        return await self._core.get(ctx, GET_CUSTOMERS, ref)


class CustomerOutstandingReader(_Customers):
    capability = "customer_outstanding"

    async def customer_outstanding(self, ctx: BusinessContext, customer_ref: str) -> Any:
        return await self._core.outstanding(ctx, customer_ref)


class CustomerOpenInvoicesReader(_Customers):
    capability = "customer_open_invoices"

    async def open_invoices(
        self, ctx: BusinessContext, customer_ref: str | None, limit: int,
        invoice_number: str | None = None,
    ) -> list[Any]:  # fmt: skip
        return await self._core.open_bills(ctx, customer_ref, limit, invoice_number)


class DailySalesReader(_Reader):
    capability = "daily_sales"

    async def daily_sales(self, ctx: BusinessContext, business_date: date) -> Any:
        rows = await self._core.sales_by_day(
            ctx, business_date, date.fromordinal(business_date.toordinal() + 1), 2
        )
        return rows[0] if rows else None

    async def sales_by_day(
        self, ctx: BusinessContext, start: date, end: date, limit: int
    ) -> list[Any]:
        return await self._core.sales_by_day(ctx, start, end, limit)


class _Products(_Reader):
    async def find_products(self, ctx: BusinessContext, name: str, limit: int) -> list[Any]:
        return await self._core.find(ctx, FIND_PRODUCTS, name, limit)

    async def get_product(self, ctx: BusinessContext, ref: str) -> Any:
        return await self._core.get(ctx, GET_PRODUCTS, ref)


class _Warehouses(_Reader):
    async def find_warehouses(self, ctx: BusinessContext, name: str, limit: int) -> list[Any]:
        return await self._core.find(ctx, FIND_WAREHOUSES, name, limit)

    async def get_warehouse(self, ctx: BusinessContext, ref: str) -> Any:
        return await self._core.get(ctx, GET_WAREHOUSES, ref)


class ProductStockReader(_Products):
    capability = "product_stock"

    async def product_stock(self, ctx: BusinessContext, product_ref: str, limit: int) -> list[Any]:
        return await self._core.stock(ctx, limit, product=product_ref)


class WarehouseStockReader(_Warehouses):
    capability = "warehouse_stock"

    async def warehouse_stock(
        self, ctx: BusinessContext, warehouse_ref: str, limit: int
    ) -> list[Any]:
        return await self._core.stock(ctx, limit, warehouse=warehouse_ref)


class LowStockReader(_Products):
    capability = "low_stock_products"

    async def low_stock(
        self, ctx: BusinessContext, limit: int, product_ref: str | None = None
    ) -> list[Any]:
        return await self._core.low_stock(ctx, limit, product_ref)


class CustomerCountReader(_Reader):
    capability = "customer_count"

    async def customer_count(self, ctx: BusinessContext) -> Any:
        return await self._core.customer_count(ctx)

    async def customers_billed(self, ctx: BusinessContext, start: date, end: date) -> Any:
        return await self._core.customers_billed(ctx, start, end)


READERS: tuple[type[_Reader], ...] = (
    CustomerOutstandingReader,
    CustomerOpenInvoicesReader,
    DailySalesReader,
    ProductStockReader,
    WarehouseStockReader,
    LowStockReader,
    CustomerCountReader,
)


def register_invenlogic_readers(adapter: PostgresErpAdapter, contract: ErpContract) -> list[str]:
    """Register a reader for every capability the contract maps VERIFIED; return their
    names. Capabilities not VERIFIED stay UNAVAILABLE / RULE_NOT_VERIFIED."""
    if contract.reader_set != READER_SET:
        return []
    verified = [r for r in READERS if contract.capability_verified(r.capability)]
    if not verified:
        return []
    core = InvenLogicCore(adapter.databases, contract)
    for reader in verified:
        adapter.register_reader(reader(core))
    return [r.capability for r in verified]


__all__ = [
    "DATABASE_KEYS",
    "QUERIES",
    "READERS",
    "READER_SET",
    "InvenLogicCore",
    "like_pattern",
    "register_invenlogic_readers",
]
