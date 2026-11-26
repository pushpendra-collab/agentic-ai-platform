"""Business capabilities and their permissions (Phase 10, ADR-059).

One capability = one typed read, one tool, one permission. The catalogue is justified
by the reference questions in the stakeholder briefs (PROJECT_STATUS § 3h/§ 3i); nothing
else is modelled. There is deliberately no write capability: creating or changing
invoices, payments, purchase orders, stock, customers or suppliers belongs to a later,
approval-gated action phase.

Business permissions are NOT granted by any built-in role: the business role catalogue
is still open (Q8). Operators grant them through `AUTH_ROLE_CATALOG_JSON`, for example
`{"member": [...], "sales_viewer": ["business.customer.read", "business.sales.read"]}`.
"""

from __future__ import annotations

from dataclasses import dataclass


class BusinessPermission:
    CUSTOMER_READ = "business.customer.read"  # customer balances and open invoices
    SALES_READ = "business.sales.read"  # sales summaries
    INVENTORY_READ = "business.inventory.read"  # stock levels, low stock
    # Scope, not an operation: see every warehouse of the tenant (context.py).
    WAREHOUSE_ALL = "business.warehouse.all"


@dataclass(frozen=True)
class Capability:
    name: str
    permission: str
    question: str  # the reference question it exists for
    # Which ERP rule a live adapter must have confirmed before it may answer.
    rule_to_verify: str


CUSTOMER_OUTSTANDING = Capability(
    "customer_outstanding",
    BusinessPermission.CUSTOMER_READ,
    "Rahul Gupta ka outstanding kitna hai?",
    "what counts as a customer's outstanding amount (statuses, credit notes, advances)",
)
CUSTOMER_OPEN_INVOICES = Capability(
    "customer_open_invoices",
    BusinessPermission.CUSTOMER_READ,
    "Rahul Gupta ka pending bill kitna hai?",
    "which invoices are open/pending and how their balance is computed",
)
DAILY_SALES = Capability(
    "daily_sales",
    BusinessPermission.SALES_READ,
    "Aaj ka total sales batao.",
    "business day boundary and time zone; which documents count (returns, drafts, cancelled)",
)
PRODUCT_STOCK = Capability(
    "product_stock",
    BusinessPermission.INVENTORY_READ,
    "iPhone 15 ka stock kitna hai?",
    "which quantity is 'stock' (on hand, available, reserved) per warehouse",
)
WAREHOUSE_STOCK = Capability(
    "warehouse_stock",
    BusinessPermission.INVENTORY_READ,
    "Godown 2 mein stock kitna hai?",
    "warehouse naming and which quantity is 'stock'",
)
LOW_STOCK = Capability(
    "low_stock_products",
    BusinessPermission.INVENTORY_READ,
    "Low-stock products kaunse hain?",
    "the low-stock threshold (reorder level) and where it is kept",
)
# 2026-11-25 (owner approval in the project chat): how many customers the company has,
# or, for a period, how many different customers were billed in it ("aaj kitne customer
# aaye?"). Counts only: no names, amounts or contact details.
CUSTOMER_COUNT = Capability(
    "customer_count",
    BusinessPermission.CUSTOMER_READ,
    "Mere paas kitne customers hain? / Aaj kitne customer aaye?",
    "which customers count (active, cash-sale placeholders) and what 'came' means (billed)",
)

CAPABILITIES: tuple[Capability, ...] = (
    CUSTOMER_OUTSTANDING,
    CUSTOMER_OPEN_INVOICES,
    DAILY_SALES,
    PRODUCT_STOCK,
    WAREHOUSE_STOCK,
    LOW_STOCK,
    CUSTOMER_COUNT,
)
