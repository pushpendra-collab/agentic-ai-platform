# Business Integration Architecture

> **For:** engineers who will connect the real ERP, and reviewers of the business layer.
> **Phase:** 10 (2026-10-02), ADR-059; Phase 11 reasoning foundation (2026-10-03), ADR-060, § 10. **REAL ERP INTEGRATION = NOT VERIFIED**: the ERP access contract and schema (Q1) have not been supplied. Everything below runs against a fake ERP only. The fake ERP is test-only and refused in production. No business write exists.
> **Phase 22 (2026-10-30), supersedes the "fake ERP only" statements below:** the company owner confirmed InvenLogic as the real ERP. The six capabilities now have readers (`business/adapters/invenlogic.py`, reader set `invenlogic_v1`, ADR-067) over three InvenLogic databases, and the fake ERP (`business/adapters/fake.py`, `ERP_ADAPTER=fake`) was **deleted**; tests run on a PostgreSQL 18.6 TEST replica (`docker/erp-test/`). The **live ERP is NOT CONTACTED** (contract DRAFT, Q1 A–G and T missing). Mapping, rules and go-live steps: [erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md). Sections that describe the fake are kept as the Phase 10–16 record and marked.
> **Phase 18 (2026-10-10, documentation only):** the capability matrix verified against code and tests, the future read contract, owner decisions and the ordered integration plan are in [erp/ERP_CONTRACT.md](erp/ERP_CONTRACT.md) § 5–§ 11. Nothing here changed in behaviour.
> Related: [SCHEMA_INTEGRATION.md](SCHEMA_INTEGRATION.md) (mapping register), [erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](erp/LIVE_ERP_VERIFICATION_CHECKLIST.md) (access contract), [../OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md) § 15–16.

## 1. Request path

```
User ─► FastAPI (bearer token) ─► Principal (tenant, user, roles → permissions; from the token only)
     ─► LangGraph
          understand_request   plans typed tool calls; sees only tools the user may run
          process_request      → BusinessDomainHandler (domain `business`, READ tools only)
              ToolRegistry     domain · permission · strict input schema · timeout · audit `tool.call`
              business tool    business.customer_outstanding | customer_open_invoices | daily_sales |
                               product_stock | warehouse_stock | low_stock_products
              BusinessReadService
                               permission re-check · BusinessContext from the Principal · deadline ·
                               name → one record (NOT_FOUND / AMBIGUOUS) · record re-validation ·
                               tenant check · warehouse-scope filter · totals in code ·
                               audit `business.read` (metadata only)
              ErpReadPort      typed read interface (no write method exists)
              adapter          postgres (read-only boundary; InvenLogic readers, one connection
                               per ERP database; the fake was deleted 2026-10-30)
          generate_response    LLM writes the reply from the BusinessResult
          verify_business_response
                               every figure in the reply must come from the results or the user's
                               words; otherwise the reply is replaced by a deterministic rendering
     ─► response
```

The LLM never sees SQL, a table name, a credential or a tenant id. It can't choose the tenant, the user, the warehouse scope or the permissions. It never computes a business figure: totals are computed by the service, and the verification node removes any figure that has no source.

## 2. Components

| Component | File | Responsibility |
|---|---|---|
| Capabilities and permissions | `business/capabilities.py` | 6 capabilities (one per reference question), each with one permission and the ERP rule that must be confirmed |
| Result semantics | `business/results.py` | `BusinessStatus` (closed set), reason codes, `BusinessResult` invariants, `DataSource` |
| Contracts | `business/models.py` | adapter `*Record`s (each states its tenant) and service `*Data` (no tenant ids) |
| Trusted context | `business/context.py` | `BusinessContext` from the `Principal`; `WarehouseScope` (ALL / LISTED / NONE), fail-closed default |
| ERP read port | `business/ports.py` | the only adapter interface; `Erp*Error` vocabulary |
| Service | `business/service.py` | all checks listed in § 1; never raises |
| ~~Fake adapter~~ | ~~`business/adapters/fake.py`~~ | **deleted 2026-10-30** (owner decision); tests use the TEST replica and `tests/business_helpers.ErpFaults` for failure injection |
| Postgres boundary | `business/adapters/postgres.py` | read-only connection(s), one `ReadOnlyErpDatabase` per contract database, security gate, statement guard; `PostgresErpAdapter` answers through registered readers only, otherwise RULE_NOT_VERIFIED |
| InvenLogic readers | `business/adapters/invenlogic.py` | reader set `invenlogic_v1`: one reader per capability over `master_data`, `sales`, `inventory`; registered only for VERIFIED mappings ([erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md)) |
| Factory | `business/factory.py` | `ERP_ADAPTER` (`none` / `postgres`) → port (or none); builds multi-database adapters (`ERP_DATABASE_URLS`) and registers the readers |
| Tools | `tools/business.py` | 6 typed READ tools; registration refuses anything else |
| Domain | `agents/domains/business.py` | runs planned `business.*` READ calls only |
| Grounding | `business/grounding.py`, `agents/nodes/verify_business_response.py` | figure check and deterministic fallback |

## 3. Result semantics

| Status | When | Reply rule |
|---|---|---|
| `SUCCESS` | the ERP answered; `data` holds the figures, `source` names the adapter | answer from `data` only; say "TEST data" when `source.test_data` |
| `NOT_FOUND` | no match, **or** a match outside the caller's tenant or warehouse scope (existence is never revealed) | say nothing matched |
| `AMBIGUOUS` | several matches; `candidates` (≤ 10) | ask which one; turn status `needs_clarification`; no figure |
| `UNAVAILABLE` | `ERP_NOT_CONFIGURED`, `ERP_UNAVAILABLE`†, `ERP_TIMEOUT`†, `RULE_NOT_VERIFIED` | say the current data could not be retrieved / is not available yet; no figure |
| `UNAUTHORIZED` | `PERMISSION_MISSING`, `WAREHOUSE_SCOPE_EMPTY` | say the user has no access |
| `VALIDATION_ERROR` | e.g. `FUTURE_DATE` | say what was wrong |
| `DEPENDENCY_ERROR` | `MALFORMED_RESPONSE`, `TENANT_MISMATCH`, `ERP_SAFETY_VIOLATION`, `INTERNAL_ERROR`: the whole answer is discarded | say the data could not be read; no figure |

† `retryable=true`. Malformed tool input never reaches the service: the registry answers `TOOL_INVALID_INPUT`.

## 4. Read-only policy (no ERP mutation exists)

| Layer | Enforcement | Verified |
|---|---|---|
| 1. Database role | ERP DBA grants a SELECT-only role; checked by `scripts/inspect_erp_security.py` | inspector on a throw-away DB (Phase 9); **live: NOT VERIFIED** |
| 2. Connection | `default_transaction_read_only=on`, statement timeout (never 0), client command timeout, own `application_name`, no SQL echo; identity check before the first read: expected database name, not the Agent DB (`alembic_version`), session read only, not superuser, not BYPASSRLS | unit + throw-away PostgreSQL |
| 3. Transaction | `SET TRANSACTION READ ONLY` per read, always rolled back | throw-away PostgreSQL: INSERT/UPDATE/DELETE/DDL refused even as **superuser** with the guard **switched off**; data unchanged |
| 4. Statement | only `ErpQuery` constants declared in code; `check_read_only_sql` (one SELECT/WITH, no comments, no write/DDL/lock/side-effect keyword or function), re-checked at execution | 28 unit cases, forged-query test |
| 5. Application | `ErpReadPort` and `BusinessReadService` have no write method (test scans the names) | unit |
| 6. Tool registry | business tools are READ only (registration refuses a WRITE); no SQL/table/tenant/scope argument; unknown tools refused; domain-restricted | unit |
| 7. Authorization | permission per capability (registry + service), warehouse scope from the principal, tenant from the token | unit + graph |

Purchase orders, invoices, payments, stock adjustments, supplier creation and customer updates don't exist. A request for one is routed `unsupported`, and the reply may not claim that anything was created.

## 5. Tenant and scope boundary

* **Tenant:** `BusinessContext.tenant_id = Principal.tenant_id`. Adapters receive it and own the mapping to the ERP's tenant mechanism, which is **unknown** (Q1: column, RLS setting, schema or database per tenant). Every record must state its tenant. The service discards the whole answer when any record belongs to another tenant (`TENANT_MISMATCH`, logged as an error).
* **Warehouse scope:** `business.warehouse.all` → all warehouses; otherwise **none** (fail closed). A `WarehouseScopeResolver` can map a principal to a listed set once the ERP's or IdP's assignment mechanism is known (**missing contract**). Records outside the scope are filtered out by the adapter and again by the service, and are reported as `NOT_FOUND`, never as forbidden.
* **Permissions** (granted by no built-in role, Q8): `business.customer.read`, `business.sales.read`, `business.inventory.read`, `business.warehouse.all`. They're granted through `AUTH_ROLE_CATALOG_JSON`. There's no second permission system.

## 6. Memory is never business truth

Memory may supply conversational context, aliases ("Rahul" = which customer), preferences (reply language) and earlier discussion. It's **never** authoritative for current stock, sales, invoice amounts, payment status, outstanding amounts or purchase amounts. That's enforced in two ways:

1. **Prompt** (`generate_response.system.v3`): figures only from `SUCCESS` results; the tool result wins over memory.
2. **Code** (`verify_business_response`): a figure in the reply that's in no business result and not in the user's message replaces the reply with a deterministic rendering of the results. Test: memory says "Rahul owes ₹50,000", the fake ERP says 45,000, and the model's reply quoting 50,000 is replaced by the 45,000 result.

Phase 11 widened the check (§ 10.6): figures written as "45k", "1.25 lakh", "45 hazaar" or "forty-five thousand" are read, and so are hedges and action claims. A draft is also replaced when the asked-for figure is unavailable. Limitations of numeric grounding:

* It checks that a number has *a* source, not that the sentence attributes it correctly. For example, an invoice balance quoted as "overdue" passes, unless the overdue figure itself is unavailable (then the draft is replaced).
* Numbers that happen to occur in a result (date parts, invoice numbers, counts) count as sourced.
* A lone small number word without a tens or scale word ("one" to "nineteen") is not read, so "which one?" is not treated as a figure. Hindi number words ("pachaas hazaar") are not read; digits with "hazaar/lakh/crore" are.
* Hedge and action-claim detection are fixed phrase lists (English and common Hinglish), not language understanding. They fail closed: a false positive gives the deterministic rendering, never a wrong figure.

## 7. Observability

| Signal | Content | Never contains |
|---|---|---|
| audit `tool.call` (registry, existing) | tool, outcome, argument hash, tenant, user, request/thread id, duration | arguments |
| audit `business.read` (new) | capability, business status, reason, adapter, duration, `test_data`, tenant, user, request/thread id | names, refs, amounts, quantities |
| log `business.read.completed` | same metadata | same |
| log `business.read.rejected` / `.safety_violation` / `.crashed` | capability, reason / error type | data |
| log `business.response.ungrounded` | count of unsourced figures | the figures |
| log `business.response.rejected` (Phase 11) | which checks failed (`unsourced_figures`, `hedged`, `action_claim`, `requested_figure_unavailable`) with counts | the reply, its figures |
| log `request.understood` → `business_query` (Phase 11) | capability, operation, metric, period token, filters, `has_customer`/`has_product`/... flags | names, refs, dates, numbers |
| log `erp.identity.verified` / `.refused` | expected database / problem list | URL, password |

The trace request → graph → capability → tool → result → response is joined by `request_id` and `thread_id` across `tool.call`, `business.read` and the node logs.

## 8. Testing with the fake ERP

*Superseded on 2026-10-30 (Phase 22): the fake ERP was deleted. Business tests now run the real reader SQL, gate and RLS on the InvenLogic TEST replica (`docker/erp-test/`; set `TEST_ERP_ADMIN_URL` and `TEST_ERP_READER_URL`, otherwise those tests are skipped). New suites: `tests/test_invenlogic_unit.py`, `tests/integration/test_invenlogic_replica.py`, `tests/erp_contract/test_invenlogic_contract.py` (contract suite, 18/18 on the replica); helpers `tests/erp_db.py` and `tests/business_helpers.py` (`ErpFaults`). See [erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md) § 5. The text below is the Phase 10–11 record.*

`ERP_ADAPTER=fake` (development/tests only, refused in production) serves `standard_test_data`: `TEST_CUSTOMER_001` "Rahul Gupta (TEST)" (outstanding 45000.00 INR; invoices `TEST_INVOICE_001` 30000.00 and `TEST_INVOICE_002` 15000.00), `TEST_CUSTOMER_002/003` "Amit Sharma (TEST)" / "Amit Sharma Traders (TEST)" (ambiguous), `TEST_PRODUCT_001..003`, `TEST_WAREHOUSE_001/002` "Godown 1/2 (TEST)", daily sales on the build date. All values are arbitrary test values. The fake injects `unavailable`, `timeout`, `hang`, `malformed`, `tenant_leak`, `rule_not_verified`, `not_configured` and `crash`.

| Suite | Level | What it proves |
|---|---|---|
| `tests/test_business_service.py` | MOCK VERIFIED | every status, failures, tenant isolation, scope, permissions, audit/log hygiene, no write method |
| `tests/test_business_tools.py` | MOCK VERIFIED | schemas, smuggled arguments, invalid input, permission, domain restriction, unknown tools, READ-only registration |
| `tests/test_business_graph.py` | MOCK VERIFIED | the six reference queries through LangGraph, memory vs ERP, invented figures, ambiguity, unavailable ERP, purchase-order request, planner visibility, `none` and `postgres` modes |
| `tests/test_erp_boundary_unit.py` | UNIT VERIFIED | SQL guard, settings isolation, production policy, factory, grounding |
| `tests/integration/test_erp_readonly_postgres.py` | VERIFIED on a throw-away PostgreSQL (not the ERP) | identity check, read-only transaction, read-only role, statement timeout, unreachable |
| `tests/test_business_reasoning_unit.py` (Phase 11) | UNIT / MOCK VERIFIED | intent, date ranges, rules, metrics, aggregation, lists, ambiguity, not found, composition, figure forms, focus |
| `tests/test_business_reasoning_graph.py` (Phase 11) | MOCK VERIFIED | tool selection, intent-compiled calls, multi-step, step limit, follow-ups, memory conflict, grounding forms, read-only refusal, failures, logs, HTTP API |
| `tests/test_business_reasoning_security.py` (Phase 11) | MOCK / UNIT VERIFIED | tenant/warehouse/SQL overrides on new arguments, permissions, cross-tenant refs, focus tampering, write tools, production refusal, live adapter rules |

Phase 11 additions to the fake data (all `TEST_*`, arbitrary): Rahul Gupta's invoices now carry gross and paid amounts. `TEST_INVOICE_001` (30000.00 open, due 2026-08-31) is overdue on the test date 2026-10-01; `TEST_INVOICE_002` (15000.00 of 25000.00 open, due 2026-10-15) is not; `TEST_INVOICE_003` is fully paid, so it is not open. `TEST_INVOICE_006` belongs to Amit Sharma Traders (8000.00, overdue). Sales exist for the build date and 1, 3 and 29 days before it. `extended_test_data` (served by the factory) adds `TEST_CUSTOMER_004` "Rahul Verma (TEST)", so "Rahul" alone is ambiguous, and `TEST_WAREHOUSE_003` "Godown 3 (TEST)". The fake declares its TEST rules (`rules.FAKE_TEST_RULES`). Tests use the time zone `Asia/Kolkata` as a **test fixture**; it says nothing about the production ERP.

## 9. What a live integration still needs (Q1, Q26)

*Update 2026-10-30 (Phase 22): items 2, 3 and 4 are done for InvenLogic: the mapping is in [erp/invenlogic_contract.json](erp/invenlogic_contract.json), the readers are in `business/adapters/invenlogic.py` with tests against a copy of the schema (the TEST replica), and the tenant is the InvenLogic company id (RLS setting per read plus a `company_id` filter in every statement). Still open: item 1 (Q1 A–G, T and the live inspection), item 5 (Q4), the warehouse-scope decision (Q29), and item 6. Steps: [erp/INVENLOGIC_MAPPING.md](erp/INVENLOGIC_MAPPING.md) § 6.*

1. The access contract (checklist § 2) and a passing `scripts/inspect_erp_security` run against the ERP.
2. For each capability: the ERP tables, columns and joins, confirmed by the ERP owner, and the rule in `business/capabilities.py` (`rule_to_verify`).
3. Then, per capability: one `ErpQuery` constant plus a row → `*Record` mapping in a `PostgresErpAdapter` method, replacing its `ErpRuleNotVerifiedError`, with tests against a copy of the real schema.
4. The tenant mapping (how `Principal.tenant_id` maps to the ERP) and the warehouse-scope source (a `WarehouseScopeResolver`).
5. The Q4 decision: SUCCESS data is sent to the LLM provider to write the reply.
6. Only then: `ERP_ADAPTER=postgres` in an environment, followed by the live checks in the checklist.

*(Phase 18: the ordered version of this list, including the activation order of the six capabilities and the later new capabilities, is [erp/ERP_CONTRACT.md](erp/ERP_CONTRACT.md) § 10. Only stock reads apply warehouse scope; whether sales and customer reads must too is open question Q29 (ERP_CONTRACT.md § 5, finding 1).)*

## 10. Phase 11: query reasoning foundation (database-independent)

This layer is built and verified against the fake ERP only (ADR-060). It completes the orchestration and reasoning side, so a live ERP needs only the adapter mapping (§ 9) plus the rules its owner confirms. The six capabilities are unchanged in number and permission. No business write exists.

### 10.1 Path of a business question

```
"Rahul Gupta ke pending invoices ka total batao."
understand_request  → domain business, business_query = {capability: customer_open_invoices,
                      customer_reference: {name: "Rahul Gupta"}, operation: total,
                      requested_metric: open_invoice_total}   (typed, untrusted)
process_request     → planned call, or ONE call compiled from the intent (tool_call_for)
BusinessReadService → resolve customer → read open invoices → re-validate, tenant check →
                      overdue flags (if rules hold) → totals in code → BusinessResult
compose()           → BusinessResponseContext: figures [open_invoice_total 45000.00 INR, ...],
                      records, steps [resolve_customer, read_open_invoices, aggregate_in_service],
                      source (test_data), warnings [TEST_DATA]
generate_response   → LLM phrases the context (generate_response.system.v4)
verify_business_response → draft kept, or replaced by render_context(); focus updated
```

### 10.2 Typed intent (`business/intent.py`)

`BusinessQueryIntent` has these fields:

* `capability`: one of the six;
* `operation`: lookup, list, total, count or check;
* `entity`;
* `customer_reference`, `product_reference`, `warehouse_reference`: `{name}` as the user wrote it, or `{ref}` from the focus;
* `invoice_number`;
* `period`: a token;
* `explicit_date`;
* `requested_metric`;
* `filters`: `overdue`;
* `follow_up` and `write_requested`;
* `ambiguity`, `confidence` and `explanation`.

Unknown fields are refused. There is no identity, scope, permission, table, column or SQL field. Logs record `safe_log_fields()` only: flags and enums, never names, refs, numbers or dates.

Natural language is never authorization. "Warehouse B ka stock" names Warehouse B, and the caller's scope decides. A warehouse outside the scope is NOT_FOUND, indistinguishable from a missing one.

### 10.3 Dates (`business/dates.py`)

| Token | Range (start inclusive, end exclusive) |
|---|---|
| `today` / `yesterday` | the business date / the day before |
| `this_week` / `last_week` | Monday of this week → tomorrow / previous Monday → this Monday (needs rule `week_starts_monday`) |
| `this_month` / `last_month` | 1st → tomorrow / 1st of last month → 1st of this month |

* **Synonyms.** `current_month`, `previous_month`, `current_week` and `previous_week` are normalised to the tokens above.
* **Today.** "Today" comes from the injected clock, in the business time zone. The model never writes a relative date.
* **No business time zone.** Without `ERP_BUSINESS_TIMEZONE`, every relative period is UNAVAILABLE / RULE_NOT_VERIFIED. Explicit calendar dates still work.
* **Fake ERP time zone.** `BusinessDateRange.timezone_source` is `TEST` with the fake adapter. **The production ERP's time zone remains unresolved.** *(Superseded 2026-10-30: the fake was deleted; the InvenLogic contract states Asia/Kolkata, adopted by the owner. Years can be Indian financial years, 1 April – 31 March, under `year_is_financial_year_april`; exactly one year rule must hold.)*
* **Multi-day periods.** They read `sales_by_day`, and the service sums the days (rule `sales_period_is_sum_of_days`). Days without a record are reported as such, never as zero.

### 10.4 Business rules (`business/rules.py`)

| Rule | Needed for | Fake | Postgres |
|---|---|---|---|
| `business_timezone` | "today", relative periods, overdue | holds when `ERP_BUSINESS_TIMEZONE` is set | same |
| `overdue_by_due_date` | overdue flags, overdue amount and count | TEST rule | **not verified** |
| `sales_period_is_sum_of_days` | week/month sales | TEST rule | **not verified** |
| `week_starts_monday` | this/last week | TEST rule | **not verified** |

A missing rule never produces a guess. Either the figure is absent and `rules_not_verified` names the rule, or the read answers RULE_NOT_VERIFIED. The adapter's mapping applies tax, returns, cancellations and what counts as "open" (`Capability.rule_to_verify`). The LLM decides none of them.

### 10.5 Metrics, records, composition

`business/metrics.py` lists 13 metrics; the table is in the module. Each metric belongs to one capability. `measure()` only selects a figure the service computed. When it can't, it returns a `MetricGap` with one of these reasons: `RULE_NOT_VERIFIED`, `INCOMPLETE_LIST`, `MIXED_CURRENCY`, `NOT_PROVIDED`, `NOT_APPLICABLE`. Totals are never computed from truncated or mixed-currency lists.

`business/composition.py` builds the `BusinessResponseContext`:

| Part | Content |
|---|---|
| `figures` | the requested metric first, then the capability's defaults; values exactly as the service produced them |
| `records` | normalised invoices / sales days / stock lines / low-stock items (ref, name, amount or quantity, status, dates, details); the `overdue` filter selects by the service's flag |
| `date_range`, `source` | resolved range; adapter and `test_data` |
| `warnings` | `TEST_DATA`, `LIST_TRUNCATED`, `WAREHOUSE_SCOPE_LIMITED`, `RULES_NOT_VERIFIED`, `OVERDUE_NOT_DETERMINED`, `DAYS_WITHOUT_SALES_RECORD`, `RETRYABLE` |
| `message`, `clarification` | fixed sentences per status; AMBIGUOUS → a question with candidate display names only (no refs, figures or other fields) |
| `steps` | typed derivation, e.g. `resolve_customer → read_open_invoices → filter_overdue_by_rule → aggregate_in_service` |

`render_context()` turns the context into plain text, formatting INR as `₹1,25,000`. It is the fallback whenever a draft is rejected. Every number it prints comes from the results (tested).

NOT_FOUND messages are fixed and plain. The only name they repeat is the one the user typed:

* "No customer matching 'X' was found. Please check the name."
* "No open invoice numbered 'X' was found."
* "No sales record was found for the requested day or period."

### 10.6 Verification (replaces Phase 10's figure-only check)

`render_context()` replaces the draft when any of these holds:

1. it states a figure with no source (digits, `45k`, `1.25L`, `45 thousand`, `45 hazaar`, `1.25 lakh`, `2 crore`, `forty-five thousand`, `₹45,000`, `INR 45,000`);
2. it hedges (`I think`, `maybe`, `probably`, `perhaps`, `possibly`, `might be`, `could be`, `shayad`, `lagta hai`, `ho sakta hai`);
3. it claims a business action ("purchase order bana diya", "order has been placed", "message sent");
4. the asked-for figure is unavailable (a gap). A sourced number could otherwise stand in for it.

Write requests (`business_query.write_requested`) are verified even though no tool runs, and their context carries a read-only notice. The log line `business.response.rejected` records which checks failed and the counts, nothing else.

### 10.7 Follow-ups and memory

`AgentState.business_focus` (state version 4) holds references only:

* the last capability;
* the customer, product and warehouse ref and display name;
* the period token;
* the candidates of the last AMBIGUOUS answer.

It lives in the thread's checkpoint and the planner sees it as "Conversation focus". It is never written to long-term memory and never holds figures (tested).

For "Usme se kitna overdue hai?", the planner plans `customer_open_invoices` with the focus `customer_ref`, and the ERP is read again. If the data changed between turns, the new figure wins, and a draft that repeats the old one is replaced. Memory stays context; the business tool stays the current truth.

### 10.8 Bounds

* **One plan per turn.** Calls run in order, at most `AGENT_MAX_TOOL_CALLS_PER_TURN` of them. Later calls are skipped and reported.
* **Four graph steps.** Every business turn takes four. There is no re-planning loop; an AMBIGUOUS answer still needs the user's next turn.
* **Measured latency.** Locally, with the fake ERP and a scripted LLM over 200 runs (2026-10-03): a business read takes 0.06 to 0.08 ms p50, and a full graph turn (4 nodes, 1 tool, in-memory checkpointer) takes 17 ms p50 and 49 ms p95. These figures exclude Claude and the real ERP, so they say nothing about capacity.

### 10.9 What a live adapter must add for Phase 11 features

* `sales_by_day`;
* the `invoice_number` and all-customers forms of `open_invoices`;
* `low_stock(product_ref=...)`;
* the optional `customer_name`, `total` and `paid` invoice fields;
* `verified_rules`, listing only the rules the ERP owner confirmed (docs/SCHEMA_INTEGRATION.md § 4).

Until then, the postgres adapter answers RULE_NOT_VERIFIED for all of them.

## 11. Phase 16: historical sales comparison and business memory (fake ERP only)

ADR-065, PROJECT_STATUS § 3n. **No new capability, port method or permission.** Customer purchase history and product-level sales are **not** implemented: the port has no customer-transaction or line-level read, and the fake ERP has no such records. The planner routes those questions to `unsupported`.

### 11.1 Comparison path

`business.daily_sales {period, compare_with: "same_period_last_year"}` → `BusinessReadService.daily_sales` → `_sales_comparison`: the period is resolved from the clock, the comparison range is the same calendar dates one year earlier (`dates.same_period_last_year`), and both sides are read with `sales_by_day` under the same deadline, retry, tenant and record checks as any period read. The result is `SalesComparisonData`: one `SalesPeriodSummary` per side (total, documents, days with a record, currencies), `change_amount`, `change_percent` (Decimal, half-up to 0.1), `direction`, `document_count_change`, `gaps`, `day_counts_differ`.

| Situation | Result |
|---|---|
| both sides have records, same currency, baseline > 0 | every figure |
| baseline total is zero | change amount, no percentage (`ZERO_BASELINE`) |
| one side has no record at all | that side has no total (never 0); no change (`BASELINE_NO_RECORDS` / `CURRENT_NO_RECORDS`) |
| currencies differ | no change (`MIXED_CURRENCY`) |
| neither side has a record | NOT_FOUND (sales) |
| no relative period | VALIDATION_ERROR / `PERIOD_REQUIRED` ("Please name the period to compare…") |
| a needed rule is not declared | UNAVAILABLE / RULE_NOT_VERIFIED, nothing read |

Rules needed: `business_timezone`, `sales_period_is_sum_of_days`, `same_period_last_year_by_date`, plus `week_starts_monday` for weeks and `year_is_calendar_year` for years (also for plain `this_year` / `last_year`). The fake declares them as TEST rules; the live adapter only through a VERIFIED contract. *(Phase 22: for years, exactly one of `year_is_calendar_year` and `year_is_financial_year_april` must hold; a contract confirming both is refused. The InvenLogic contract confirms the financial year. The fake no longer exists.)*

### 11.2 Reply and verification

Composition adds two `sales_period` records (both date ranges), the comparison figures, a gap for every missing figure and a fixed basis notice ("same dates one year earlier; sales as recorded, not profit; internal company data only"). `verify_business_response` now also: runs on every business-domain turn, with or without results; requires a percentage to equal a percentage field of a result (or one the user wrote); rejects a sales reply that speaks of profit, margin or earnings ("not profit" is allowed).

### 11.3 Memory

An episode of a business turn keeps `analyses`, from `focus.analysis_references`: capability, status, period, comparison range, adapter, `test_data`, `retrieved_at`. It never holds figures, names, entity refs or tenant ids. Recall marks every item with `basis` (`stated_by_user`, `approved_by_user`, `document`, `interaction_summary`, `unconfirmed`) and `recorded_at`. Memory remains context: a later session learns that an analysis was done, and every figure is read again (§ 6).

### 11.4 What a live adapter needs for Phase 16

The VERIFIED `daily_sales` mapping (already required), the owner's confirmation of `year_is_calendar_year` and `same_period_last_year_by_date` in the contract's `rules`, and enough history in the ERP. If the ERP can state how far back its sales history goes, a coverage capability could tell "no sales" from "no data". None exists today. *(Phase 18: raised as Q30; proposed shape in [erp/ERP_CONTRACT.md](erp/ERP_CONTRACT.md) § 6.7.)*
