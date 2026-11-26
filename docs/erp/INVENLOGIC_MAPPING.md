# InvenLogic ERP integration (2026-10-30)

> **For:** the company owner, the InvenLogic database owner, and engineers who connect the agent to the live ERP.
> **Status:** the readers, the contract and the tests are done and pass on a TEST replica of the ERP. **The live ERP has NOT been contacted.** It is activated only after the owner supplies the items in § 6.
> **Source:** *Inven Logic Schema Atlas* (Postgres 18.6, six service databases, read 2026-10-29), confirmed by the owner on 2026-10-30 as the company's real ERP.
> **Related:** [invenlogic_contract.json](invenlogic_contract.json) · [ERP_CONTRACT.md](ERP_CONTRACT.md) · [../../docker/erp-test/README.md](../../docker/erp-test/README.md) · `src/agentic_platform/business/adapters/invenlogic.py` · ADR-067

## 1. What the agent reads

InvenLogic keeps one PostgreSQL database per service. The agent reads three of them, as a read-only role, and never writes:

| Contract key | Database | Tables read |
|---|---|---|
| `master_data` | `master_data_db` | `customer`, `product`, `uom`, `warehouse` |
| `sales` | `sales_db` | `sales_invoice`, `sales_opening_bill`, `sales_receipt`, `sales_credit_note`, `receivable_allocation` |
| `inventory` | `inventory_db` | `stock_balance`, `reorder_rule` |

It never reads `auth_db` (users, password hashes, sessions, signing keys), `purchase_db` or `accounting_db`. Foreign keys never cross databases. So a reader that needs a name (for example the customer of an invoice) reads the rows in one database and the names in `master_data` with a second query, and joins them in code.

## 2. Tenants

Every table has `company_id`, and RLS is forced with `company_id = invenlogic_company_id()`. The platform tenant id **is** the InvenLogic company id (decimal digits). The adapter sets the RLS setting for each read transaction. Every statement also filters `company_id` itself, so a mistake in the ERP's RLS still cannot leak another company's rows. A tenant id that is not a company id is refused (`DEPENDENCY_ERROR` / `ERP_SAFETY_VIOLATION`).

**To confirm on the live ERP:** the name of the setting that `invenlogic_company_id()` reads. The contract says `invenlogic.company_id` (the TEST replica's value). The security gate checks that the live function really reads the contract's setting, and refuses the connection if it does not.

## 3. The six tools and their rules (owner-approved defaults, 2026-10-30)

The owner asked to adopt the proposed defaults. The accountant should re-confirm them; each one is a `rules` entry in the contract.

| Tool (capability) | Answer | Rule |
|---|---|---|
| `business.daily_sales` (`daily_sales`) | sales of a day, week, month or year, and year-on-year comparison | sum of `sales_invoice.grand_total` (**GST inclusive**) of **POSTED** invoices by `invoice_date`. DRAFT, DISCARDED and CANCELLED are excluded. **Credit notes and returns are not deducted** (gross sales). |
| `business.customer_outstanding` | what a customer owes | open invoice balances + open opening-bill balances − unallocated amounts of posted receipts (advances) − unapplied amounts of posted credit notes |
| `business.customer_open_invoices` | pending bills, overdue, paid so far | paid = live allocations (`receivable_allocation`, not undone); balance = total − paid; open while above zero; opening bills are included (refs `si:<id>` / `ob:<id>`); overdue = due date before today |
| `business.product_stock` | stock of a product per warehouse | `stock_balance.quantity_on_hand` (all batches, expired included, `not_for_sale` warehouses included) |
| `business.warehouse_stock` | stock in one warehouse | same |
| `business.low_stock_products` | items at or below their reorder level | an active `reorder_rule` whose warehouse holds **at most** `min_qty` (base unit). An item with no rule is never "low". |

Periods: time zone **Asia/Kolkata**, weeks start Monday, months are calendar months, and years are **Indian financial years (1 April – 31 March)**. Currency: INR.

**Statuses.** The contract lists `POSTED` (counted), `CANCELLED` (void), and `DRAFT` / `DISCARDED` (never posted). The Atlas does not list the actual status values, so these are assumptions. Any other value in a document a read relies on fails that read with `MALFORMED_RESPONSE`; it is never guessed. Before go-live, run `SELECT DISTINCT status` on the four document tables and correct the contract.

## 4. Safety layers (unchanged, now per database)

Read-only role → `default_transaction_read_only` → one read-only transaction per read, always rolled back → only reviewed `ErpQuery` constants (one SELECT, no write or DDL words) → at most `max_rows + 1` rows. Before the first read, the security gate checks each database separately:

- the database name;
- that it is not the Agent DB;
- no superuser, no BYPASSRLS, no write/CREATE privilege, owns nothing;
- the PostgreSQL major version;
- every mapped table and column, with its exact type;
- that RLS is enabled with policies calling `invenlogic_company_id()`, and that the function reads the confirmed setting.

Any problem refuses the connection. A unit test checks that every column the reader SQL touches is mapped in the contract, so the gate covers every column that is read.

## 5. What the tests prove (TEST replica)

`docker/erp-test` runs PostgreSQL **18.6** with the same tables, columns, types and RLS as the Atlas, and a read-only role. The business tests now run the real SQL there, through the real gate and RLS. Highlights:

- **Contract suite:** 18/18 checks. Connection failures are real (closed port).
- **Live readiness:** `scripts/erp_readiness --live` on the replica reports `READY_FOR_ACTIVATION` for all three databases.
- **RLS:** the reader role sees no row without the setting, and only its own company's rows with it.
- **Read-only:** the reader role cannot write.
- **Gate refusals:** the gate refuses a superuser, and refuses a contract whose column type differs from the catalog.
- **Status:** an unknown status fails the read.
- **Outstanding rule:** checked with opening bills, advances, credit notes and undone payments.

## 6. To connect the live ERP (owner / DBA)

Run `python -m scripts.erp_readiness --contract docs/erp/invenlogic_contract.json`. Today it lists what is missing: **A, B, C, D, E, F, G, T**. The business rules are already in the contract.

1. **A, G:** add `ownership` (owner name, application owner, `is_intended_erp: true`, confirmation).
2. **B, C, D, T:** add `databases` for `master_data`, `sales` and `inventory`: host, port, database name, `postgres_major_version: 18`, and the Flyway version (`V31`, `V36`, `V24` in the Atlas).
3. **E, F:** the DBA creates a read-only role in the three databases: `LOGIN`, `default_transaction_read_only=on`, `CONNECT` + `USAGE` on `public` + `SELECT` on the tables in § 1 only, and no other grants. Add `access` (`read_only_role`, `delivery: secret_store`, `tls_mode: verify-full`). The password goes to the secret store, never into a file in this repository.
4. Confirm the RLS setting name (§ 2) and the status values (§ 3), and correct the contract if they differ.
5. Run discovery: set `ERP_INSPECT_DATABASE_URLS` (JSON key → URL, from the secret store), then `python -m scripts.erp_readiness --contract <file> --live`. Fix every FAIL.
6. Set `status: VERIFIED` and each capability `status: VERIFIED`; re-run `--live` until it exits 0.
7. Run the contract suite against dedicated TEST records in the ERP: `ERP_CONTRACT_TEST_CONTRACT`, `ERP_CONTRACT_TEST_FIXTURES`, `ERP_DATABASE_URLS`, then `pytest tests/erp_contract/test_real_erp_contract.py`.
8. Configure the agent:
   - `ERP_ADAPTER=postgres`
   - `ERP_CONTRACT_PATH`
   - `ERP_DATABASE_URLS` (secret store; use an IP or a name that resolves to IPv4, see below)
   - `ERP_BUSINESS_TIMEZONE=Asia/Kolkata`
   - `ERP_SSL_MODE=verify-full`
   - the business roles in `AUTH_ROLE_CATALOG_JSON`

   Use the InvenLogic company id as each user's tenant id.

**Windows note:** `localhost` in a database URL can first try IPv6 and stall about 2 s per new connection when Postgres listens on IPv4 only. Use `127.0.0.1` or the server's IP.

## 7. Not covered yet

- Product-wise sales, a customer's purchase history, payment history, purchases and suppliers, and GST/accounting reads. The tables exist (`sales_invoice_line`, `sales_receipt`, `purchase_db`, `accounting_db`), but no capability, permission or rule exists for them yet (ADR-066).
- Warehouse scope of sales and customer reads (Q29) and role permissions (Q8) remain owner decisions.
- Performance on large data: the status check and the outstanding sums scan a company's documents. Indexes on `(company_id, customer_id)` and `(company_id, invoice_date)` are an ERP-owner action; the agent never creates indexes.
