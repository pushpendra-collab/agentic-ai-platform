# ERP Schema Integration

> **For:** engineers who will build the ERP read layer, and the ERP owner who confirms each mapping.
> **Last updated:** 2026-10-30 (Phase 22: the ERP is InvenLogic; mappings drafted from the owner-confirmed Schema Atlas, § 3 and § 4 updated). **Status: MAPPINGS DRAFTED, NONE VERIFIED ON THE LIVE ERP.** The company owner confirmed the *Inven Logic Schema Atlas* (PostgreSQL 18.6) as the real ERP on 2026-10-30. Its mappings and the owner-adopted rules are in [erp/invenlogic_contract.json](erp/invenlogic_contract.json) (DRAFT) and explained in [erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md). They were checked against a TEST replica of the schema, not against the live catalog, so by § 2 rule 1 none is VERIFIED yet. *Earlier record (2026-10-10, Phase 16), superseded where it says no contract exists:* **Status: NO MAPPING VERIFIED.** Phase 14 added the typed ERP access contract that each register row below becomes part of ([erp/ERP_CONTRACT.md](erp/ERP_CONTRACT.md), ADR-063); no contract has been supplied (Q1). Live ERP access is blocked (Q1, [erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](erp/LIVE_ERP_VERIFICATION_CHECKLIST.md)). Phase 18 (2026-10-10, documentation only) added the verified capability matrix and the future read contract ([erp/ERP_CONTRACT.md](erp/ERP_CONTRACT.md) § 5–§ 10) and two rows to the rules register in § 4 (Q29, Q30). No mapping was added.

## 1. Current state

* **Phase 22 (2026-10-30)** supersedes the next three points where they disagree: the Schema Atlas was read (2026-10-29) and confirmed by the owner as the real ERP; `PostgresErpAdapter` has InvenLogic readers (`business/adapters/invenlogic.py`), one connection per database (`master_data`, `sales`, `inventory`); the fake adapter was deleted. Table and column names in § 3 come from the Atlas. The live ERP has not been contacted.
* **Phase 10** added the read layer's contracts ([BUSINESS_INTEGRATION.md](BUSINESS_INTEGRATION.md)): six capabilities, typed `*Record` shapes an adapter must produce, and `PostgresErpAdapter`, which has **no mapping**. Each register row below becomes one `ErpQuery` plus a row mapping once it's VERIFIED. Until then the capability answers `UNAVAILABLE / RULE_NOT_VERIFIED`. The fake adapter's TEST data is not a model of the ERP.
* The repository doesn't contain the Schema Atlas the brief mentions either. No table or column name below comes from it or from guesswork.
* The Agent DB keeps exactly one database setting pair (ADR-056). The ERP has its own `ERP_*` settings, which Alembic never reads (ADR-059). Two things connect to an ERP, both read only: the operator tool `scripts/inspect_erp_security.py` (ADR-058) and the business layer with `ERP_ADAPTER=postgres`. The business layer is off by default, and has no mapping yet.

## 2. Rules for every mapping

1. A mapping is **VERIFIED** only when the table, its columns and types, and its tenant and warehouse scope columns were read from the live ERP catalog, and the ERP owner confirmed the meaning. Record the inspector run (date, database, Flyway version).
2. A business rule that the ERP owner hasn't confirmed returns **`UNAVAILABLE / RULE NOT VERIFIED`**. It's never approximated. Examples of such rules: which invoice statuses count as outstanding, what "overdue" and "low stock" mean, the business-day boundary and time zone for "today's sales", and how returns, credit notes, cancellations and drafts are treated.
3. The LLM never sees ERP SQL and never computes a business figure. Figures come from typed tool results with a deterministic source.
4. Reads run as the ERP read-only role, with the tenant (and warehouse scope) taken from the authenticated principal. They're never taken from tool arguments or the user's text.

## 3. Mapping register

| Entity | ERP table(s) | Key columns | Tenant column / mechanism | Warehouse scope | Status columns and meaning | Status |
|---|---|---|---|---|---|---|
| Customers | `master_data_db.customer` | `id`; names `ledger_name`, `display_name`, `short_name` (match only) | `company_id`, RLS via `invenlogic_company_id()` | n/a | `active` | DRAFT (TEST replica) |
| Invoices | `sales_db.sales_invoice`, `sales_opening_bill`, `receivable_allocation` | `id`, `invoice_number` / `bill_number`, `customer_id`, `grand_total` / `amount`, `due_date`; allocations `amount`, `undone_at` | as above | not attributed (Q29) | `status` (`POSTED` counted; `CANCELLED`, `DRAFT`, `DISCARDED` not; values to confirm) | DRAFT (TEST replica) |
| Invoice items | `sales_db.sales_invoice_line` (exists, not read) | — | — | — | — | NOT MAPPED (no capability) |
| Payments | `sales_db.sales_receipt`, `sales_credit_note` | `unallocated_amount`, `unapplied_amount` (outstanding only) | as above | n/a | `status` | DRAFT for outstanding; payment history NOT MAPPED |
| Sales | `sales_db.sales_invoice` | `invoice_date`, `grand_total` (GST inclusive) | as above | not attributed (Q29) | `status` = `POSTED` | DRAFT (TEST replica) |
| Products | `master_data_db.product`, `uom` | `id`, `name`, `sku` (match only), `uom_id` → `uom.code` | as above | n/a | `active` | DRAFT (TEST replica) |
| Stock | `inventory_db.stock_balance`, `reorder_rule` | `product_id`, `quantity_on_hand`; `min_qty` | as above | `warehouse_id` | reorder rule `active` | DRAFT (TEST replica) |
| Warehouses | `master_data_db.warehouse` | `id`, `name`, `code` (match only) | as above | `id` | `active` | DRAFT (TEST replica) |
| Inventory movements | — | — | — | — | — | NOT MAPPED |
| Suppliers | `purchase_db` (not read) | — | — | n/a | — | NOT MAPPED |
| Purchase data | `purchase_db` (not read) | — | — | — | — | NOT MAPPED |
| GST data | `accounting_db` (not read) | — | — | — | — | NOT MAPPED |

*Phase 22: the cells above come from the Atlas and [erp/invenlogic_contract.json](erp/invenlogic_contract.json); the exact column list and types the gate checks are in the contract. "DRAFT (TEST replica)" means checked by the gate on the TEST replica, not on the live catalog. Before 2026-10-30 every row was BLOCKED.*

## 4. Business rules register

*Phase 22 (2026-10-30): for InvenLogic, the owner adopted the proposed defaults for every rule from "Customer outstanding" to "Same period last year" below; the definitions are in [erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md) § 3 and in the contract's `rules`. In short: time zone Asia/Kolkata; weeks start Monday; calendar months; years are **Indian financial years** (new rule `year_is_financial_year_april`, 1 April – 31 March), so `year_is_calendar_year` is **not** confirmed (Q28 answered for InvenLogic); sales are POSTED invoices' GST-inclusive `grand_total`, credit notes not deducted; overdue = due date before today; low stock = at or below an active reorder rule's `min_qty`. They stay "NOT VERIFIED" in the sense of § 2 until the contract is marked VERIFIED after the live checks; the accountant should re-confirm them. Customer purchase, product-level sales, warehouse attribution (Q29) and history coverage (Q30) are still not defined.*

| Rule | Needed by | Definition (from the ERP owner) | Status |
|---|---|---|---|
| Customer outstanding | "Rahul Gupta ka outstanding kitna hai?" | — | NOT VERIFIED → `UNAVAILABLE / RULE NOT VERIFIED` |
| Pending bill / invoice balance | "Rahul Gupta ka pending bill kitna hai?" | — | NOT VERIFIED |
| Daily sales (time zone, statuses, returns) | "Aaj ka total sales batao." | — | NOT VERIFIED |
| Product stock (which warehouses, reserved stock) | "iPhone 15 ka stock kitna hai?" | — | NOT VERIFIED |
| Warehouse naming ("Godown 2") | "Godown 2 mein stock kitna hai?" | — | NOT VERIFIED |
| Low-stock threshold | "Low-stock products kaunse hain?" | — | NOT VERIFIED |
| Overdue customer | overdue list | — | NOT VERIFIED |
| Overdue invoice (`overdue_by_due_date`, Phase 11): open invoice with due date before the business date? grace days? | "Usme se kitna overdue hai?" | — | NOT VERIFIED → no overdue figure from the live adapter |
| Period sales = sum of business days (`sales_period_is_sum_of_days`, Phase 11) | "Is mahine ki sales?" | — | NOT VERIFIED → RULE_NOT_VERIFIED |
| Week start (`week_starts_monday`, Phase 11) | "Is hafte ki sales?" | — | NOT VERIFIED → RULE_NOT_VERIFIED |
| Business time zone (`ERP_BUSINESS_TIMEZONE`) | every "today" / relative period | — (the fake ERP tests use a TEST zone only) | NOT VERIFIED |
| Invoice gross / paid amounts (optional `total`, `paid`, Phase 11) | "kitna pay ho chuka hai?" (on open invoices) | — | NOT VERIFIED |
| Customer/product name resolution (ambiguous, not found) | all named queries | — | NOT VERIFIED |
| Calendar year (`year_is_calendar_year`, Phase 16): "this/last year" = 1 Jan–31 Dec, not a financial year | "Pichhle saal ki sales?" | — | NOT VERIFIED → RULE_NOT_VERIFIED (Q28) |
| Same period last year (`same_period_last_year_by_date`, Phase 16): same calendar dates, 29 Feb → 28 Feb, same days for to-date periods | "Is month ki sales pichhle saal isi month se compare karo" | — | NOT VERIFIED → RULE_NOT_VERIFIED |
| Customer purchase (which documents, cancellations, returns, credit notes; units vs distinct products) | "Rahul Gupta ne pichhle saal kitne products kharide?" | — | NOT DEFINED: no capability (Q27) |
| Product-level sales (sales lines per product) | "Kaunse products zyada bike?" | — | NOT DEFINED: no capability (Q1, Q27) |
| Warehouse/outlet attribution of sales, invoices, balances, payments and purchase documents, and where a user's warehouse assignment comes from (Phase 18) | every non-stock read for a warehouse-limited user | — | NOT DEFINED (Q29); new document-level capabilities answer only scope ALL until then |
| ERP history coverage: first complete business date, archiving (Phase 18) | "Pichhle saal se compare karo" on old periods | — | NOT DEFINED (Q30); a period without records has no total |
