# ERP Access Contract (Q1)

> **For:** the ERP database owner and the ERP application owner, who supply and confirm the contract; and the engineers who activate the live ERP adapter.
> **Status (2026-10-30, Phase 22): the real ERP is InvenLogic; its contract exists, the live ERP is NOT CONTACTED.** The company owner confirmed the *Inven Logic Schema Atlas* (PostgreSQL 18.6) as the company's real ERP. Contract: [invenlogic_contract.json](invenlogic_contract.json) (DRAFT; three databases, owner-adopted rules). Mapping, rules and go-live steps: [INVENLOGIC_MAPPING.md](INVENLOGIC_MAPPING.md). `python -m scripts.erp_readiness --contract docs/erp/invenlogic_contract.json` reports BLOCKED with only **A, B, C, D, E, F, G, T** missing. The six readers exist (`business/adapters/invenlogic.py`, ADR-067) and pass on a TEST replica; the fake ERP was deleted. The Phase 14–21 notes below are kept as history; where they say "no contract", "no reader", "all 20 items A–T missing" or describe the fake ERP, they are superseded by this line.
> **Status (2026-10-05, Phase 14), superseded:** NO CONTRACT SUPPLIED. Real ERP integration is BLOCKED at the input gate.** `python -m scripts.erp_readiness` reports all 20 Q1 items (A–T) missing. No ERP has been contacted.
> **Phase 18 (2026-10-10, audit and contract preparation, documentation only):** re-checked against the code and tests: still BLOCKED, readiness exit 3, A–T missing. Added the verified capability matrix (§ 5), the specification for read capabilities not yet in the contract (§ 6), the owner decisions that block it (§ 7), what can be tested now (§ 8), what is blocked (§ 9) and the ordered integration plan (§ 10). **No ERP connection was attempted** (§ 11).
> **Phase 21 (2026-10-10, documentation only):** owner approval checklist added (§ 12). Readiness re-run offline: still BLOCKED, exit 3, A–T missing. No decision has been approved.
> **Related:** ADR-063 · ADR-066 (Proposed) · ADR-067 · [INVENLOGIC_MAPPING.md](INVENLOGIC_MAPPING.md) · [LIVE_ERP_VERIFICATION_CHECKLIST.md](LIVE_ERP_VERIFICATION_CHECKLIST.md) · [../SCHEMA_INTEGRATION.md](../SCHEMA_INTEGRATION.md) · [OPERATIONS_RUNBOOK.md](../../OPERATIONS_RUNBOOK.md) § 19

## 1. What the contract is

A JSON file, reviewed like code and kept with the deployment configuration (not in this repository), named by `ERP_CONTRACT_PATH`. It holds **no secrets**: the read-only credential arrives separately from the secret store as `ERP_DATABASE_URL`. A contract containing anything that looks like a password, token, API key or connection string is refused.

The platform never infers the contract. Every table, column, status value, rule and time zone in it comes from the ERP owner. The model and its checks are in `src/agentic_platform/business/erp_contract.py`. An empty starting point is `docs/erp/erp_contract.template.json`.

## 2. Q1 items and where they go

| Q1 | Item | Contract field |
|---|---|---|
| A | ERP owner confirmation | `ownership` (`erp_owner`, `application_owner`, `confirmation`) |
| B | database host | `database.host`, `database.port` |
| C | database name | `database.name` (must equal `ERP_EXPECTED_DATABASE`) |
| D | PostgreSQL version | `database.postgres_major_version` (checked against the server) |
| E | read-only role | `access.read_only_role` |
| F | credentials via a secure mechanism | `access.delivery = "secret_store"` (the credential itself is never in the file) |
| G | this database IS the intended ERP | `ownership.is_intended_erp = true` |
| H | tenant isolation mechanism | `tenant_isolation`: `column` (+ `column`) or `rls_session_setting` (+ `setting`) |
| I | warehouse/outlet isolation | `warehouse_isolation` (`column`, `ref_column`; out-of-scope = `not_found`, as the existing security model defines) |
| J | time zone rules | `time.erp_timestamp_storage`, `time.business_timezone` (IANA; must equal `ERP_BUSINESS_TIMEZONE`) |
| K | business ownership | `rules.business_ownership` |
| L | customer identification | `rules.customer_identification` |
| M | sales calculation | `rules.sales_calculation` (posted / cancelled / returned; tax-inclusive or -exclusive) |
| N | outstanding balance | `rules.outstanding_balance` (outstanding, paid, partially paid, overdue, cancelled, disputed) |
| O | stock calculation | `rules.stock_calculation` (available, reserved, damaged, warehouse vs outlet, transferable) |
| P | date/period rules | `rules.date_period` + `time.day_boundary`, `time.week_start`, `time.month` |
| Q | invoice status semantics | `rules.invoice_status_semantics` + `invoice_statuses` (closed list per meaning) |
| R | authorization expectations | `permission_expectations` (platform permissions stay authoritative) |
| S | views/functions/tables per capability | `capabilities.<capability>` (§ 3) |
| T | schema/version | `database.schema_version` (e.g. the Flyway version) |

`status` is `DRAFT` until every item is present and confirmed. Only a `VERIFIED` contract lets a capability be activated, and only for capabilities whose own mapping is `VERIFIED`.

## 3. Capability mappings (brief § 6)

*For InvenLogic (2026-10-30), the "TO BE SUPPLIED" cells below are filled in [invenlogic_contract.json](invenlogic_contract.json) and summarised in [INVENLOGIC_MAPPING.md](INVENLOGIC_MAPPING.md) § 1–§ 3. The table stays as the generic template.*

The six platform capabilities and their public contract (permission, result statuses, record shapes) are fixed. A mapping says how the ERP answers one capability; it can't change the permission and can only fill fields of the platform's record shapes (data minimization: a column that fills no record field and filters nothing is refused).

| Capability | Permission (fixed) | ERP source, columns, joins (2–4) | Tenant / warehouse boundary (5–6) | Status / date / time zone (7–9) | Null / ambiguity (10–11) | NOT_FOUND / UNAVAILABLE / RULE_NOT_VERIFIED (12–14, platform behaviour) |
|---|---|---|---|---|---|---|
| `customer_outstanding` | `business.customer.read` | TO BE SUPPLIED | TO BE SUPPLIED / n/a | outstanding statuses (N, Q) TO BE SUPPLIED | TO BE SUPPLIED / several name matches → AMBIGUOUS with candidates | no customer → NOT_FOUND; down/timeout → UNAVAILABLE; unconfirmed rule → RULE_NOT_VERIFIED |
| `customer_open_invoices` | `business.customer.read` | TO BE SUPPLIED | TO BE SUPPLIED / n/a | open statuses, cancelled/draft excluded (Q) TO BE SUPPLIED | TO BE SUPPLIED / as above | as above; overdue figures only with `overdue_by_due_date` confirmed |
| `daily_sales` | `business.sales.read` | TO BE SUPPLIED | TO BE SUPPLIED / TO BE SUPPLIED | posted/cancelled/returned, tax basis (M); business day (J, P) TO BE SUPPLIED | TO BE SUPPLIED / n/a | no sales row → NOT_FOUND; future date → VALIDATION_ERROR; periods need `sales_period_is_sum_of_days` |
| `product_stock` | `business.inventory.read` | TO BE SUPPLIED | TO BE SUPPLIED / warehouse column (I) TO BE SUPPLIED | which quantity is stock (O) TO BE SUPPLIED | TO BE SUPPLIED / AMBIGUOUS | out-of-scope warehouse → hidden (NOT_FOUND) |
| `warehouse_stock` | `business.inventory.read` | TO BE SUPPLIED | TO BE SUPPLIED / warehouse column (I) TO BE SUPPLIED | as above | TO BE SUPPLIED / AMBIGUOUS | out-of-scope warehouse → NOT_FOUND |
| `low_stock_products` | `business.inventory.read` | TO BE SUPPLIED | TO BE SUPPLIED / warehouse column (I) TO BE SUPPLIED | low-stock threshold (O) TO BE SUPPLIED | TO BE SUPPLIED / n/a | as above |

Until a capability's row is supplied, confirmed and checked against the live catalog, it answers **UNAVAILABLE / RULE_NOT_VERIFIED**, exactly as today.

## 4. How a contract becomes active (summary; full procedure in the runbook § 19)

1. The owner fills the contract (status `DRAFT`). `python -m scripts.erp_readiness --contract <file>` lists what is still missing. With A–C, E–H present it reports `READY_FOR_DISCOVERY`.
2. Discovery (read-only, catalog only): `... --live` with `ERP_INSPECT_DATABASE_URL` from the secret store. It runs the Phase 9 inspector (stops on any FAIL), then the adapter's security gate. No business row is read.
3. The owner confirms every rule and mapping; status `VERIFIED`. `... --live` must exit 0 (`READY_FOR_ACTIVATION`).
4. Engineers write one reviewed capability reader per VERIFIED mapping (explicit `ErpQuery` constants, `:tenant` filter, only record fields), and run the contract suite (`tests/erp_contract/`) against the real ERP's dedicated test records. The adapter refuses to register a reader whose mapping is not VERIFIED.
5. Configure `ERP_ADAPTER=postgres`, `ERP_CONTRACT_PATH`, `ERP_DATABASE_URL` (secret store), `ERP_EXPECTED_DATABASE`, `ERP_BUSINESS_TIMEZONE`, `ERP_SSL_MODE=verify-full`. Production refuses to start if the contract is missing, invalid, not VERIFIED, or disagrees with these settings.

**Multi-database ERP (InvenLogic, Phase 22).** The contract has `databases` (key → host, port, name, version, Flyway version) instead of `database`, and each mapped relation names its database. Discovery uses `ERP_INSPECT_DATABASE_URLS` and the agent uses `ERP_DATABASE_URLS` (JSON objects, database key → URL, from the secret store); production refuses keys that differ from the contract's databases. The gate runs per database. Step 4 is already done for InvenLogic: the readers are in the code and register for each mapping marked VERIFIED. Exact steps: [INVENLOGIC_MAPPING.md](INVENLOGIC_MAPPING.md) § 6.

Any index needed for acceptable read performance is an **ERP-owner action**: the platform documents the query plan; it never creates an index or changes the ERP schema.

## 5. Verified capability matrix (Phase 18, 2026-10-10)

**Evidence rule.** A row says a capability exists only where code in `src/` implements it and a passing test exercises it. The tests are in the Phase 18 baseline run: 480 passed, 1 skipped (PROJECT_STATUS § 3p). "Live adapter" means `PostgresErpAdapter` as it is today. It has **no reader**, so every method raises `ErpRuleNotVerifiedError` without connecting.

*Superseded on 2026-10-30 (Phase 22): rows 1–4, 7, 10, 11 and 14 (the six capabilities) now have InvenLogic readers, verified on a TEST replica (contract suite 18/18), not yet on the live ERP; "FAKE-TESTED" reads "tested on the TEST replica", since the fake ERP was deleted. For InvenLogic, Q28 is answered by the owner-adopted defaults: rows 2 and 3 use the Indian financial year (`year_is_financial_year_april`) and `same_period_last_year_by_date`. Rows 5, 6, 8, 9, 12, 13, 15 and 16 are unchanged (INVENLOGIC_MAPPING.md § 7). The matrix below is the Phase 18 record.*

| Class | Meaning |
|---|---|
| **FAKE-TESTED** | implemented and tested against the fake ERP (TEST data and TEST rules, which say nothing about the real ERP) |
| **NO LIVE READER** | defined in the port and in this contract, but not supported by the live adapter: UNAVAILABLE / RULE_NOT_VERIFIED |
| **BLOCKED-RULE** | blocked by business rules nobody has confirmed |
| **BLOCKED-OWNER** | blocked by information only the ERP owner can supply (relations, mechanisms, identifiers) |
| **REUSABLE** | already implemented elsewhere and reusable by a new capability |
| **NOT IMPLEMENTED** | no port method, record shape, capability, tool or fake record exists |

| # | Area | What exists in the code | Evidence (tests) | Fake ERP | Live adapter | Blocked by |
|---|---|---|---|---|---|---|
| 1 | Daily sales (today, yesterday, an explicit date) | capability `daily_sales`, `ErpReadPort.daily_sales`, tool `business.daily_sales`. The total is the ERP's figure as the mapping defines it | `test_business_service.py::test_daily_sales_today_uses_the_configured_business_timezone`, `::test_explicit_past_date_without_sales_is_not_found`; contract suite `todays_sales`, `timezone_boundary`, `invalid_date_range` | FAKE-TESTED | NO LIVE READER | BLOCKED-OWNER: Q1 J, M, P, S |
| 2 | Period sales (this/last week, month, year) | `ErpReadPort.sales_by_day`; the service sums the days. Rule-gated | `test_business_reasoning_unit.py::test_sales_period_is_the_sum_of_its_days`, `::test_sales_period_rules` | FAKE-TESTED (TEST rules) | NO LIVE READER | BLOCKED-RULE: `sales_period_is_sum_of_days`, `week_starts_monday`, `year_is_calendar_year` (Q28); BLOCKED-OWNER: Q1 M, P, S |
| 3 | Historical comparison (same period last year) | `compare_with` on `business.daily_sales`, `BusinessReadService._sales_comparison`. Both sides come from `sales_by_day` | `test_historical_business.py::test_missing_history_is_not_a_zero_and_gives_no_change`, `::test_comparison_without_its_rules_is_rule_not_verified`, `::test_tenant_isolation_holds_for_comparisons` | FAKE-TESTED | NO LIVE READER | BLOCKED-RULE: `same_period_last_year_by_date` (Q28); BLOCKED-OWNER: history coverage (Q30) |
| 4 | Customer lookup (name → one customer; AMBIGUOUS / NOT_FOUND) | `find_customers`, `get_customer`. Used **only** as the resolution step of rows 10–11; there is no customer tool of its own | contract suite `ambiguous_customer`, `missing_customer`; `test_business_service.py` | REUSABLE (the fake matches by case-insensitive substring: a TEST rule) | served only by a `customer_outstanding` or `customer_open_invoices` reader | BLOCKED-OWNER: Q1 L (matching and identification) |
| 5 | Customer sales history (what a customer bought from the company, how often, for how much) | **nothing**: no port method, record, capability, tool or fake record. The planner routes it to `unsupported` | `test_historical_business.py::test_graph_purchase_history_and_product_sales_are_unsupported` (proves only the refusal) | NOT IMPLEMENTED | — | BLOCKED-RULE: Q27; BLOCKED-OWNER: Q1 L, S; scope Q29 |
| 6 | Product sales and product performance ("kaunse products zyada bike?") | **nothing** (as row 5) | same test | NOT IMPLEMENTED | — | BLOCKED-RULE: Q27 (line-level semantics); BLOCKED-OWNER: Q1 S; scope Q29 |
| 7 | Inventory by warehouse (product stock, warehouse stock, low stock) | `product_stock`, `warehouse_stock`, `low_stock_products`; the adapter and the service filter by warehouse scope; out of scope = NOT_FOUND | `test_business_service.py::test_product_stock_sums_visible_warehouses`, `::test_warehouse_stock`, `::test_low_stock_products`, `::test_listed_scope_limits_totals_and_hides_other_warehouses`, `::test_stock_without_warehouse_scope_is_refused`; `test_business_reasoning_unit.py::test_stock_in_one_warehouse_and_is_it_low`; contract suite `warehouse_stock`, `unauthorized_warehouse` | FAKE-TESTED | NO LIVE READER | BLOCKED-OWNER: Q1 I, O, S |
| 8 | Inventory by outlet | **no separate concept.** Q1 item I says "warehouse/outlet", but the platform has one `warehouse` entity | — | not distinguished | — | BLOCKED-OWNER: are outlets warehouses in the ERP, or something else? (Q1 I, O) |
| 9 | Purchase orders, goods receipt notes, purchase invoices | **nothing** to read. The reserved `purchase` domain describes *actions* and is not implemented; such requests go to `unsupported` | `test_supervisor_graph.py::test_automatic_purchase_order_never_executes` (proves only the refusal) | NOT IMPLEMENTED | — | BLOCKED-RULE: Q9; BLOCKED-OWNER: Q1 S; permission Q8 |
| 10 | Customer outstanding balance | `customer_outstanding` | `test_business_service.py::test_customer_outstanding`; contract suite `customer_outstanding` | FAKE-TESTED | NO LIVE READER | BLOCKED-RULE: Q1 N; BLOCKED-OWNER: Q1 Q, S |
| 11 | Open / pending invoices (all customers, one customer, one invoice number; overdue; paid so far) | `customer_open_invoices`; optional `total` / `paid`; overdue gated by `overdue_by_due_date` | `test_business_service.py::test_open_invoices_truncated_has_no_total`; `test_business_reasoning_unit.py::test_paid_invoices_are_not_open`, `::test_open_invoices_of_all_customers`; contract suite `invoice_status_filtering`, `cancelled_records`, `partial_payments`, `unexpected_status` | FAKE-TESTED | NO LIVE READER | BLOCKED-RULE: `overdue_by_due_date`, Q1 N; BLOCKED-OWNER: Q1 Q, S |
| 12 | Customer payment history (payments received, dates, allocations) | **nothing.** The only related figure is `InvoiceRecord.paid`, the amount already settled against one *open* invoice | — | NOT IMPLEMENTED (`paid` is REUSABLE) | — | BLOCKED-RULE: Q1 N (advances, unallocated receipts, reversals); scope Q29 |
| 13 | Supplier records and supplier purchase history | **nothing**: no supplier entity, record or capability | — | NOT IMPLEMENTED | — | BLOCKED-RULE: Q9; BLOCKED-OWNER: Q1 S; permission Q8 |
| 14 | Returns and cancellations | cancelled invoices are never open: a fake rule, checked by the contract suite (`cancelled_records`). `InvoiceStatuses.cancelled` is a closed group in this contract. Returns are not modelled; they belong inside the `daily_sales` mapping (rule M) | contract suite `cancelled_records` | partly (cancelled ≠ open) | no reader | BLOCKED-RULE: Q1 M, N, Q |
| 15 | Discounts, taxes, tax adjustments | not modelled. A sales total is "as the mapping defines it": tax-inclusive or exclusive is rule M. No tax figure exists | — | NOT IMPLEMENTED | — | BLOCKED-RULE: Q1 M; BLOCKED-OWNER: Q7 (GSTIN) |
| 16 | Accounting reads (ledgers, credit notes, GST data) | **nothing.** The reserved `accounting` and `gst` domains describe actions and are not implemented | — | NOT IMPLEMENTED | — | Q7, Q1; out of scope of this contract (§ 6.9) |

**Reusable for every future capability (implemented, tested):** the contract model and its validation (`business/erp_contract.py`) · the security gate and database-layer tenant binding (`business/erp_gate.py`, `ReadOnlyErpDatabase.fetch`) · per-capability activation (`register_reader`) · the statement guard (`adapters/sql_guard.py`) · `BusinessReadService` (permission re-check, context from the principal, one deadline, ERP-unavailable retry, record re-validation, tenant check, totals in code, `business.read` audit) · `BusinessDateRange` / `resolve_period` / `same_period_last_year` · `DataSource` provenance · fake failure injection · the contract suite (`tests/erp_contract/`) · grounding and the reference-only focus.

**Findings of the audit (no code changed):**

1. **Sales, customer and invoice reads are tenant-wide.** Only the three stock capabilities apply warehouse scope (`service.py`). A user who holds `business.sales.read` or `business.customer.read` sees the whole tenant's figures, whatever their warehouse scope. Since Phase 10 this has been by design (§ 3 lists the customer warehouse boundary as "n/a"), and Phase 16 recorded it for sales. Whether that is acceptable is an owner decision: **Q29** (§ 7).
2. **Activation dependency.** In the adapter, `find_warehouses` / `get_warehouse` are served only by a `warehouse_stock` reader (`METHOD_CAPABILITIES`). `product_stock` with a warehouse filter resolves the warehouse through them, so activating `product_stock` alone makes that form answer RULE_NOT_VERIFIED. This fails closed and is not a defect. The order in § 10 accounts for it.
3. **Adding a capability changes Q1 item S for every contract.** `missing_inputs` counts S as present only when the contract maps *every* name in `CAPABILITIES`, and `validate_contract` refuses names outside `CAPABILITIES`. A seventh capability would therefore make every existing contract incomplete on S. The phase that adds one must first decide whether S means "all capabilities" or "the capabilities to activate" (ADR-066).
4. **There is no pagination cursor.** Lists are bounded: the service asks for `limit + 1` and reports `truncated`, and never totals a truncated list. Per-product stock refuses a truncated answer outright.

## 6. Future read contract (specification only, nothing implemented)

The capabilities in this section **do not exist**. Their names, permission names and record shapes are proposals (ADR-066, status Proposed). ERP relations, columns, status values and business rules are **TO BE SUPPLIED** by the ERP owner. Nothing here names an ERP table, column, endpoint or credential.

### 6.1 Conventions every new capability follows (taken from the existing code)

| Concern | Rule | Where it already holds |
|---|---|---|
| Placement | one `Capability` (one permission), one or more `ErpReadPort` methods, one `*Record` (adapter → service, states `tenant_id`) and one `*Data` (service → tool, no tenant ids), an entry in `CAPABILITY_RECORDS` (data minimization) and in `METHOD_CAPABILITIES`, a reader registered only for a VERIFIED mapping, and one READ tool. **No parallel tool framework.** | `capabilities.py`, `ports.py`, `models.py`, `erp_contract.py`, `adapters/postgres.py`, `tools/business.py` |
| Inputs | strict tool input (`extra="forbid"`): a name as the user wrote it (2–100 characters) **or** an opaque `ref` from an earlier result; a closed period token (`DatePeriod`) **or** an explicit ISO date; a bounded `limit`; closed tokens for any option. Never a tenant, user, warehouse scope, permission, table, column or SQL | `tools/business.py`, `test_business_tools.py` |
| Identifiers | `ref` is opaque (`REF_PATTERN`, at most 64 characters) and chosen by the adapter from an ERP key the owner confirms is **stable**: it survives renames, and the owner states what happens on merge or deletion (Q1 L). A document's printed number is a separate field. Refs live in the thread focus only, never in long-term memory | `models.py`, `focus.py` |
| Dates and time zone | business dates are in `ERP_BUSINESS_TIMEZONE` (= contract `time.business_timezone`). Ranges are half-open `[start, end)`. The service resolves periods from the clock (`resolve_period`); the model never computes a date. A future date is VALIDATION_ERROR / `FUTURE_DATE`. **Which document date counts** (document, posting or delivery date) is a rule the owner confirms (Q1 M, P) | `dates.py`, `service.py` |
| Tenant | taken from the principal only. Every record states `tenant_id`, and one foreign record discards the whole answer (`TENANT_MISMATCH`). The database layer binds the tenant (column or RLS setting, Q1 H) | `context.py`, `service.py`, `ReadOnlyErpDatabase.fetch` |
| Warehouse / outlet scope | stock: the adapter filters, the service filters again, and out of scope = NOT_FOUND. Document-level reads (sales, payments, purchases) have no warehouse attribution today: **Q29**. **Safe rule until Q29 is decided:** a new document-level capability answers only callers whose scope is ALL (`business.warehouse.all`). Any other caller gets UNAUTHORIZED, never tenant-wide figures. The reason code is defined when the capability is implemented | `context.py` |
| Authorization | one permission per capability, checked by the tool registry and again by the service. No built-in role grants a business permission (Q8) | `capabilities.py`, `service.py` |
| Limits and pagination | the adapter is asked for `limit + 1`; the result carries `truncated`; no total over a truncated list; `ERP_MAX_ROWS` (default 200) caps every read; tool limits are at most 100. There are no cursors. A capability whose answer can exceed the cap narrows by period or entity, or returns records the mapping aggregates (as `SalesRecord` is one record per day). A cursor would be a new, separately reviewed decision | `service.py`, `core/config.py` |
| Provenance | every SUCCESS carries `DataSource` (`system`, `adapter`, `retrieved_at`, `test_data`); date results carry `BusinessDateRange` with `timezone_source` | `results.py`, `dates.py` |
| Coverage | a history read states which part of the range the ERP can vouch for, once the owner says how (**Q30**). Until then: a range without records has **no total** (never zero), a gap code says why, and no change or percentage is computed against it (as in Phase 16) | `models.SalesPeriodSummary`, `ComparisonGap` |
| Business semantics | cancellations, returns, credit notes, discounts, taxes and status meanings are mapping rules in the contract's `rules`, with closed status lists like `invoice_statuses`. A value outside the closed list makes the read MALFORMED_RESPONSE. The platform never guesses one | `erp_contract.py` (`InvoiceStatuses.group_of`) |
| Errors and retry | `Erp*Error` vocabulary only. ERP_UNAVAILABLE is retried within the read's single deadline (`ERP_READ_MAX_ATTEMPTS`, default 2). Timeouts and data errors are never retried. Malformed or foreign records → DEPENDENCY_ERROR. No reader → UNAVAILABLE / RULE_NOT_VERIFIED | `ports.py`, `service.py`, `observability/retry.py` |
| Audit and grounding | `business.read` with metadata only (no names, refs or amounts). Every figure in a reply must come from a result, and derived figures are computed by the service | `service.py`, `grounding.py` |

### 6.2 `customer_sales_history` (proposed): what a customer bought from the company

*Customer **sales**, as the company sees them; not supplier purchases.* Questions: "Rahul Gupta ne pichhle saal kitne products kharide?", "… kitni baar purchase kiya?", "Is customer ki previous purchase history batao".

* **Input:** `customer_name` or `customer_ref` (exactly one); `period` token or an explicit start and end date (the longest allowed span is an owner decision); `limit` ≤ 100.
* **Port (proposed):** `customer_sales(ctx, customer_ref, start, end, limit)`. It returns one record per sales document: `tenant_id`, `ref`, `number`, `customer_ref`, `document_date`, `kind` (closed list from the owner, e.g. sale vs return/credit), `total`, `currency`. Quantity fields are added only once Q27 says whether "how many products" means units, distinct products or both.
* **Output:** the customer `EntityRef`, the resolved `date_range`, the document lines, `document_count`, `total` (only when the list is complete and in one currency), `truncated`, gaps.
* **Needs:** Q27 (which documents count; cancellations, returns, credit notes), Q1 L (identification), Q1 M (tax, discounts), Q29 (scope), Q30 (coverage).
* **Permission (proposed):** `business.sales.read` (Q8 confirms). **Safe now:** routed to `unsupported`. No fake records until Q27 defines them.

### 6.3 `product_sales` (proposed): sales per product over a period

Questions: "Kaunse products zyada bike?", "Last year ke same period mein kaunse products zyada bike?".

* **Input:** `period` (required); optional `product_name` / `product_ref`; `rank_by` (a closed token; which of quantity or amount exist is decided by Q27); `limit` ≤ 100.
* **Port (proposed):** `product_sales(ctx, start, end, limit, product_ref=None)`. It returns one record per product, **aggregated by the mapping** over the range: `tenant_id`, `product_ref`, `product_name`, `quantity`, `unit`, optional `amount` and `currency`, `document_count`. Ordering is by the requested measure, descending, with ties broken by `ref`.
* **Output:** product lines, `truncated`. A truncated top-N is a ranking, never a total of all products. A last-year comparison reuses the Phase 16 rules per product, and only after the single-period read is VERIFIED.
* **Needs:** Q27 (line-level semantics: returns netted or not, free items, bundles), Q1 M (line discounts and taxes), the owner's units of measure (no conversion is assumed), Q29, Q30.
* **Permission (proposed):** `business.sales.read`. **Safe now:** `unsupported`.

### 6.4 `customer_payment_history` (proposed)

* **Input:** customer (name or ref), period, `limit` ≤ 100.
* **Record (proposed):** `ref`, `document_date`, `amount`, `currency`, and an optional mode and allocation to invoice numbers, **only if** the owner confirms they exist.
* **Needs:** Q1 N (advances, unallocated receipts, reversals and bounced payments, what "paid" means), Q29.
* **Permission (proposed):** `business.customer.read` (Q8 confirms). **Safe now:** `unsupported`. The per-invoice `paid` of open invoices stays the only payment figure.

### 6.5 Inventory by outlet

This is not a new capability. If the owner confirms that outlets are warehouses in the ERP, outlet stock is the existing stock capabilities under contract item I. If they are not, the owner must describe them before anything is modelled. **Safe now:** outlets are not distinguished.

### 6.6 Supplier and purchase reads (proposed): what the company buys

*Supplier **purchases**; never confused with a customer "purchase", which is a customer sale (§ 6.2).*

| Proposed capability | Input | Output (shape only) |
|---|---|---|
| supplier resolution (a step, like customer lookup) | supplier name or ref | `EntityRef`; AMBIGUOUS / NOT_FOUND as for customers |
| `purchase_orders` | supplier (optional), status group (closed list from the owner), period, `limit` | documents: number, dates, amount, currency, status group; `truncated` |
| `goods_receipts` | supplier or purchase order number, period, `limit` | receipts per order: number, date, receiving warehouse (scope), status group |
| `purchase_invoices` | supplier, period, open-only flag, `limit` | supplier bills: number, dates, amount, balance payable, status group |
| `supplier_purchase_history` | supplier, period | as `customer_sales_history`, on the purchase side |

* **Needs:** Q9 (statuses, partial GRNs, three-way matching, cancellations, GST placement), Q1 S, Q8 (a new permission, proposed `business.purchase.read`), Q29 (receiving warehouse as scope).
* **Safe now:** `unsupported`. The reserved `purchase` domain stays action-only and unimplemented.

### 6.7 History coverage (proposed metadata, not a user capability)

Two optional fields on history results: `coverage_start` (the first business date the ERP vouches for) and `complete_through` (the last complete business date), **as the owner defines them** (Q30). With them, "no sales" can be told apart from "no data". **Safe now:** Phase 16 behaviour (no total, a gap code, no percentage).

### 6.8 Returns, cancellations, discounts, tax adjustments

No standalone capability is proposed. These are semantics *inside* the sales, payment and purchase mappings (rules M, N, Q, Q27, Q9). Each must be stated in the contract before the mapping that depends on it can be VERIFIED.

### 6.9 Accounting and GST reads

Out of scope. No contract is specified until Q7 (company / GSTIN structure) is answered and an accounting owner is named.

## 7. Owner decisions that block the contract

The register itself is PROJECT_STATUS § 9; this table adds what each decision changes. Nothing here is answered on the owner's behalf.

| Q | Decision needed | Why it affects correctness or security | Who confirms | Safe behaviour until resolved |
|---|---|---|---|---|
| **Q1** (A–T) | the access contract: owner, database, read-only role, credential delivery, intended-ERP confirmation, tenant and warehouse isolation, time zone, rules K–Q, authorization expectations, mappings, schema version (§ 2) | without it no read can be proven read-only, tenant-isolated or correct | ERP database owner and ERP application owner; the ERP DBA creates the role | readiness BLOCKED (exit 3). `ERP_ADAPTER=postgres` answers RULE_NOT_VERIFIED without connecting. Production refuses a missing or unverified contract |
| **Q27** | which documents are a customer's purchase (invoices, cash memos, orders?); how cancellations, returns and credit notes count; units vs distinct products; which ERP relations hold sales lines | otherwise returns or cancelled documents are double-counted, and "how many products" has two different answers | business owner (sales and accounts) for the meaning; ERP application owner for the relations | `unsupported`; no capability and no fake records |
| **Q28** | calendar year or financial year (April–March); same calendar dates or same weekdays | a comparison against the wrong period looks right and is wrong | business owner (finance) | rules `year_is_calendar_year` and `same_period_last_year_by_date` are TEST rules in the fake; the live adapter answers RULE_NOT_VERIFIED |
| **Q29** (new) | are sales, invoices, balances, payments and purchase documents attributed to a warehouse or outlet, and must a user with a limited warehouse scope see only their own? Where does a user's warehouse assignment come from (ERP, identity provider or platform)? | **security:** today any holder of `business.sales.read` or `business.customer.read` sees tenant-wide figures, whatever their warehouse scope (§ 5, finding 1) | business owner, ERP application owner, security reviewer | the six capabilities stay as they are (tenant-wide, documented). New document-level capabilities answer only scope ALL (§ 6.1). *Phase 19: confirmed in code, still undecided; the options are in PROJECT_STATUS § 3q.4. Phase 20: option (a), company-wide for authorized users, proposed separately for sales and for customer/invoice reads. Both are PROPOSED — OWNER APPROVAL PENDING, not approved and not policy (PROJECT_STATUS § 3r)* |
| **Q30** (new) | from which date is ERP history complete (migration cut-over, archiving, purging), and can the ERP state it? | "no sales" and "no data" look the same; a year-over-year figure on partial history is wrong | ERP application owner | a side without records has no total, and no change or percentage is computed (Phase 16) |
| **Q7** | one company or several; several GSTINs or branches | the tenant mapping (H) and ownership rule (K) depend on it: a wrong mapping leaks across companies | business owner | tenant = the platform tenant only; nothing is mapped |
| **Q8** | which roles receive `business.*` permissions, plus the proposed `business.purchase.read` | over-broad grants expose balances and sales | business owner and security reviewer | no built-in role grants a business permission; operators grant them explicitly (`AUTH_ROLE_CATALOG_JSON`). *Phase 20: PENDING — OWNER DECISION REQUIRED; the Q29 proposals grant no permission* |
| **Q9** | purchase workflow: statuses, partial GRNs, matching, cancellations, GST placement | purchase reads would mis-state open orders and payables | purchase / accounts owner | `unsupported` |
| **Q4** | may ERP data be sent to the LLM provider to phrase the reply? | data protection | business owner / data protection officer | live answers wait; tests use TEST data only |
| **Q3** | deployment target (network path to the ERP) | connectivity, TLS, allowed source addresses | infrastructure owner | nothing connects |

## 8. What can be tested now with the fake ERP

*Superseded on 2026-10-30 (Phase 22): the fake ERP and `tests/erp_contract/test_fake_erp_contract.py` were deleted by owner decision. The same platform behaviour is now tested against a PostgreSQL 18.6 TEST replica of the InvenLogic tables (`docker/erp-test/`): the contract suite through `tests/erp_contract/test_invenlogic_contract.py`, failure modes through `tests/business_helpers.ErpFaults`, and the readers through `tests/test_invenlogic_unit.py` and `tests/integration/test_invenlogic_replica.py` (INVENLOGIC_MAPPING.md § 5). The list below is the Phase 18 record.*

These tests prove the **platform's** behaviour; they prove nothing about the real ERP's data or rules.

* The six capabilities, including the Phase 11 forms (all customers, one invoice number, product stock in one warehouse, "is X low?") and the Phase 16 comparisons, in `ERP_ADAPTER=fake` (development and tests only; refused in production).
* Every result status, tenant isolation, warehouse scope, permission checks, grounding and memory-vs-ERP conflicts (`test_business_*`, `test_historical_business.py`).
* Failure modes: `unavailable`, `timeout`, `hang`, `malformed`, `tenant_leak`, `rule_not_verified`, `not_configured`, `crash`, `swallow_cancel`, transient failures for the retry.
* The 18-check contract suite (`tests/erp_contract/test_fake_erp_contract.py`).
* Contract validation against the TEST contract (`tests/test_erp_contract_unit.py`), and the offline readiness gate (`python -m scripts.erp_readiness`).
* The security gate and tenant binding on a **throw-away** PostgreSQL (`tests/integration/test_erp_gate_postgres.py`, `test_erp_readonly_postgres.py`, `test_erp_inspection_postgres.py`). This is the project's own test server, not an ERP.
* That customer purchase history, product sales and purchase-order requests are refused (`unsupported`), never answered.

**Not testable honestly now:** any capability in § 6. Fake records for them would have to invent the semantics that Q27, Q9 and Q1 N leave open, which the Phase 16 and Phase 18 briefs forbid.

## 9. Blocked until the contract or owner decisions arrive

| Item | Blocked by |
|---|---|
| Any live answer from the six capabilities | Q1 (all of A–T), Q4. *Since 2026-10-30, for InvenLogic: Q1 A, B, C, D, E, F, G, T, plus confirmation of the RLS setting and status values (INVENLOGIC_MAPPING.md § 6)* |
| Relative periods and "today" on the live ERP | Q1 J, P (`ERP_BUSINESS_TIMEZONE` from the contract) |
| Live period sales, weeks, years, comparisons | rules `sales_period_is_sum_of_days`, `week_starts_monday`, `year_is_calendar_year`, `same_period_last_year_by_date` (Q1 M, P, Q28), coverage (Q30) |
| Live overdue figures | rule `overdue_by_due_date` (Q1 N, Q) |
| Customer sales history, product sales | Q27, Q1 S, Q29, Q30 |
| Customer payment history | Q1 N, Q29 |
| Outlet stock as distinct from warehouses | Q1 I, O |
| Supplier, purchase order, GRN and purchase invoice reads | Q9, Q8, Q1 S, Q29 |
| Tax, discount and accounting reads | Q1 M, Q7 |
| The real-ERP contract suite (`test_real_erp_contract.py`, skipped) | Q1 plus owner-supplied test records (`ERP_CONTRACT_TEST_FIXTURES`) |

## 10. Ordered plan for the later real ERP integration

The mechanics of each step are in OPERATIONS_RUNBOOK.md § 19; this is the order. Stop at the first failure.

1. **Owner inputs, security first:** Q1 (A–T), Q7 (tenant mapping), Q29 (scope of non-stock reads), Q8 (role grants), Q4 (data to the LLM). Q27, Q28 and Q30 are needed before the history steps (5.vi and 7).
2. **Contract DRAFT → offline readiness** (`python -m scripts.erp_readiness --contract <file>`) until `READY_FOR_DISCOVERY`.
3. **Read-only discovery** (`--live`, catalog only, no business row): the Phase 9 inspector, then the adapter gate.
4. **Owner sign-off:** mappings and rules VERIFIED; `--live` exits 0 (`READY_FOR_ACTIVATION`).
5. **Activate the existing capabilities one at a time.** For each: one reviewed reader, the contract suite against the owner's test records, and two real test tenants. Recommended order (lowest semantic risk first, and dependencies first):
   1. `warehouse_stock` (it also serves warehouse resolution for step ii, § 5 finding 2);
   2. `product_stock`;
   3. `low_stock_products` (threshold, rule O);
   4. `customer_outstanding` (identification L, rule N);
   5. `customer_open_invoices` (statuses Q, `overdue_by_due_date`);
   6. `daily_sales` for single days (J, M), then periods and comparisons once their rules (Q28) and coverage (Q30) are confirmed.
6. **Production configuration and watch** (runbook § 19, steps 6–7).
7. **Only then, new capabilities (§ 6), one per phase:** owner semantics → decide the meaning of item S (§ 5, finding 3) → code: capability, records, port method, `CAPABILITY_RECORDS`, `METHOD_CAPABILITIES`, tool → fake fixtures built **from the confirmed semantics** → contract-suite checks → mapping VERIFIED → reader. Suggested order: history coverage (Q30; it improves the comparisons) → `customer_sales_history` (Q27) → `product_sales` → `customer_payment_history` → supplier and purchase reads (Q9).
8. **Not in this plan:** any ERP write, and accounting or GST reads (§ 6.9).

## 11. No real ERP connection in Phase 18

Phase 18 did not connect to, inspect, start or modify any ERP or candidate ERP. `inventoryai-postgres` was `Exited (0)` before and after the work and was not started. `inven-logic`, `new_agent` and the schema atlas were not read (the Atlas was read on 2026-10-29 and confirmed by the owner on 2026-10-30, Phase 22). No `ERP_*` variable was set, and no credential was read. No migration was run. The readiness tool ran only in its offline mode (no `--live`), which connects to nothing. Nothing in this document comes from a live ERP.

## 12. Owner approval checklist (Phase 21, 2026-10-10)

> **For:** the business owner, the ERP application owner and the security reviewer. Each line needs a named person, a date and an explicit answer. **Nothing below is pre-filled, and nothing is approved until you answer it.** The readiness status of every item is in PROJECT_STATUS § 3s.

**Scope of these approvals.** Accepting either Q29 line does **not** approve Q8, create or grant any permission, authorize reads in any other business area (payments, purchases, customer purchase history, product sales), or show that the real ERP holds the required data. Real ERP access stays disabled until Q1 and the other prerequisites below are met.

### 12.1 Q29: warehouse scope of non-stock reads (two separate decisions)

Proposed option (a), recorded in Phase 20 (PROJECT_STATUS § 3r). It matches how the code behaves today, but that is not approval. Answer each line on its own:

| # | Decision | Accept | Reject (then choose (b) or (c), PROJECT_STATUS § 3q.4) | Name, role, date |
|---|---|---|---|---|
| 29-S | Users who **already hold** `business.sales.read` may see **company-wide** sales (`daily_sales`: day, period, year-over-year comparison), not filtered by warehouse | ☐ | ☐ | |
| 29-C | Users who **already hold** `business.customer.read` may see **company-wide** customer outstanding balances and open invoices (`customer_outstanding`, `customer_open_invoices`) | ☐ | ☐ | |

Required approvers for each line: business owner, ERP application owner, security reviewer (§ 7).

### 12.2 Q8: role permissions (owner to supply; no proposal is made)

Please supply the role catalogue. For each role, list which of these permissions it receives and whether it is warehouse-limited:

* `business.sales.read`
* `business.customer.read`
* `business.inventory.read`
* `business.warehouse.all` (sees all warehouses; without it a user's warehouse scope is empty)
* the proposed `business.purchase.read` (ADR-066; it guards no capability yet)

Also needed: how a user is assigned a role (the identity provider's group or claim, Q6) and who may change grants. Approvers: business owner and security reviewer. Until answered, no built-in role grants any business permission.

### 12.3 Q1: access contract (ERP database owner and ERP application owner)

*Update 2026-10-30 (Phase 22): with `--contract docs/erp/invenlogic_contract.json` the tool reports BLOCKED with only **A, B, C, D, E, F, G, T** missing; H–S are in the contract (rules adopted by the owner, to be re-confirmed by the accountant). Remaining steps: [INVENLOGIC_MAPPING.md](INVENLOGIC_MAPPING.md) § 6.* Phase 21 record: `python -m scripts.erp_readiness` (offline, 2026-10-10): **BLOCKED, exit 3; all 20 items A–T missing.** The evidence needed is every item of § 2: A owner confirmation · B host and port · C database name · D PostgreSQL version · E read-only role (created by the ERP DBA, with the role shape of LIVE_ERP_VERIFICATION_CHECKLIST.md § 2) · F credential delivered through the secret store (never in the contract or the repository) · G written confirmation that this database is the intended ERP · H tenant isolation mechanism · I warehouse/outlet isolation · J time zone rules · K–Q business rules (ownership, customer identification, sales, outstanding balance, stock, dates and periods, invoice statuses) · R authorization expectations · S the views, functions or tables for each capability (§ 3) · T schema version. Also: connection method, TLS mode and CA, allowed source network, and timeouts (LIVE_ERP_VERIFICATION_CHECKLIST.md § 2), and owner-supplied contract test records (`ERP_CONTRACT_TEST_FIXTURES`) with two real test tenants.

### 12.4 Q4: business data sent to the LLM provider (business owner / data protection officer)

The question (PROJECT_STATUS § 9 Q4, § 7 above): **may ERP data be sent to the LLM provider to phrase the reply?** Today's design sends a successful read's data to the model to write the answer (BUSINESS_INTEGRATION.md § 9, item 5). Evidence needed: a written yes or no from the business owner or data protection officer, with any limits (which fields, residency, retention at the provider). Related but separate: who provides the production credential (Q22).

### 12.5 Other prerequisites already documented for live ERP access

Q7 (company / GSTIN structure, which decides tenant mapping H and ownership K) · Q3 (deployment target and network path to the ERP) · Q26 (per-capability approval of each mapping and rule) · a passing read-only security inspection (LIVE_ERP_VERIFICATION_CHECKLIST.md § 3) · Q27, Q28 and Q30 before the period, comparison and history steps (§ 10). Order: § 10.
