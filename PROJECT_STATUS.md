# Project Status

> **Current phase:** Phase 22: InvenLogic real ERP integration, **READERS BUILT AND VERIFIED ON A TEST REPLICA; FAKE ERP DELETED; LIVE COMPANY ERP NOT CONTACTED** (§ 3t). On 2026-10-30 the company owner confirmed the *Inven Logic Schema Atlas* (PostgreSQL 18.6, six service databases) as the company's real ERP, asked for it to be integrated with LLM-callable read tools, adopted the proposed business-rule defaults, and chose to delete the fake ERP completely. Delivered: six capability readers (reader set `invenlogic_v1`) over the `master_data`, `sales` and `inventory` databases, a multi-database contract and security gate, the Indian financial-year rule, a PostgreSQL 18.6 TEST replica (`docker/erp-test/`) that replaces the fake in tests, and the contract [docs/erp/invenlogic_contract.json](docs/erp/invenlogic_contract.json) (DRAFT). On the replica: contract suite 18/18, `erp_readiness --live` READY_FOR_ACTIVATION for all three databases. Offline readiness against the contract: BLOCKED, only **A, B, C, D, E, F, G, T** missing (was all 20). Full suite: 1373 passed, 35 skipped, 0 failed with PostgreSQL 16, Redis 7 and the InvenLogic TEST replica (PostgreSQL 18.6); without the replica 966 passed, 442 skipped, 0 failed (baseline before Phase 22: 1326 passed, 18 skipped). ruff and mypy clean. Local Ollama end-to-end: 5 Hindi/English business questions through the CLI with Ollama qwen3:8b (LLM_OLLAMA_NUM_CTX=4096) and the InvenLogic TEST replica: 5/5 completed, every ERP read SUCCESS; outstanding ₹45,000, today's sales ₹1,25,000, iPhone 15 stock 15 pcs, low stock iPhone 3/5 and Galaxy 0/2 all correct; for "pending bills dikhao" the model picked customer_outstanding instead of customer_open_invoices (right total, no bill list). 2.4-8.1 min per turn on CPU. Next steps for the owner: [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6. Previous: Phase 21: owner decision readiness and safety gate, **DOCUMENTATION ONLY; NOTHING APPROVED; NO CODE CHANGE; REAL ERP NOT CONTACTED** (§ 3s). Readiness per decision verified, and a reviewer checklist added ([docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 12). Q29 (two proposals) and Q8, Q1 (A–T all missing, readiness exit 3) and Q4 all remain open. Previous: Phase 20: proposed owner decisions for Q29, **DOCUMENTATION ONLY; PROPOSALS, NOT APPROVALS; NO CODE CHANGE; REAL ERP NOT CONTACTED** (§ 3r). Recorded for review: Q29 sales read scope, option (a) company-wide for authorized users, **PROPOSED — OWNER APPROVAL PENDING**; Q29 customer outstanding and invoice read scope, option (a), **PROPOSED — OWNER APPROVAL PENDING** (separate proposal); Q8 role permissions **PENDING — OWNER DECISION REQUIRED**. Q29 and Q8 remain unresolved; runtime authorization behaviour is unchanged. Previous: Phase 19: warehouse scope of sales, customer and invoice reads, **BLOCKED ON OWNER DECISION Q29; NO CODE CHANGE; REAL ERP NOT CONTACTED** (§ 3q). The audit confirmed that these reads are tenant-wide for every caller, whatever their warehouse scope (stock reads are scoped). The repository defines no policy saying they must be scoped, and their records carry no warehouse attribute to filter on, so no authorization change was made. The decision needed is in § 3q.4. Previous: Phase 18: ERP access contract preparation, **DOCUMENTATION ONLY; REAL ERP NOT CONTACTED** (§ 3p). Delivered: a capability matrix verified against code and tests (6 capabilities FAKE-TESTED, 0 with a live reader; customer sales history, product sales, payment history, supplier/purchase reads NOT IMPLEMENTED), a specification for the missing read capabilities (ADR-066, Proposed), owner decisions with safe behaviour (new Q29: warehouse scope of non-stock reads; Q30: ERP history coverage), and an ordered integration plan ([docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 5–§ 11). Readiness still BLOCKED (Q1 A–T). No source, test, configuration or database change. Previous: Phase 16: long-term business memory and historical business intelligence, **PARTIAL, MOCK VERIFIED against the fake ERP** (§ 3n). Delivered: year-over-year sales comparison (totals, documents, change, %; missing or zero baseline handled), year periods, analysis references in episodic memory with provenance labels on recall, verification of every business-domain reply, percentage grounding, profit guard. **Customer purchase history and product-level sales: BLOCKED** (no port method, record or rule exists; Q1, Q27). Found by the audit: **Phase 15 is in the code and its tests pass, but it was never documented** (§ 3n.1). **Phase 17 (2026-10-10, documentation only) closed that gap:** Phase 15 is now documented (§ 3o, ADR-064, ARCHITECTURE.md § A.19, runbook § 20); its tests were re-run and pass; its deployment smoke test and backup scripts have never been run. Previous: Phase 14: production ERP integration readiness and real ERP adapter. **BLOCKED at the input gate: Q1 (ERP access contract) missing; REAL ERP NOT TOUCHED** (§ 3m). Delivered behind the gate: typed ERP contract + validator, fail-closed security gate on effective privileges/schema/isolation, database-layer tenant binding, per-capability activation, readiness tool, production refusal, contract test suite (fake ERP 18/18). Previous: Phase 13: production observability, reliability and agent evaluation foundation, COMPLETE (§ 3l): typed observability events (request → turn → span), error taxonomy, strict deadlines, ERP-read retry policy, bounded metrics, safe diagnostics, a 36-case scripted evaluation dataset with golden snapshots, failure injection and a local performance baseline; fake ERP only, no business writes. Previous: Phase 12: Supervisor and multi-domain orchestration foundation, COMPLETE against the fake ERP (§ 3k). Before that: Phase 11: business intelligence and query reasoning foundation (database-independent), COMPLETE against the fake ERP (§ 3j). Before that: Phase 10: business read layer and capability foundation. **COMPLETE (foundation). REAL ERP INTEGRATION = NOT VERIFIED**: no ERP access contract or schema exists (Q1), so the read-only `postgres` adapter answers UNAVAILABLE / RULE_NOT_VERIFIED. Everything else is verified with a fake ERP and a throw-away PostgreSQL (§ 3i). Phase 9's live verifications stay BLOCKED (Q1, Q3, Q6, Q22). **Go-live verdict: NOT READY.**
> **Last updated:** 2026-10-30
> **Related:** [ARCHITECTURE.md](ARCHITECTURE.md) · [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) · [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) · [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md)

## 1. Phase tracker

| Phase | Scope | Status |
|---|---|---|
| Phase 0 | Inspect repository, document current and target architecture, ADRs | ✅ Done |
| Phase 1 | LangGraph agent foundation (§2) | ✅ Done (awaiting a first live LLM run) |
| Phase 2 | PostgreSQL database foundation (§3) | ✅ Done (108 tests passing, 9 of them against a real PostgreSQL 16) |
| Phase 3 | **Redis and agent persistence** (stakeholder definition). Part A (§3a): asyncpg PostgreSQL checkpointer, `audit_log`, `idempotency_record`. Part B (§3b): Redis layer, locks, cache, temporary state, idempotency fast-path, thread ids, resume, persistence errors | ✅ Done (219 tests passing, 46 of them against real PostgreSQL 16 and Redis 7) |
| Phase 4 | **Agent memory system** (§3c): memory manager, 7 memory types, ports + adapters, pgvector, embeddings, tenant/user isolation, retention | ✅ Done (265 tests passing, 61 of them against real PostgreSQL 16 + pgvector and Redis 7) |
| Phase 5 | **Identity, authorization, permissions and human-in-the-loop** (§3d): principal, ownership, RBAC, memory/tool permissions, approvals, audit, tenant RLS | ✅ Done (317 tests passing, 70 of them against real PostgreSQL 16 + pgvector and Redis 7) |
| Phase 6 | **HTTP API, identity-provider boundary and scheduled maintenance** (§3e): FastAPI API, bearer authentication, OIDC/JWT adapter, API authorization, conversation/agent/approval/memory/audit endpoints, health/readiness, correlation ids, maintenance jobs | ✅ Done (396 tests passing, 78 of them against real PostgreSQL 16 + pgvector and Redis 7). The OIDC adapter is not verified against a real identity provider (Q6) |
| Phase 7 | **Production readiness and deployment** (§3f): production config policy, identity-provider verification, token lifecycle, LLM integration and failure handling, container image and process roles, secrets boundary, health/readiness, maintenance worker, migrations | ✅ Done (468 passed / 4 skipped with PostgreSQL 16, Redis 7 and a reference Keycloak). OIDC verified against Keycloak (reference, not the production choice, Q6); **live LLM completion NOT VERIFIED** (no credentials) |
| Phase 8 | **Go-live hardening** (§3g): TLS gateway, two-layer rate limiting, trusted proxies, load and failure testing, hung-dependency handling, Agent DB / ERP separation guard, production configuration gate, runbooks, go-live checklist | ✅ Done (514 passed / 4 skipped with PostgreSQL 16, Redis 7 and a reference Keycloak; 3 defects found by drills and fixed). **Not production-verified:** identity provider (Q6), live Claude (Q22), deployment target (Q3) |
| Phase 9 | **Production go-live verification and live ERP integration** (§3h): production IdP, live Claude, deployment target, ERP access contract, ERP read-only/RLS verification, live business read layer, tools and queries | ⛔ **NOT COMPLETE / BLOCKED**: Q1, Q3, Q6 and Q22 unanswered; the business read layer and tools do not exist. Delivered: baseline audit, ERP read-only inspector (`scripts/inspect_erp_security.py`, verified on a throw-away database), ERP verification checklist, schema register, runbook § 15, ADR-058. (541 collected: 537 passed / 4 skipped / 0 failed) |
| Phase 10 | **Business read layer and capability foundation** (§3i): typed capabilities, ERP read port, deterministic results, fake ERP, read-only PostgreSQL boundary (no schema mapping), 6 READ tools, `business` domain, figure grounding | ✅ Done, MOCK VERIFIED (665 collected: 661 passed / 4 skipped / 0 failed). **Live ERP NOT VERIFIED** (Q1) |
| Phase 11 | **Business intelligence and query reasoning foundation** (§3j), database-independent: typed query intent, deterministic date ranges, rule-gated derived figures, metrics, multi-record composition, ambiguity/not-found handling, bounded multi-step reads, reference-only conversation focus for follow-ups, widened grounding | ✅ Done, MOCK VERIFIED against the fake ERP (828 collected: 811 passed / 17 skipped / 0 failed). **Real ERP NOT VERIFIED** (Q1, intentionally deferred). No business writes |
| Phase 12 | **Supervisor and multi-domain orchestration foundation** (§3k): deterministic Supervisor node, typed `SupervisorDecision`, closed domain catalogue (implemented `general`/`business`, 7 reserved), unsupported and multi-domain handling, routing limits, common domain result contract, checkpoint type allow-list | ✅ Done, MOCK VERIFIED against the fake ERP (933 collected: 916 passed / 17 skipped / 0 failed). **Real ERP NOT VERIFIED** (Q1); live classification NOT VERIFIED (Q22). No business writes |
| Phase 13 | **Production observability, reliability and agent evaluation foundation** (§3l): typed observability events separate from audit, request/turn/span correlation, redaction policy, error taxonomy, retry policy (ERP reads only), strict deadlines, bounded metrics, diagnostics (`Agent.diagnose`, `--trace`, log reconstruction), evaluation dataset + golden suite, failure injection, performance baseline | ✅ Done, MOCK VERIFIED against the fake ERP (1107 passed, 17 skipped, 0 failed (1124 collected = 933 + 191 new; 3 min 1 s, 13:58:32 → 14:01:38, no host stall)). **Real ERP NOT VERIFIED** (Q1); live model NOT VERIFIED (Q22); no metrics exporter (Q3). No business writes |
| Phase 14 | **Production ERP integration readiness and real ERP adapter** (§3m): Q1 input gate, ERP contract model and validation, security gate (effective privileges, schema, tenant/warehouse isolation), tenant binding, capability activation, readiness tool, production policy, adapter contract suite | ⛔ **BLOCKED at the input gate (Q1 missing: items A–T)**. Real ERP NOT TOUCHED; no mapping, rule or reader written. Gate tooling MOCK VERIFIED + VERIFIED LOCALLY on throw-away PostgreSQL (1193 passed, 18 skipped, 0 failed (1211 collected = 1124 + 87 new; 4 min 40 s, 17:00:24 → 17:05:09, no host stall)). No business writes |
| Phase 15 | **Production hardening and operational readiness** (§3o, ADR-064): bounded shutdown sequence, in-flight request counting, per-process concurrent-turn cap (`SERVER_BUSY`), build information, readiness `erp` component (never gates or connects), production policy additions, default response headers, exporter boundary, pinned image dependencies, Agent DB backup scripts, deployment smoke test | ✅ Implemented 2026-10-05; **documented 2026-10-10 (Phase 17)**. Phase 15 tests re-run in Phase 17: 87 passed / 1 skipped (`test_operational_readiness.py` 40, `integration/test_lifecycle_postgres.py` 4, `test_production_unit.py` 44 incl. 1 POSIX skip); full suite 1302 passed, 18 skipped, 0 failed. **Deployment smoke test and backup scripts NOT RUN** (no recorded results). No migration; no ERP contact |
| Phase 16 | **Long-term business memory and historical business intelligence** (§3n): same-period-last-year sales comparison on `daily_sales`, year periods, two new named rules, comparison metrics and rendering, analysis references in episodes, `basis` on recall, verification of every business-domain turn, percentage grounding, profit guard | ✅ Partial, MOCK VERIFIED against the fake ERP (1302 passed, 18 skipped, 0 failed; 1320 collected = 1256 + 64 new). **Customer purchase history and product-level sales BLOCKED** (Q1, Q27). Real ERP NOT VERIFIED; no business writes; no migration |
| Phase 17 | **Documentation reconciliation** (§3o): Phase 15 documented from its code and tests; ADR-064 written; no code, test, configuration or ERP change | ✅ Done (documentation only; tests re-run as evidence, § 3o.5) |
| Phase 18 | **ERP access contract preparation** (§3p): audit of the ERP contract against code and tests, verified capability matrix, future read-contract specification (ADR-066, Proposed), owner decisions (Q29, Q30 new), testable vs blocked lists, ordered integration plan | ✅ Done (documentation only; targeted baseline 480 passed / 1 skipped; readiness BLOCKED, exit 3). **Real ERP NOT CONTACTED**; nothing implemented |
| Phase 19 | **Warehouse scope of sales, customer and invoice reads** (§3q): security audit of the request path; finding confirmed against the fake ERP | ⛔ BLOCKED on owner decision Q29 (no authoritative policy; no warehouse attribution on non-stock records). No code change; targeted suite 480 passed / 1 skipped before and after. **Real ERP NOT CONTACTED** |
| Phase 20 | **Proposed owner decisions for Q29** (§3r): option (a) proposed separately for sales and for customer outstanding / invoices; Q8 left pending | 📝 Documentation only. Both Q29 proposals **PROPOSED — OWNER APPROVAL PENDING**; Q8 **PENDING — OWNER DECISION REQUIRED**; Q29 and Q8 unresolved. No code, test or configuration change; no tests run. **Real ERP NOT CONTACTED** |
| Phase 21 | **Owner decision readiness and safety gate** (§3s): proposed / approved / implemented / real-ERP status per decision; reviewer checklist (ERP_CONTRACT.md § 12) | 📝 Documentation only. Nothing approved; Q29, Q8, Q1 (A–T) and Q4 open; readiness BLOCKED, exit 3. No code, test or configuration change. **Real ERP NOT CONTACTED** |
| Phase 22 | **InvenLogic real ERP integration** (§3t, ADR-067): owner confirmed InvenLogic as the real ERP; six capability readers (`invenlogic_v1`) over three of its databases; multi-database contract, gate and adapter; financial-year rule; fake ERP deleted; PostgreSQL 18.6 TEST replica for tests | ✅ Readers done, VERIFIED ON THE TEST REPLICA (contract suite 18/18; `erp_readiness --live` READY_FOR_ACTIVATION for all three databases). Full suite: 1373 passed, 35 skipped, 0 failed with PostgreSQL 16, Redis 7 and the InvenLogic TEST replica (PostgreSQL 18.6); without the replica 966 passed, 442 skipped, 0 failed (baseline before Phase 22: 1326 passed, 18 skipped). ruff and mypy clean. Ollama end-to-end: 5 Hindi/English business questions through the CLI with Ollama qwen3:8b (LLM_OLLAMA_NUM_CTX=4096) and the InvenLogic TEST replica: 5/5 completed, every ERP read SUCCESS; outstanding ₹45,000, today's sales ₹1,25,000, iPhone 15 stock 15 pcs, low stock iPhone 3/5 and Galaxy 0/2 all correct; for "pending bills dikhao" the model picked customer_outstanding instead of customer_open_invoices (right total, no bill list). 2.4-8.1 min per turn on CPU. ⛔ **Live company ERP NOT CONTACTED**: readiness BLOCKED on A, B, C, D, E, F, G, T (owner/DBA inputs) |
| 2026-11-25/26 | **Routing repair, customer counts, chat tables, live testing** (§3u): unanswerable reads no longer reported as "purchase"; new READ capability `business.customer_count`; tool-calling robustness for the local model; deterministic answers where the model mislabelled or denied data; open-bills query 4.4 s → 0.012 s | ✅ Done on the TEST replica. Full suite 1435 passed, 35 skipped, 0 failed; live chat (Ollama qwen3:8b, company 900000002) 20/20 correct (§3u.4). Live company ERP NOT CONTACTED |
| Later | Purchase Agent and other domain agents | ⏳ Not started. Needs business documentation. Their keys are reserved in the domain catalogue (§3k). |

**Not done, by design:** no Purchase, Accounting or GST logic; no business tables in the Agent DB; Sales and Inventory are read only, from the company's InvenLogic ERP (Phase 22: six read capabilities, no write logic); no invented business schema (the InvenLogic mapping comes from the owner-confirmed Schema Atlas); no fake database operations (the fake ERP was deleted in Phase 22; tests use a TEST replica database); no business roles or approval thresholds; no domain agent beyond `general` and the read-only `business` domain (Phase 12 added the Supervisor; future domains are reserved, not implemented). Write tools run only through human approval (Phase 5). The HTTP API exists (Phase 6) and is deployable as a container (Phase 7). No production identity provider has been chosen (Q6; the adapter is verified against a reference Keycloak), no successful live completion with a hosted model has run (no Claude credentials, Q22; the local Ollama provider is integrated since 2026-10-29, end-to-end result: 5 Hindi/English business questions through the CLI with Ollama qwen3:8b (LLM_OLLAMA_NUM_CTX=4096) and the InvenLogic TEST replica: 5/5 completed, every ERP read SUCCESS; outstanding ₹45,000, today's sales ₹1,25,000, iPhone 15 stock 15 pcs, low stock iPhone 3/5 and Galaxy 0/2 all correct; for "pending bills dikhao" the model picked customer_outstanding instead of customer_open_invoices (right total, no bill list). 2.4-8.1 min per turn on CPU, see § 3t.5), and there is no frontend or token revocation. Phase 8 added the TLS gateway and rate limiting (deployment-neutral; the platform is still Q3).

## 2. Phase 1 summary: agent foundation

```
START → understand_request → process_request → generate_response → END
              │  (needs clarification) ───────────────↗        │
              └──(error)──► handle_error ◄──(error)────────────┘ → END
```

Typed `AgentState`; provider-neutral `LLMProvider` with an Anthropic adapter (`claude-opus-5-5`, structured output, refusal fallback); versioned prompts; tool registry; error hierarchy; structured logging; pydantic-settings configuration; `AgentResponse`; CLI. Business requests are answered as `unsupported` and never attempted. Domain agents plug in through `DomainRegistry` (ADR-019). Details: ARCHITECTURE.md § A.4, ADR-017 to ADR-021.

## 3. Phase 2 summary: PostgreSQL foundation

No PostgreSQL existed in the project (no code, config, containers or local install), so nothing could be reused. Phase 2 created the database layer. There is exactly **one** database system: PostgreSQL 16.

```
Agent → Tool → Business Service → UnitOfWork (transaction) → Repository → PostgreSQL
```

| # | Requirement | Where |
|---|---|---|
| 1 | Connection management | `db/engine.py` `Database` (one `AsyncEngine` per process, lazy connect, `dispose()`) |
| 2 | Connection pooling | `QueuePool`: size/overflow/timeout/recycle/pre-ping from settings; `Database.pool_status()` |
| 3–4 | Configuration and environment variables | `core/config.py` `DatabaseSettings` (`DATABASE_*`, §7); `.env.example` |
| 5 | Session management | `async_sessionmaker` inside `Database`; sessions only via `UnitOfWork` |
| 6 | Transaction management | `db/unit_of_work.py`: commit / rollback / `read_only` (ADR-023) |
| 7 | Repository abstraction | `db/repository.py` `Repository[Model]` |
| 8 | Service/repository separation | Services open units of work; repositories never commit (`services/database_health.py` ↔ `db/health.py`) |
| 9 | Migration strategy | Alembic in `db/migrations/`, `alembic.ini`, baseline `0001`, advisory lock (ADR-025) |
| 10 | Database error handling | `db/errors.py` SQLSTATE → `DatabaseError` hierarchy in `core/errors.py` |
| 11 | Database health check | `DatabaseHealthService` (`ok` / `degraded` / `unavailable` / `not_configured`); CLI `--db-health`; tool `system.database_health` |
| 12 | Tests | `tests/test_db_unit.py` (no server), `tests/integration/test_postgres.py` (real PostgreSQL) |

**The LLM cannot execute SQL.** No tool accepts SQL or SQL fragments; a test checks every registered tool. Repositories take typed arguments only. Read paths run in read-only transactions. The runtime role cannot run DDL. Every statement has a server-side timeout (ADR-004, ADR-024).

### How to run

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env                     # set ANTHROPIC_API_KEY; DB defaults match docker-compose

docker compose up -d postgres              # PostgreSQL 16 + roles + agentic_test DB
.venv\Scripts\alembic upgrade head         # uses DATABASE_MIGRATION_URL (owner role)
.venv\Scripts\python -m agentic_platform --db-health
.venv\Scripts\python -m agentic_platform "Is the database working?"

# tests: unit tests always run; integration tests need these two variables
$env:TEST_DATABASE_URL="postgresql://agentic_owner:agentic_owner_dev@localhost:5432/agentic_test"
$env:TEST_DATABASE_APP_URL="postgresql://agentic_app:agentic_app_dev@localhost:5432/agentic_test"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy
```

### Verification performed (2026-10-01)

* `pytest` with a database: **108 passed, 0 skipped** (99 unit + 9 integration against `postgres:16` in Docker). Without `TEST_DATABASE_URL`: 99 passed, 9 skipped.
* `ruff check`: clean. `mypy --strict`: clean (47 files).
* Against the dev database: `alembic upgrade head` / `downgrade base` / `upgrade head` succeeded. `--db-health` reported `degraded` before migrating and `ok` after, connected as the least-privilege `agentic_app` role.
* Integration tests verified: commit, rollback, read-only rejection, unique-violation mapping (no row values leaked), statement timeout, pool exhaustion, wrong password (not retryable), migration up/down, app role has DML but no CREATE/DROP, and the agent → tool → service → repository → PostgreSQL path end to end.
* **Still not verified:** a real Claude call (no API credentials on this machine; Known issue 1).

## 3a. Phase 3, part A: durable state, audit, idempotency

**Scope** (first Phase 3 delivery, chosen by the stakeholder on 2026-10-01 from the earlier §10 proposal): durable conversations plus the audit and idempotency tables with their repositories and services. The checkpointer is built on **asyncpg** (no psycopg). Principal, permissions and HITL were deferred (now proposed for Phase 5). When the stakeholder then defined Phase 3 as "Redis and agent persistence", this work was kept as its checkpoint/persistence foundation (it was not rebuilt), and part B (§3b) added the rest.

| # | Deliverable | Where |
|---|---|---|
| 1 | PostgreSQL LangGraph checkpointer (async `BaseCheckpointSaver`) | `agents/checkpointer.py` `PostgresCheckpointSaver`, storage in `db/checkpoints.py` (ADR-027) |
| 2 | Checkpointer selection | `AGENT_CHECKPOINTER` = `auto` (default: PostgreSQL if `DATABASE_URL` is set, else memory) / `postgres` / `memory` / `none`; `create_checkpointer`; `Agent.from_settings` |
| 3 | Resumable CLI conversations | `--thread <id>`; the CLI prints the conversation id |
| 4 | Audit log (append-only, enforced by triggers) | `db/audit.py`, `services/audit.py` (ADR-028) |
| 5 | Every tool call audited when a database is configured | `ToolRegistry(auditor=...)` in `tools/registry.py`, wired in `agents/dependencies.py` |
| 6 | Idempotency store and protocol | `db/idempotency.py`, `services/idempotency.py` (ADR-029). Ready for future write tools (proposed Phase 5); used for agent turn idempotency since §3b |
| 7 | Migration `0002` | `db/migrations/versions/20261001_0002_checkpoints_audit_idempotency.py` |
| 8 | Tests | `tests/test_persistence_unit.py` (16, no server), `tests/integration/test_persistence_postgres.py` (17, real PostgreSQL) |

### How to run (additions to §3)

```powershell
.venv\Scripts\alembic upgrade head                                   # now migrates to 0002
.venv\Scripts\python -m agentic_platform "What time is it?"           # prints "(conversation <id>)"
.venv\Scripts\python -m agentic_platform --thread <id> "And in London?"  # continues it, even after a restart
```

### Verification performed (2026-10-01)

* `pytest` with a database: **141 passed, 0 skipped** (115 unit + 26 integration against `postgres:16`). A second run of the integration tests on the same database also passed, so the tests do not depend on a fresh database. Without `TEST_DATABASE_URL`: 115 passed, 26 skipped.
* `ruff check`: clean. `mypy --strict`: clean (54 files).
* Dev database `agentic`: `alembic upgrade head` (0001 → 0002), `downgrade -1`, `upgrade head` all succeeded. `--db-health` reported `ok` at revision `0002` as `agentic_app`.
* Integration tests verified:
  * ORM models match migration `0002` (Alembic autogenerate diff is empty).
  * Saver put/get/list (filter, before, limit), regular vs special pending writes, and delete.
  * `interrupt()` → `Command(resume=...)` works across a fresh engine and saver.
  * An agent conversation continues after a "restart" (new `Agent`), running as the least-privilege `agentic_app` role.
  * Audit: append/read and rollback with the caller's unit of work. UPDATE/DELETE/TRUNCATE are rejected for both the app and owner roles. Agent tool calls (including an unknown tool) are audited with the request id.
  * Idempotency: claim, replay, key reuse, in-progress, failed → retry, crashed-worker lease takeover, TTL expiry, lost lease, completion committed or rolled back with the business unit of work, and 5 concurrent claims with exactly one winner.
* **Still not verified:** a real Claude call (Known issue 1). The CLI conversation flow is covered by tests with a scripted LLM, not by a live run.

### Database changes (Phase 3, migration `0002`)

| Object | Purpose |
|---|---|
| `agent_checkpoints` | One row per checkpoint: serialized checkpoint (bytea) + JSONB metadata; PK (thread, ns, checkpoint id `COLLATE "C"`) |
| `agent_checkpoint_blobs` | Channel values per (thread, ns, channel, version) |
| `agent_checkpoint_writes` | Pending writes per (thread, ns, checkpoint, task, idx) |
| `audit_log` | Append-only audit trail; identity PK; checks on actor type, status and approval decision; indexes on `occurred_at`, (`thread_id`, `occurred_at`) and (`tenant_id`, `occurred_at`) |
| `audit_log_reject_change()` + triggers `audit_log_no_update_delete`, `audit_log_no_truncate` | Make `audit_log` append-only for every role |
| `idempotency_record` | PK (`scope`, `idempotency_key`); status/length checks; index on `expires_at` |

No role or grant changes: `agentic_app` gets DML on the new tables through the Phase 2 default privileges. Still no business tables.

### Files created (Phase 3)

| Path | Purpose |
|---|---|
| `src/agentic_platform/agents/checkpointer.py` | `PostgresCheckpointSaver`, `create_checkpointer` |
| `src/agentic_platform/db/checkpoints.py` | Checkpoint tables + `CheckpointRepository` |
| `src/agentic_platform/db/audit.py` | `AuditLogEntry` + `AuditRepository` |
| `src/agentic_platform/db/idempotency.py` | `IdempotencyRecord` + `IdempotencyRepository` |
| `src/agentic_platform/services/audit.py` | `AuditService`, `AuditEvent`, `AuditRecord` |
| `src/agentic_platform/services/idempotency.py` | `IdempotencyService`, `IdempotencyClaim` |
| `src/agentic_platform/core/hashing.py` | `stable_hash` |
| `src/agentic_platform/db/migrations/versions/20261001_0002_checkpoints_audit_idempotency.py` | Migration `0002` |
| `tests/test_persistence_unit.py`, `tests/integration/test_persistence_postgres.py` | Phase 3 tests |

### Files modified (Phase 3)

| Path | Change |
|---|---|
| `src/agentic_platform/core/config.py` | `AgentSettings.checkpointer` (`AGENT_CHECKPOINTER`) |
| `src/agentic_platform/core/errors.py` | `IdempotencyError` family |
| `src/agentic_platform/db/models.py` | Imports platform models; `PLATFORM_TABLES` |
| `src/agentic_platform/tools/registry.py` | Optional `auditor`; every call audited |
| `src/agentic_platform/agents/dependencies.py` | Registry gets `AuditService` when a database is configured |
| `src/agentic_platform/agents/agent.py` | `from_settings` picks the checkpointer from settings |
| `src/agentic_platform/__main__.py` | Settings-driven checkpointer; `--thread` |
| `tests/test_db_unit.py` | Head is now `0002`; schema guard allows platform tables only |
| `.env.example` | `AGENT_CHECKPOINTER` |
| `ARCHITECTURE.md` | Header, § A.4 row, new § A.6 |
| `ARCHITECTURE_DECISIONS.md` | ADR-009/012/022 status; ADR-027 to ADR-029 |
| `PROJECT_STATUS.md` | This file |

**Behaviour change:** `Agent.from_settings()` with no `checkpointer` argument used to run stateless. It now uses `AGENT_CHECKPOINTER` (default `auto`). Set `AGENT_CHECKPOINTER=none` for the old behaviour. With `DATABASE_URL` set, the database must be migrated to `0002` before the agent can take a turn.

## 3b. Phase 3, part B: Redis, thread model, resume

**Scope** (stakeholder definition of Phase 3, 2026-10-01). Items already covered by part A were reused, not rebuilt:

| # | Requirement | Where |
|---|---|---|
| 1 | Redis connection | `cache/redis.py` `RedisConnection`: one asyncio client and pool per process, lazy, `dispose()` per event loop, socket/connect timeouts, client name |
| 2 | Redis configuration | `core/config.py` `RedisSettings` (`REDIS_*`, §7); only `redis://` / `rediss://`; secret-masked URL; `docker-compose.yml` service `redis` |
| 3 | Redis service abstraction | `cache/backend.py` `KeyValueBackend` contract, with `RedisBackend` (prefix, TTL enforced, Lua compare-and-delete/expire) and `InMemoryBackend` fallback; `build_cache_runtime`; `cache/errors.py`; `services/redis_health.py` + CLI `--redis-health` |
| 4 | Cache abstraction | `cache/cache.py` `Cache` (cache-aside, JSON, namespaces, `get_or_load`, **fails open**) |
| 5 | Temporary state support | `cache/temporary_state.py` `TemporaryStateStore` (scoped, mandatory TTL, `touch`, 64 KiB cap, **fails closed**) |
| 6 | Idempotency support | Part A's PostgreSQL store plus a **Redis fast-path** in `services/idempotency.py` (ADR-030), plus **turn idempotency**: `Agent.arun(idempotency_key=...)` replays the stored response |
| 7 | Distributed lock abstraction | `cache/locks.py` `LockManager` (`SET NX PX` + token, wait/retry, auto-renewal, lost-lock detection, safe release, **fails closed**); used for one turn per thread (ADR-031) |
| 8 | LangGraph checkpoint/persistence | Part A's `PostgresCheckpointSaver` (ADR-027), now run with `durability="sync"` |
| 9 | Conversation/thread identification | `agents/threads.py` (format rules, generation, lock name, config); `Agent.athread_status`; CLI `--status` |
| 10 | Agent workflow resume | `Agent.aresume`: crashed turns continue from the last finished step, interrupted turns resume with `Command(resume=...)`; pending-turn guard; `status="interrupted"`; CLI `--resume`. Node wrappers no longer swallow `interrupt()` (ADR-032) |
| 11 | Persistence error handling | `CacheError` / `LockError` families; checkpoint/lock/idempotency failures become structured, retryable `failed` responses (`DATABASE_UNAVAILABLE`, `CACHE_UNAVAILABLE`, `THREAD_BUSY`, `TURN_INCOMPLETE`); fail-open vs fail-closed per use (ARCHITECTURE.md § A.7) |
| 12 | Tests | `tests/cache_contract.py` (backend contract), `tests/test_redis_unit.py` (38), `tests/test_resume_unit.py` (20), `tests/integration/test_redis.py` (20, real Redis + PostgreSQL) |

### Redis usage

Redis holds **only ephemeral, rebuildable data**. PostgreSQL remains the source of truth for business data, the audit log, idempotency records and conversation checkpoints.

| Use | Key pattern | TTL | If Redis is down |
|---|---|---|---|
| Thread lock (one turn per conversation) | `<prefix>:lock:thread:<thread_id>` | `AGENT_THREAD_LOCK_TTL_SECONDS` (30 s), renewed every 10 s | Turn fails, retryable (`CACHE_UNAVAILABLE`) |
| Idempotency fast-path (completed results) | `<prefix>:idem:<scope>:<sha256(key)>` | `REDIS_IDEMPOTENCY_CACHE_TTL_SECONDS` (1 h), never longer than the PostgreSQL record | PostgreSQL answers |
| Cache (available; no production caller yet) | `<prefix>:cache:<namespace>:<key>` | `REDIS_CACHE_DEFAULT_TTL_SECONDS` (5 min) or per call | Miss; the loader answers |
| Temporary state (available; no production caller yet) | `<prefix>:state:<scope>:<key>` | `REDIS_TEMPORARY_STATE_TTL_SECONDS` (1 h) or per call | Caller gets `CacheUnavailableError` |
| Health probe | `<prefix>:health:<random>` | 10 s | Health reports `unavailable` |

Not in Redis, by design: conversation state and checkpoints (they must survive a Redis flush to be resumable), business data, and audit or idempotency records of record.

Without `REDIS_URL` the same abstractions run on the in-memory backend. That is fine for one process (dev, CLI, tests), but locks are not shared between processes. **Production with more than one process needs Redis.**

### Checkpoint architecture (summary; details in ARCHITECTURE.md § A.6, § A.7)

* **Store:** PostgreSQL tables `agent_checkpoints` / `agent_checkpoint_blobs` / `agent_checkpoint_writes`, written by our asyncpg `PostgresCheckpointSaver`, keyed by `thread_id`.
* **Durability:** `durability="sync"`, so each finished node is saved before the next starts.
* **New input** on a thread starts a turn; a thread with an unfinished turn refuses new input until it is resumed.
* **Crash:** the response is `TURN_INCOMPLETE`, and the thread is pending at the next node. `--resume` / `aresume` continue from there without repeating finished LLM or tool calls (verified after a simulated restart).
* **Interrupt:** a node calling `interrupt(value)` returns `status="interrupted"` with the value. `aresume(thread, resume_value=...)` continues. This is the mechanism the planned approval gate (proposed Phase 5) will use (verified with a test-only domain handler; the production graph has no interrupting node yet).
* **Concurrency:** turns on one thread are serialized by the distributed lock.

### How to run (additions)

```powershell
docker compose up -d postgres redis
.venv\Scripts\python -m agentic_platform --redis-health
.venv\Scripts\python -m agentic_platform --thread <id> --status
.venv\Scripts\python -m agentic_platform --thread <id> --resume            # finish a crashed turn
.venv\Scripts\python -m agentic_platform --thread <id> --resume "answer"   # answer an interrupt

$env:TEST_REDIS_URL="redis://:agentic_redis_dev@localhost:6379/15"     # plus the two TEST_DATABASE_* variables
.venv\Scripts\python -m pytest
```

### Verification performed (2026-10-01)

* `pytest` with PostgreSQL 16 and Redis 7.4 in Docker: **219 passed, 0 skipped** (173 unit + 46 integration). A second run of the integration tests on the same servers also passed. Without servers: 173 passed, 46 skipped.
* `ruff check`: clean. `mypy --strict`: clean (63 source files).
* Live CLI against the dev servers:
  * `--redis-health` → `ok` (Redis 7.4.11).
  * `--thread demo-thread-1 --status` → `exists: false`.
  * An invalid thread id is rejected with a clear message.
  * `--resume` on a thread with nothing pending is refused.
* Integration tests verified:
  * The Redis backend passes the same contract as the in-memory one.
  * Every key is prefixed and has a TTL.
  * A wrong password is unavailable and not retryable.
  * Locks are shared across separate connections; 30 contended critical sections ran with at most one holder at a time; renewal holds a lock for longer than 3× its TTL.
  * Idempotency fast-path: replay works even with PostgreSQL unreachable, a different request still gets `IDEMPOTENCY_KEY_REUSED` from PostgreSQL, PostgreSQL refills a flushed cache, and everything works with Redis down.
  * Agent turn idempotency: a replay from a "second process" makes no LLM call.
  * A thread lock held by another process gives `THREAD_BUSY`.
  * A crashed turn resumes after a "restart" with a single LLM call, and no lock keys are left behind.
* **Still not verified:** a real Claude call (Known issue 1). Resume has been exercised with a scripted LLM and simulated crashes, not with a real killed process.

### Files created (Phase 3, part B)

| Path | Purpose |
|---|---|
| `src/agentic_platform/cache/__init__.py` | Public API, `CacheRuntime`, `build_cache_runtime` |
| `src/agentic_platform/cache/backend.py` | `KeyValueBackend` contract, `InMemoryBackend` |
| `src/agentic_platform/cache/redis.py` | `RedisConnection`, `RedisBackend` |
| `src/agentic_platform/cache/errors.py` | redis-py → `CacheError` translation |
| `src/agentic_platform/cache/cache.py` | `Cache` |
| `src/agentic_platform/cache/temporary_state.py` | `TemporaryStateStore` |
| `src/agentic_platform/cache/locks.py` | `LockManager`, `Lock` |
| `src/agentic_platform/services/redis_health.py` | `RedisHealthService` |
| `src/agentic_platform/agents/threads.py` | Thread id rules and helpers |
| `tests/cache_contract.py` | Backend contract suite (shared by unit and integration tests) |
| `tests/test_redis_unit.py`, `tests/test_resume_unit.py` | Unit tests |
| `tests/integration/test_redis.py` | Redis (+ PostgreSQL) integration tests |

### Files modified (Phase 3, part B)

| Path | Change |
|---|---|
| `src/agentic_platform/core/config.py` | `RedisSettings`, `normalise_redis_url`, `AppSettings.redis`; `AGENT_THREAD_LOCK_TTL_SECONDS` / `AGENT_THREAD_LOCK_WAIT_SECONDS` |
| `src/agentic_platform/core/errors.py` | `CacheError`, `LockError` families; `InvalidThreadIdError`, `ThreadPendingError`, `NothingToResumeError`, `ThreadBusyError` |
| `src/agentic_platform/agents/agent.py` | Thread lock, pending-turn guard, `aresume` / `resume`, `athread_status` / `thread_status`, turn idempotency, `durability="sync"`, persistence errors as structured failures, `TURN_INCOMPLETE`, Redis disposal |
| `src/agentic_platform/agents/schemas.py` | `ResponseStatus` (+`interrupted`), `InterruptInfo`, `AgentResponse.interrupts` / `replayed`, `ThreadStatus` |
| `src/agentic_platform/agents/dependencies.py` | `cache`, `locks`, `idempotency` dependencies |
| `src/agentic_platform/agents/nodes/_common.py` | Re-raise `GraphBubbleUp` so `interrupt()` works inside nodes |
| `src/agentic_platform/services/idempotency.py` | Redis fast-path (`fast_path=`) |
| `src/agentic_platform/__main__.py` | `--redis-health`, `--status`, `--resume` |
| `pyproject.toml` | `redis>=5.2,<9`; `redis` pytest marker |
| `docker-compose.yml` | `redis` service (Redis 7, password, no persistence, `noeviction`) |
| `.env.example` | `REDIS_*`, `AGENT_THREAD_LOCK_*`, `REDIS_PASSWORD` / `REDIS_PORT`, `TEST_REDIS_URL` |
| `tests/conftest.py` | Isolates `REDIS_URL` and `AGENT_CHECKPOINTER` from unit tests |
| `ARCHITECTURE.md` | Header, § A.6 title, new § A.7, § B.10 statuses |
| `ARCHITECTURE_DECISIONS.md` | ADR-007/012 status; ADR-030 to ADR-032 |
| `PROJECT_STATUS.md` | This file |

No database migration in part B (schema still at `0002`).

**Behaviour changes:**
* Turns now refuse new input on a thread with an unfinished turn (`ThreadPendingError`).
* Concurrent turns on one thread get `THREAD_BUSY`.
* `AgentResponse.status` can be `interrupted`.
* With `REDIS_URL` set and Redis down, agent turns fail (retryable) because the thread lock fails closed.

## 3c. Phase 4: agent memory system

**Architecture** (as requested): `Agent → MemoryManager → memory type → storage adapter`. Memory types depend only on the interfaces in `memory/ports.py`, so stores can be replaced later. Details: ARCHITECTURE.md § A.8, ADR-033 to ADR-036.

### What is actually implemented

| # | Memory type | Status | What it does | Storage |
|---|---|---|---|---|
| 1 | Working / short-term | ✅ Implemented (API) | Per-thread JSON scratch (`set/get/delete/clear`), recalled into each turn. Short-term message window = graph `messages` (existing) | Redis (in-memory fallback), TTL 1 h |
| 2 | Conversation | ✅ Implemented | History read from the checkpointer; one stored summary per thread (`save_summary`), recalled into each turn | Checkpointer + PostgreSQL |
| 3 | Long-term | ✅ Implemented | User/tenant facts by (namespace, key), upsert/get/list/forget, similarity recall, optional TTL | PostgreSQL + pgvector |
| 4 | Semantic | ✅ Implemented | Knowledge documents chunked, embedded, searched; re-ingest replaces; knowledge source types only; CLI `--ingest` | PostgreSQL + pgvector |
| 5 | Episodic | ✅ Implemented, **written automatically** | Every completed/clarifying/unsupported turn is recorded (summary, intent, outcome, tools) and recalled by similarity | PostgreSQL + pgvector, 90-day expiry |
| 6 | Procedural | ✅ Implemented (API) | Built-in procedures = versioned prompt files; tenant procedures = new DB version per `define`, get latest/specific, delete version | Prompt files + PostgreSQL |
| 7 | LangGraph checkpoint state | ✅ Implemented (from Phase 3) + memory view | `CheckpointMemory`: latest/history/delete per thread | PostgreSQL (`agent_checkpoint*`) |

| Requirement | Where |
|---|---|
| Memory manager | `memory/manager.py` `MemoryManager`, `build_memory_manager` |
| Interfaces | `memory/ports.py` `MemoryStore`, `VectorStore`, `Embedder`, `ConversationSource` |
| Read / write / retrieval / update / delete | Memory type methods; `MemoryStore.insert/upsert/find/update/delete/replace` |
| Retention | Working TTL; `expires_at` (episodes 90 d, optional fact TTL); `purge_expired()` + CLI `--memory-purge`; `forget_thread`; `forget_user` |
| Embedding abstraction | `memory/embeddings.py`: `HashingEmbedder` (default, local, lexical), `VoyageEmbedder` (semantic, HTTP) |
| Vector retrieval abstraction | `VectorStore` port; `PostgresMemoryStore.search` (pgvector cosine `<=>`), `InMemoryMemoryStore.search` |
| Metadata | `memory_item`: kind, namespace, key, version, thread, JSONB metadata, source type/ref, embedding model, created/updated/expires |
| Isolation | `MemoryScope(tenant_id, user_id)` on every call; explicit tenant filters; **PostgreSQL row-level security**; user-private vs tenant-shared rules |
| Agent integration | `Agent.arun/aresume(scope=...)`: recall before the turn → `memory_context` in state (`STATE_VERSION` 2) → answer prompt (system prompt v2); episode after the turn; failures never fail a turn |
| Not business truth | Semantic memory accepts knowledge source types only; recalled context is labelled "not a source of truth"; prompt v2 forbids presenting amounts/statuses from memory as fact |

### What remains planned (not implemented)

* **Automatic conversation summarisation** (LLM-written rolling summaries); summaries are only stored/recalled when written through the API.
* **Memory-writing by the agent**: no tool or node extracts facts from conversations into long-term memory (deliberately: needs permissions + approval, Phase 5). Writes come from the API/CLI.
* **Agent use of working memory and procedural memory**: both are available and working memory is recalled, but no node writes working memory and the agent's prompts are still loaded from `PromptRegistry`, not tenant procedure overrides.
* **Semantic embeddings in production**: the default embedder is lexical. The Voyage adapter is tested only against a mocked HTTP API (no key here); model name to confirm.
* **ANN index** (HNSW) per embedding model, once a production model is fixed; searches are exact per tenant today.
* **Recall before routing**: only `generate_response` sees memory; `understand_request` does not.
* **Authenticated scope**: tenant/user are caller-asserted; checkpoint threads are not bound to tenants/users (Phase 5).
* **PII policy and scheduled retention** (Q11): `purge_expired` exists but nothing schedules it.
* Long-term store via LangGraph `BaseStore` API compatibility (not needed by the current graph).

### How to run (additions)

```powershell
docker compose up -d postgres redis        # postgres image is now pgvector/pgvector:pg16-trixie
.venv\Scripts\alembic upgrade head          # 0003: CREATE EXTENSION vector, memory_item, RLS
.venv\Scripts\python -m agentic_platform --tenant acme --ingest handbook.md
.venv\Scripts\python -m agentic_platform --tenant acme --user asha "How many days of paid leave?"
.venv\Scripts\python -m agentic_platform --memory-purge
```

### Verification performed (2026-10-01)

* `pytest` with PostgreSQL 16 + pgvector 0.8.6 and Redis 7.4: **265 passed, 0 skipped** (204 unit + 61 integration). Without servers: 204 passed, 61 skipped.
* `ruff check`: clean. `mypy --strict`: clean (81 source files).
* Dev database `agentic`: migrated 0002 → 0003 → down → up; `--db-health` `ok` at revision `0003`.
* Live CLI against the dev servers: `--tenant demo-co --ingest handbook.md` stored a tenant-shared semantic chunk (`hashing-v1-256`, 256-dim `vector`); an invalid tenant id is rejected; `--memory-purge` runs (demo rows removed afterwards).
* One integration run took 13.6 minutes instead of under a minute. A profiled rerun (slowest test 2.2 s) and a lock monitor on `pg_stat_activity` showed no lock waits or idle transactions, and the final run took 28.5 s for all 265 tests. The cause was **not identified** (possibly host-level scanning); watch for it.
* The same **memory contract suite** (11 checks: upsert/get/forget, user + tenant isolation, ranking, TTL + purge, semantic ingest/replace/delete, private documents, business-record refusal, episodes, procedural versions, summaries, update/forget_user) passes on both the in-memory adapter and PostgreSQL+pgvector (as the RLS-restricted app role).
* RLS verified with deliberately unfiltered SQL: another tenant's rows are invisible, no tenant bound = no rows, cross-tenant insert rejected (`DATABASE_PERMISSION_DENIED`); the maintenance purge crosses tenants.
* pgvector: cosine ranking, `vector` column type, vectors of a different embedding model are never returned.
* Agent end to end on PostgreSQL: a knowledge document ingested for a tenant is recalled into the answer prompt of a later turn; episodes of both turns are stored with expiry; another tenant does not see the first tenant's facts; memory failures (embedder down) do not fail turns; memory can be disabled.
* Bugs found and fixed by these tests: ORM rows expired by the read-only rollback before conversion (vector search); an ordering tie on Windows' coarse clock in the in-memory store; a chunk-size off-by-one.
* **Still not verified:** a real Claude call (Known issue 1) and a real Voyage call (no key).

### Database changes (Phase 4, migration `0003`)

| Object | Purpose |
|---|---|
| Extension `vector` (pgvector 0.8.6) | Vector type and distance operators. Not dropped on downgrade |
| Table `memory_item` | All durable memory kinds; `embedding vector` + `embedding_model`; JSONB metadata; `expires_at` |
| Checks | kind, content length, tenant length, embedding ⇔ model, version ≥ 1 |
| Indexes | `ix_memory_item_scope`, `_thread`, `_source`, `_expires_at`; partial unique `uq_memory_item_long_term_key`, `_procedural_version`, `_conversation_summary` |
| RLS policy `memory_item_tenant_isolation` | Tenant isolation for the runtime role; maintenance flag for purge |
| Docker image | `postgres:16` → `pgvector/pgvector:pg16-trixie` (same Debian base; existing volume kept) |

### Files created (Phase 4)

| Path | Purpose |
|---|---|
| `src/agentic_platform/memory/__init__.py` | Public API |
| `src/agentic_platform/memory/models.py` | `MemoryScope`, `MemoryKind`, `MemoryRecord`, `NewMemory`, queries, `Episode`, `MemoryContext` |
| `src/agentic_platform/memory/ports.py` | Storage interfaces |
| `src/agentic_platform/memory/manager.py` | `MemoryManager`, `build_memory_manager` |
| `src/agentic_platform/memory/working.py`, `conversation.py`, `long_term.py`, `semantic.py`, `episodic.py`, `procedural.py`, `checkpoints.py` | The memory types |
| `src/agentic_platform/memory/embeddings.py`, `chunking.py` | Embedders; document chunking |
| `src/agentic_platform/memory/adapters/{__init__,postgres,in_memory,checkpoint}.py` | Storage adapters |
| `src/agentic_platform/db/memory.py` | `Vector` type, `MemoryItem`, `MemoryRepository` |
| `src/agentic_platform/db/migrations/versions/20261001_0003_agent_memory.py` | Migration `0003` |
| `src/agentic_platform/prompts/templates/generate_response.system.v2.md` | Prompt with memory rules |
| `tests/memory_contract.py`, `tests/test_memory_unit.py`, `tests/integration/test_memory_postgres.py` | Tests |

### Files modified (Phase 4)

| Path | Change |
|---|---|
| `src/agentic_platform/core/config.py` | `MemorySettings` (`MEMORY_*`, `VOYAGE_API_KEY`), `AppSettings.memory` |
| `src/agentic_platform/core/errors.py` | `AgentMemoryError` family, `EmbeddingError`, `InvalidMemoryScopeError` |
| `src/agentic_platform/agents/agent.py` | `scope=` on run/resume; recall before turns; episodes after; checkpointer bound to memory |
| `src/agentic_platform/agents/state.py` | `memory_context`; `STATE_VERSION` 2 |
| `src/agentic_platform/agents/nodes/generate_response.py` | Adds recalled memory to `<agent_context>` |
| `src/agentic_platform/agents/dependencies.py` | `memory` dependency; injectable embedder |
| `src/agentic_platform/db/models.py` | Registers `memory_item` |
| `src/agentic_platform/__main__.py` | `--tenant`, `--user`, `--ingest`, `--memory-purge` |
| `docker-compose.yml` | pgvector image |
| `.env.example` | `MEMORY_*`, `VOYAGE_API_KEY` |
| `tests/test_db_unit.py` | Head `0003`; offline SQL checks extension, RLS, vector column |
| `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `PROJECT_STATUS.md` | § A.8, B.4/B.10 status; ADR-008/016 status, ADR-033 to ADR-036; this file |

**Behaviour changes:** the answer prompt is now `generate_response.system` v2; every finished turn writes an episode when memory is enabled (default); with a database, migration `0003` is required (pgvector must be installable).

## 3d. Phase 5: identity, authorization, permissions and human-in-the-loop

**Scope** (stakeholder definition, 2026-10-01): authenticated identity, trusted tenant and user context, conversation ownership, a reusable authorization layer, memory and tool permissions, human approval on the existing pause/resume, an audit trail and tenant isolation. Nothing for Purchase, Sales, Inventory, Accounting or GST. Details: ARCHITECTURE.md § A.9, ADR-037 to ADR-042.

Legend: **IMPLEMENTED** (code + tests) · **PARTIAL** (works, with a stated gap) · **PLANNED** · **OPEN** (needs a decision).

### 1. Summary

| Requirement | Status | Where |
|---|---|---|
| Identity context (user, tenant, roles, permissions, session) | IMPLEMENTED | `security/identity.py` `Principal`, `IdentityResolver` |
| Authentication | PARTIAL: development mechanisms only, no IdP (Q6) | `security/authentication.py` `Authenticator` protocol, `HmacTokenAuthenticator`, `LocalIdentity` |
| Trusted tenant/user propagation | IMPLEMENTED | `security/context.py` `acting_as`; `Agent` takes `principal=` (the Phase 4 `scope=` is removed) |
| Conversation and checkpoint ownership | IMPLEMENTED (no sharing: OPEN Q16) | `conversation_thread`, `services/conversations.py`, `<tenant>/<thread>` storage keys |
| Reusable authorization layer (RBAC) | IMPLEMENTED (provisional roles, Q8) | `security/permissions.py`, `security/authorization.py` |
| Memory permissions | IMPLEMENTED | `memory/authorized.py` `AuthorizedMemory` |
| Tool authorization boundary | IMPLEMENTED | `tools/registry.py`: visibility → permission → risk → approval → execute → audit |
| HITL approval on the existing pause/resume | IMPLEMENTED | `approval_request`, `services/approvals.py`, `agents/nodes/approval_gate.py` |
| Safe resume (re-authorization, approval validation, idempotency) | IMPLEMENTED | `ToolRegistry.execute_approved`, `ApprovalService.validate_for_execution` |
| Audit trail | IMPLEMENTED (needs the database) | `security/audit.py`, tenant-scoped `audit_log` |
| Tenant isolation, application and RLS | IMPLEMENTED | migration `0004`; `Database.unit_of_work(tenant_id=...)` |
| Safe errors | IMPLEMENTED | `SecurityError` family in `core/errors.py` |
| First protected operation | IMPLEMENTED, **off by default** | `memory.remember_fact` (`MEMORY_AGENT_FACT_WRITES=approval`) |
| HTTP API / IdP / session revocation | PLANNED *(Phase 6: HTTP API implemented; OIDC adapter implemented but not verified against a real IdP; revocation still planned, §3e)* | — |

### 2. Identity architecture

* **Created by:**
  * `HmacTokenAuthenticator.authenticate(token)`: development tokens signed with `AUTH_TOKEN_SECRET`;
  * `LocalIdentity.principal(...)`: CLI flags `--tenant/--user/--role`, or the `Agent` default. Allowed outside production only.

  Roles are resolved to permissions on the server. A token can never carry permissions, and unknown roles grant nothing.
* **Propagates:**
  1. `Agent.arun/aresume/athread_status/aforget_conversation(principal=...)`
  2. `acting_as(principal)` (contextvar + log fields `tenant_id`, `user_id`)
  3. ownership check, then the storage key `<tenant>/<thread>`
  4. graph nodes (`require_principal()`), `DomainRequest.principal`, `ToolRegistry.execute*(principal=)`
  5. `AuthorizedMemory` (the scope comes from the principal)
  6. `PostgresCheckpointSaver` (RLS tenant = the principal's tenant)
* **On resume:** the principal comes from a new authentication, never from the checkpoint. Graph state keeps only `actor = {tenant_id, user_id}` and the resume must come from that actor.
* **Replaceable:** a production OIDC/JWT authenticator implements the same `Authenticator` protocol (ADR-037).

### 3. Authorization model

* One `Authorizer` (`require`, `require_tenant`, `require_owner`, `deny`), called by:
  * the agent entry point (conversations);
  * the tool registry (tools);
  * `AuthorizedMemory` (memory);
  * `ApprovalService` (approvals);
  * the audit reader.
* Permissions are resolved at authentication, so each check is an in-memory set lookup with no database query.
* Other tenants' and other users' resources are reported as "not found".
* Every denial is audited (`authz.denied` or `tool.call` with status `denied`).
* The LLM is never an authority: it only sees tools the user may run, and the registry and services check again.

### 4. Permission model

| Permission | Allows | Default roles |
|---|---|---|
| `conversation.read` / `.write` / `.delete` | read status, start/continue/resume, forget **own** conversations | member |
| `memory.read` | recall own + tenant-shared memory | member |
| `memory.write` / `memory.delete` | own private memory (facts, private notes, summaries, episodes) | member |
| `memory.shared.write` / `.delete` | tenant knowledge, shared facts, procedures | knowledge_manager |
| `tool.execute` | run read tools through the agent | member |
| `approval.request` | ask for approval of a protected operation | member |
| `approval.decide` | approve/reject other users' requests | approver |
| `audit.read` | read the tenant's audit trail | auditor |

`tenant_admin` holds all twelve permissions. The roles are provisional platform roles, not business roles. `AUTH_ROLE_CATALOG_JSON` replaces them, and future domain permissions (for example `purchase.order.create`) plug in as strings.

### 5. Memory security changes

* `AuthorizedMemory` derives the `MemoryScope` from the principal; no caller can name another tenant or user.
* Every write and delete is permission-checked and audited:
  * `memory.fact.create/update/delete`, `memory.document.*`, `memory.procedure.*`, `memory.summary.write`, `memory.user.forget`;
  * content appears in the audit only as a SHA-256 hash.
* Tenant-shared writes need `memory.shared.write` (open question Q15 is answered provisionally by the role catalogue).
* Automatic recall is skipped without `memory.read`, and automatic episodes without `memory.write`. Neither fails a turn.
* **The Phase 4 design is preserved:** the agent still has no unrestricted memory writes. Its only path is the `memory.remember_fact` tool, which is off by default and needs the user's approval for every write.
* `MemoryManager` itself is unchanged except that `forget_thread` / `ConversationMemory.forget` accept the checkpoint storage key.

### 6. Conversation ownership changes

* `conversation_thread (tenant_id, thread_id) → owner_user_id`, created by a race-free upsert on the first turn.
* Only the owner can continue, resume, read the status of, or forget a conversation.
* There is **no sharing and no administrator access** (OPEN Q16).
* Another user's or tenant's conversation looks exactly like a missing one (`CONVERSATION_NOT_FOUND`, `exists=false`).
* Checkpoints, thread locks and turn idempotency keys use `<tenant>/<thread>`, so the same public id in two tenants names two separate conversations.
* Forget removes the checkpoints, summary, working memory and episodes, cancels the thread's open approvals, deletes the ownership row, and is audited.

### 7. HITL approval flow

```
LLM plans a protected call ─► registry: visible? permission? input valid? approval needed?
  ─► approval_request(pending) + audit (one transaction) ─► ToolResult(pending_approval)
  ─► approval_gate: interrupt({type: approval_required, approval_id, summary, risk, expires_at})
  ─► AgentResponse(status="interrupted")
human: --approve / --reject (approver permission; self-approval only if the policy allows)
caller: --resume (refused while pending)
  ─► approval_gate re-runs: principal (fresh authentication) == turn actor
  ─► execute_approved: approval matches tenant + requester + thread + turn + operation + argument hash,
     is approved, unexpired, unused ─► permission re-checked with current roles
     ─► idempotency claim (approval.execute) ─► execute ─► executed + operation.executed audit
```

* Lifecycle: `pending → approved → executed`, `pending → rejected | expired | cancelled`, `approved → expired | cancelled`.
* Rejected, expired, stale, cancelled or invalid approvals return an error tool result, are audited as `operation.rejected`, and never execute.
* Policy is configurable (`APPROVAL_TTL_SECONDS`, `APPROVAL_MIN_RISK`, per-tool `ApprovalPolicy`), with no business thresholds.

### 8. Audit implementation

* `security/audit.py` builds every event from the principal, including tenant, actor, `request_id` and `thread_id` correlation and entity references (approval or memory ids).
* Actors:
  * `user` for the user's own actions;
  * `agent` + user id for the agent acting for the user (ADR-013);
  * `system` for maintenance.
* New actions:
  * sign-in and authorization: `auth.failed`, `authz.denied`;
  * memory: `memory.*`;
  * conversations: `conversation.forget`;
  * approvals: `approval.requested/decided/cancelled/expired`, `operation.executed/rejected`;
  * `tool.call` now carries tenant and actor.
* Never stored: tokens, secrets, tool arguments or memory content. Failed sign-ins store a reason code and no tenant.
* Approval and memory-write events are fail-closed (approval events are written atomically with the state change). Denials are best-effort.
* Reads need `audit.read` and are tenant-scoped (CLI `--audit`). The runtime role can no longer UPDATE or DELETE `audit_log`.

### 9. Database / migration changes (migration `0004`)

| Object | Purpose |
|---|---|
| `conversation_thread` | Ownership. PK (`tenant_id`, `thread_id`), `owner_user_id`, timestamps, index (tenant, owner, updated_at), length checks |
| `approval_request` | Approval lifecycle. UUID PK, tenant, requester, `thread_id` + `request_id`, operation + SHA-256, permissions, policy, risk, summary, reason, `resource_refs`, status, decision, expiry, execution. 9 checks, 4 indexes (one partial on pending expiry) |
| RLS | `conversation_thread`, `approval_request` (tenant; maintenance flag `app.maintenance`); `audit_log` (tenant read, tenant-or-null insert); `agent_checkpoints`, `_blobs`, `_writes` (key prefix = tenant) |
| Privileges | UPDATE/DELETE/TRUNCATE on `audit_log` revoked from non-owner roles (downgrade re-grants UPDATE/DELETE) |
| Code | `Database.unit_of_work(tenant_id=...)` binds the RLS tenant per transaction; free for read transactions (folded into the read-only statement). Memory and checkpoints use it |

Verified:
* `alembic upgrade head` / `downgrade -1` / `upgrade head` on the dev database `agentic` (0003 ↔ 0004); `--db-health` is `ok` at `0004`;
* integration test `0004` down/up checks tables, RLS flags and privileges;
* `base ↔ head` (existing test);
* models match migrations (drift test).

### 10. Files created / modified

**Created**

| Path | Purpose |
|---|---|
| `src/agentic_platform/security/__init__.py` | Public API |
| `src/agentic_platform/security/identity.py` | `Principal`, `IdentityResolver` |
| `src/agentic_platform/security/authentication.py` | `Authenticator`, `HmacTokenAuthenticator`, `LocalIdentity`, `build_token_authenticator` |
| `src/agentic_platform/security/context.py` | `acting_as`, `current_principal`, `require_principal` |
| `src/agentic_platform/security/permissions.py` | `Permission`, `RoleCatalog`, default roles |
| `src/agentic_platform/security/authorization.py` | `Authorizer` |
| `src/agentic_platform/security/policy.py` | `ApprovalPolicy`, risk levels, `approval_required` |
| `src/agentic_platform/security/audit.py` | `SecurityAudit`, `AuditAction` |
| `src/agentic_platform/core/threads.py` | Thread-id rules, `storage_thread_id` (moved from `agents/threads.py`, which re-exports) |
| `src/agentic_platform/db/tenancy.py` | RLS session settings |
| `src/agentic_platform/db/conversations.py` | `ConversationThread`, `ConversationRepository` |
| `src/agentic_platform/db/approvals.py` | `ApprovalRequestRow`, `ApprovalRepository` |
| `src/agentic_platform/db/migrations/versions/20261001_0004_identity_approvals.py` | Migration `0004` |
| `src/agentic_platform/services/conversations.py` | `ConversationService` + PostgreSQL / in-memory stores |
| `src/agentic_platform/services/approvals.py` | `ApprovalService` + PostgreSQL / in-memory stores |
| `src/agentic_platform/memory/authorized.py` | `AuthorizedMemory` |
| `src/agentic_platform/tools/memory.py` | `memory.remember_fact` (approval-gated, opt-in) |
| `src/agentic_platform/agents/nodes/approval_gate.py` | HITL node |
| `tests/test_security_unit.py`, `tests/integration/test_security_postgres.py` | Phase 5 tests |

**Modified**

| Path | Change |
|---|---|
| `core/errors.py` | `SecurityError` family (unauthenticated, invalid identity, missing tenant, permission denied, not found / cross-tenant, conversation not found, invalid resume context, approval errors); `ToolPermissionDeniedError` |
| `core/config.py` | `AuthSettings` (`AUTH_*`), `ApprovalSettings` (`APPROVAL_*`), `MEMORY_AGENT_FACT_WRITES`, `AppSettings.local_identity_allowed` |
| `agents/agent.py` | `principal=` everywhere; ownership before the lock; storage keys; actor + approval checks on resume; `aforget_conversation`; security errors propagate |
| `agents/threads.py`, `state.py` (v3: `actor`, `pending_action`), `schemas.py`, `graph.py`, `edges.py`, `nodes/__init__.py`, `nodes/process_request.py`, `nodes/understand_request.py`, `domains/base.py`, `domains/general.py` | Principal in domain requests, tool listing filtered by permission, `approval_gate` routing |
| `agents/dependencies.py` | `SecurityServices`, `build_security`, auditor injection, `authorized_memory()`, opt-in memory tool |
| `agents/checkpointer.py` | Every transaction bound to the request's tenant (RLS) |
| `tools/base.py`, `tools/registry.py` | Permission/risk/approval metadata; `pending_approval`; `PendingAction`; `execute_call`, `execute_approved`; tenant/actor in audit |
| `services/audit.py`, `db/audit.py` | Tenant-scoped reads (`for_thread(..., tenant_id=)`, `for_tenant`), RLS binding on write |
| `db/unit_of_work.py`, `db/engine.py` | `tenant_id=` binding |
| `db/memory.py`, `db/checkpoints.py`, `db/models.py` | Shared tenancy helper; docs; new tables registered |
| `memory/manager.py`, `memory/conversation.py`, `memory/adapters/postgres.py` | Checkpoint storage key on forget; tenant binding through the unit of work |
| `__main__.py` | Single event loop; `--token`, `--role`, `--issue-token`, `--approvals`, `--approve`, `--reject`, `--note`, `--forget`, `--audit`; `--memory-purge` also expires approvals |
| `.env.example` | `AUTH_*`, `APPROVAL_*`, `MEMORY_AGENT_FACT_WRITES` |
| Existing tests (`conftest.py`, `test_tools.py`, `test_persistence_unit.py`, `test_graph.py`, `test_resume_unit.py`, `test_memory_unit.py`, `test_db_unit.py`, `integration/test_redis.py`, `test_memory_postgres.py`, `test_persistence_postgres.py`) | Adapted to the new APIs (`principal=`, tenant-scoped audit reads, namespaced lock keys, head `0004`, `approval_gate` node). No assertion was removed; identity assertions were added |
| `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `PROJECT_STATUS.md` | § A.9; ADR-037 to ADR-042 and status updates; this section |

**Behaviour changes:**
* `Agent.arun/aresume/run/resume` take `principal=` instead of `scope=`. Without a principal the local development identity is used, and that is refused in production.
* Resuming an unknown or not-owned thread raises `ConversationNotFoundError` (it used to raise `NothingToResumeError`).
* Conversations stored before Phase 5 are unreachable (their keys have no tenant prefix).
* `ToolRegistry.execute` requires `principal=`.
* `AuditService.for_thread` requires `tenant_id=`.
* CLI `--ingest` now needs `memory.shared.write` (for example `--role member --role knowledge_manager`).
* With a database, migration `0004` is required.

### 11. Tests run and exact results (2026-10-01)

| Run | Result |
|---|---|
| Baseline before Phase 5 (PostgreSQL 16 + pgvector 0.8.6, Redis 7.4) | 265 passed (56 s wall) |
| Unit only, no servers | **247 passed, 70 skipped** |
| Full suite with PostgreSQL + Redis | **317 passed, 0 skipped** (pytest 35 s and 66 s on two runs; host variance, see § 3c's earlier 13.6-minute run) |
| Integration suite rerun on the same database | 70 passed |
| New Phase 5 tests | 43 unit (`test_security_unit.py`) + 9 integration (`test_security_postgres.py`) |

**What the new tests cover:**

* **Identity:**
  * token round trip; permissions resolved on the server; forged permissions and unknown roles ignored;
  * missing, malformed, tampered, expired, wrong-issuer and over-long tokens; missing tenant; invalid ids;
  * local identity refused in production; anonymous agent calls refused when local identity is off;
  * blank settings mean "unset".
* **Authorization:**
  * allow, deny and audit; role catalogue configuration and validation;
  * cross-tenant access looks like "not found";
  * tool permission enforced, with the denial audited and the tool hidden from the model;
  * an LLM-planned call without permission is refused;
  * an LLM adding `tenant_id` / `shared` to a protected call is rejected.
* **Tenant isolation:**
  * conversations (same id in two tenants, no history leak), other users refused;
  * RLS with deliberately unfiltered SQL on `conversation_thread`, `approval_request`, `audit_log` and all checkpoint tables (other tenant invisible, no tenant = no rows, cross-tenant insert rejected);
  * the checkpoint saver as another tenant sees nothing;
  * approvals and audit reads of another tenant.
* **Memory:**
  * private create, update and delete, all audited without content;
  * shared writes and deletes need shared permissions;
  * recall skipped without `memory.read`; `forget_me`; tenant scope derived from the principal.
* **HITL:**
  * pause, pending refusal, approve → resume → execute once (audited, correlated);
  * reject, expired (in-memory and database clock), approved-but-expired;
  * authorization re-checked after resume;
  * resume by another actor refused; stale arguments refused;
  * wrong tenant, user, workflow or operation refused;
  * self-approval rules; cancel; forget cancels open approvals;
  * the memory tool is off by default;
  * concurrent decisions have exactly one winner;
  * flow across separate processes on PostgreSQL with idempotent replay.
* **Migrations:** `0004` down/up (tables, RLS flags, audit privileges); a tenant-bound read-only unit still rejects writes.
* **Regression:** all Phase 1-4 tests pass (adapted only to the new APIs).

**Live CLI against the dev servers** (no LLM call; there are still no Anthropic credentials):
* `--ingest` as `member` is refused (and audited), and works with `knowledge_manager`;
* `--audit` is refused without `auditor`;
* `--issue-token` then `--token ... --approvals/--audit` works;
* a forged token is refused (`auth.failed` audited with no tenant and no token material);
* status of an unknown thread is `exists: false`;
* `APP_ENV=production` without a token is refused;
* `--memory-purge` also expires approvals.

The demo memory rows were removed afterwards. The audit rows stay, because the log is append-only.

### 12. Lint and type-check

* `ruff check src tests`: **clean**.
* `mypy --strict`: **clean, 98 source files**.

### 13. Known issues

1. **No live LLM run yet** (Known issue 1). The approval flow is verified with a scripted LLM; whether Claude plans `memory.remember_fact` sensibly is untested.
2. **Per-turn overhead:** 29 instead of 18 SQL statements; about +25-35 ms median per turn on local Docker for Windows (about 2.3 ms per round trip). It comes mostly from RLS binding in LangGraph's per-node checkpoint write transactions; reads bind for free. It is expected to be smaller on a low-latency production network, but that is not measured.
3. **Conversations from before Phase 5 are unreachable** (keys without a tenant prefix). They were development data.
4. **A paused approval blocks its thread** until it is resumed. An expired approval resumes into an `APPROVAL_EXPIRED` result. There is still no "abandon pending turn" operation.
5. **At-least-once window:** a crash after an approved operation ran but before its idempotency record completed re-runs it after the lease expires (same as ADR-029). `memory.remember_fact` is an idempotent upsert, but future write tools must be designed for this.
6. **Integration tests share `agentic_test`**: approval and audit rows accumulate; the audit rows cannot be deleted.

### 14. Security limitations

1. **No identity provider:**
   * development HMAC tokens have no rotation, revocation or refresh, and the secret is an environment variable;
   * the local identity lets anyone with shell access claim any identity outside production, by design and for development only.
2. *(Phase 6: the HTTP layer authenticates every request with the configured `Authenticator`; the local identity is never accepted over HTTP, §3e.)* **No HTTP layer:** `Agent` trusts its caller to pass a principal that came from an `Authenticator`. The trust boundary is wherever authentication happens.
3. **Permissions are fixed per request:** a role change takes effect on the next authentication. Approvers' permissions are checked when they decide, not again at execution (the requester's permissions are re-checked).
4. **RLS protects against application bugs, not a compromised application** (ADR-034). A superuser can still drop the audit triggers; there is no hash chaining or off-host audit shipping.
5. **Audit needs the database.** Without `DATABASE_URL`, events are only logged. Audit retention is undefined (Q11).
6. **Approval summaries are stored** (at most 1000 characters; for `memory.remember_fact` this includes the fact text, possibly personal data). Their retention is undefined (Q11).
7. **No sharing and no administrator paths:** an administrator cannot delete or erase another user's conversations or memory yet (Q16).
8. **No "edit before approve"**; approve and reject only.

### 15. Architecture decisions

* **Added:** ADR-037 (principal and authentication), ADR-038 (permissions and authorizer), ADR-039 (conversation ownership), ADR-040 (HITL approvals), ADR-041 (tenant RLS and its measured cost), ADR-042 (security audit).
* **Status changed:**
  * ADR-010, ADR-011 and ADR-013 → Accepted (implemented);
  * ADR-012 → partially implemented;
  * ADR-009 → tenant-namespaced keys;
  * ADR-016 → applied to conversations, approvals, audit and checkpoints.

### 16. Deliberately NOT implemented

* Purchase / Sales / Inventory / Accounting / GST agents, workflows or tables.
* Business approval thresholds or business roles.
* Notification (WhatsApp/SMS) flows; unrestricted SQL tools.
* An HTTP API or UI; integration with a real identity provider.
* Conversation sharing or administrator access.
* "Edit" approvals; automatic extraction of facts from conversations.
* A full Tool System phase: only the authorization boundary plus one opt-in protected tool.

### 17. Recommended next phase

Phase 6: HTTP API and production identity integration (details and alternatives in §10). *(Done: §3e.)*

## 3e. Phase 6: HTTP API, identity-provider boundary and scheduled maintenance

**Scope** (stakeholder definition, 2026-10-02): an HTTP API around the existing agent and services, an authentication boundary that a real identity provider can plug into, principal propagation from the HTTP request, API authorization, conversation/agent/approval/memory endpoints, health and readiness, error handling, request correlation, and a scheduled-maintenance framework for the expiry rules that already exist. Platform infrastructure only: no business agents. Details: ARCHITECTURE.md § A.10, ADR-043 to ADR-046.

Legend: **IMPLEMENTED** (code + tests) · **PARTIAL** (works, with a stated gap) · **PLANNED** · **OPEN** (needs a decision).

### 1. Summary

| Requirement | Status | Where |
|---|---|---|
| HTTP API foundation (FastAPI, ADR-014 confirmed in code) | IMPLEMENTED | `api/` (`create_app`, `python -m agentic_platform.api`) |
| Authentication integration boundary | IMPLEMENTED | `api/dependencies.py` `get_principal`; `security/identity_provider.py` |
| Real identity-provider integration | **PARTIAL**: OIDC/JWT adapter implemented and tested with locally generated keys and a stubbed JWKS endpoint; **not verified against a real identity provider** (none chosen, Q6) | `security/oidc.py` `OidcJwtProvider` |
| HTTP request → principal → agent/services | IMPLEMENTED | bearer token → `Authenticator` → `Principal` → `Agent(principal=)` / services |
| API authorization (centralized, Phase 5 `Authorizer`) | IMPLEMENTED | no role checks in handlers; services and the agent decide |
| Conversation / agent API | IMPLEMENTED | `api/routes/agent.py`, `api/routes/conversations.py` |
| Approval API (decide, cancel, resume) | IMPLEMENTED | `api/routes/approvals.py`, `application/approvals.py` |
| Memory API (operations `AuthorizedMemory` already supports) | IMPLEMENTED | `api/routes/memory.py` |
| Audit read API | IMPLEMENTED | `GET /api/v1/audit`, `application/audit.py` |
| Health / readiness | IMPLEMENTED | `GET /health`, `GET /ready`, `services/readiness.py` |
| API error handling (one envelope, status mapping) | IMPLEMENTED | `api/errors.py` |
| Request / correlation ids | IMPLEMENTED | `api/middleware.py` |
| Scheduled maintenance framework | IMPLEMENTED | `maintenance/` (runner, jobs, scheduler, worker) |
| Expired memory maintenance | IMPLEMENTED | job `memory.expired` |
| Expired approval maintenance | IMPLEMENTED | job `approvals.expired` |
| Expired idempotency records | IMPLEMENTED | job `idempotency.expired` (needs a database) |
| Maintenance observability | IMPLEMENTED | `JobRunReport`, `maintenance.job.*` logs, `maintenance.run` audit rows |
| Token revocation, refresh, introspection | PLANNED (not required by the chosen design yet; Q19) | — |
| Rate limiting, CORS | PLANNED (frontend and deployment undecided, Q5/Q20) | — |
| Retention for checkpoints, audit log, decided approvals | OPEN (Q11): no job, by design | — |

**Two security fixes found while building the API** (both in Phase 5 code, both now covered by tests):

1. **Private approvals were visible to every member.** `memory.remember_fact` names `memory.write` as its approver permission so users can approve their own facts, but every member holds `memory.write`. Any other member of the tenant could therefore list, read (the summary contains the fact text) and decide another user's request. Fixed with a persisted `requester_only` policy flag (migration `0005`): such a request is visible to, and decidable by, its requester only. Tenant admins and approvers do not see it either.
2. **An executed approval could run again once its idempotency record expired.** `ToolRegistry` relied on the idempotency record alone; after its 24 h TTL (or the new purge job) a replayed `execute_approved` on an `executed` approval claimed a fresh key and executed again. Now the approval state alone forbids it (`APPROVAL_ALREADY_USED`). A PostgreSQL test fails without the fix and passes with it.

### How to run (additions)

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"            # adds fastapi, uvicorn, httpx, PyJWT[crypto]
.venv\Scripts\alembic upgrade head                          # 0005
$env:AUTH_TOKEN_SECRET = "<at least 32 random characters>"  # development tokens (never in production)
.venv\Scripts\python -m agentic_platform.api                # http://127.0.0.1:8000/docs
$token = .venv\Scripts\python -m agentic_platform --issue-token --tenant acme --user asha --role member
curl.exe -H "Authorization: Bearer $token" http://127.0.0.1:8000/api/v1/me
.venv\Scripts\python -m agentic_platform.maintenance         # every maintenance job once (cron)
.venv\Scripts\python -m agentic_platform.maintenance --loop  # local scheduler
.venv\Scripts\python -m agentic_platform.api --openapi > openapi.json
# Production identity: AUTH_PROVIDER=oidc, AUTH_OIDC_ISSUER, AUTH_OIDC_AUDIENCE (+ claim names, role map)
```

### 2. HTTP API architecture

```
HTTP request
  ↓ RequestContextMiddleware     server-generated request id, log context, 1 MiB body limit,
  ↓                              last-resort 500 envelope (pure ASGI)
  ↓ FastAPI router               /api/v1/* (versioned) · /health · /ready
  ↓ get_principal                Authorization: Bearer → Authenticator → Principal
  ↓ handler                      request/response mapping only; no business rules, no SQL
  ↓ Agent / application services ownership, permissions (Authorizer), approvals, memory
  ↓ LangGraph → tools → services → repositories
  ↓ PostgreSQL (RLS) · Redis
```

* Handlers call the `Agent` (turns, resume, status, forget), `ConversationService` (list, existence), `ApprovalWorkflow` (Phase 5 `ApprovalService` + `Agent.aresume`), `AuthorizedMemory` and `AuditReader`. None of them touches a repository or a session.
* `create_app()` builds one `Agent`, one authenticator and the services at startup. A misconfiguration (for example no usable authentication) stops the process at startup instead of failing requests.
* One process serves one event loop. Scale out with more processes; Redis is then required for the cross-process thread lock (ADR-031).

### 3. Authentication architecture

* **Only** the `Authorization: Bearer <token>` header is read: never a query parameter, cookie or body field.
* **The local development identity is never accepted over HTTP.** No header → 401.
* Accepted tokens depend on `AUTH_PROVIDER`:
  * `dev_token`: the Phase 5 HMAC development tokens. Default when `AUTH_TOKEN_SECRET` is set outside production. **Refused in production at startup.**
  * `oidc`: JWT access tokens from an OIDC/OAuth2 provider (§4).
  * nothing configured → the API refuses to start.
* Token checks:
  * development tokens: signature (HMAC-SHA256, constant-time compare), version, issuer, optional audience (`AUTH_TOKEN_AUDIENCE`, new), issue/expiry time, maximum lifetime;
  * OIDC: signature (JWKS by `kid`), allowed asymmetric algorithm, `exp`, `nbf`, `iat`, `iss`, `aud`, user and tenant claims.
* Malformed, forged, expired, wrong-issuer and wrong-audience tokens → 401 `INVALID_CREDENTIALS` and an `auth.failed` audit row with a reason code. Never the token.
* A request with no credential at all → 401 `AUTHENTICATION_REQUIRED` **without** an audit row, so unauthenticated probes cannot fill the append-only audit log.
* An unreachable identity provider → 503 `IDENTITY_PROVIDER_UNAVAILABLE` (retryable), not "invalid credentials".
* Tokens, the signing secret and `Authorization` headers are never logged (checked by tests and on the live server log).
* **Token revocation is not implemented** (§13): a token stays valid until it expires. Role changes take effect on the next token.

### 4. Identity-provider abstraction

```
Bearer token ─► IdentityProvider.verify() ─► ExternalIdentity ─► IdentityMapper ─► Principal
               (OidcJwtProvider; others     (subject, tenant,    (RoleMapping:      (roles →
                implement the same           external roles,      external → platform  permissions
                protocol)                    session, expiry)     roles)               on the server)
```

* `IdentityProvider` protocol (`verify(token) -> ExternalIdentity`), `ExternalIdentity`, `RoleMapping`, `IdentityMapper` and `ProviderAuthenticator` (the Phase 5 `Authenticator` protocol) live in `security/identity_provider.py`. Nothing outside `security/oidc.py` knows a token format.
* **External claims never become permissions.**
  * With `AUTH_OIDC_ROLE_MAP_JSON`, only mapped external roles or groups grant platform roles.
  * Without it, external names that match a catalogue role are used.
  * Either way the role catalogue resolves permissions on the server; a `permissions` claim is ignored; unknown roles grant nothing.
* `OidcJwtProvider`:
  * configurable issuer, audience, algorithms and user/tenant/roles claim names;
  * JWKS from `AUTH_OIDC_JWKS_URL` or OIDC discovery; the discovery document must name the configured issuer;
  * key cache with TTL; refresh on an unknown `kid` (key rotation), at most every 30 s;
  * symmetric keys in the JWKS are ignored; https is required in production.
* PyJWT + `cryptography` are an optional extra (`pip install -e ".[oidc]"`, also in `dev`). `cryptography` loads on this machine (Application Control did not block it), but it is a compiled wheel, so it is optional.
* **What remains untested:**
  * a real provider (Keycloak, Entra ID, Okta, Auth0, ...);
  * real JWKS rotation timing, network failures other than HTTP errors, clock skew against a real provider;
  * provider-specific claim layouts (nested role claims such as Keycloak's `realm_access.roles` are not supported: only top-level claims);
  * opaque (non-JWT) access tokens and token introspection.

### 5. Principal propagation

`Authorization` header → `get_principal` (once per request; FastAPI caches it) → `Principal` → passed explicitly to `Agent.arun/aresume/athread_status/aforget_conversation(principal=)`, `ApprovalWorkflow`, `AuthorizedMemory`, `ConversationService`, `AuditReader`. From there the Phase 5 path is unchanged: `acting_as` → ownership → storage key `<tenant>/<thread>` → graph nodes → tool registry → `AuthorizedMemory` → checkpoint saver RLS binding.

* Request bodies reject unknown fields (`extra="forbid"`), so `tenant_id`, `user_id`, `roles` or `permissions` in a body are a 422, never an override (tested for all four).
* Path ids (`thread_id`, `approval_id`, memory names) are untrusted: validated by pattern, then resolved inside the principal's tenant and checked for ownership/visibility.

### 6. API authorization

Every protected endpoint relies on the Phase 5 `Authorizer`, through the service it calls; handlers contain no role or permission logic.

| Endpoint group | Check (where) |
|---|---|
| turns, start, continue, resume | `conversation.write` + ownership (`ConversationService`, inside `Agent`) |
| conversation status, history, list | `conversation.read` + ownership |
| forget | `conversation.delete` + ownership |
| approvals | visibility (requester, or approver permission and not requester-only), approver permission, self-approval policy (`ApprovalService`) |
| memory | `memory.read/write/delete`, `memory.shared.write/delete` (`AuthorizedMemory`) |
| audit | `audit.read`, own tenant only (`AuditReader`) |

### 7. Implemented endpoints

| Method and path | Purpose | Success |
|---|---|---|
| `GET /health` | liveness (no dependencies, no auth) | 200 |
| `GET /ready` | readiness: database, Redis, authentication (no auth, no secrets) | 200 / 503 |
| `GET /api/v1/me` | the caller's principal (user, tenant, roles, permissions, auth method) | 200 |
| `POST /api/v1/agent/turn` | message to a new or existing conversation (`thread_id`, `idempotency_key` optional) | 200 / 202 |
| `GET /api/v1/conversations` | my conversations | 200 |
| `POST /api/v1/conversations` | start a conversation; the server picks the id | 201 / 202 |
| `GET /api/v1/conversations/{id}` | metadata: pending, awaiting approval or input, message count | 200 |
| `POST /api/v1/conversations/{id}/messages` | continue (must exist and be mine) | 200 / 202 |
| `POST /api/v1/conversations/{id}/resume` | resume a paused/crashed turn (optional `answer`) | 200 / 202 |
| `GET /api/v1/conversations/{id}/history` | messages (from the checkpointer, via memory) | 200 |
| `DELETE /api/v1/conversations/{id}` | forget (state, summary, working memory, episodes; cancels open approvals) | 200 |
| `GET /api/v1/approvals` | open approvals I requested or may decide | 200 |
| `GET /api/v1/approvals/{id}` | one approval (requester sees `thread_id`; `can_decide`) | 200 |
| `POST /api/v1/approvals/{id}/approve` / `reject` | decide (`note`, `resume`) | 200 |
| `POST /api/v1/approvals/{id}/cancel` | requester withdraws | 200 |
| `GET /api/v1/memory/facts` | my and the tenant's shared facts | 200 |
| `GET/PUT/DELETE /api/v1/memory/facts/{ns}/{key}` | read, create/update (`shared`, `ttl_seconds`), delete a fact | 200 |
| `PUT/DELETE /api/v1/memory/documents/{ref}` | tenant knowledge (default) or a private note | 200 |
| `POST /api/v1/memory/knowledge/search` | semantic search (context, not business truth) | 200 |
| `DELETE /api/v1/memory/me` | delete my private durable memory | 200 |
| `GET /api/v1/audit` | my tenant's audit trail (`thread_id`, `limit`) | 200 |

OpenAPI: `/openapi.json` and `/docs` (off in production unless `API_DOCS_ENABLED=true`; `python -m agentic_platform.api --openapi` prints the schema). The schema documents the bearer scheme, request/response models, the 202 approval-required response and the error envelope for 401/403/404/409/410/422/500/503. Live server: 20 paths.

**Turn responses** (`TurnResponse`): `request_id`, `thread_id`, `status`, `message`, `approval_required` (approval id, operation, summary, risk, expiry), `input_request`, `tool_calls` (name, status, error code, approval id; no tool output or arguments), `error`, `replayed`, `duration_ms`. No node names, checkpoints, intents, hashes or policies. HTTP status: 200/201 finished, 202 paused, 409 busy/idempotency conflict, 502 language model error, 503 retryable failure, 500 otherwise.

### 8. Approval API flow

```
POST /agent/turn ─► 202 {approval_required: {approval_id, operation, summary, risk, expires_at}}
POST /approvals/{id}/approve|reject  (approver: ApprovalService.decide, unchanged)
  ├─ decider is the requester and {"resume": true} ─► Agent.aresume ─► approval_gate:
  │      actor check, approval re-validation, requester permission re-check,
  │      execute at most once, audit ─► next_action "resumed" + turn
  └─ otherwise ─► next_action "resume_conversation"
POST /conversations/{thread_id}/resume  (requester) ─► same Phase 5 path ─► 200
```

* Only the requester can resume (Phase 5 rule: the resuming principal must be the turn's actor). An approver's decision is recorded; the requester then resumes. No HTTP-specific approval logic exists.
* Resume before a decision → 409 `APPROVAL_PENDING`. Deciding twice → 409 `APPROVAL_ALREADY_DECIDED`. Expired → 410 `APPROVAL_EXPIRED` (a later resume yields an `APPROVAL_EXPIRED` tool result; nothing runs). Rejected → the resumed turn reports `APPROVAL_REJECTED`.
* Unknown, malformed, other-tenant, not-visible and requester-only-of-someone-else approvals → 404 `APPROVAL_NOT_FOUND`.

### 9. Conversation API flow

Start (`POST /conversations`, server id) or turn with an id (`POST /agent/turn`) → `ConversationService.open_for_turn` creates or touches the ownership row → turn. Continue requires an existing conversation of mine (one extra read). Status/history/forget/resume → `require_access` (permission + ownership). Another user's or tenant's conversation answers exactly like a missing one (same 404 code and message, tested). A paused turn blocks new messages (409 `THREAD_HAS_PENDING_TURN`).

### 10. Memory API changes

No new memory capability: the API exposes `AuthorizedMemory` operations only (facts, documents/notes, knowledge search, forget-me, conversation history). Not exposed: episodes, working memory, procedures, summaries, embeddings, raw queries. New in the memory layer: `MemoryStore.purge_expired_batch(limit)` (both adapters) and `MemoryManager.purge_expired_batch(limit)` for bounded maintenance; the existing `purge_expired()` is unchanged.

### 11. Health and readiness

* `/health`: process alive; touches no dependency; always 200 while serving.
* `/ready`: `ReadinessService` runs the existing `DatabaseHealthService` and `RedisHealthService` concurrently:
  * database ready only when `ok` (reachable and at the migration head); not configured is ready outside production, **not ready in production**;
  * Redis ready when `ok` or not configured;
  * authentication ready when an authenticator was built.
  * 503 when anything is not ready.
* The report holds status, latency and error code per check only: no URLs, hosts, versions, pool details or credentials (tested with a password in the URL).
* Live: `/ready` → `ready` (database `ok` at `0005`, Redis `ok`).

### 12. Scheduled maintenance

```
scheduler (local loop / cron / container / cloud job)
  ↓ MaintenanceRunner   per-job lock "maintenance:<job>" (busy → skipped) · batches · report
  ↓ MaintenanceJob.run_batch(limit)   one transaction, ≤ MAINTENANCE_BATCH_SIZE rows,
  ↓                                   FOR UPDATE SKIP LOCKED, oldest expiry first
  ↓ existing services (MemoryManager, ApprovalService, IdempotencyService)
```

| Job | Rule applied (existing; nothing invented) | Effect |
|---|---|---|
| `memory.expired` | `memory_item.expires_at`: episodes after `MEMORY_EPISODIC_RETENTION_DAYS` (90 d), facts only when written with a TTL | delete; counts by kind |
| `approvals.expired` | `approval_request.expires_at` (`APPROVAL_TTL_SECONDS`) | pending / approved-unused → `expired`, audited per approval; rows kept |
| `idempotency.expired` | `idempotency_record.expires_at` (24 h TTL, ADR-029) | delete (claims already ignore expired rows) |

* **Bounded:** each batch is one transaction with at most `MAINTENANCE_BATCH_SIZE` rows, and a run has at most `MAINTENANCE_MAX_BATCHES` batches; the rest waits for the next run (`complete=false`).
* **Idempotent:** batches select only rows that are still due. Tested: a repeated run changes nothing; two concurrent runners never process a row twice (one `approval.expired` event per approval).
* **Failure-tolerant:** a failed batch rolls back; earlier batches stay. The job stops for this run (`partial` / `failed`) and the other jobs still run.
* **Observable:** `JobRunReport` (job, run id, start, end, duration, batches, processed, affected, errors, error codes, per-kind counts, complete) is logged (`maintenance.job.started/finished`) and written to `audit_log` as `maintenance.run` (system actor, no tenant, `request_id` = run id). Counts and codes only, never memory content.
* **Where it runs:**
  * `python -m agentic_platform.maintenance` runs every job once (cron, container or cloud job; exit code 1 on failure);
  * `--loop` is the local scheduler (`MAINTENANCE_INTERVAL_SECONDS`);
  * `--job NAME` runs one job; `--list` shows each job's rule;
  * CLI `--memory-purge` now runs all jobs once and prints the reports;
  * `API_RUN_MAINTENANCE=true` runs the scheduler inside the API process (single-process development only; off by default, so request handling is isolated from maintenance).
* **Not scheduled, by design (no documented retention, Q11):**
  * LangGraph checkpoints;
  * the audit log;
  * decided/expired approval rows;
  * conversation ownership rows.
  Working memory needs no job (Redis TTL).

### 13. Database / migration changes (migration `0005`)

| Object | Purpose |
|---|---|
| `approval_request.requester_only boolean NOT NULL DEFAULT false` | private requests: only the requester may see and decide (§1 fix 1) |
| `ck_approval_request_requester_only_self` | requester-only implies self-approval allowed |

No new tables, no RLS or privilege changes, audit immutability untouched. Verified:
* `alembic upgrade head` / `downgrade -1` / `upgrade head` on the dev database `agentic` (0004 ↔ 0005); `--db-health` is `ok` at `0005`;
* integration test 0005 down/up (column and constraint; RLS flag unchanged);
* existing `base ↔ head` and model/migration drift tests pass.

Noticed, not changed: migration `0004`'s check constraints have a doubled prefix (`ck_approval_request_ck_approval_request_status`, ...) because explicit names went through the naming convention. Cosmetic; `0005` uses `op.f()`.

### 14. Configuration and environment variables

New groups (all optional; table in §7): `AUTH_PROVIDER`, `AUTH_TOKEN_AUDIENCE`, `AUTH_OIDC_*` (issuer, audience, JWKS URL, algorithms, claim names, role map, cache, timeout, leeway), `API_*` (host, port, docs, body limit, in-process maintenance), `MAINTENANCE_*` (batch size, max batches, interval, lock TTL). No URL, client id or secret is hard-coded; there are no production defaults for any of them.

### 15. Files created / modified

**Created**

| Path | Purpose |
|---|---|
| `src/agentic_platform/api/__init__.py`, `app.py`, `__main__.py` | App factory, uvicorn entry point, OpenAPI export |
| `src/agentic_platform/api/runtime.py` | `ApiRuntime` (agent, authenticator, services, optional scheduler) |
| `src/agentic_platform/api/dependencies.py` | Bearer parsing, `get_principal` |
| `src/agentic_platform/api/middleware.py` | Request id, log context, body limit, last-resort 500 |
| `src/agentic_platform/api/errors.py` | Error envelope, status mapping, handlers |
| `src/agentic_platform/api/schemas.py` | Public request/response models |
| `src/agentic_platform/api/routes/{__init__,agent,conversations,approvals,memory,system}.py` | Endpoints |
| `src/agentic_platform/application/{__init__,approvals,audit}.py` | `ApprovalWorkflow`, `AuditReader` |
| `src/agentic_platform/security/identity_provider.py` | IdP boundary, role mapping, provider selection |
| `src/agentic_platform/security/oidc.py` | `OidcJwtProvider`, `JwksCache` |
| `src/agentic_platform/maintenance/{__init__,jobs,runner,scheduler,__main__}.py` | Maintenance framework and worker |
| `src/agentic_platform/services/readiness.py` | `ReadinessService` |
| `src/agentic_platform/db/migrations/versions/20261002_0005_approval_requester_only.py` | Migration `0005` |
| `tests/api_helpers.py`, `tests/test_api_unit.py`, `tests/test_identity_provider_unit.py`, `tests/test_maintenance_unit.py`, `tests/integration/test_api_postgres.py` | Phase 6 tests |

**Modified**

| Path | Change |
|---|---|
| `core/config.py` | `AuthSettings` (provider, audience, OIDC), `ApiSettings`, `MaintenanceSettings`, `AppSettings.docs_enabled` |
| `core/errors.py` | `FeatureUnavailableError`, `IdentityProviderUnavailableError` |
| `security/authentication.py` | Optional token audience; docs |
| `security/identity.py` | `auth_method="oidc"` |
| `security/__init__.py`, `security/audit.py` | Exports; `maintenance.run` action |
| `security/policy.py`, `tools/memory.py` | `ApprovalPolicy.requester_only`; `memory.remember_fact` is requester-only |
| `services/approvals.py`, `db/approvals.py` | `requester_only` visibility rule; bounded `expire_due(limit)` |
| `tools/registry.py` | An `executed` approval never runs again (§1 fix 2) |
| `memory/ports.py`, `memory/manager.py`, `memory/adapters/{postgres,in_memory}.py`, `db/memory.py` | `purge_expired_batch(limit)` |
| `services/idempotency.py`, `db/idempotency.py` | Bounded `purge_expired(limit)` |
| `__main__.py` | `--memory-purge` runs the maintenance runner; `--audit` uses `AuditReader` |
| `pyproject.toml` | `fastapi`, `uvicorn`, `httpx`; extra `oidc` (PyJWT[crypto]); scripts `agentic-platform-api`, `agentic-platform-maintenance` |
| `.env.example` | `AUTH_PROVIDER`, `AUTH_TOKEN_AUDIENCE`, `AUTH_OIDC_*`, `API_*`, `MAINTENANCE_*` |
| `tests/conftest.py` | Isolates `AUTH_*`, `API_*`, `MAINTENANCE_*` |
| `tests/test_db_unit.py` | Head `0005`; offline SQL includes `0005` (no assertion removed) |
| `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `PROJECT_STATUS.md` | § A.10; ADR-043 to ADR-046 and status updates; this section |

**Behaviour changes:**
* `memory.remember_fact` approvals are visible to and decidable by the requester only (other members and approvers get "not found").
* An `executed` approval returns `APPROVAL_ALREADY_USED` even when its idempotency record is gone.
* CLI `--memory-purge` prints one JSON report per job and also purges expired idempotency records (with a database).
* With a database, migration `0005` is required.

### 16. Tests run and exact results (2026-10-02)

PostgreSQL 16.15 + pgvector, Redis 7.4.11 (Docker), Python 3.11.9, FastAPI 0.142.2, Starlette 1.7.0, uvicorn 0.54.0, PyJWT 2.15.1, cryptography 50.0.2.

| Run | Result |
|---|---|
| Baseline before Phase 6 (unit only) | 247 passed, 70 skipped |
| Unit only, no servers | **318 passed, 78 skipped** |
| Full suite with PostgreSQL + Redis | **396 passed, 0 skipped** (59 s) |
| Integration suite rerun on the same database | **78 passed** |
| New Phase 6 tests | 79: 37 `test_api_unit.py`, 24 `test_identity_provider_unit.py`, 10 `test_maintenance_unit.py`, 8 `integration/test_api_postgres.py` |
| Regression | All Phase 1-5 tests pass; the only changed assertions are the migration head (`0004` → `0005`), with `0005` checks added |

**What the new tests cover:**
* **Authentication:**
  * valid, missing, malformed (4 header forms), forged (tampered, other key), expired and wrong-issuer development tokens;
  * OIDC: valid RS256/ES256, expired, not-yet-valid, wrong audience, wrong issuer, missing `exp`/`aud`/`sub`/tenant;
  * OIDC: `alg=none`, HS256-with-the-public-key confusion, forged signature with a known `kid`, unknown `kid`, key rotation and refresh rate limit;
  * OIDC: provider outage (503, not audited as a failed sign-in), lying discovery document, https in production, configuration rules;
  * development tokens refused in production; audience check.
* **Principal:**
  * correct user and tenant; roles mapped; permissions resolved on the server;
  * `permissions` claims and unknown roles ignored;
  * `tenant_id` / `user_id` / `roles` / `permissions` in a body → 422.
* **HTTP:**
  * status codes and one error envelope (with `request_id`) for 401/403/404/409/410/413/422/500/502/503;
  * no input echo in validation errors; no stack trace or internal detail in a 500;
  * OpenAPI content; docs off in production.
* **Agent:**
  * completed (200), interrupted (202), failed (503 with `Retry-After`, 500);
  * one request id from the HTTP header through the turn, tool call and audit rows (the client's `X-Request-ID` is not reused);
  * idempotent replay over HTTP; key reuse → 409.
* **Conversations:**
  * owner lifecycle (start, continue, status, history, list, forget);
  * another user and another tenant get identical 404s on every conversation route (status, history, continue, resume, forget) and an empty list;
  * permission from roles; continue of a missing conversation; pending turn blocks.
* **Approvals:**
  * pause → self-approve with resume → executed once;
  * reject → resume; approver decides → requester resumes; self-approval refused;
  * private request invisible to another member, an approver, a tenant admin and another tenant;
  * expired (410), invalid ids, double decision, cancel;
  * the full flow on PostgreSQL with the audit trail correlated by request id.
* **Memory:**
  * private CRUD with TTL; shared writes/deletes need shared permissions;
  * cross-tenant invisibility (facts, search, delete); no memory permission → 403; memory disabled → 503.
* **Audit:** `audit.read` required; no database → 503; on PostgreSQL another tenant's auditor sees none of the rows.
* **Tenant isolation over HTTP on PostgreSQL:** conversations, approvals, memory and audit; the same public id names two conversations; RLS still enabled on all seven RLS tables.
* **Maintenance:**
  * expired removed, non-expired kept (memory, approvals, idempotency; in memory and on PostgreSQL);
  * batching and the batch budget; repeat runs; two concurrent runners;
  * partial failure and crash; lock held → skipped;
  * statistics in logs and `audit_log`; scheduler start/stop; in-process scheduler opt-in; CLI and worker.
* **Security regressions:**
  * executed approval + purged idempotency record → not executed again (fails without the fix);
  * migration `0005` down/up.
* **Health:** liveness, readiness ok, dependency failure (no secrets in the report), production without a database → 503.

**Live checks against the dev servers** (uvicorn on 127.0.0.1, PostgreSQL `agentic` at `0005`, Redis):
* `/health` 200 with `X-Request-ID`; `/ready` → `ready`;
* `/api/v1/me` without a token → 401 + `WWW-Authenticate: Bearer`; with a token → the principal; with a forged token → 401;
* a body naming `tenant_id` → 422;
* fact create/list/delete; conversation forget;
* a real turn → 502 `LLM_AUTH_ERROR`: there are still no Anthropic credentials (Known issue 1);
* the server log contains no token, secret, `Bearer` string or fact text;
* `python -m agentic_platform.maintenance` → three `ok` reports;
* `APP_ENV=production` with only a development secret → the API refuses to start (exit 2).

### 17. Lint and type-check

* `ruff check src tests`: **clean**.
* `mypy --strict`: **clean, 123 source files**.

### 18. Performance

Measured on this machine (Docker for Windows), the same PostgreSQL-backed agent with a scripted LLM and one tool call, 40 turns each, two runs:

| Path | Median | p90 |
|---|---|---|
| turn, direct `Agent.arun` | 145-148 ms | 157-163 ms |
| turn, through uvicorn + HTTP | 154-156 ms | 165-171 ms |
| `GET /api/v1/me` (authentication only) | 2.2-3.5 ms | 4-5 ms |
| `GET /health` | 1.6-2.5 ms | 3-4 ms |

* **HTTP overhead per turn: about 6-11 ms median.** The turn itself is unchanged from Phase 5 (about 140 ms there).
* Authentication is parsed once per request; authorization still needs no queries.
* `POST /conversations/{id}/messages` adds one ownership read (by design: "continue" must not create).
* Maintenance never loads unbounded rows. Batches use the existing `expires_at` indexes and `SKIP LOCKED`.
* `/ready` queries PostgreSQL and Redis on every probe (about 100-150 ms on this machine; not cached).

### 19. Security verification

* Untrusted: bodies, path/query ids, client `X-Request-ID`, token claims beyond what the provider vouches for, LLM output, memory. Trusted: the verified principal, `Authorizer`, RLS and constraints, approval records.
* Not logged: tokens, secrets, `Authorization` headers, query strings, request bodies, memory content, tool arguments (tests check logs and audit events; live log checked).
* Not exposed: SQL, database errors, stack traces, other tenants' identifiers, approval hashes/policies, internal graph state.
* Cross-tenant and not-yours resources: 404 with the same code and message as missing ones.
* RLS remains enabled on all seven tenant-scoped tables (tested), audit immutability unchanged.

### 20. Known limitations

1. **No real identity provider verified** (Q6). The OIDC adapter is tested with local keys and a stubbed JWKS. Nested claims (for example `realm_access.roles`) are not supported.
2. **No token revocation, introspection or refresh:** a stolen token is valid until it expires. Development tokens have no rotation.
3. **No rate limiting and no CORS** (no frontend decided).
4. **Turns are synchronous over HTTP:** the request waits for the LLM (up to `LLM_TIMEOUT_SECONDS`). No streaming and no background turn execution.
5. **Approvers cannot resume** someone else's paused turn. The requester must call resume after the decision (Phase 5 actor rule).
6. **A turn `idempotency_key` without a database** answers 500 `CONFIGURATION_ERROR` (as `Agent` did before).
7. **Readiness is not cached;** every probe queries PostgreSQL and Redis.
8. **Maintenance `maintenance.run` rows are tenant-less:** visible to operators (owner role) only, not through the API. Per-approval `approval.expired` events are still written after the batch commits (best-effort, as in Phase 5).
9. **Multiple API processes need Redis** (thread lock). The in-memory fallbacks are per process.
10. **Still no live LLM call** (Known issue 1).

### 21. Architecture decisions

* **Added:** ADR-043 (HTTP API and authentication boundary), ADR-044 (scheduled maintenance), ADR-045 (correlation, error envelope, health and readiness), ADR-046 (requester-only approvals and single execution after purge).
* **Status changed:**
  * ADR-014 → Accepted (FastAPI in use);
  * ADR-037 → amended (OIDC provider behind the same protocol; local identity never over HTTP);
  * ADR-040 → amended by ADR-046;
  * ADR-029 → the scheduled purge exists.

### 22. Deliberately NOT implemented

* Purchase / Sales / Inventory / Accounting / GST agents, workflows or tables; business approval thresholds or roles; WhatsApp/SMS; supplier or customer features.
* A supervisor or domain-agent phase.
* A frontend, CORS, rate limiting, token revocation, refresh tokens, session management.
* Retention for checkpoints, the audit log, decided approvals or conversation rows (Q11).
* Administrator or sharing access to other users' conversations (Q16).
* An HTTP-specific approval engine, a second agent implementation, or new memory capabilities.

## 3f. Phase 7: production readiness and deployment

**Scope** (stakeholder definition, 2026-10-02): make the platform operable in production: identity provider, OIDC verification, token lifecycle and revocation, first real LLM request, production configuration, deployment architecture (API, PostgreSQL, Redis, maintenance), secrets, health/readiness, logging, deployment and security tests, documentation. No business agents. Details: ARCHITECTURE.md § A.11, ADR-047 to ADR-052.

Legend: **IMPLEMENTED** (code + tests) · **VERIFIED** (exercised against the real thing) · **PARTIALLY VERIFIED** · **PLANNED** · **OPEN QUESTION**.

### 1. Summary

| Item | Status | Where / evidence |
|---|---|---|
| Production configuration policy (no dev auth, required dependencies, no dev passwords, ...) | IMPLEMENTED, VERIFIED (image refuses to start) | `core/production.py`; `--check-config` on API and worker (ADR-047) |
| Development tokens refused by every entry point in production | IMPLEMENTED | `build_token_authenticator` returns nothing in production |
| Identity-provider selection | **OPEN QUESTION (Q6)** | options and requirements in § 3; no provider invented |
| OIDC adapter against a real OIDC implementation | **VERIFIED** (Keycloak 26.4, local reference instance) | `tests/live/test_oidc_live.py` (12 checks + 2 provider-neutral) |
| OIDC against the production provider | NOT VERIFIED (no provider chosen) | provider-neutral live mode ready (`LIVE_OIDC_*`) |
| Token lifetime bound | IMPLEMENTED, VERIFIED | `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` (ADR-048) |
| Token lifecycle / revocation strategy | DOCUMENTED; behaviour VERIFIED against Keycloak | § 4, ADR-048; immediate revocation not guaranteed (Q19) |
| Nested role claims | Not added (not required: top-level claims via provider mappers) | ADR-051 |
| LLM through the existing abstraction | IMPLEMENTED (unchanged path; endpoint override added) | `LLM_ANTHROPIC_BASE_URL`; request shape checked against the current API reference |
| LLM failure handling | IMPLEMENTED, VERIFIED through the real SDK/HTTP stack; invalid key VERIFIED against the real endpoint | `tests/test_llm_failures.py`, `tests/live/test_llm_live.py` (ADR-052) |
| Live LLM smoke test (successful completion) | **NOT VERIFIED**: no Anthropic credential on this machine (no key, no `ant` profile) | harness ready: `tests/live/test_llm_live.py::test_live_smoke` |
| Application image | IMPLEMENTED, VERIFIED (built, run, non-root) | `Dockerfile`, `.dockerignore` (ADR-050) |
| API / worker / migrate deployment | IMPLEMENTED, VERIFIED locally in containers | `docker-compose.yml` profile `app` |
| PostgreSQL / Redis production services | DOCUMENTED (managed, TLS, persistent); local services unchanged | § 6, ARCHITECTURE.md § A.11 |
| Secrets boundary | IMPLEMENTED, VERIFIED | `APP_SECRETS_DIR` secret files; `deploy/env/*.env.example` (ADR-049) |
| Health / readiness | IMPLEMENTED, VERIFIED in containers | `/ready` adds non-gating `llm` component |
| Graceful shutdown (API, worker) | IMPLEMENTED, VERIFIED (SIGTERM in container, exit 0) | `API_GRACEFUL_SHUTDOWN_SECONDS`; `MaintenanceRunner.request_stop` |
| Migration strategy | IMPLEMENTED (job before rollout), VERIFIED (container job; concurrent jobs serialized) | ADR-050 |
| Production manifests for a specific platform | PLANNED (target is Q3) | — |

### 2. Live verifications that remain

1. **First successful Claude completion.** Needs a credential: `$env:RUN_LIVE_LLM_TESTS="1"; $env:ANTHROPIC_API_KEY="<key>"; .venv\Scripts\python -m pytest tests/live/test_llm_live.py -m live_llm -s` (add the three `TEST_*` variables to also run it on PostgreSQL + Redis). It checks: API starts, principal, LangGraph, real response, structured output (the time tool is planned), memory recall, authorization, approval (nothing written without approval), request-id correlation (audit rows), no secrets in logs. About four short requests.
2. **The production identity provider.** After Q6: configure the provider (audience mapper, admin-only tenant attribute, top-level roles claim, ≤ 15-minute access tokens), then run `tests/live/test_oidc_live.py` with `LIVE_OIDC_ISSUER`, `LIVE_OIDC_AUDIENCE`, `LIVE_OIDC_ACCESS_TOKEN` (+ claim and role-map variables).

### 3. Identity provider decision

**No production provider is selected** (none is specified in the repository or by the stakeholders; ADR-051). Options, all compatible with the adapter without code changes as long as they emit top-level claims:

| Category | Examples | Fit |
|---|---|---|
| Enterprise IdP the organisation already runs | Microsoft Entra ID, Okta, Google Workspace | preferred if staff already sign in there: users, MFA and offboarding exist. Tenant claim via app roles/custom claims or a static mapping per app registration |
| Managed CIAM | Auth0, AWS Cognito, Azure AD B2C | for external users/customers; custom claims via actions/triggers |
| Self-hosted OIDC | Keycloak, Authentik, Zitadel | full control, data residency; the organisation operates it (HA, upgrades, backups) |

Required configuration for any of them: `AUTH_PROVIDER=oidc`, `AUTH_OIDC_ISSUER` (https), `AUTH_OIDC_AUDIENCE` (this API, distinct from client ids), claim names (`AUTH_OIDC_USER_CLAIM` = a stable subject, `AUTH_OIDC_TENANT_CLAIM`, `AUTH_OIDC_ROLES_CLAIM`), `AUTH_OIDC_ROLE_MAP_JSON`, `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS`. Provider-side: RS256/ES256 signed JWT access tokens, an admin-only tenant attribute (users must not edit it), roles or groups in a top-level claim.

**What was verified against Keycloak 26.4** (local reference instance, `docker compose --profile idp-test up -d keycloak`, realm built by `tests/live/keycloak_admin.py`):

| Check | Result |
|---|---|
| Discovery → JWKS, RS256 signature, `kid` | ✅ accepted; principal = stable `sub`, tenant from attribute, roles mapped |
| JWKS caching | ✅ 11 verifications = 1 discovery + 1 JWKS request |
| Key rotation (new higher-priority key) | ✅ unknown `kid` refused inside the 30 s floor, accepted after one refresh; old-key tokens valid while published, refused after the key is removed and the cache expires |
| Invalid signature / payload tampered (other tenant) / roles added | ✅ refused |
| `alg=none`, HS256 with the public key | ✅ refused ("algorithm not allowed") |
| Expired (10 s client) | ✅ refused with 0 s leeway; leeway is clock skew only |
| Wrong audience (other client), **ID token** used as access token | ✅ refused ("audience"), audited without the token |
| Wrong issuer; unknown realm | ✅ refused; unreachable discovery = `IDENTITY_PROVIDER_UNAVAILABLE` |
| Malformed, over-long, missing | ✅ refused (401 / `AUTHENTICATION_REQUIRED`) |
| Max lifetime (300 s token vs 120 s bound) | ✅ refused; accepted with 300 s |
| Role mapping | ✅ `kc-auditors` → `auditor`; unmapped `kc-forged-admin`, `default-roles-*`, `offline_access` grant nothing |
| Missing tenant claim | ✅ `TENANT_REQUIRED` |
| Tenant attribute admin-only | ✅ realm user profile `unmanagedAttributePolicy=ADMIN_EDIT` |
| HTTP API (in-process and **in the API container**) | ✅ `/me`, 422 for client-supplied `tenant_id`/`user_id`/`roles`/`permissions`, cross-tenant 404 identical to missing, tampered 401 with `WWW-Authenticate` |
| `nbf` / `iat` | `iat` present and required on real tokens; Keycloak does not emit `nbf` (the check is unit-tested with local keys) |

### 4. Token lifecycle and revocation (ADR-048)

| Question | Decision |
|---|---|
| Access-token lifetime | Issued by the provider; recommended 5-15 minutes. The API enforces `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` (production warning when unset). Final value depends on the provider (Q6/Q19) |
| Refresh tokens | Client ↔ provider only (authorization code + PKCE). The API never receives, stores or refreshes them |
| Revocation in the API | None: no deny-list, no introspection per request (keeps authentication local, ~2 ms) |
| User disabled | Verified: no new token is issued; an existing token works until `exp` (+ leeway) |
| Role removed at the provider | Verified: effective with the next token |
| Role catalogue changed on the server | Effective on restart (`AUTH_ROLE_CATALOG_JSON`) |
| Approval execution | Re-checks the requester's current permissions (Phase 5) |
| Tokens stored by the API | Never (audit stores reason codes; logs never contain tokens; checked in container logs) |
| Limitation | **Immediate revocation is not guaranteed.** Worst case = remaining lifetime + leeway (≈ 15.5 min with the recommended settings). If required (Q19): introspection, a `sid`/`jti` deny-list in Redis fed by back-channel logout, or shorter tokens |

### 5. LLM integration (ADR-052)

* Path unchanged: `Agent → LangGraph node → LLMProvider (LLM_PROVIDER) → AnthropicProvider → AsyncAnthropic`. Configurable: `LLM_PROVIDER`, `LLM_MODEL` (`claude-opus-5-5`), `LLM_EFFORT`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_ANTHROPIC_FALLBACKS`, `LLM_ANTHROPIC_BASE_URL` (new), credential `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / workload identity federation. No key in code, images or the repository.
* The request shape was checked against the current Claude API reference (SDK 1.11.0): `beta.messages.parse(output_format=Model)`, `output_config.effort`, `fallbacks="default"` with beta `server-side-fallback-2026-07-01`; thinking is adaptive by default on Opus 5.5.
* **Three defects found and fixed** by driving the real SDK against a local stub (the existing tests mocked the SDK):
  1. a non-JSON 200 body raised `JSONDecodeError` → turned into `INTERNAL_ERROR` instead of `LLM_OUTPUT_ERROR`;
  2. a 200 without the Messages shape raised `TypeError` → `INTERNAL_ERROR`;
  3. **security:** a structured-output validation failure raised a pydantic `ValidationError` whose message quotes the model output; it was logged with a traceback. Now `LLM_OUTPUT_ERROR` with the exception type only, and translated errors are not chained. Provider error messages (which can echo request content) are no longer kept either.
* Failure matrix (provider level and over HTTP): invalid key/permission → 502 `LLM_AUTH_ERROR` (not retryable); 429, 500, 529, timeout, connection refused → 503 `LLM_UNAVAILABLE` + `Retry-After`; 400 → 502 `LLM_ERROR`; malformed/refusal/truncated → 502. SDK retries `LLM_MAX_RETRIES` first (verified: 3 attempts with 2 retries). No key, prompt, provider message, provider request id or traceback reaches the client or the logs; `/ready` and other endpoints keep working (degraded, not down).
* **Live:** the real endpoint answered an invalid key with 401 `authentication_error` → API 502 `LLM_AUTH_ERROR`, key absent from logs. Observed: Anthropic issues no request id for a rejected key. A successful completion is **NOT VERIFIED**.

### 6. Deployment (ADR-050)

**Docker.** One image (`Dockerfile`, `python:3.11-slim-bookworm`, 467 MB, non-root uid 10001, `.[oidc]`, no secrets, `HEALTHCHECK` on `/health`, default `APP_ENV=production`). `docker-compose.yml` keeps `postgres` and `redis` byte-identical in effect (config hashes `0459c5e3…` / `26c0b2c3…` unchanged, containers not recreated, volume `agentic_ai01102026_pgdata` kept) and adds, behind profiles:

| Service | Profile | Role |
|---|---|---|
| `migrate` | `app` | `alembic upgrade head` with the owner role, exits; `api`/`maintenance` wait for its success |
| `api` | `app` | runtime role only, Redis, port 8000 |
| `maintenance` | `app` | `--loop`, `stop_grace_period: 60s`, no HTTP healthcheck |
| `keycloak` | `idp-test` | reference OIDC provider for verification only |

**Process model, startup and dependency classes:** ARCHITECTURE.md § A.11 (REQUIRED: configuration, PostgreSQL at head, Redis, identity configuration; DEGRADED: LLM; OPTIONAL for serving: maintenance worker, because expiry is also enforced at read time).

**PostgreSQL (production):** managed PostgreSQL 16 with pgvector ≥ 0.8, persistent storage, automated backups + PITR, `DATABASE_SSL_MODE=verify-full`, the runtime connects as `agentic_app` (DML only, RLS), only the migration job gets `agentic_owner` (which must not be a superuser in production). PgBouncer transaction pooling is still unsupported as configured (Known issue 5).

**Redis (production):** managed Redis 7, `rediss://`, password/ACL, private network, `maxmemory-policy noeviction`. **No persistence needed**: Redis holds locks, fast-paths and working memory only; a restart loses nothing durable (turns in progress fail retryably; thread locks fail closed). Shared instances are separated by `REDIS_KEY_PREFIX`.

**Maintenance worker / scheduler:**
* Preferred: scheduled one-shot job (`python -m agentic_platform.maintenance`), every `MAINTENANCE_INTERVAL_SECONDS` equivalent (default hourly; no new retention period introduced). Alternative: one `--loop` worker.
* Competing runs are safe: per-job Redis lock (busy → `skipped`) plus `FOR UPDATE SKIP LOCKED`; locking unchanged.
* Retries: a failed batch rolls back and the job stops for this run (`partial`/`failed`, exit 1); the next scheduled run retries. No in-process retry loop.
* Observability: `maintenance.job.*` logs, `maintenance.run` audit rows, exit code.
* Manual: `python -m agentic_platform.maintenance [--job NAME]`, `--list`, `--check-config`.

**Migrations:** a migration job runs before new API replicas start; the API never migrates; readiness stays 503 while the schema is behind; concurrent jobs are serialized by the advisory lock (verified: two simultaneous jobs on `agentic_test`, one applied `0004`, `0005`, the other waited and applied nothing, both exit 0). Downgrades exist for every revision (`base ↔ head` test).

**Deployment procedure:**
1. Build and tag the image; `--check-config` with the target environment (must exit 0).
2. Run the migrate job with the owner URL; wait for exit 0.
3. Roll out API replicas (readiness gates traffic) and update the worker/schedule.
4. Smoke: `/health`, `/ready`, `/api/v1/me` with a real token, one harmless turn.

**Rollback:** redeploy the previous image tag (keep migrations backward compatible for one release; additive changes first, removals in a later release). Schema rollback only by an operator with `alembic downgrade -1` after checking the data impact. Never `docker compose down -v`, never drop or recreate the database volume.

### 7. Secrets and configuration (ADR-049)

* Sources: environment variables > `.env` (development only) > secret files in `APP_SECRETS_DIR` (Docker/Kubernetes secrets, Vault Agent, CSI drivers). Verified by a subprocess test.
* Environments: development `.env.example` (local dev passwords only), staging/production `deploy/env/*.env.example` (placeholders only; staging runs `APP_ENV=production`).
* Not in source control, Dockerfiles, `.env.example` (no real values), tests (only obviously fake values), logs or audit rows; `.dockerignore` excludes `.env*`. The production report prints codes and setting names, never values (tested with passwords and secrets present).
* No secret-manager SDK: the choice belongs to the deployment target (Q3).

### 8. Health, readiness, logging

* `/health`: liveness only. `/ready`: database (`ok` = reachable + at head), Redis, authentication, and **new** `llm` (configuration only, never gating, never a provider call). No hosts, URLs or versions (verified in containers with an unreachable `no-such-db` / `no-such-redis`: process alive, `/ready` 503 naming the component, turns 503 + `Retry-After`, logs without host names, passwords or tracebacks).
* New log events: `config.production.warning`, `llm.call.failed`, `maintenance.stop_requested`, `auth.dev_tokens.refused_in_production`. Container logs checked: no token, `Bearer`, client secret or development password.

### 9. Security verification

| Area | Check | Result |
|---|---|---|
| Authentication | missing / invalid / expired / wrong issuer / wrong audience / invalid signature / alg confusion | ✅ real Keycloak tokens + unit tests |
| Authorization | unauthorized permission, cross-tenant, client-supplied tenant/user/roles/permissions | ✅ (Phase 5/6 tests + real tokens over HTTP) |
| Approval | wrong requester/approver, expired, replayed, changed authorization, wrong tenant | ✅ existing Phase 5/6 suites pass unchanged |
| Infrastructure | secrets not exposed, debug off (docs off in production, `DEBUG` warned), database persistent (volume kept), Redis password + noeviction, health/ready safe | ✅ |
| Production misconfiguration | dev token secret, local identity, non-OIDC, dev passwords, no DB/Redis, SQL echo, runtime-as-owner, in-process maintenance, missing LLM credential | ✅ refused at start (unit + container) |
| RLS / tenant isolation | all 7 RLS tables still enabled on the dev database; integration suites pass | ✅ |

### 10. Performance

* Authentication with real Keycloak RS256 tokens: **5.6-5.7 ms median** for `GET /api/v1/me` (p90 7-8 ms) vs 3.5-3.7 ms with development tokens (in-process, 300 requests, two runs). About 2 ms for the signature check; no network per request (JWKS cached).
* The production policy runs once at startup; the `llm` readiness check does no I/O. Turn latency unchanged (no change to the turn path except error translation).

### 11. Files created / modified

**Created:** `src/agentic_platform/core/production.py`; `Dockerfile`; `.dockerignore`; `deploy/env/production.env.example`; `deploy/env/staging.env.example`; `tests/test_production_unit.py` (39); `tests/test_llm_failures.py` (21); `tests/live/__init__.py`; `tests/live/keycloak_admin.py`; `tests/live/test_oidc_live.py` (14); `tests/live/test_llm_live.py` (2).

**Modified:**

| Path | Change |
|---|---|
| `core/config.py` | `APP_SECRETS_DIR` secret files; `LLM_ANTHROPIC_BASE_URL`; `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS`; `API_GRACEFUL_SHUTDOWN_SECONDS` |
| `llm/anthropic_provider.py` | base URL; controlled error translation (no provider message/model output, not chained); malformed responses; `llm.call.failed` |
| `security/oidc.py` | max token lifetime |
| `security/authentication.py` | no development-token authenticator in production |
| `api/__main__.py` | production policy, `--check-config`, graceful shutdown timeout |
| `api/runtime.py`, `services/readiness.py` | `llm` readiness component |
| `maintenance/__main__.py`, `runner.py`, `scheduler.py` | production policy, `--check-config`, SIGTERM/SIGINT graceful stop (`request_stop`) |
| `docker-compose.yml` | profiles `app` (migrate, api, maintenance) and `idp-test` (keycloak); header; existing services unchanged |
| `pyproject.toml` | markers `live_oidc`, `live_llm` |
| `.env.example` | new variables, environment notes, live-test variables |
| `tests/test_api_unit.py` | pre-existing flaky canary `7781` (hex, could appear in a random request id: 0.04 % per response, one intermittent failure seen) → non-hex `QZPX` |
| `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `PROJECT_STATUS.md` | § A.11; ADR-047 to ADR-052; this section |

No database migration in Phase 7 (head stays `0005`).

### 12. Tests run and exact results (2026-10-02)

PostgreSQL 16.15 + pgvector 0.8.6, Redis 7.4.11, Keycloak 26.4 (Docker), Python 3.11.9, anthropic 1.11.0, uvicorn 0.54.0, PyJWT 2.15.1.

| Run | Result |
|---|---|
| Baseline before Phase 7 (PostgreSQL + Redis) | 396 passed (exit 0) |
| **Full suite: PostgreSQL + Redis + Keycloak + `RUN_LIVE_LLM_TESTS=1`** | **468 passed, 4 skipped** (130.7 s). Skipped: live LLM smoke (no credential), 2 provider-neutral OIDC tests (no `LIVE_OIDC_*` in that run), POSIX SIGTERM test on Windows |
| Provider-neutral OIDC mode, separately, with a Keycloak token | 2 passed |
| Unit only, no servers | 377 passed, 95 skipped (three further consecutive runs green) |
| Total collected | 472 = 396 baseline + 76 new |
| Regression | All Phase 1-6 tests pass; one Phase 6 test assertion made deterministic (canary, § 11) |

**Live/container checks (not pytest):** image build; container stack (`migrate` exit 0, `api` healthy as uid 10001, `/ready` ready, Keycloak token → `/me` with `auditor` mapped, turn → 502 `LLM_AUTH_ERROR`, `/audit` 200 for the auditor); SIGTERM: worker `maintenance.stop_requested` → exit 0, API `api.stopped` → exit 0; bare image → exit 2 with the refused codes; dev secret in the production image → refused; unreachable database/Redis → alive, `/ready` 503, turns 503; concurrent migration jobs; dev database unchanged (same container id and start time, revision `0005`, pgvector 0.8.6, RLS on 7 tables, audit rows 11 → 14 from the verification run).

### 13. Lint and type-check

* `ruff check src tests`: **All checks passed!**
* `mypy --strict`: **Success: no issues found in 124 source files.**

### 14. Known limitations

1. **No successful live LLM completion** (no credentials). Structured output, refusal fallback and effort behaviour against the real model remain unobserved.
2. **No production identity provider** (Q6); verification used a reference Keycloak in dev mode over http.
3. **Tokens cannot be revoked before `exp`** (ADR-048); bounded only by lifetime + leeway.
4. **No platform-specific manifests** (Kubernetes/ECS/...): Q3. No TLS termination, rate limiting or CORS in the application (Q20, Q5).
5. **`create_app()` does not run the production policy**; only the supported entry points do (embedding the app elsewhere must call `enforce_production_policy`).
6. **The SIGTERM path of the worker is verified in the Linux container only**; Windows event loops cannot install signal handlers (Ctrl+C still works).
7. **Secret rotation needs a restart** (connections are built at start).
8. **Keycloak test realm uses the password grant** for test clients only; real clients must use authorization code + PKCE.
9. Phase 6 limitations not addressed here remain (synchronous turns, approvers cannot resume, readiness not cached, tenant-less maintenance audit rows).

### 15. Architecture decisions

* **Added:** ADR-047 (production configuration policy), ADR-048 (token lifecycle and revocation), ADR-049 (secrets boundary), ADR-050 (deployment architecture), ADR-051 (identity-provider verification and selection status), ADR-052 (LLM failure handling and degraded operation).
* **Status changed:** none superseded. ADR-043's "not verified against a real provider" is now "verified against a reference provider" (ADR-051); ADR-037's development tokens are now refused by every production entry point (ADR-047).

### 16. Deliberately NOT implemented

* Purchase / Sales / Inventory / Accounting / GST agents, GRN, invoices, quotations, challans, stock transfer, AI reorder, WhatsApp/SMS, reminders, supplier intelligence, business approval thresholds or workflows.
* A production identity-provider choice (Q6), a token deny-list or introspection (Q19), nested-claim parsing (not required).
* A cloud-specific secret manager SDK or platform manifests (Q3).
* A frontend, CORS, rate limiting.
* New retention periods (Q11) or Redis persistence (not needed).
* Automatic migrations on API startup.

### 17. Recommended next phase

Phase 8 (proposal, not started): **go-live hardening once Q3/Q6 are answered**: choose the identity provider and run the live OIDC tests against it, run the live LLM smoke with a real key, write the target-platform manifests (managed PostgreSQL with pgvector, Redis with TLS, secret manager, scheduled maintenance), TLS termination and rate limiting at the gateway (Q20), and a load test of the deployed stack. Alternatives in §10.

## 3g. Phase 8: go-live hardening

**Scope** (stakeholder definition, 2026-10-02): harden and verify the platform for a controlled go-live: production identity provider, live Claude, deployment target, TLS, rate limiting, gateway, load testing, failure testing, production configuration, security, runbooks, go-live checklist. No business agents. Details: ARCHITECTURE.md § A.12, ADR-053 to ADR-057, [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md), [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md).

Legend: **IMPLEMENTED** (code + tests) · **VERIFIED** (exercised against the real thing) · **VERIFIED LOCALLY** (real components on this machine, not production) · **NOT VERIFIED** · **OPEN QUESTION**.

### 1. Summary

| Item | Status | Evidence |
|---|---|---|
| Production identity provider | **OPEN QUESTION (Q6)**: none specified in the repository or by the stakeholders; not chosen silently | adapter unchanged and provider-neutral; reference Keycloak suite re-run (§ 10) |
| Live Claude completion | **NOT VERIFIED**: no Anthropic credential on this machine (no `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, profile or `.env`) | live invalid-key check against the real endpoint re-run (§ 10); harness unchanged |
| Deployment target | **OPEN QUESTION (Q3)**: not chosen silently | deployment-neutral package: image, gateway, env templates, runbooks |
| TLS gateway | IMPLEMENTED, VERIFIED LOCALLY | `deploy/gateway/` (ADR-054); compose profile `gateway` |
| Rate limiting (gateway + API) | IMPLEMENTED, VERIFIED LOCALLY | ADR-053; `services/rate_limit.py`; nginx `limit_req`/`limit_conn` |
| Trusted proxy handling | IMPLEMENTED | `API_FORWARDED_ALLOW_IPS`; `*` is a production error |
| SSE / streaming protection | NOT APPLICABLE | **the API has no SSE endpoint** (turns are synchronous, ADR-043). The Phase 8 brief assumed one; none was invented. The gateway is ready for one (§ 6) |
| Load test | VERIFIED LOCALLY (platform only, LLM stubbed) | `deploy/loadtest/`; § 7. Not production capacity |
| Failure tests | VERIFIED LOCALLY | § 8; **three defects found and fixed** (hung-database readiness, stale gateway upstream address, gateway marking its only upstream down) |
| Production configuration gate | IMPLEMENTED, VERIFIED (unit, subprocess and inside the image) | production template accepted; unsafe variants refused (§ 9) |
| Agent DB / ERP DB separation | IMPLEMENTED, VERIFIED | migration guard (ADR-056); § 5 |
| Security re-verification | VERIFIED (suites) / VERIFIED LOCALLY (stack) | § 9 |
| Runbooks, go-live checklist | DOCUMENTED | `OPERATIONS_RUNBOOK.md` (14 runbooks), `GO_LIVE_CHECKLIST.md` |
| **Go-live verdict** | **NOT READY**: blocked on Q3, Q6, Q22 (and Q20 for the limit values) | GO_LIVE_CHECKLIST.md |

### 2. Decisions still required (nothing chosen silently)

| Decision | Why it blocks | What is ready |
|---|---|---|
| **Q6 identity provider** | production login and token validation can only be verified against the real provider | `LIVE_OIDC_*` provider-neutral live tests; requirements in § 3f.3 |
| **Q3 deployment target** (and Q13 Redis, Q21 scheduler) | manifests, managed PostgreSQL/Redis, certificates, log shipping, alerts, backups | one image, three roles, gateway, env templates, runbooks; any container platform |
| **Q22 Anthropic credential** (and Q4 data policy) | the first live completion | `tests/live/test_llm_live.py` (about four harmless requests) |
| **Q20 rate-limit values** | current values are provisional technical defaults | all values are configuration (`RATE_LIMIT_*`, `GATEWAY_*`) |
| **Q24 first-turn retry** (new) | a client retrying a *new-conversation* turn without `thread_id` can create a second conversation; the existing contract deduplicates per conversation only | documented client rule: create or name the conversation, then send with `idempotency_key` |

### 3. Rate limiting (ADR-053)

* **Gateway**, per client address, before authentication: `limit_req` (20 r/s, burst 40), turn POSTs (30/min, burst 5), `limit_conn` (20 per address); 429 with the API's envelope and code.
* **API**, per verified caller (`sha256(tenant, user)` from the principal), after authentication: `turn` 20, `approval_decision` 60, `write` 60, `read` 600 per 60 s window; Redis `INCR` + TTL, shared by replicas; 429 `RATE_LIMITED`, `retryable`, `Retry-After` = rest of the window. On by default in production only; production warns when off.
* Not authorization: 401 before counting (not counted); under budget, permissions still decide (403 stays 403). Client headers and query parameters cannot choose or reset the bucket.
* Redis down: fail open (logged) by default; `RATE_LIMIT_FAIL_OPEN=false` refuses. Turns fail closed anyway (thread lock).
* All numbers are provisional configuration (Q20).

### 4. Gateway and TLS (ADR-054)

* `deploy/gateway/templates/default.conf.template`, `snippets/proxy.conf`, `snippets/security_headers.conf`; unprivileged nginx 1.27.5 (uid 101); `deploy/gateway/dev-certs.sh` (self-signed, 30 days, localhost; `deploy/gateway/certs/` git- and docker-ignored, `*.key`/`*.pem` git-ignored).
* TLS 1.2/1.3 (Mozilla intermediate), HTTP → 308 HTTPS, HSTS, `nosniff`, `X-Frame-Options DENY`, CSP `default-src 'none'`, `no-store`, 1 MiB bodies, 15 s header/body timeouts, 30 s read timeout (300 s for turns), `/ready` internal only, JSON error bodies for 413/429/503/504.
* Replaces `X-Forwarded-*` (never appends), forwards its `$request_id` as `X-Request-ID` (logged by the API as `client_request_id`), forwards `Authorization` untouched, logs no headers or query strings.
* Upstream re-resolved at run time (`server api:8000 resolve`, `GATEWAY_RESOLVER`) and never marked down by nginx (`max_fails=0`).

### 5. Database safety and ERP separation (ADR-056)

* The repository and configuration contain **no ERP database connection** (`DatabaseSettings` has only `url` and `migration_url`, both for the Agent DB; tested). Open question Q1 still records that no ERP/system-of-record database was found or specified; the Phase 8 brief states one exists. Nothing in this phase connects to it.
* New guard: the migration job refuses a database that has user tables but no `alembic_version` (for example an ERP database) before creating anything; override `AGENTIC_MIGRATE_NON_EMPTY_DATABASE=1` for deliberate adoption only. Verified against a scratch database (created and dropped by the test): no `alembic_version`, no extension, its table and row untouched.
* Deployment never migrates automatically: migrate job → Agent DB → API (unchanged). The local development database was not reset: revision `0005`, RLS on 7 tables, both audit triggers, **14 audit rows (unchanged since Phase 7)**, volume `agentic_ai01102026_pgdata` (created 2026-10-01) and container kept; `postgres`/`redis` compose config hashes unchanged (`0459c5e3…` / `26c0b2c3…`). All Phase 8 traffic used the disposable `agentic_test` database. Another project's volume on this machine (`inven-logic_postgres-data`) was not touched.

### 6. SSE / streaming

There is no streaming endpoint: `POST /api/v1/agent/turn`, `/conversations`, `/conversations/{id}/messages` and `/resume` are synchronous JSON (ADR-043). Nothing was added. Verified instead for the synchronous turn: authentication and authorization before any work (401/403 never reach the agent), rate limit before the agent (a 429 turn consumes no LLM call), gateway read timeout 300 s for turns only, idempotent retry after a cut connection (§ 8: same `thread_id` + key → replayed, different message → 409, one copy in history). A future SSE route needs `proxy_buffering off`, a heartbeat shorter than its read timeout, and must keep status codes final before the first byte.

### 7. Load test (VERIFIED LOCALLY, not production capacity)

**Environment:** Windows 11 laptop, Docker Desktop (12 CPUs, 7.5 GiB to Docker); **one** API process (uvicorn, single worker) in a container; nginx gateway with TLS 1.3; PostgreSQL 16.15 + pgvector 0.8.6 and Redis 7.4 (local containers); real RS256 access tokens from the reference Keycloak (3 users, 2 tenants); LLM replaced by `deploy/loadtest/llm_stub.py` (500 ms per call, two calls per turn); disposable `agentic_test` database; limits raised so the limiter is not what is measured. Client: `deploy/loadtest/run_load.py` on the same machine (competes for CPU). Raw results: `deploy/loadtest/results/*.json`.

| Scenario (30 s; 20 s at c=40) | Concurrency | Requests | req/s | p50 | p95 | p99 | Errors | API CPU (mean) | API memory | PG conns (max) | Redis clients (max) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| probes (`/health`, `/ready`) | 10 | 6 000 | 200.0 | 36 ms | 133 ms | 200 ms | 0 % | 93 % | 164 MiB | 10 | 11 |
| reads (`/me`, conversations, history, memory, approvals) | 10 | 3 677 | 122.6 | 72 ms | 163 ms | 220 ms | 0 % | 100 % | 168 MiB | 10 | 11 |
| turns (stub LLM) | 5 | 114 | 3.7 | 1 306 ms | 1 618 ms | 1 698 ms | 0 % | 39 % | 168 MiB | 7 | 11 |
| mixed (90 % reads, 10 % turns) | 10 | 1 506 | 48.0 | 30 ms | 1 453 ms | 1 651 ms | 0 % | 84 % | 172 MiB | 11 | 11 |
| reads | 40 | 2 976 | 147.3 | 228 ms | 674 ms | 929 ms | 0 % | 101 % | 173 MiB | 11 | **20** |
| mixed | 40 | 1 594 | 73.1 | 239 ms | 3 210 ms | 3 937 ms | 0 % | 98 % | 175 MiB | 11 | **20** |

Findings:
* One API process is **CPU-bound at about one core** (~120-150 authenticated reads/s); beyond that, latency grows by queueing, with no errors. Scale with replicas, not threads.
* A turn adds **~300-450 ms of platform time** to the model time (two stubbed calls = 1 000 ms; p50 1 306 ms at c=5). Real Claude latency (seconds) will dominate; **LLM latency was not measurable** without a credential.
* PostgreSQL connections stayed within the pool (`5 + 5` + worker). Redis reached **20 clients = `REDIS_MAX_CONNECTIONS`** at 40 concurrent requests per process: size `REDIS_MAX_CONNECTIONS` ≥ expected concurrent requests per replica (runbook § 6).
* The runs were made before the ADR-055 and gateway-resolve changes; a short re-run on the final build is in § 10.

### 8. Failure tests (VERIFIED LOCALLY, real containers)

Method: the running stack (gateway → API → PostgreSQL/Redis/Keycloak/LLM stub); dependencies frozen with `docker pause` (hung: connections open, no answers) or stopped; always restored. No volume removed, no database reset.

| Failure | Observed | Verdict |
|---|---|---|
| PostgreSQL hung (before fix) | `/ready` did not answer for > 90 s; requests hung | **defect** |
| PostgreSQL hung (after fix) | `/health` 200; `/ready` 503 `DATABASE_TIMEOUT` in 3.0 s (also through the gateway); token-only `/me` 200; DB reads/turns 503 `DATABASE_UNAVAILABLE` + `Retry-After: 2` in ~5 s; 15 parallel reads → 15 × 503 in 15 s; one request that already held a pooled connection waited past 45 s (bounded by the gateway's 30 s → 504); full recovery without restart | fixed (ADR-055) |
| PostgreSQL pool exhausted (pool 1, no overflow, 50 ms pool timeout) | 70 concurrent requests → 5 × 200, 65 × 503 `DATABASE_UNAVAILABLE`, `Retry-After: 2`; no 500, no traceback; immediately healthy afterwards | pass |
| Redis hung / stopped | `/ready` 503 `CACHE_TIMEOUT`/`CACHE_UNAVAILABLE` in ≤ 2 s; reads 200 (+2 s: rate limit fails open); turns 503 `CACHE_TIMEOUT` (lock fails closed); recovery without restart | pass |
| LLM unreachable | turns 503 `LLM_UNAVAILABLE` + `Retry-After`; `/ready` 200 (degraded); `llm.call.failed` logged; recovery without restart | pass |
| LLM invalid key / timeout / 429 / 5xx / malformed / invalid structured output | `tests/test_llm_failures.py` (real SDK) and the live invalid-key test | pass (§ 10) |
| IdP unreachable, warm key cache | valid token 200; tampered/malformed 401 with `WWW-Authenticate` | pass |
| IdP unreachable, cold cache (API restarted) | valid token 503 `IDENTITY_PROVIDER_UNAVAILABLE` + `Retry-After` (fails closed); malformed 401; recovery without restart | pass |
| API SIGTERM with a turn in flight | turn completed 200; exit 0 after 1.7 s | pass |
| API recreated (new address) | **before fix:** the gateway kept the old address and answered 503 indefinitely; **after fix:** gateway reached the new container in 3.1 s | fixed (ADR-054) |
| API starting while traffic arrives | **before fix:** one refused connect marked the only upstream down (nginx default `max_fails=1`): 646 instant 503s in 2 s of the load re-run; **after fix** (`max_fails=0`): 503 only while no API process listens (single-container replacement), no `no live upstreams` | fixed (ADR-054); run ≥ 2 replicas |
| API down | gateway JSON 503 `SERVICE_UNAVAILABLE` / 504 `GATEWAY_TIMEOUT` | pass |
| Client retry after a cut turn (same `thread_id` + key) | 200, `replayed=true`, same request id; different message with the same key → 409 `IDEMPOTENCY_KEY_REUSED`; one copy in history | pass |
| Client retry of a *first* turn without `thread_id` | a second conversation is created (existing per-conversation contract) | documented (Q24) |
| Duplicate maintenance worker (lock held) | held job `skipped`, other jobs `ok`, foreign lock untouched, exit 0 | pass |
| Maintenance with unreachable database | all jobs `failed`, exit 1; no host, URL or password in the logs (the audit fallback logs an operator traceback) | pass |
| Maintenance SIGTERM (`--loop`) | `maintenance.stop_requested`, exit 0 | pass |
| Unsafe configuration | image without configuration → exit 2 (API and worker); dev auth / `API_FORWARDED_ALLOW_IPS=*` → exit 2 | pass |

### 9. Security and configuration verification

| Area | Check | Result |
|---|---|---|
| Production cannot use development authentication | dev token secret, `AUTH_PROVIDER=dev_token`, local identity | refused at start (unit, subprocess, image) |
| Identity override | body `tenant_id` → 422; `X-Tenant-ID`, `X-User-ID`, `X-Forwarded-For`, `X-Request-ID` through the gateway → ignored (tenant from token) | pass |
| Rate limits are not authorization | 401 first and not counted; 403 under budget; per tenant+user buckets; spoofed headers/query cannot change the bucket | pass (unit + live stack) |
| Secrets | image scan (no private keys, no `.env`; only the public CA bundle); container logs of gateway and API (no `Bearer`, token fragment, client secret, passwords, API key); `--check-config` output; production template has no secret values (test) | pass |
| Cross-tenant, RLS, audit immutability, approval replay, requester-only | Phase 5-7 suites (unit + PostgreSQL integration) | pass (§ 10) |
| Configuration gate | rendered `deploy/env/production.env.example` + secret files → API and worker exit 0 with only `LEXICAL_EMBEDDINGS`; missing LLM key / Redis / DB, dev auth, http issuer, `*` proxies, dev password → exit 1 | pass (subprocess and image) |

### 10. Tests run and exact results (2026-10-02)

PostgreSQL 16.15 + pgvector 0.8.6, Redis 7.4, Keycloak 26.4 (Docker), Python 3.11.9, nginx 1.27.5 (unprivileged), uvicorn 0.54.0, SQLAlchemy 2.1.1, asyncpg 0.31.0.

| Run | Result |
|---|---|
| Baseline at the start of Phase 8 (PostgreSQL + Redis) | 1 failure: `test_failed_turn_maps_to_an_http_error_status`, a **pre-existing flaky canary** (`"429"` matched inside a random hex request id `…d1429d…`, the same class as Phase 7's `7781`); fixed deterministically |
| **Full suite: PostgreSQL + Redis + Keycloak + `RUN_LIVE_LLM_TESTS=1`** | **514 passed, 4 skipped, 0 failed** (exit 0). Skipped: live Claude smoke (no credential), 2 provider-neutral OIDC tests (no production provider), POSIX SIGTERM test on Windows |
| Unit only, no servers | 420 passed, 98 skipped (104.5 s) |
| Total collected | 518 = 472 (Phase 7) + 46 new |
| Live checks inside the full run | Keycloak live OIDC suite (12) passed; invalid Anthropic key against the real endpoint → `LLM_AUTH_ERROR`, key not logged |
| Regression | all Phase 1-7 tests pass |

New tests: `tests/test_rate_limit_unit.py` (16), `tests/test_deployment_unit.py` (20), `tests/test_failure_modes_unit.py` (2), `tests/integration/test_database_separation.py` (2), plus 4 production-policy cases and the `increment` contract on both backends (2).

**Lint and type-check:** `ruff check src tests`: All checks passed! · `mypy --strict` (src): Success: no issues found in 124 source files.

**Container and stack checks (not pytest):** image build; uid 10001; image scan; bare image exit 2 (API and worker); `--check-config` in the image with the rendered production template and secret files (exit 0, API and worker) and unsafe variants (exit 2); compose `app` + `gateway` + load-test overlay (migrate exit 0 with the new guard on `agentic_test`); TLS/redirect/headers/body limit/rate limits through the gateway; log hygiene; drills in § 8; load runs in § 7.

**Final-build load re-run** (after the ADR-055 and gateway fixes, same environment, c=10, 30 s):

| Scenario | Concurrency | Requests | req/s | p50 | p95 | p99 | Errors | API CPU (mean) | PG conns (max) | Redis clients (max) |
|---|---|---|---|---|---|---|---|---|---|---|
| probes | 10 | 6 388 | 212.9 | 55 ms | 122 ms | 166 ms | 0 % | 100 % | 10 | 12 |
| reads | 10 | 3 559 | 118.2 | 75 ms | 171 ms | 222 ms | 0 % | 97 % | 11 | 12 |
| turns | 5 | 126 | 4.2 | 1168 ms | 1346 ms | 1374 ms | 0 % | 30 % | 6 | 12 |
| mixed | 10 | 2 056 | 66.1 | 16 ms | 1264 ms | 1398 ms | 0 % | 83 % | 8 | 12 |

All requests 200. Same shape as the first run; turn overhead lower (p50 1 168 ms vs 1 306 ms). Files: `deploy/loadtest/results/phase8-final-c10.json` (probes, reads), `phase8-final-c10-turns-mixed.json` (turns, mixed; re-run after a host stall invalidated the first attempt, whose entries were removed).

Host note: the development machine suspended or stalled several times during long runs (wall-clock jumps of minutes, also visible as container uptime jumps). Every run affected by a stall was discarded and repeated; none of the numbers above comes from a disturbed run.

### 11. Files created / modified

**Created:** `src/agentic_platform/services/rate_limit.py`; `deploy/gateway/templates/default.conf.template`, `deploy/gateway/snippets/proxy.conf`, `deploy/gateway/snippets/security_headers.conf`, `deploy/gateway/dev-certs.sh`; `deploy/loadtest/llm_stub.py`, `deploy/loadtest/compose.loadtest.yml`, `deploy/loadtest/run_load.py`, `deploy/loadtest/results/phase8-local-c10.json`, `phase8-local-c40.json`, `phase8-final-c10.json`, `phase8-final-c10-turns-mixed.json`; `tests/test_rate_limit_unit.py`, `tests/test_deployment_unit.py`, `tests/test_failure_modes_unit.py`, `tests/integration/test_database_separation.py`; `OPERATIONS_RUNBOOK.md`, `GO_LIVE_CHECKLIST.md`.

**Modified:**

| Path | Change |
|---|---|
| `core/config.py` | `RateLimitSettings` (`RATE_LIMIT_*`), `AppSettings.rate_limit_enabled`; `API_PROXY_HEADERS`, `API_FORWARDED_ALLOW_IPS`; `DATABASE_COMMAND_TIMEOUT_SECONDS` |
| `core/errors.py` | `RateLimitedError` (`RATE_LIMITED`, retryable, `retry_after_seconds`) |
| `core/production.py` | `FORWARDED_ALLOW_ALL` (error), `RATE_LIMIT_DISABLED`, `TURN_RATE_LIMIT_DISABLED` (warnings) |
| `cache/backend.py`, `cache/redis.py` | `KeyValueBackend.increment` (fixed-window counter; Lua on Redis) |
| `api/dependencies.py`, `api/runtime.py`, `api/routes/*.py` | `rate_limit(kind)` dependency on every `/api/v1` route; `ApiRuntime.rate_limiter` |
| `api/errors.py` | 429 mapping with the real `Retry-After` |
| `api/__main__.py` | `uvicorn_options` (proxy headers, trusted addresses) |
| `db/engine.py` | asyncpg `command_timeout` |
| `services/database_health.py` | caller-side hard deadline for the probe (ADR-055) |
| `db/migrations/__init__.py`, `db/migrations/env.py` | foreign-database guard (ADR-056) |
| `docker-compose.yml` | profile `gateway` (nginx-unprivileged 1.27.5, `resolve`, `max_fails=0` in its template); API `API_FORWARDED_ALLOW_IPS`, `RATE_LIMIT_ENABLED` pass-through; `postgres`/`redis` unchanged |
| `deploy/env/production.env.example`, `staging.env.example`, `.env.example` | new variables |
| `.gitignore` | `deploy/gateway/certs/`, `*.key`, `*.pem` |
| `tests/cache_contract.py`, `tests/conftest.py`, `tests/test_db_unit.py`, `tests/test_production_unit.py` | increment contract (memory + Redis), env isolation, command timeout, new policy codes |
| `tests/test_api_unit.py` | pre-existing flaky canary `"429"` matched a random hex request id (failed once in the Phase 8 baseline) → checks the body without ids |
| `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `PROJECT_STATUS.md` | § A.12; ADR-053 to ADR-057; this section |

No database migration in Phase 8 (head stays `0005`).

### 12. Known limitations

1. **Not production-verified:** identity provider (Q6), Claude completion (Q22), platform manifests, managed PostgreSQL/Redis, certificates, log shipping and alerts (Q3).
2. A **fully hung** database can still hold the requests that already had a pooled connection until the gateway timeout (ADR-055); readiness reports it within 3 s.
3. Rate limits use fixed windows (up to 2× burst across a boundary); values are provisional (Q20); with Redis down each request waits for the Redis timeout (+2 s) because the limiter fails open.
4. The gateway's `/ready` allow-list (`GATEWAY_INTERNAL_CIDR`) and resolver address are platform-specific.
5. The load test measured one process on a laptop with a stubbed model; it is not a capacity statement.
6. Phase 7 limitations remain (no token revocation before `exp`, secret rotation needs a restart, SIGTERM path of the worker verified in Linux only, no ANN index, lexical embeddings).

### 13. Deliberately NOT implemented

* Any business agent or feature (Purchase, Sales, Inventory, payments, notifications, RAG, supervisor, business approval thresholds).
* An SSE endpoint (none existed; not in scope).
* A choice of identity provider, cloud, secret manager or certificate authority.
* Changes to the first-turn idempotency contract (Q24).

### 14. Recommended next step

Answer Q6, Q3 and Q22, then: run gate 2 (live OIDC against the production provider) and gate 3 (live Claude), write the manifests for the chosen platform from the deployment-neutral package, provision managed PostgreSQL/Redis with TLS, set certificates and alerts, approve the rate-limit values (Q20), and repeat the load test on the target platform with the real model. Business-agent work can start in parallel only once the business documentation exists (Q9).

## 3h. Phase 9: production go-live verification and live ERP integration

**Scope** (stakeholder brief, 2026-10-02): resolve and verify the production identity provider (Q6), the deployment target (Q3), live Claude (Q22) and the ERP owner/access contract (Q1). Then connect read only to the existing ERP PostgreSQL database and verify security, RLS, the live schema, the business read layer, business tools and the LangGraph business query agent on real data, tenant isolation, memory and approval safety, observability and failures. No new business features. Details: ARCHITECTURE.md § A.13, ADR-058, [docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md), [docs/SCHEMA_INTEGRATION.md](docs/SCHEMA_INTEGRATION.md).

Legend: **VERIFIED** (evidence from a run) · **VERIFIED LOCALLY** · **NOT VERIFIED** · **BLOCKED** (input not supplied) · **NOT IMPLEMENTED** (does not exist in the repository) · **NOT APPLICABLE**.

### 1. Audit findings (Part A): the brief and the repository disagree

The brief says it must be checked against the real repository first. The check found that the following assumptions of the brief **are not true here**:

| The brief assumes | Repository fact |
|---|---|
| a business read layer, typed business tools, a supervisor, inventory intelligence, a business query agent | **none exist.** Registered tools: `system.current_time`, `system.database_health`, `memory.remember_fact` (approval-gated). Domains: `general` only. Phase 8 lists all business features as deliberately not implemented (§ 3g.13) |
| "the existing ERP configuration" and `python -m scripts.inspect_erp_security` | no ERP setting (one Agent DB setting pair, ADR-056) and no `scripts/` directory. **The inspector was written in this phase** (§ 3) |
| `DEVELOPMENT_RULES.md`, `InvenLogic_PHASE_BY_PHASE_CLAUDE_PLAN.md`, `docs/SCHEMA_INTEGRATION.md`, `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md`, a Schema Atlas | not in this repository, and not found in Desktop, Documents or Downloads. The two `docs/` files were **created** in this phase as blocked-state documents |
| an ERP owner/access contract, a production IdP, a deployment target, an Anthropic credential | none supplied: no `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`, no `LIVE_OIDC_*`, no `ERP_*` variables, no `.env`, no `ant` profile, no answers to Q1/Q3/Q6/Q22 |

Found on the machine but **not confirmed as the ERP, and not touched**: compose project `inven-logic` (`C:\Users\Dell\Documents\Abc\inven-logic`, a separate application repository), container `inventoryai-postgres` (postgres:16-alpine, host port 5433, seed directory mounted, stopped) and volume `inven-logic_postgres-data`. Only their Docker metadata was read; environment variable *names*, not values. The brief forbids guessing ERP hosts or credentials, so none of them was started or connected to. The ERP owner must say whether this is a copy of the production ERP schema (Q1).

Platform components re-checked by the baseline run (unchanged since Phase 8, all green): image/gateway/TLS/rate-limit/production-gate unit suites, OIDC (reference Keycloak, 12 live checks), Anthropic adapter failure matrix and the live invalid-key check, approvals, audit, Redis, Agent DB (PostgreSQL integration suites), maintenance worker, migrations and the ERP separation guard.

### 2. Part-by-part result

| Part | Result | Evidence / what is missing |
|---|---|---|
| A. Current-state audit | DONE | § 1; baseline **514 passed / 4 skipped / 0 failed** (518 collected), identical to Phase 8 |
| B. Identity provider (Q6) | **BLOCKED** | no provider supplied; none chosen. Provider-neutral adapter unchanged; reference Keycloak suite green. Needed: provider, issuer, audience, tenant claim, roles claim and role map, a test token (`LIVE_OIDC_ISSUER`, `LIVE_OIDC_AUDIENCE`, `LIVE_OIDC_ACCESS_TOKEN`, optional `LIVE_OIDC_*_CLAIM`, `LIVE_OIDC_ROLE_MAP_JSON`, `LIVE_OIDC_EXPECTED_TENANT`) |
| C. Claude live (Q22) | **CLAUDE LIVE VERIFICATION: BLOCKED** | no credential. Re-verified: the real endpoint rejects an invalid key, mapped to `LLM_AUTH_ERROR`, key not logged; timeout/429/5xx/malformed/retry matrix through the real SDK (`tests/test_llm_failures.py`). Needed: `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`/workload identity) for the target workspace + the Q4 data policy; then `RUN_LIVE_LLM_TESTS=1 pytest tests/live/test_llm_live.py -m live_llm -s` |
| D. Deployment target (Q3) | **DEPLOYMENT TARGET: BLOCKED** | package stays platform-neutral; no capacity invented. Needed: platform, managed PostgreSQL/Redis services, certificate issuer, secret store, scheduler (Q21), egress path (Q23), traffic estimate (Q25) |
| E. ERP owner and access contract (Q1) | **ERP LIVE ACCESS: BLOCKED** | every field in checklist § 2 is TO BE SUPPLIED; nothing guessed |
| F. Safe ERP connection | **BLOCKED** (tool ready) | `scripts/inspect_erp_security.py` written and verified on a throw-away database (§ 3); never run against the ERP |
| G. Live ERP schema | **BLOCKED** | `docs/SCHEMA_INTEGRATION.md`: 12 entities, 0 verified |
| H. Live business read layer | **NOT IMPLEMENTED** (and BLOCKED) | does not exist; not written against guessed tables |
| I. Live business tools | **NOT IMPLEMENTED** (and BLOCKED) | no business tool exists. Registry guarantees re-verified (unchanged): unknown tool names refused, domain allow-list, schema validation, write tools only through approval (`tests/test_tools.py`, `tests/test_security_unit.py`); no tool accepts SQL |
| J. LangGraph business queries | **BLOCKED** | the six reference queries need the read layer, the tools, real ERP data and live Claude. Not run; no result simulated |
| K. Tenant isolation on ERP data | **BLOCKED** | needs Q1 and two real test tenants/users. Agent-DB tenant isolation (RLS, cross-tenant 404, client-supplied `tenant_id`/`user_id`/roles/permissions refused with 422) re-verified by the regression suites |
| L. Memory safety vs ERP truth | **NOT APPLICABLE yet** | no ERP figure exists for memory to contradict. Requirement recorded for the read-layer phase: ERP tool results always win; memory is context only |
| M. Approval safety | **VERIFIED (platform)** | no purchase-order or any ERP write tool exists, so "purchase order bana do" cannot place an order; approval suites (requester-only, replay blocked, execution re-checks permissions) green. The exact wording of the answer depends on live Claude (BLOCKED) |
| N. Observability | **VERIFIED LOCALLY (platform); business trace NOT APPLICABLE** | request/correlation ids, audit rows, tool metadata, error classes, no token/key logging (regression suites; Phase 8 container log checks). No business tool, so no business trace |
| O. Failure testing | **VERIFIED LOCALLY (platform); ERP failures BLOCKED** | Phase 8 drills + regression suites cover DB/Redis/Claude/IdP/pool/invalid and expired tokens. ERP unavailable, timeouts, malformed result, unknown or ambiguous customer/product and unauthorized warehouse need the read layer. Inspector failure paths verified: unreachable server → `NOT INSPECTED: <type>` without host/user/password; wrong database / Agent DB → refused |
| P. Documentation | DONE | GO_LIVE_CHECKLIST, OPERATIONS_RUNBOOK (§ 15 + safety rule 6), ARCHITECTURE § A.13, ADR-058, the two `docs/` files, this section |
| Q. Testing | DONE | § 6 |

### 3. ERP read-only inspector (ADR-058)

`python -m scripts.inspect_erp_security --expect-database <name> [--json]`, with the URL in `ERP_INSPECT_DATABASE_URL`. Operator tool, outside the package and the image (the Dockerfile copies `src/` only).

* **Identity first.** Before connecting, it refuses the host/port/database of `DATABASE_URL` and `DATABASE_MIGRATION_URL`. After connecting, it refuses a database that isn't `--expect-database`, or one that has `alembic_version` (the Agent DB). Exit 2.
* **Never writes.** It runs with `default_transaction_read_only=on`, in one READ ONLY transaction that is always rolled back, with statement timeout 10 s and lock timeout 1 s. It reads only the catalog and `flyway_schema_history`, never business rows.
* **Write refusal proven by the server, not assumed.** `EXPLAIN INSERT ... DEFAULT VALUES`, `EXPLAIN UPDATE ... SET c = c WHERE false` and `EXPLAIN DELETE ... WHERE false` run on each readable table (up to `--probe-limit`, default 25). PostgreSQL checks privileges when it starts the executor, also for EXPLAIN, but never executes the statement. Expected answer: `42501`. DDL is proven from the catalog: no CREATE on the database or a schema, and no owned object.
* **Checks:** NOSUPERUSER, NOBYPASSRLS, no CREATEDB/CREATEROLE/REPLICATION, no elevated or `pg_write_all_data` memberships, owns nothing, no table or column write privilege, no sequence UPDATE (USAGE = WARN), no CREATE, SECURITY DEFINER functions (WARN), RLS / FORCE RLS / policy count per readable table, owner-rights views (WARN), `current_setting(...)` names used by policies (the tenant mechanism), Flyway latest/applied/failed.
* **Output:** PASS/FAIL/WARN/INFO lines or JSON. Exit 0 / 1 (FAIL) / 2 (refused, unreachable). It never prints the password, and connection errors print the exception type only.

Verified against a **throw-away** PostgreSQL 16 database shaped like an ERP: tenant table with RLS + FORCE and a `current_setting('app.tenant_id')` policy, a table without RLS, a view, a sequence, Flyway history. A throw-away SELECT-only role stands in for the ERP role. Database and role are created and dropped by the test (`tests/integration/test_erp_inspection_postgres.py`):

| Case | Result |
|---|---|
| SELECT-only role, `default_transaction_read_only=on` | no FAIL; all 6 INSERT/UPDATE/DELETE plans refused with `42501`; RLS/FORCE/policy facts, tenant setting `app.tenant_id`, owner-rights view, Flyway `2.1` (2 applied, 0 failed) read correctly; the table without RLS reported as WARN |
| Superuser owner | FAIL on superuser, BYPASSRLS, ownership, table writes, DDL, probes; **plans accepted but never executed**: rows, sequence position and catalog identical before and after |
| One `GRANT INSERT ON stock_item` | FAIL on table writes and probes; exactly `INSERT public.stock_item` accepted |
| Wrong database name; the Agent DB (`agentic_test`) | refused |
| CLI against the development Agent DB (`agentic`, runtime role) | `REFUSED: ... alembic_version ... Agent DB`, exit 2 |

Plus unit tests for the verdict rules, the URL guards and output hygiene (`tests/test_erp_inspection_unit.py`).

### 4. Inputs required to unblock (exactly)

| Question | Who | What exactly |
|---|---|---|
| **Q1 ERP** | ERP owner / DBA | every row of checklist § 2: owners, host/port/database, connection path, a dedicated read-only role in the required shape, authentication and TLS, tenant mechanism, warehouse scope mechanism, RLS ownership, allowed source network, timeouts; whether `inven-logic` / `inventoryai-postgres` reflects the production schema; the Schema Atlas; the business rule definitions in `docs/SCHEMA_INTEGRATION.md` § 4; two test tenants with test users |
| **Q6 IdP** | IT / security | provider, issuer, audience for this API, tenant claim, roles claim and role map, token lifetime, a test user per tenant (and a disabled one) |
| **Q22 Claude** | account owner | credential for the production workspace (secret store, never the repository) and the Q4 decision on sending business data |
| **Q3 Deployment** | engineering lead / ops | platform, managed PostgreSQL and Redis, certificate issuer, secret store, scheduler, egress, log shipping and alerting, traffic estimate (Q25) |
| **Q26 (new)** | product owner | approval to build the ERP read layer and typed business tools in a separate phase once Q1 is answered. The Phase 9 brief treated them as existing |

### 5. Database impact

* **ERP schema: NONE. ERP data: NONE.** No connection to any ERP was made.
* **Agent DB (`agentic`, development): NONE.** No migration (head stays `0005`). The inspector CLI was pointed at it once and refused it after the identity query, before any other check.
* `agentic_test`: used by the regression suites as before. Throw-away databases `agentic_p9_erp_*` and roles `agentic_p9_reader_*` were created and dropped by the new integration test.
* Other projects' containers and volumes (`inventoryai-postgres`, `inven-logic_postgres-data`): metadata read only, not started or modified.

### 6. Tests run and exact results (2026-10-02)

PostgreSQL 16 + pgvector, Redis 7.4, Keycloak 26.4 (Docker), Python 3.11.9. `RUN_LIVE_LLM_TESTS=1` (invalid-key check against the real Anthropic endpoint).

| Run | Result |
|---|---|
| Baseline at the start of Phase 9 (PostgreSQL + Redis + Keycloak + live invalid-key) | **514 passed, 4 skipped, 0 failed** (518 collected; 2 min 17 s) |
| **Final full suite: PostgreSQL + Redis + Keycloak + live invalid-key** | **537 passed, 4 skipped, 0 failed** (541 collected = 518 + 23 new; exit 0; 3 min 11 s, no host stall) |
| Unit only, no servers | 439 passed, 102 skipped (66 s) |
| Skipped (both full runs) | live Claude smoke (no credential), 2 provider-neutral OIDC tests (no production IdP), POSIX SIGTERM test on Windows |
| Live ERP / live production IdP / live Claude completion | **not run: BLOCKED** (no inputs) |

New tests: `tests/test_erp_inspection_unit.py` (19), `tests/integration/test_erp_inspection_postgres.py` (4). No existing test was changed or weakened.

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy --strict` (src, 125 files): Success · `mypy --strict scripts` (2 files): Success. `ruff format --check` reports 15 test files that were already unformatted before Phase 9. Formatting is not a project gate, so they were left unchanged.

### 7. Files created / modified

**Created:** `scripts/__init__.py`, `scripts/inspect_erp_security.py`, `tests/test_erp_inspection_unit.py`, `tests/integration/test_erp_inspection_postgres.py`, `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md`, `docs/SCHEMA_INTEGRATION.md`.

**Modified:** `PROJECT_STATUS.md` (header, tracker, this section, Q1/Q3/Q6/Q22, Q26, § 10), `GO_LIVE_CHECKLIST.md` (verdict, legend, gate 5, ERP section, tests), `OPERATIONS_RUNBOOK.md` (safety rule 6, § 15), `ARCHITECTURE.md` (§ A.13), `ARCHITECTURE_DECISIONS.md` (ADR-058). No application code (`src/`) changed.

### 8. Known limitations

1. Everything in § 2 marked BLOCKED: production IdP, live Claude, deployment target, ERP access, live schema, business tools and queries, ERP tenant isolation.
2. The inspector proves the role's privileges, not the correctness of the ERP's RLS policies for each tenant. That needs two real tenants and the read layer (Part K).
3. `EXPLAIN`-based write probes cover tables the role can read (up to `--probe-limit`). Unreadable tables are covered by the catalog privilege check only.
4. SECURITY DEFINER functions are counted, not analysed. Each one needs the ERP owner's review.
5. Phase 8 limitations remain (§ 3g.12).

### 9. Go-live blockers

Q6 (identity provider), Q22 (Claude credential + Q4), Q3 (deployment target, with Q13/Q20/Q21/Q23/Q25), Q1 (ERP access contract), and for any business use: the ERP read layer and business tools (Q26) with confirmed business rules.

### 10. Recommended next step (not started)

Repeat Phase 9's blocked parts as soon as the inputs arrive, in this order. Each step only runs existing harnesses:

1. Q1: gate 5 (`scripts/inspect_erp_security`) against the ERP. Then fill the schema register with the ERP owner.
2. Q22: gate 3 (live Claude).
3. Q6: gate 2 (live OIDC).
4. Q3: manifests from the deployment-neutral package.

The ERP read layer and typed business tools are a separate, new build phase (Q26). Only after it can the business queries, ERP tenant isolation, memory-vs-ERP and ERP failure tests in the brief run. **Phase 10 has not been started.**

## 3i. Phase 10: business read layer and capability foundation

**Scope** (stakeholder brief, 2026-10-02): build a schema-safe, read-only foundation for the business layer that the real ERP can plug into later. No invented tables, columns, rules or tenant mechanism; no ERP writes; mock ERP for tests; live ERP explicitly NOT VERIFIED. The brief is the stakeholder's approval to build the foundation (Q26, partly answered: the live mapping still needs Q1). Details: [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md), ARCHITECTURE.md § A.14, ADR-059.

Legend: **UNIT VERIFIED** (pure code) · **MOCK VERIFIED** (fake ERP through the real registry/service/graph) · **VERIFIED LOCALLY** (throw-away PostgreSQL, not the ERP) · **LIVE ERP NOT VERIFIED**.

### 1. Implementation map (from the actual repository, before coding)

| Existing piece | How Phase 10 uses it |
|---|---|
| `ToolRegistry` (domain, permission, strict input, timeout, approval, audit `tool.call`) | business tools register into it unchanged; no second tool path |
| `DomainHandler` / `DomainRegistry` (documented extension point) | new `business` domain; the planned-call loop moved into `run_planned_tool_calls`, shared with `general` |
| `Principal`, `RoleCatalog`, `acting_as` / `require_principal` | tenant, user, permissions and scope come only from here; four `business.*` permission strings, granted by no built-in role |
| `Database`, `UnitOfWork(read_only=True)`, `translate_db_error` | reused for the ERP connection with its own engine and settings |
| `AuditService` / `AuditEvent` | new `business.read` events, metadata only |
| production policy (`core/production.py`) | new `ERP_*` checks |
| versioned prompts | `understand_request.system.v2`, `generate_response.system.v3` (v1/v2 kept) |
| graph topology | one node added after `generate_response`, for business turns only |

### 2. Definition of done

| Item | Result | Evidence |
|---|---|---|
| Business domain boundary | ✅ MOCK VERIFIED | `agents/domains/business.py` (READ tools only), `tools/business.py`; graph tests |
| Typed business read interfaces | ✅ UNIT VERIFIED | `business/models.py` (records + data), `business/capabilities.py` (6 capabilities) |
| ERP adapter boundary | ✅ | `business/ports.py`; `adapters/fake.py` (MOCK VERIFIED); `adapters/postgres.py` (VERIFIED LOCALLY on throw-away PostgreSQL; **no schema mapping**) |
| Read-only policy enforced | ✅ layers 2–7 verified locally; layer 1 (ERP role) LIVE NOT VERIFIED | `docs/BUSINESS_INTEGRATION.md` § 4; writes refused by the read-only transaction even as superuser with the SQL guard off |
| No arbitrary SQL tool | ✅ | no tool takes SQL/table/column; only `ErpQuery` constants declared in code run; unknown tool names refused |
| Typed tool schemas | ✅ | strict inputs (exactly one of name/ref; dates; limits); smuggled `tenant_id`/`user_id`/`sql`/`table`/`permissions` → `TOOL_INVALID_INPUT` |
| Authorization through the existing Principal | ✅ | registry permission + service re-check; planner sees only permitted tools |
| Tenant / scope boundaries defined | ✅ platform side; **ERP tenant and warehouse mechanisms UNKNOWN (Q1)** | `business/context.py`; tenant from the token; scope ALL/LISTED/NONE, fail closed; cross-tenant and out-of-scope = NOT_FOUND |
| Deterministic result semantics | ✅ | 7 statuses, reason codes, invariants (`business/results.py`) |
| Mock ERP adapter | ✅ | `TEST_*` data, 8 failure modes, refused in production |
| Mock tests pass | ✅ | § 5 |
| LangGraph integration foundation | ✅ | `business` domain + `verify_business_response`; six reference queries MOCK VERIFIED |
| Memory cannot override business truth | ✅ MOCK VERIFIED | grounding node: memory "₹50,000" vs ERP 45,000 → reply replaced with 45,000 |
| No real ERP mutation | ✅ | no write method in port/service (name scan test), READ-only registration, read-only sessions/transactions |
| Existing security model reused | ✅ | no second permission system; registry, Principal, audit, production gate reused |
| Audit / trace integration | ✅ | `business.read` audit + `business.read.completed` log (metadata only), joined to `tool.call` by request/thread id |
| Documentation updated | ✅ | § 7 |
| Full regression / ruff / mypy strict | ✅ | § 5 |
| **Live ERP integration** | **NOT VERIFIED** | no access contract, schema or rules (Q1) |

### 3. Reference queries (MOCK VERIFIED; no claim about real data)

| Query | Capability → tool | Fake ERP result (TEST data) |
|---|---|---|
| "Aaj ka total sales batao." | `daily_sales` (`day="today"`, needs `ERP_BUSINESS_TIMEZONE`, else RULE_NOT_VERIFIED) | 125000.00 INR, 7 documents |
| "Rahul Gupta ka pending bill kitna hai?" | `customer_open_invoices` | 2 invoices, total 45000.00 INR (computed by the service) |
| "Rahul Gupta ka outstanding kitna hai?" | `customer_outstanding` | 45000.00 INR |
| "iPhone 15 ka stock kitna hai?" | `product_stock` | 15 pcs (12 + 3), per visible warehouse |
| "Godown 2 mein stock kitna hai?" | `warehouse_stock` | 2 products |
| "Low-stock products kaunse hain?" | `low_stock_products` | 2 items with the ERP's level |

With `ERP_ADAPTER=postgres` every one of them answers **UNAVAILABLE / RULE_NOT_VERIFIED** (tested, without connecting). With `none` (the default), they're `unsupported`, exactly as in Phase 9.

### 4. Changed behaviour of existing code (deliberate, reviewed)

* `agents/nodes/understand_request.py`: the planner prompt now lists the tools of **every** registered domain (grouped), not only `general`'s. With `ERP_ADAPTER=none` only `general` exists, so the tool list is the same as before.
* `agents/domains/general.py`: the loop moved to `run_planned_tool_calls` (same behaviour; all existing tests pass unchanged).
* Graph: `verify_business_response` added. `tests/test_graph.py::test_graph_structure` was **extended** with the new node and its two edges (an exact-set assertion; nothing removed). Non-business turns keep their path and step count.
* Prompts: new versions `understand_request.system.v2` and `generate_response.system.v3` are now the defaults (business routing, result statuses, memory rule). v1/v2 are unchanged.
* `tests/conftest.py`: `ERP_*` variables are isolated like `AUTH_*` (test isolation only).

### 5. Tests run and exact results (2026-10-02)

| Run | Result |
|---|---|
| Baseline before Phase 10 (= Phase 9 final) | 537 passed, 4 skipped, 0 failed (541) |
| **Final full suite: PostgreSQL 16 + Redis 7 + Keycloak 26.4 + live invalid-key** | **661 passed, 4 skipped, 0 failed** (665 = 541 + 124 new; 2 min 50 s, no stall). Skipped: live Claude smoke, 2 production-IdP tests, POSIX SIGTERM on Windows. An earlier run spanning a host stall (35 min) had 3 reference-Keycloak failures (`invalid_grant`); that suite passed alone (12/12) and in this repeat, so the stalled run was discarded |
| Unit only, no servers | 555 passed, 110 skipped (75 s) |
| New tests | 124: `test_business_service.py` 33 · `test_business_tools.py` 27 · `test_business_graph.py` 16 · `test_erp_boundary_unit.py` 40 · `integration/test_erp_readonly_postgres.py` 8 |
| Separation | UNIT VERIFIED 40 · MOCK VERIFIED 76 · VERIFIED LOCALLY (throw-away PostgreSQL) 8 · **LIVE ERP: 0 (NOT VERIFIED)** |

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy --strict` (src, 140 files): Success · `mypy --strict scripts`: Success.

Host note: one run of `test_business_graph.py` reported 53 min wall time during a host stall (clock jump). The same 16 tests take 0.56 s; the stalled run was discarded.

### 6. Database impact

* **ERP: NONE.** No ERP was contacted. `inven-logic` / `inventoryai-postgres` were not started, read or copied.
* **Agent DB: NONE.** No migration (head stays `0005`), no new table. `business.read` audit rows use the existing `audit_log`.
* Local test PostgreSQL: throw-away databases `agentic_p10_erp_*` and roles `agentic_p10_reader_*` are created and dropped by the new integration test. `agentic_test` is used as before.

### 7. Files

**Created:** `src/agentic_platform/business/{__init__,capabilities,context,models,results,ports,service,factory,grounding}.py`, `business/adapters/{__init__,fake,postgres}.py`, `src/agentic_platform/tools/business.py`, `src/agentic_platform/agents/domains/business.py`, `src/agentic_platform/agents/nodes/verify_business_response.py`, `prompts/templates/understand_request.system.v2.md`, `prompts/templates/generate_response.system.v3.md`, `docs/BUSINESS_INTEGRATION.md`, `tests/business_helpers.py`, `tests/test_business_service.py`, `tests/test_business_tools.py`, `tests/test_business_graph.py`, `tests/test_erp_boundary_unit.py`, `tests/integration/test_erp_readonly_postgres.py`.

**Modified:** `core/config.py` (`ErpSettings`, `AppSettings.erp`), `core/production.py` (ERP checks), `agents/dependencies.py` (wiring, `AgentDependencies.business`), `agents/domains/base.py` (`run_planned_tool_calls`), `agents/domains/general.py`, `agents/nodes/__init__.py`, `agents/nodes/understand_request.py`, `agents/edges.py`, `agents/graph.py`, `.env.example`, `deploy/env/production.env.example` (`ERP_ADAPTER=none`), `tests/conftest.py`, `tests/test_graph.py`, `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.14), `ARCHITECTURE_DECISIONS.md` (ADR-059), `OPERATIONS_RUNBOOK.md` (§ 16, rule 6), `GO_LIVE_CHECKLIST.md`, `docs/SCHEMA_INTEGRATION.md`, `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md`.

### 8. Known limitations

1. **Live ERP NOT VERIFIED:** `PostgresErpAdapter` has no mapping; every capability is RULE_NOT_VERIFIED until Q1.
2. ERP tenant mechanism and warehouse assignment are unknown. Scope is all-or-nothing by permission unless a `WarehouseScopeResolver` is supplied.
3. Name matching semantics belong to the adapter. The fake's substring match is not a model of the ERP's.
4. The grounding check sees digits only (not "forty-five thousand" or "45k"). Numbers that happen to occur elsewhere in a result (dates, document counts) are accepted as sourced.
5. SUCCESS data is sent to the LLM provider to write the reply: **Q4** must allow this before live use.
6. The planner plans all tool calls at once. An AMBIGUOUS answer needs a second user turn (clarification), by design.
7. Business permissions and roles are provisional (Q8). Daily-sales "today" needs a confirmed `ERP_BUSINESS_TIMEZONE`.
8. Live Claude, the identity provider and the deployment target remain NOT VERIFIED (Q22, Q6, Q3).

### 9. External inputs still required

Q1 ERP access contract and schema mapping · Q4 data policy (business data to the LLM) · Q6 identity provider · Q22 Anthropic credential · Q3 deployment target · Q26 schema-level approval of each capability's mapping and rule · Q8 business roles.

### 10. Next step (not started)

Reassess once Q1 arrives. The first business capability phase would map the six capabilities to the verified ERP schema (one `ErpQuery` + row mapping each), run the live checklist and tenant-isolation tests with two real tenants, then the reference queries with live Claude. *(Phase 11 was later delivered as the database-independent reasoning foundation, § 3j.)*

## 3j. Phase 11: business intelligence and query reasoning foundation (database-independent)

**Scope** (stakeholder brief, 2026-10-03): make the business-query layer complete on the orchestration and reasoning side, using only the fake ERP. When the real schema arrives, only the adapter mapping should remain. Explicitly out of scope: any real ERP (including `inventoryai-postgres` and `C:\Users\Dell\Documents\Abc\inven-logic`), invented ERP tables or columns, ERP credentials, Anthropic credentials, business writes, and Phase 12. Details: [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md) § 10, ARCHITECTURE.md § A.15, ADR-060.

*(An earlier Phase 11 brief that required live ERP access stopped at its input gate the same day without changing the repository. This brief replaces it.)*

### 1. Implementation map (from the actual repository, before coding)

| Existing piece | How Phase 11 uses it |
|---|---|
| 6 capabilities / 6 READ tools / 4 `business.*` permissions | unchanged in number; optional typed arguments only (period, invoice number, all customers, warehouse filter, product filter) |
| `BusinessReadService` (permission re-check, context from the principal, deadline, resolution, re-validation, tenant check, scope filter, audit) | new paths go through the same `_run`; derived figures computed here only, rule-gated |
| `ErpReadPort` + fake / postgres adapters | `verified_rules`, `sales_by_day`, optional filters; postgres raises RULE_NOT_VERIFIED for all of them and vouches for no rule |
| `RequestUnderstanding` (planner output) | optional typed `business_query` |
| `verify_business_response` | widened: figure forms, hedges, action claims, unavailable asked-for figure; renders from the composed context |
| LangGraph checkpoint (`AgentState`) | `business_focus` (references only), state version 4 |
| `ClockService`, `ERP_BUSINESS_TIMEZONE` | the only source of "today"; relative periods need the zone |
| prompts | new defaults `understand_request.system.v3`, `understand_request.focus.v1`, `generate_response.system.v4` (earlier versions unchanged) |

### 2. Definition of done

| Item | Result | Evidence |
|---|---|---|
| Business query intent is typed | ✅ UNIT + MOCK VERIFIED | `business/intent.py`; smuggled tenant/user/scope/permission/SQL/table/column refused; intent → one validated call |
| Business date ranges are deterministic | ✅ UNIT VERIFIED | `business/dates.py`; 6 periods + month/year/Monday boundaries; zone decides "today"; no zone → RULE_NOT_VERIFIED |
| Business metrics are deterministic | ✅ UNIT VERIFIED | `business/metrics.py` (13 metrics, selection only), `MetricGap` reasons |
| Multi-record results | ✅ | open invoices (one or all customers), sales days, stock lines, low-stock items normalised as `RecordView`s |
| Safe aggregation | ✅ | service-only sums from complete single-currency lists; overdue / period / week sums rule-gated → RULE_NOT_VERIFIED |
| Ambiguous queries | ✅ | AMBIGUOUS + entity; safe clarification (display names only); candidates carried in the focus; choice by ref next turn |
| Not-found queries | ✅ | entity-specific fixed messages; hedged drafts replaced |
| Safe multi-step read workflows | ✅ MOCK VERIFIED | resolve → retrieve → filter → aggregate inside one typed read, with typed `steps`; several reads per plan; step limit enforced; no loop |
| Conversational follow-ups | ✅ MOCK VERIFIED | `business_focus` in the checkpoint; ERP re-read each turn; changed data wins |
| Memory cannot override business truth | ✅ MOCK VERIFIED | memory ₹50,000 vs fake ERP 45,000 (sales and customer), in 5 written forms |
| Response grounding enforced | ✅ | 45,000 / 45k / forty-five thousand / ₹45,000 / INR 45,000 accepted when true, replaced when from memory |
| All six capabilities against the fake ERP | ✅ 6/6 | tool-selection tests (customer, invoice, payment due, sales, product, inventory, low stock) |
| Tenant isolation | ✅ | tenant-wide lists, other tenant's refs/invoice numbers, tenant leak on new reads, tampered focus ref, concurrent tenants |
| Warehouse isolation | ✅ | out-of-scope warehouse named or by ref = NOT_FOUND; empty scope = UNAUTHORIZED before the ERP |
| Authorization | ✅ | registry `PERMISSION_DENIED` and service re-check on every new path |
| Arbitrary SQL rejected | ✅ | no SQL/table/column argument anywhere; injection-shaped refs, numbers and periods refused before the ERP |
| No write tool | ✅ | 6 READ tools; WRITE registration refused; write intents never compiled; false "PO bana diya" replaced |
| Fake ERP cannot run in production | ✅ | factory + production policy (unchanged), re-tested |
| Deterministic failure handling | ✅ | unavailable, timeout, hang, malformed, tenant leak, crash, rule not verified, invalid input, unknown capability: no figure, fixed message |
| Safe observability | ✅ | full-turn log scan: no names, refs, invoice numbers, amounts, currency or keys; intent logged as flags |
| Full regression / ruff / mypy strict | ✅ | § 5 |
| Documentation | ✅ | § 7 |
| **Real ERP** | **NOT VERIFIED**, intentionally deferred | Q1 |

### 3. Changed behaviour of existing code (deliberate, reviewed)

* **Fake data (additive):** gross/paid amounts on invoices; `TEST_INVOICE_002` due date moved to 2026-10-15 (not overdue on the test date; balance unchanged); a paid `TEST_INVOICE_003`; `TEST_INVOICE_006` and an outstanding record for Amit Sharma Traders; sales on 3 more days. The fake now lists only invoices with a balance (its "open" rule). `extended_test_data`, now served by the factory, adds a second "Rahul" and "Godown 3". The standard set keeps Phase 10's uniqueness ("Rahul", two "Godown"s), so Phase 10 tests run unchanged.
* **Replies:** a rejected draft is now rendered from the composed context (`render_context`, INR as ₹ with Indian grouping) instead of `render_results`, which stays as-is. Hedged drafts, action claims and drafts whose asked-for figure is unavailable are replaced too.
* **Routing:** a business write request (`business_query.write_requested`) is verified even though it runs no tool.
* **Tests changed:** `tests/test_business_tools.py::test_no_tool_accepts_identity_scope_or_sql_arguments`. Its exact allow-list of argument names was **extended** by exactly `period` and `invoice_number`, with a comment. The forbidden-word check is unchanged. No test was deleted or weakened otherwise.
* **Formatting only:** `ruff format` also reflowed `agents/checkpointer.py`, `agents/dependencies.py`, `agents/nodes/_common.py` and `agents/nodes/process_request.py` (layout only; ruff's formatter preserves the AST). No git history exists to restore their previous layout.

### 4. Reference queries (MOCK VERIFIED, TEST data, test date 2026-10-01 in a TEST time zone)

| Query | Capability / arguments | Fake ERP answer |
|---|---|---|
| "Rahul Gupta ka kitna bill pending hai?" | `customer_outstanding` / `customer_open_invoices` | ₹45,000 (2 open invoices) |
| "Usme se kitna overdue hai?" (follow-up) | `customer_open_invoices` `customer_ref` from focus, metric `overdue_amount` | ₹30,000 (TEST-INV-001) |
| "Rahul Gupta ke pending invoices ka total batao." | `customer_open_invoices`, metric `open_invoice_total` | ₹45,000, steps resolve → read → aggregate |
| "Kaunse invoices unpaid hain?" | `customer_open_invoices` (all customers) | 4 invoices, ₹65,000 |
| "Aaj ka total sales batao." | `daily_sales` `period=today` | ₹1,25,000 (7 documents) |
| "Is hafte ki sales?" | `daily_sales` `period=this_week` | ₹2,45,000 (2026-09-28 to 2026-10-01) |
| "Warehouse Godown 2 mein iPhone 15 ka stock?" | `product_stock` + `warehouse_name` | 3 pcs |
| "iPhone 15 ka stock low hai kya?" | `low_stock_products` + `product_name` | yes: 3 pcs, level 5 |
| "Rahul ka bill batao." | → AMBIGUOUS | asks: Rahul Gupta (TEST) or Rahul Verma (TEST) |
| "Stock kam hai, automatically purchase order bana do." | `unsupported`, `write_requested` | nothing runs; read-only explanation |
| "Rahul ne pichle mahine kitna payment kiya?" | no capability | `unsupported` (payment history is not one of the six) |

### 5. Tests run and exact results (2026-10-03)

| Run | Result |
|---|---|
| Baseline before Phase 11 (PostgreSQL 16 + Redis 7; reference Keycloak **stopped**) | 648 passed, 17 skipped, 0 failed (665) |
| **Final full suite** (same servers: the project's own `agentic-postgres` test database and `agentic-redis`; no ERP) | **811 passed, 17 skipped, 0 failed** (828 = 665 + 163 new; 2 min 13 s, no host stall: 11:41:59 → 11:44:15) |
| New tests | 163: `test_business_reasoning_unit.py` 84 · `test_business_reasoning_graph.py` 43 · `test_business_reasoning_security.py` 36 |
| Skipped (17) | live Claude ×2 (no credential, Q22), reference Keycloak ×12 (container stopped this session; it passed in Phase 10), production IdP ×2 (Q6), POSIX signals on Windows ×1 |

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy` (strict, src, 146 files): Success · `mypy --strict scripts`: Success.

**Latency** (fake ERP, scripted LLM, in-memory checkpointer, 200 runs; excludes Claude and any ERP): business read p50 0.06 to 0.08 ms; full business turn p50 17 ms, p95 49 ms. No uncontrolled loop: a business turn is always 4 graph steps, and planned calls beyond `AGENT_MAX_TOOL_CALLS_PER_TURN` are skipped.

### 6. Database impact

* **ERP: NONE.** No ERP contacted. `inventoryai-postgres` was not started (it stays `Exited`). `inven-logic` and the `new_agent` schema atlas were not read or used.
* **Agent DB: NONE.** No migration (head stays `0005`), no new table. The focus lives in the existing LangGraph checkpoint (state version 3 → 4, additive: older checkpoints have no focus).
* The test suite used the project's own `agentic_test` database and Redis db 15, as in earlier phases.

### 7. Files

**Created:** `src/agentic_platform/business/{rules,dates,metrics,intent,composition,focus}.py`, `prompts/templates/understand_request.system.v3.md`, `prompts/templates/understand_request.focus.v1.md`, `prompts/templates/generate_response.system.v4.md`, `tests/test_business_reasoning_unit.py`, `tests/test_business_reasoning_graph.py`, `tests/test_business_reasoning_security.py`.

**Modified:** `business/models.py`, `business/results.py` (`entity`), `business/ports.py`, `business/service.py`, `business/grounding.py`, `business/adapters/fake.py`, `business/adapters/postgres.py`, `tools/business.py`, `agents/schemas.py`, `agents/state.py`, `agents/edges.py`, `agents/domains/business.py`, `agents/nodes/understand_request.py`, `agents/nodes/generate_response.py`, `agents/nodes/verify_business_response.py`, `tests/test_business_tools.py` (allow-list extension, § 3); formatting only: `agents/checkpointer.py`, `agents/dependencies.py`, `agents/nodes/_common.py`, `agents/nodes/process_request.py`. Docs: `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.15), `ARCHITECTURE_DECISIONS.md` (ADR-060), `docs/BUSINESS_INTEGRATION.md` (§ 6, § 7, § 8, § 10), `docs/SCHEMA_INTEGRATION.md` (rules register), `OPERATIONS_RUNBOOK.md` (§ 16).

### 8. Known limitations

1. **Real ERP NOT VERIFIED** (Q1). Every Phase 11 feature answers RULE_NOT_VERIFIED through the postgres adapter until it is mapped and its rules are confirmed.
2. The production ERP's business time zone is unresolved. Tests use a TEST zone.
3. Numeric grounding checks that a number has a source, not that the sentence attributes it correctly (mitigated when the asked-for figure is unavailable). Lone small number words and Hindi number words are not read. Hedge and claim detection use phrase lists.
4. No live Claude run (Q22). The planner's structured output with the nested `business_query` is verified with a scripted LLM only.
5. No payment-history or customer-count capability. Such questions are `unsupported`.
6. One plan per turn. AMBIGUOUS needs the user's next turn (by design, no re-planning loop).
7. Checkpoints store Pydantic objects (as in earlier phases, now including the intent). LangGraph warns that its future strict msgpack mode will need an allow-list (`agents.schemas`, `tools.base`, `business.*`).
8. SUCCESS data still goes to the LLM provider to phrase the reply (Q4).

### 9. External inputs still required

Q1 ERP access contract and schema · Q4 data policy · Q6 identity provider · Q22 Anthropic credential · Q3 deployment target · Q8 business role catalogue.

### 10. Next step (not started)

Phase 11 stops here. Phase 12 has not been started. When Q1 arrives: map the six capabilities, `sales_by_day` and the Phase 11 filters in `PostgresErpAdapter`, declare only the rules the ERP owner confirms, set `ERP_BUSINESS_TIMEZONE` from the owner's answer, then run the live checklist and the reference queries with live Claude (Q22).

## 3k. Phase 12: Supervisor and multi-domain orchestration foundation

**Scope** (stakeholder brief, 2026-10-03): a production-oriented Supervisor that decides which domain handles a request (GENERAL, BUSINESS, or none), with typed routing decisions, a controlled domain registry, unsupported and multi-domain handling, routing limits, a common domain result contract, checkpoint-safe state, follow-up and topic-change routing. LangGraph orchestration only. Explicitly out of scope: any real ERP (including `inventoryai-postgres` and `C:\Users\Dell\Documents\Abc\inven-logic`), ERP schema mapping, business writes, purchase orders, automatic reorder, WhatsApp/SMS, accounting/GST writes, frontend, deployment, and Phase 13. Details: ARCHITECTURE.md § A.16, ADR-061, OPERATIONS_RUNBOOK.md § 17.

### 1. Implementation map (from the actual repository, before coding)

| Existing piece | How Phase 12 uses it |
|---|---|
| `understand_request` (one structured LLM call: domain + plan + `business_query`) | kept as the classifier. It gains optional `routing` hints and the `supervisor.routing` prompt. **No second LLM call** |
| `DomainRegistry` / `DomainHandler` (ADR-019) | the controlled registry of *implemented* domains; refuses reserved keys |
| `process_request` | becomes the domain router: dispatches on the Supervisor's decision, once, with a deadline |
| `DomainResult` | strengthened into the common domain result contract |
| `verify_business_response`, grounding, focus (Phases 10–11) | unchanged; the Supervisor sits in front of them |
| `Principal`, tool registry, business service permission checks | unchanged; the Supervisor never sees them |
| Checkpointers (`InMemorySaver`, `PostgresCheckpointSaver`) | now built with an explicit type allow-list serializer |
| Test harness (`ScriptedLLM` replays outputs in order) | unchanged; a deterministic Supervisor needs no extra scripted output |

### 2. Definition of done

| Item | Result | Evidence |
|---|---|---|
| Supervisor exists | ✅ | `agents/supervisor.py` (`Supervisor.decide`), node `supervise` |
| Typed SupervisorDecision | ✅ UNIT VERIFIED | outcome, domain, requested domain, reason, confidence, clarification flag, requested action, unavailable parts, typed routing metadata; `extra="forbid"`; no tenant/user/permission/SQL/table/tool-argument field (tested for 7 smuggled fields × 5 models) |
| Domain registry | ✅ | `agents/routing.py`: implemented `general`, `business`; reserved `purchase`, `sales`, `inventory`, `accounting`, `gst`, `knowledge`, `notification`; `DomainRegistry.register` refuses reserved keys |
| GENERAL routing | ✅ MOCK VERIFIED | the brief's 3 general examples → `general`, business handler 0 runs, no ERP read |
| BUSINESS routing | ✅ MOCK VERIFIED | the brief's 3 business examples → `business`, delegated (business handler 1 run, ERP read, writer call), answer kept |
| Unsupported domain handling | ✅ MOCK VERIFIED | WhatsApp reminder, automatic PO (3 plan shapes), unknown and invalid labels, business without ERP: fixed reply, nothing runs, no writer call, no "sent"/"bana diya" |
| Multi-domain requests | ✅ MOCK VERIFIED | read + PO: the read runs, a fixed "not done" notice is appended; read + send ("report bana ke supplier ko bhejo"): asks first, nothing runs |
| Supervisor cannot execute business tools / SQL | ✅ | the Supervisor gets only the registered domain *names*; import scan (no db, business service/adapters/ports, tools, llm, security, memory, sqlalchemy, asyncpg); no SQL vocabulary |
| Supervisor cannot bypass authorization | ✅ MOCK VERIFIED | routed to `business` without business permissions → `PERMISSION_DENIED`, no ERP read, `domain_outcome` UNAUTHORIZED; cross-tenant ref → NOT_FOUND; out-of-scope warehouse → no figure |
| Supervisor cannot override business truth | ✅ MOCK VERIFIED | NOT_FOUND reply kept verbatim; RULE_NOT_VERIFIED never becomes a figure; ₹45,000 kept; the partial notice is appended, never replaces; fixed texts have no figures, hedges or action claims |
| Memory boundary preserved | ✅ MOCK VERIFIED | a remembered ₹50,000 is replaced by the ERP's 45,000 on a supervised (partial) route; the previous domain is recorded but never decides |
| Follow-up routing / topic change | ✅ MOCK VERIFIED | "Uska overdue kitna hai?" stays `business` (focus ref, fresh read); "Waise LangGraph kya hota hai?" switches to `general` with no ERP read; `topic_changed` recorded |
| Routing recursion prevented | ✅ | 1 routing decision and 1 domain run per turn (enforced and tested in `supervise` and `process_request`); no edge back to `supervise` or `understand_request` (graph test) |
| Checkpoint state serializable | ✅ MOCK VERIFIED + VERIFIED LOCALLY | `orchestration`, `last_domain` are plain JSON (state v5); every checkpointer (in-memory and `PostgresCheckpointSaver`, also when built directly) uses an explicit msgpack allow-list; strict-mode run over 5 turn kinds: 0 blocked / 0 unlisted types; an unlisted type is blocked; the PostgreSQL 16 integration suite (checkpoint, resume, approval, API) runs with the allow-list |
| Existing Business tests | ✅ PASS | Phase 10/11 suites pass; only step-count assertions changed (§ 3) |
| Existing General tests | ✅ PASS | `test_graph.py` passes; structure/step assertions updated (§ 3) |
| Security tests | ✅ PASS | the brief's § 23 list, in `test_supervisor_unit.py` / `test_supervisor_graph.py` |
| Fake ERP test-only / real ERP untouched | ✅ | `inventoryai-postgres` stays `Exited`; nothing read from `inven-logic` or `new_agent` |
| Ruff / mypy strict / full suite | ✅ | § 5 |
| Documentation | ✅ | § 7 |

### 3. Changed behaviour of existing code (deliberate, reviewed)

* **Topology.** `understand_request → supervise → {process_request | generate_response | finalize_turn}`. Turns that ended after `generate_response` or `verify_business_response` now end through `finalize_turn`. Every successful turn takes two more steps.
* **Routing.** `process_request` dispatches on the Supervisor's decision, not on the planner's label. A planner label for a *reserved* domain (for example `purchase`), an unknown label, or `business` without an ERP now gets a **fixed reply without a writer call** (before: an LLM-written "not available" reply). The plain `unsupported` label keeps its Phase 1 LLM-explained reply. A typed business read capability labelled `general`/`sales`/`inventory` now goes to `business`. `understanding.domain` is overwritten with the routed domain or the sanitised label, so free text is never echoed in `AgentResponse.domain`.
* **Prompts.** New defaults `understand_request.system.v4` (partial-request routing; never route to business only because earlier messages were) and `supervisor.routing.v1` (appended; lists unavailable domains). v1–v3 are unchanged; the template variables of `understand_request.system` are unchanged.
* **`DomainResult`** gains `outcome`, `next_action`, `errors`, `metadata` (additive, defaults keep old constructors valid).
* **Checkpointers** use `checkpoint_serializer()` (explicit allow-list) instead of LangGraph's permissive default: `create_checkpointer` for the in-memory saver, and `PostgresCheckpointSaver` by default (a caller can still pass its own `serde`).
* **Tests changed (exact replacements, nothing removed or weakened):** step counts in `tests/test_graph.py` (3→5, 2→4, 3→5), `tests/test_business_graph.py` (4→6), `tests/test_business_reasoning_graph.py` (4→6); `test_graph_structure` gains the two nodes and asserts the new edges in place of `understand_request → process/generate`, `generate_response → END` and `verify_business_response → END`; `tests/test_edges.py::test_after_understanding` / `test_after_generation` assert the new targets (`supervise`, `finalize_turn`), and the clarification route they used to check is asserted on the new `test_after_supervision`. Each change carries a comment.
* **Line endings:** each modified `src/` and `tests/` file keeps its original line endings; new files use LF (the repository majority). `ARCHITECTURE.md`, `ARCHITECTURE_DECISIONS.md`, `OPERATIONS_RUNBOOK.md` and `.env.example` were rewritten with CRLF by the editing tool; their previous line endings could not be determined (no git history).

### 4. Routing examples (MOCK VERIFIED; scripted planner output, fake ERP TEST data)

| Request | Planner classification (scripted) | Supervisor decision | Result |
|---|---|---|---|
| "Rahul Gupta ka pending bill batao." | `business`, open-invoices call | ROUTE `business` (classified) | ₹45,000 (2 open invoices), answer kept |
| "Aaj ki sales kitni hai?" | `business`, intent `daily_sales` + `today` | ROUTE `business` | ₹1,25,000 |
| "Warehouse 2 mein product TEST_PRODUCT_001 ka stock kitna hai?" | `business`, `product_stock` refs | ROUTE `business` | 3 pcs |
| "LangGraph kya hai?" / list vs tuple / "Explain agentic AI." | `general` | ROUTE `general` | writer answer, no ERP read |
| "WhatsApp par Rahul ko payment reminder bhejo." | `unsupported` + `notification:send` | UNSUPPORTED_CAPABILITY (not_available) | fixed reply, nothing sent, 1 LLM call |
| "Purchase order automatically bana do." | `purchase` (or `unsupported` + `purchase:write`) | UNSUPPORTED_CAPABILITY | fixed reply, nothing created |
| "Stock kam hai aur supplier ko purchase order bhi bana do." | `business` low-stock call + `purchase:write` | ROUTE `business` (partial_route) | low-stock answer + "Note: part of your request was not done …" |
| "Inventory ka report bana ke supplier ko bhejo." | `business` stock call + `notification:send` | CLARIFY (send_not_available) | asks whether to only show it; nothing runs |
| "Uska overdue kitna hai?" (after the pending bill) | `business`, follow-up with focus ref | ROUTE `business`, follow_up | ₹30,000 (fresh read) |
| "Waise LangGraph kya hota hai?" (same thread) | `general` | ROUTE `general`, topic_changed | general answer, no ERP read |

What these prove is the orchestration given each classification. Whether the real model produces these classifications is **not verified** (no live Claude, Q22).

### 5. Tests run and exact results (2026-10-03)

| Run | Result |
|---|---|
| Baseline before Phase 12 (= Phase 11 final) | 811 passed, 17 skipped, 0 failed (828) |
| **Final full suite** (PostgreSQL 16 `agentic_test` + Redis 7 db 15, the project's own containers; reference Keycloak stopped; no ERP) | **916 passed, 17 skipped, 0 failed** (933 = 828 + 105 new; 2 min 3 s, no host stall: 17:44:07 → 17:46:13). Integration tests alone: 93 passed (62 s), with the allow-list serializer as the PostgreSQL saver's default |
| New tests | 105: `test_supervisor_unit.py` 62 · `test_supervisor_graph.py` 41 · `test_edges.py` +2 (`test_after_supervision`, `test_after_verification`) |
| Skipped (17) | live Claude ×2 (Q22), reference Keycloak ×12 (container stopped), production IdP ×2 (Q6), POSIX signals on Windows ×1. Same as Phase 11 |

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy` (strict, src, 151 files): Success · the three new/rewritten test files also pass `mypy --strict`.

**Host note:** the machine stalled several times during this phase (wall-clock jumps of about 13, 50 and 88 minutes, with no test progress and no faulthandler dump). One stalled run overlapped a second pytest process and was stopped; one was stopped because it ran on code that had since changed. All of those runs were discarded. Every reported number comes from a stall-free run on the final code.

**Overhead (§ 29).** Scripted LLM, fake ERP, in-memory checkpointer, 300 turns per run after 20 warm-up turns, before (pre-Phase-12 snapshot) and after interleaved twice, all four runs stall-free:

| Turn | Steps before → after | p50 before | p50 after |
|---|---|---|---|
| general (one system tool) | 3 → 5 | 22.7 / 23.7 ms | 25.0 / 24.8 ms |
| business (one ERP read) | 4 → 6 | 25.1 / 28.1 ms | 28.6 / 21.4 ms |

`Supervisor.decide()` + JSON state: 14–16 µs. The added cost is the two graph steps (about 1–2 ms on general turns; within noise on business turns) plus, with a PostgreSQL checkpointer, two more checkpoint writes per turn (not measured here). **No extra LLM call**: a separate LLM router would have added a full model round trip per turn (ADR-061). A single-domain request runs exactly one domain handler (tested by counting handler runs).

### 6. Database impact

* **ERP: NONE.** No ERP contacted. `inventoryai-postgres` was not started (it stays `Exited`). `inven-logic` and the `new_agent` schema atlas were not read or used.
* **Agent DB: NONE.** No migration (head stays `0005`), no new table. The new state fields live in the existing LangGraph checkpoint (state version 4 → 5, additive). Older checkpoints simply have no `orchestration` / `last_domain`, and per-turn `orchestration` is reset each turn.
* The test suite used the project's own `agentic_test` database and Redis db 15, as in earlier phases.

### 7. Files

**Created:** `src/agentic_platform/agents/{routing,supervisor,serialization}.py`, `src/agentic_platform/agents/nodes/{supervise,finalize_turn}.py`, `src/agentic_platform/prompts/templates/understand_request.system.v4.md`, `src/agentic_platform/prompts/templates/supervisor.routing.v1.md`, `tests/test_supervisor_unit.py`, `tests/test_supervisor_graph.py`.

**Modified:** `agents/schemas.py` (`RequestUnderstanding.routing`, `DomainResult` contract, `DomainOutcome`), `agents/state.py` (v5: `orchestration`, `last_domain`), `agents/graph.py`, `agents/edges.py`, `agents/nodes/__init__.py`, `agents/nodes/process_request.py` (domain router), `agents/nodes/understand_request.py`, `agents/nodes/generate_response.py`, `agents/domains/base.py` (reserved-key guard, `with_tool_outcome`), `agents/domains/business.py` (`business_outcome`), `agents/domains/general.py` (catalogue constant), `agents/checkpointer.py` (allow-list serializer; the PostgreSQL saver's default), `core/config.py` (`AGENT_DOMAIN_TIMEOUT_SECONDS`), `core/errors.py` (`OrchestrationError`, `OrchestrationLimitError`, `DomainTimeoutError`), `.env.example`, `tests/test_graph.py`, `tests/test_edges.py`, `tests/test_business_graph.py`, `tests/test_business_reasoning_graph.py` (§ 3). Docs: `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.16, header, A.4 routing row), `ARCHITECTURE_DECISIONS.md` (ADR-061; index rows for ADR-060/061; ADR-003 status), `OPERATIONS_RUNBOOK.md` (§ 17, header, signals table).

### 8. Known limitations

1. **Real ERP NOT VERIFIED** (Q1). All business routing is verified against the fake ERP only.
2. **Live classification NOT VERIFIED** (Q22). The Supervisor is deterministic and fully tested, but its input is the planner's classification, which is scripted in tests. Whether Claude fills `domain` and `routing.unavailable` as the prompts ask has not been observed.
3. The planner still sees the tool descriptions of every domain the user may use, because classification and planning remain one call. Execution is restricted per domain (handler + registry); a separate routing call would narrow what the model sees at the cost of a round trip (ADR-061).
4. Two domains never *run* in one turn. A mixed request's unsupported part is reported, or the user is asked first (read + send). Sequencing two implemented domains would need a new phase.
5. `confidence` is recorded, not acted on. The planner asks for clarification itself when unsure.
6. The partial-route notice and the fixed replies are in English, whatever the user's language.
7. Claim detection for partial *general* replies reuses the Phase 11 phrase list (`claims_action`): regex, not NLP; it fails closed by replacing the reply.
8. The checkpoint allow-list must be extended when a new non-JSON type enters `AgentState`; the strict-mode test fails until it is.
9. SUCCESS business data still goes to the LLM provider to phrase replies (Q4).

### 9. External inputs still required

Q1 ERP access contract/schema · Q4 data policy · Q6 identity provider · Q22 Anthropic credential · Q3 deployment target · Q8 business role catalogue.

### 10. Next step (not started)

Phase 12 stops here. Phase 13 has not been started. Live verification of routing needs Q22 (observe the planner's classification of the § 4 examples with Claude). A future domain (for example Purchase) is added by implementing its handler, moving its key out of `RESERVED_DOMAINS`, and documenting its business rules (Q9, Q8, Q17).

## 3l. Phase 13: production observability, reliability and agent evaluation foundation

**Scope** (stakeholder brief, 2026-10-05): make the existing agent observable, diagnosable, measurable, reproducible, resilient and testable: structured events, request/turn/node tracing, error classification, retry and timeout behaviour, correlation ids, safe metrics, a deterministic evaluation dataset, golden regression tests, failure injection, reliability/security regression, a performance baseline and safe diagnostics. Explicitly out of scope: any real ERP (`inventoryai-postgres` stayed `Exited`; `inven-logic` and `new_agent` were not read), ERP migrations, business writes of any kind, WhatsApp/SMS, automatic reorder, a second LLM routing call, deployment, and Phase 14. Details: ARCHITECTURE.md § A.17, ADR-062, OPERATIONS_RUNBOOK.md § 18.

### 1. Implementation map (from the actual repository, before coding)

| Existing piece | What Phase 13 found | What Phase 13 did |
|---|---|---|
| `core/logging.py` (JSON logs, `log_context` contextvar), request ids (ADR-045) | correlation by request/thread only; no turn or span id; no scrubbing of log lines | extended, not replaced: contextvar `TraceContext` adds `turn_id`, `span_id`, `parent_span_id`; both formatters now scrub credentials |
| `instrumented_node` (`node.started/completed/failed` logs) | timing in logs, no typed events or categories | each node is a stage span (`planner.*`, `supervisor.*`, `domain.*`, ...); old log lines kept |
| Audit (`AuditEvent`, `SecurityAudit`) | authoritative security record | unchanged; observability events are a separate type with no identity fields |
| Error hierarchy (`core/errors.py`, stable public codes) | no operational grouping | `ErrorCategory` computed from codes/types; API contract unchanged; 2 checkpoint errors added |
| Retries | only inside the Anthropic SDK (`LLM_MAX_RETRIES`); none elsewhere | documented policy; ERP reads retried on `ERP_UNAVAILABLE` only, within one deadline |
| Timeouts | `asyncio.timeout` (domain), `asyncio.wait_for` (tools, business reads) | **defect:** both let a coroutine that swallows its cancellation return a result after the deadline (Python 3.11). Replaced by `strict_deadline` |
| `ToolRegistry._validate` | failure detail quoted pydantic errors **with input values** into `tool.call.failed` logs | **defect fixed:** locations and error types only |
| `handle_error` | reached without a recorded error when an edge rejected a tampered decision: a failed turn with an **empty `errors` list** | **gap fixed:** records `ORCHESTRATION_FAILED` |
| Checkpointers + Phase 12 allow-list | no load/save visibility; serializer failures surfaced as uncategorised crashes | instance-level instrumentation (class unchanged), serde listener for blocked types, classified errors; allow-list unchanged |
| Test harness (`ScriptedLLM`, fake ERP failure modes) | no transient failure, no swallowed cancellation | fake ERP gains `transient_failures`, `swallow_cancel`, `swallow_cancel_always` (test data only) |

### 2. Definition of done

| Item | Result | Evidence |
|---|---|---|
| Observability architecture (request → turn → node/domain/tool/LLM/checkpoint spans) | ✅ MOCK VERIFIED | `observability/hub.py`; lifecycle order, id consistency and span nesting asserted in `test_observability_graph.py` |
| Typed structured events, all brief § 5 lifecycle events | ✅ UNIT + MOCK VERIFIED | `observability/events.py` (`EventName`: request, auth, authz, turn ×5, 8 stages × started/completed/failed, domain run, tool, LLM, business read, retry, timeout, checkpoint ×5) |
| Observability vs security audit kept separate | ✅ | `ObsEvent.kind="observability"`, no tenant/user/actor/hash field; audit unchanged; test shows an audit row (with tenant/user) and an event (without) for the same read |
| Safe logging / redaction policy with tests proving secrets are not emitted | ✅ UNIT + MOCK VERIFIED | `core/redaction.py`; JSON and text formatters (incl. tracebacks), event attributes, a full turn whose user text contains a password, an API key and a bearer JWT |
| Error taxonomy (16 categories), deterministic, API contract stable | ✅ UNIT VERIFIED | every `AgentPlatformError` code maps to a category (only `PLATFORM_ERROR`/`AGENT_STATE_ERROR` are `internal` by design); `PublicError` fields unchanged |
| Retry policy + 6 deterministic tests | ✅ UNIT + MOCK VERIFIED | first-attempt success, transient→success, exhaustion, non-retryable, timeout not retried, deadline stops retry, cancellation never retried |
| Timeout behaviour | ✅ UNIT + MOCK VERIFIED | swallowed cancellation can no longer succeed (domain, tool, ERP read); no background survivor; turn ends via `handle_error`, nothing pending, `last_domain` not recorded |
| Metrics (requests, latency, LLM, graph, business, security) with no high-cardinality labels | ✅ UNIT VERIFIED; no exporter | `observability/metrics.py`: 20 declared metrics; undeclared label keys raise; ≤200 series per metric; snapshot of a business turn contains no tenant, user, name or request id |
| Evaluation dataset without an API key | ✅ 36/36 MOCK VERIFIED | `tests/evaluation/cases.py`: general 3, business 7, follow-up 4, multi-domain 4, safety 8, reliability 10; each states route, tools, ERP access, outcome, error category, invariants |
| Golden regression suite (behaviour, not wording) | ✅ | `tests/evaluation/golden/golden_snapshots.json` (36 snapshots); repeatability test; no snapshot has a write |
| Failure injection (test infrastructure only) | ✅ MOCK VERIFIED + real clients on a closed port | ERP unavailable/timeout/not configured/malformed/crash/hang, tool crash, model failure/refusal, writer failure after a read, malformed planner output, tampered supervisor decision, checkpoint serialization and load failure, **Redis unavailable** and **PostgreSQL unavailable** (real clients, 127.0.0.1:1) |
| Checkpoint reliability (brief § 14) | ✅ | valid load, unlisted type blocked + `checkpoint.rejected`, corrupted bytes → `CHECKPOINT_UNREADABLE` (no chained content), save failure → `CHECKPOINT_SERIALIZATION_FAILED`, store outage keeps `DATABASE_UNAVAILABLE`, resume re-checks *current* permissions, failed turns leave no success state; allow-list still 9 types |
| Security regression (brief § 15) | ✅ | Phase 5/10/11/12 suites pass unchanged; new: user text/planner output cannot grant permissions, tenant/SQL tool arguments refused, cross-tenant and cross-warehouse results, memory never beats ERP, secrets absent from logs/events, audit/observability separation, resume with downgraded role refused |
| Performance baseline | ✅ VERIFIED LOCALLY (not capacity) | § 4 |
| Diagnostics | ✅ | `Agent.diagnose(request_id)`, CLI `--trace`, `python -m agentic_platform.observability.diagnose --request-id <id> < log` (rebuilds the tree from JSON logs) |
| Real ERP untouched, no business writes, no second LLM call | ✅ | `ObservedLLM` adds no call (asserted: 2 model calls per business turn) |
| Ruff / mypy strict / full suite | ✅ | § 5 |

### 3. Changed behaviour of existing code (deliberate, reviewed)

* **Deadlines** (`process_request`, `ToolRegistry._invoke`, `BusinessReadService._run`): `strict_deadline` instead of `asyncio.timeout` / `asyncio.wait_for`. Same limits and same public codes (`DOMAIN_TIMEOUT`, `TOOL_EXECUTION_FAILED`, `UNAVAILABLE/ERP_TIMEOUT`); the difference is that a result produced after the deadline is now discarded.
* **ERP reads** retry once (default `ERP_READ_MAX_ATTEMPTS=2`, backoff 0.1 s) on `ERP_UNAVAILABLE` only, inside the unchanged `ERP_QUERY_TIMEOUT_SECONDS`. An unreachable fake ERP is now called twice per read (no existing test asserted the count).
* **Tool validation detail** no longer contains input values (public message unchanged).
* **`handle_error`** records `ORCHESTRATION_FAILED` when reached with no recorded error (before: failed response with empty `errors`).
* **Checkpoint failures** that are not platform errors become `CHECKPOINT_UNREADABLE` (load) / `CHECKPOINT_SERIALIZATION_FAILED` (save) instead of a generic crash (`INTERNAL_ERROR` / `TURN_INCOMPLETE`). Store outages keep their `DATABASE_*` codes (existing test unchanged and passing).
* **LLM provider** is wrapped in `ObservedLLM` in `build_dependencies` (attribute access is delegated, so test doubles such as `ScriptedLLM.calls` still work).
* **Logs:** one extra INFO line per stage/tool/LLM call (`agentic_platform.observability`; checkpoint events at DEBUG), switchable with `OBSERVABILITY_LOG_EVENTS=false`. All Phase 1–12 log lines are kept.
* **`Agent.arun`** generates the request id before validating input (so a rejected call has a request id in its `turn.rejected` event); the id value and the exceptions raised are unchanged.
* **Fake ERP** (test data only): `transient_failures`, `swallow_cancel`, `swallow_cancel_always`.
* **Tests changed (exact, nothing removed or weakened):** `tests/test_supervisor_graph.py::test_supervisor_logs_are_safe` collected every log line starting with `supervisor.`; the new `supervisor.started/completed/decision` events (from the observability logger) matched it. The set is now limited to non-observability loggers (smallest change), and the test **adds** a check that the observability lines contain no names, figures, refs, user text, tenant or user. The original assertions are unchanged.
* **Line endings:** each modified file keeps its original line endings (CRLF: `core/errors.py`, `agents/agent.py`, `agents/nodes/_common.py`, `agents/nodes/process_request.py`, `.env.example` and the four root docs); new files use LF.

### 4. Performance baseline (VERIFIED LOCALLY; not a capacity claim)

Machine: Windows 11 Pro (10.0.26200), Intel 12-core (Family 6 Model 154), Python 3.11.9; Docker Desktop running (project PostgreSQL/Redis idle). Scripted model (no network), fake ERP in-process, `InMemorySaver` with the allow-list serializer, logging at WARNING, agent memory **off** (see below). 200 measured turns after 20 warm-up turns per scenario; `python -m tests.perf.benchmark`. Three runs: run 1 was disturbed by host noise (the run took 49 s instead of 25 s, first three scenarios 4× slower) and is discarded. Runs 2 and 3 (12:45 and 12:46):

| Scenario | median (run 2 / 3) | p95 (run 2 / 3) | graph overhead median* |
|---|---|---|---|
| general turn | 12.9 / 13.4 ms | 20.1 / 36.2 ms | 8.6 / 8.9 ms |
| general turn, no checkpointer | 10.8 / 10.9 ms | 17.4 / 14.8 ms | 6.4 / 6.5 ms |
| business turn (one ERP read, grounding) | 15.7 / 16.2 ms | 21.1 / 28.1 ms | 9.8 / 10.1 ms |
| business follow-up ("Uska overdue") | 16.8 / 17.1 ms | 22.2 / 33.6 ms | 10.7 / 10.9 ms |
| multi-domain unsupported (fixed reply) | 10.1 / 10.7 ms | 14.8 / 14.7 ms | 6.3 / 6.8 ms |
| Supervisor decision alone | 0.010 ms | 0.015 ms | — |
| fake ERP tool call alone (registry + service + fake) | 0.39 / 0.42 ms | 0.65 / 0.68 ms | — |

\* turn duration minus the sum of node durations: LangGraph scheduling plus checkpoint writes between steps. **Checkpoint overhead** (in-memory saver vs none): 2.1 / 2.4 ms per general turn. **Retry overhead** (one transient ERP failure, backoff 0): 0.03–0.06 ms plus the configured backoff (0.1 s by default). **Observability cost**: 0.033 ms per emitted event × 41 events per business turn ≈ 1.3–1.4 ms per turn (most of it the 15 checkpoint events, logged at DEBUG).

**Memory finding (pre-existing, not Phase 13):** with agent memory on, the in-memory episodic adapter (development/tests only; production uses pgvector) recalls by a linear cosine scan over all stored episodes, so latency grows with every turn: 14.7 ms → 21.5 ms median from the first to the last quarter of 200 turns, and 17 ms → 123 ms over 400 turns in one profiling run. Phase 12's overhead figures (300 turns, memory on) include this growth, so they are not comparable with the table above.

### 5. Tests run and exact results (2026-10-05)

| Run | Result |
|---|---|
| Baseline before Phase 13 (= Phase 12 final) | 916 passed, 17 skipped, 0 failed (933) |
| **Final full suite** (PostgreSQL 16 `agentic_test` + Redis 7 db 15, the project's own containers; reference Keycloak stopped; no ERP) | 1107 passed, 17 skipped, 0 failed (1124 collected = 933 + 191 new; 3 min 1 s, 13:58:32 → 14:01:38, no host stall) |
| New tests | 191: `test_observability_unit.py` 82 · `test_observability_graph.py` 20 · `test_failure_injection.py` 13 · `tests/evaluation/test_evaluation_suite.py` 76 (36 expectation + 36 golden + 4 dataset/repeatability) |
| Evaluation scorecard (`python -m tests.evaluation.run`) | 36/36: general 3/3, business 7/7, follow-up 4/4, multi-domain 4/4, safety 8/8, reliability 10/10 |
| Skipped (17) | live Claude ×2 (Q22), reference Keycloak ×12 (container stopped), production IdP ×2 (Q6), POSIX signals on Windows ×1. Same as Phase 12 |

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy` (strict, src, 163 files): Success · the new test modules (`tests/test_observability_*.py`, `tests/test_failure_injection.py`, `tests/evaluation`, `tests/perf`) also pass `mypy --strict`.

**Host note:** one full run (12:49) produced no output and was stopped at its 30-minute limit, with the wall clock jumping about an hour; a second (13:48) took 8 min 30 s with a subprocess reporting a *negative* timeout (clock jump) and 2 timing-related failures (`test_deployment_unit.py::test_gate_refuses_unsafe_variants_of_the_template[change1-REDIS_REQUIRED]`, the subprocess timeout; `safety_cross_tenant_result`, a 2 s ERP deadline crossed by the stall). Both passed 3/3 in isolation. The evaluation harness's default ERP deadline was raised to 10 s so a stall cannot turn a normal read into a timeout. Both stalled runs were discarded; the reported result is the clean run 13:58:32 → 14:01:38 on the final code. An earlier clean run (12:32 → 12:36, before the last test-only changes) gave the same 1107 / 17 / 0.

**Defects found by the new suites and fixed in this phase:** (1) a business read whose dependency swallowed the deadline's cancellation crashed on the result-consistency check (`data` assigned inside the deadline block); safe (no figure) but misclassified, fixed. (2) a failed turn with an empty `errors` list after a tampered decision (`handle_error`, § 3). (3) tool-validation logs quoting input values (§ 3). (4) the Python 3.11 swallowed-cancellation timeout gap (§ 3).

### 6. Database impact

* **ERP: NONE.** No ERP contacted or started. `inventoryai-postgres` was checked after Docker Desktop started: `Exited (0) 3 days ago`, untouched. `inven-logic` and the `new_agent` schema atlas were not read.
* **Agent DB: NONE.** No migration (head stays `0005`), no new table, no state version change (state v5; no new state field). Observability data is in-process only (logs, metrics, trace buffer).
* The test suite used the project's own `agentic_test` database and Redis db 15, as in earlier phases. Docker Desktop was started for that; the project's `agentic-postgres` and `agentic-redis` came up through their own restart policy.

### 7. Files

**Created:** `src/agentic_platform/core/redaction.py`; `src/agentic_platform/observability/{__init__,events,hub,taxonomy,metrics,retry,deadline,checkpoints,llm,diagnostics,diagnose}.py`; `tests/test_observability_unit.py`, `tests/test_observability_graph.py`, `tests/test_failure_injection.py`; `tests/evaluation/{__init__,harness,cases,run,test_evaluation_suite}.py`, `tests/evaluation/golden/golden_snapshots.json`; `tests/perf/{__init__,benchmark}.py`.

**Modified:** `core/errors.py` (`CheckpointError`, `CheckpointSerializationError`), `core/logging.py` (redaction), `core/config.py` (`ObservabilitySettings`; `ERP_READ_MAX_ATTEMPTS`, `ERP_READ_RETRY_BACKOFF_SECONDS`), `agents/agent.py` (turn scope, lifecycle events, checkpoint instrumentation, `metrics`, `diagnose`), `agents/dependencies.py` (`ObservedLLM`), `agents/nodes/_common.py` (stage spans), `agents/nodes/process_request.py` (domain-run span, strict deadline), `agents/nodes/supervise.py` (`supervisor.decision`), `agents/nodes/verify_business_response.py` (`verification.result`), `agents/nodes/handle_error.py` (§ 3), `tools/registry.py` (tool spans, strict deadline, validation detail), `business/service.py` (retry, strict deadline, `business.read`), `business/adapters/fake.py` (failure modes), `security/authorization.py` (`authz.denied` event), `api/dependencies.py` (`auth.*` events), `__main__.py` (`--trace`), `.env.example`, `tests/test_supervisor_graph.py` (§ 3). Docs: `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.17, header), `ARCHITECTURE_DECISIONS.md` (ADR-062, index row, header), `OPERATIONS_RUNBOOK.md` (§ 18, header).

### 8. Known limitations

1. **Real ERP NOT VERIFIED** (Q1); everything business-related runs against the fake ERP. **Live model NOT VERIFIED** (Q22): the evaluation scripts the planner's classification; whether Claude produces those classifications is not measured.
2. **No metrics exporter and no distributed tracing.** Metrics and traces are per process and reset on restart; multiple API replicas each have their own. Events are JSON log lines, so a log platform can aggregate them. Prometheus/OpenTelemetry need the deployment decision (Q3).
3. **No HTTP diagnostics endpoint** (deliberate, ADR-062): there is no platform-operator role, and a global view would cross tenants. Diagnostics are operator tooling (CLI, `Agent.diagnose`, log reconstruction).
4. **Token usage** is recorded only when the provider reports it. `ScriptedLLM` reports none, so token metrics are untested against real numbers.
5. **Cancellation-ignoring code:** Python cannot force-stop a coroutine that ignores cancellation repeatedly. The innermost deadline then cannot return on time; the next deadline out (tool, then domain) ends it, and the outcome is a failure, never a success (tested). An ERP driver behaving like that would hold the turn up to the tool timeout.
6. **Redaction is pattern-based for free text** (log messages, exception text): it masks known credential shapes, not arbitrary secrets. Event metadata is allow-listed and safe by construction.
7. **Per-turn event volume**: about 40 events per business turn (most are checkpoint events at DEBUG); about 1.3 ms of local overhead per turn.
8. **Golden snapshots** must be regenerated after a reviewed behaviour change (`python -m tests.evaluation.run --update-golden`); they record structure, not wording.
9. **In-memory episodic memory** (development only) slows linearly with stored episodes (§ 4). Production uses pgvector; not changed in this phase.
10. Retry backoff is deterministic without jitter (one process, few callers). Jitter should be added if the ERP is shared by many replicas.

### 9. External inputs still required

Q1 ERP access contract/schema · Q22 Anthropic credential (live classification, token metrics) · Q3 deployment target (metrics exporter, tracing collector, log platform) · Q6 identity provider · Q4 data policy · Q8 business role catalogue.

### 10. Next step (not started)

Phase 13 stops here. Phase 14 has not been started. With Q22, the evaluation dataset can be run against the live planner (replace the scripted classifications) to measure real routing accuracy; with Q3, wire the event stream to the chosen metrics/tracing backend.

## 3m. Phase 14: production ERP integration readiness and real ERP adapter — BLOCKED at the input gate (Q1)

**Scope** (stakeholder brief, 2026-10-05): prepare and, **only if the ERP access contract (Q1) is verified**, implement the real ERP read adapter. Not allowed: inventing an ERP schema, assuming `inventoryai-postgres` is the ERP, inferring rules from table names, ERP migrations, writes of any kind, Phase 15. Details: ARCHITECTURE.md § A.18, ADR-063, OPERATIONS_RUNBOOK.md § 19, [docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md).

### 1. Input gate result

**Q1 is missing.** No contract, owner confirmation, host, database name, role, credential, tenant/warehouse mechanism, time-zone rule, business rule, status list, mapping or schema version has been supplied. There is no `.env`, no `ERP_*` variable and no contract file anywhere in the repository. `python -m scripts.erp_readiness` reports **BLOCKED, all 20 items (A–T) missing**. Following the brief, the real ERP integration stopped at the gate: **no ERP was contacted**, and no schema, mapping, rule or reader was written. Phase 14 delivered only what the gate allows: contract validation, the schema-mapping framework, adapter interfaces, configuration validation, security checks, the integration test harness, documentation and fake-ERP compatibility.

### 2. Implementation map (from the actual repository, before coding)

| Existing piece | Phase 14 finding | Phase 14 change |
|---|---|---|
| `PostgresErpAdapter` (Phase 10) | no mapping; every capability RULE_NOT_VERIFIED without connecting | kept fail-closed; adds per-capability readers that can be registered only for VERIFIED mappings (none exist) |
| `ReadOnlyErpDatabase.verify` | checked name, Agent DB, read-only session, superuser, BYPASSRLS | adds effective-privilege checks (writes, CREATE, ownership, elevated roles, CREATEDB/ROLE/REPLICATION) and, with a contract, version/schema/type/tenant/warehouse checks |
| `ReadOnlyErpDatabase.fetch` | readers would have had to bind the tenant themselves | the principal's tenant is mandatory and bound by the database layer (column or RLS setting); no contract → no business read |
| `check_read_only_sql` / `ErpQuery` | in `adapters/postgres.py` | moved to `adapters/sql_guard.py` (re-exported; behaviour unchanged) |
| `scripts/inspect_erp_security.py` (Phase 9) | full operator report, never run on an ERP | reused unchanged by the readiness tool's live path |
| Production policy | `ERP_ADAPTER=postgres` needed URL + expected database | also needs a loadable, VERIFIED contract matching database and time zone |
| Fake ERP | no cancelled invoices | `cancelled_invoices` (TEST rule: never open), used by the contract suite |
| Business service, tools, grounding, Supervisor, observability | unchanged | `business.read` events already carry capability, adapter, status, reason, attempts, duration |

### 3. Definition of done (as far as the gate allows)

| Item | Result | Evidence |
|---|---|---|
| Hard input gate | ✅ BLOCKED reported, nothing fabricated | `scripts/erp_readiness.py` (exit 3, A–T listed); `--live` refused while BLOCKED |
| Contract validation | ✅ UNIT VERIFIED | `business/erp_contract.py`: 60 unit tests in `tests/test_erp_contract_unit.py` (completeness, consistency, fixed permissions, data minimization, no SQL fragments, no credentials, IANA zone, closed status list) |
| Schema mapping framework | ✅ framework only; mappings **BLOCKED** | contract capability mappings (15 items each) + catalog verification; register in docs/erp/ERP_CONTRACT.md § 3 is "TO BE SUPPLIED" |
| ERP security gate (brief § 4) | ✅ UNIT + VERIFIED LOCALLY on a throw-away PostgreSQL 16 | refuses: Agent DB, unexpected name, superuser, BYPASSRLS, write privileges, owned relations, CREATE, elevated membership, non-read-only session, missing/unprovable tenant or warehouse isolation, schema/type/version mismatch (`tests/integration/test_erp_gate_postgres.py`) |
| Tenant isolation mechanism | ✅ both supported mechanisms proven on throw-away PostgreSQL | column: a caller-supplied tenant is overwritten, a query without `:tenant` refused; RLS: binding alone returns only the principal's tenant |
| Warehouse isolation | ✅ contract + gate (column required for stock); policy NOT_FOUND per ADR-059; fake suite proves allowed / hidden / cross-tenant same ref | real ERP mechanism BLOCKED (Q1 I) |
| Read-only, parameterised, explicit SQL | ✅ | guard unchanged; gate queries bind privilege names; readers must be explicit `ErpQuery` constants; nothing from LLM/user input |
| Data minimization | ✅ | a contract column must fill a platform record field or be a filter (tenant/warehouse/status/date/join); record shapes carry no address/phone/email/GST |
| Fake ERP compatibility + common contract suite | ✅ 18/18 against the fake; real-ERP run skipped (Q1) | `tests/erp_contract/` (mutation-checked: removing the cancelled-invoice rule fails 2 checks) |
| Real ERP safety test (brief § 15) | ✅ tool ready, verified on throw-away PostgreSQL; **NOT RUN on an ERP** (no Q1) | readiness `--live`: inspector PASS + gate PASS → exit 0; a `DELETE` grant stops before any read |
| Configuration | ✅ | `ERP_CONTRACT_PATH`; default stays `ERP_ADAPTER=none`; production refuses incomplete/unverified/mismatching contracts |
| Grounding, observability | ✅ unchanged and passing | Phase 11/13 suites, evaluation 36/36 |
| Business rules, time zone | **NOT VERIFIED** | nothing assumed (not even Asia/Kolkata); the TEST contract's zone is test data |

### 4. Changed behaviour of existing code (deliberate, reviewed)

* `ReadOnlyErpDatabase.verify` runs the privilege gate on every first connection (also without a contract). A role with any write privilege, CREATE, owned relation or elevated membership is now refused where Phase 10 accepted it.
* `ReadOnlyErpDatabase.fetch` takes a mandatory keyword `tenant_id` and refuses business reads without a contract's tenant mechanism. No caller existed (no reader), so nothing in the running system changes: `ERP_ADAPTER=postgres` still answers RULE_NOT_VERIFIED without connecting.
* `PostgresErpAdapter(database, contract=None)`: `verified_rules` comes from a VERIFIED contract (empty otherwise, as before).
* Production policy: new errors `ERP_CONTRACT_MISSING`, `ERP_CONTRACT_INVALID`, `ERP_CONTRACT_NOT_VERIFIED`, `ERP_CONTRACT_DATABASE_MISMATCH`, `ERP_TIMEZONE_MISMATCH`; warning `ERP_NO_VERIFIED_CAPABILITY`.
* Fake ERP: `TenantData.cancelled_invoices` (default empty; existing data unchanged).
* **Test changed (exact, nothing removed or weakened):** `tests/test_erp_boundary_unit.py::test_production_policy_for_the_postgres_adapter` asserted that a postgres configuration without a contract had no `ERP_*` issue. Production now also requires the contract, so the test gives that configuration a complete TEST contract (database and time zone matching) and keeps the original assertion, and **adds** the assertion that the same configuration without a contract is refused (`ERP_CONTRACT_MISSING`).
* `ruff format` was applied to the edited `adapters/postgres.py` and `business/factory.py`.
* **Tests changed in `tests/integration/test_erp_readonly_postgres.py` (Phase 10 connection-layer tests):** they called `fetch(query)` without a tenant, which business reads no longer allow. Each now passes a minimal contract (RLS session-setting isolation, no mappings) and `tenant_id`. The identity, read-only-transaction, timeout and reachability assertions are unchanged.
  * **The move of the guard created a defect, now fixed.** The two write tests disable the guard by patching `postgres.check_read_only_sql`. After the move, `ErpQuery` validated itself with `sql_guard.check_read_only_sql`. So `test_read_only_role_cannot_write_either` **passed for the wrong reason**: the guard refused the DELETE at construction, not the read-only role. Both tests now patch the guard in both modules, and are **strengthened** to require the server's refusal code (`DATABASE_READ_ONLY` / `DATABASE_PERMISSION_DENIED`). They can no longer pass through the guard.
  * `PostgresErpAdapter.verified_rules` stays a class attribute (empty), so `tests/test_business_reasoning_security.py` is unchanged. An instance with a VERIFIED contract lists the confirmed rules.

### 5. Tests run and exact results (2026-10-05)

| Run | Result |
|---|---|
| Baseline before Phase 14 (= Phase 13 final) | 1107 passed, 17 skipped, 0 failed (1124) |
| **Final full suite** (PostgreSQL 16 `agentic_test` + Redis 7 db 15; no ERP) | **1193 passed, 18 skipped, 0 failed (1211 collected = 1124 + 87 new; 4 min 40 s, 17:00:24 → 17:05:09, no host stall)** |
| New tests | 87: `tests/test_erp_contract_unit.py` 60 · `tests/erp_contract/test_fake_erp_contract.py` 19 (18 contract checks + coverage) · `tests/erp_contract/test_real_erp_contract.py` 1 (skipped, Q1) · `tests/integration/test_erp_gate_postgres.py` 7 |
| Skipped | the 17 of Phase 13 + `tests/erp_contract/test_real_erp_contract.py` (Q1 inputs missing) |

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy` (strict, src): Success · new test modules and `scripts/erp_readiness.py` pass `mypy --strict`.

### 6. Database impact

* **ERP: NONE. Not contacted.** `inventoryai-postgres` stayed `Exited (0)` (checked before and after every run); `inven-logic` and `new_agent` were not read.
* **Agent DB: NONE.** No migration (head stays `0005`), no table, no state change.
* The integration tests created and dropped throw-away databases (`agentic_p14_erp_*`) and roles (`agentic_p14_reader_*`) on the project's own test PostgreSQL, as earlier phases did.

### 7. Files

**Created:** `src/agentic_platform/business/erp_contract.py`, `src/agentic_platform/business/erp_gate.py`, `src/agentic_platform/business/adapters/sql_guard.py`, `scripts/erp_readiness.py`, `docs/erp/ERP_CONTRACT.md`, `docs/erp/erp_contract.template.json`, `tests/erp_contract/{__init__,fixtures,suite,test_fake_erp_contract,test_real_erp_contract}.py`, `tests/test_erp_contract_unit.py`, `tests/integration/test_erp_gate_postgres.py`.

**Modified:** `business/adapters/postgres.py` (gate, tenant binding, readers), `business/adapters/fake.py` (cancelled invoices), `business/factory.py` (contract loading), `core/config.py` (`ERP_CONTRACT_PATH`), `core/production.py` (contract policy), `.env.example`, `tests/test_erp_boundary_unit.py` (§ 4), `tests/integration/test_erp_readonly_postgres.py` (§ 4), `docs/SCHEMA_INTEGRATION.md` (status), `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md` (status). Docs: `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.18), `ARCHITECTURE_DECISIONS.md` (ADR-063), `OPERATIONS_RUNBOOK.md` (§ 19).

### 8. Known limitations

1. **Real ERP NOT CONNECTED; no capability mapped; business rules and time zone NOT VERIFIED** (Q1).
2. No query plan could be inspected: there is no ERP schema and no reader. Index needs will be documented as ERP-owner actions once readers exist.
3. The gate runs once per process (first read). A privilege change on the ERP after startup is caught only at the next restart.
4. Tenant filtering with `mechanism=column` is enforced by forcing `:tenant` and refusing queries without it; whether a reader's SQL uses it correctly is a review item, backed by the contract suite's cross-tenant checks.
5. The gate proves that RLS is enabled and a policy reads the confirmed setting. It doesn't prove what that policy means. That's the owner's confirmation.
6. Warehouse scope is enforced by readers and re-checked by the service (ports contract); the database layer forces only the tenant.
7. The real-ERP contract suite needs owner-supplied test records (fixtures), which do not exist.
8. Only PostgreSQL ERPs are supported (as since Phase 10).

### 9. External inputs still required

**Q1, all items A–T** (docs/erp/ERP_CONTRACT.md § 2) · owner-supplied contract test fixtures · Q3 deployment target (network path to the ERP) · Q4 data policy (business data to the LLM) · Q6, Q22 as before.

### 10. Next step (not started)

Phase 14 stops at the input gate. Phase 15 has not been started. *(Phase 17 correction, 2026-10-10: this was true when written at 17:05; Phase 15 was implemented the same day, 17:05 → 18:16, see § 3o.)* When the ERP owner supplies the contract: readiness (offline, then `--live`), owner sign-off, one reviewed reader per VERIFIED mapping, contract suite against the real ERP, then production configuration (runbook § 19).

## 3n. Phase 16: long-term business memory and historical business intelligence — PARTIAL (fake ERP only)

**Scope** (stakeholder brief, 2026-10-10): after a mandatory audit, implement only what is genuinely missing of: long-term business memory, customer purchase-history queries, year-over-year comparisons, historical analytics tools and their LangGraph integration, **only where the existing business-read interface or fake ERP can supply authorized records**. Not allowed: the real ERP, guessed schema or rules, fabricated history, memory or vector search as the source of figures, LLM-chosen tenant/scope, SQL for the LLM, new paid services or mandatory credentials, Phase 17. Details: ARCHITECTURE.md § A.20, ADR-065, [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md) § 11.

### 1. Audit findings (before coding)

* **The brief assumes Phases 1–15 are complete.** Phases 1–14 match this file. **Phase 15 exists only in code:** lifecycle/shutdown (`services/lifecycle.py`), in-flight request counting and `API_MAX_CONCURRENT_TURNS` (`api/runtime.py`, `SERVER_BUSY`), build information (`core/build.py`), readiness's `erp` component, adapter `aclose()`/`gate_state()`, production policy additions (debug refused), `tests/test_operational_readiness.py`, `tests/integration/test_lifecycle_postgres.py`. The files were written 2026-10-05 17:05 → 18:16, after the last documentation update (17:05). They cite **ADR-064, which does not exist** in ARCHITECTURE_DECISIONS.md. Until now this file said "Phase 15 has not been started". **Its tests pass.** Phase 16 neither reviewed nor changed Phase 15; documenting it (ADR-064, a § 3 section, runbook) is open work for its owner.
* **Customer purchase history is not supported by the read layer.** `ErpReadPort` has customer search/resolution, outstanding and open invoices, day sales totals (`daily_sales`, `sales_by_day`), products, warehouses and stock. It has no customer sales documents, no line items and no product-level sales, and the fake ERP has no such records. The brief's own rule ("only implement these if the existing business-read interface or fake ERP can supply the required authorized records") therefore blocks brief § 3B and the product question of § 3C.
* **Year-over-year on totals is supported by existing reads:** `sales_by_day` returns the validated day records of any range. The fake ERP had four TEST days near "today" and **no history**.
* **Memory was already complete** (Phase 4/5): long-term facts upserted by (namespace, key) with TTL, user/tenant scope, `memory.*` permissions, audit with hashed content, approval-gated agent writes (off by default); episodic (90-day TTL), semantic knowledge (knowledge source types only), procedural (versioned), conversation summaries; recall best-effort with per-section errors, labelled "not a source of truth"; RLS in PostgreSQL; erasure (`forget_me`). The per-thread business focus holds references only.
* **Grounding had two holes.** (a) `verify_business_response` ran only when the business domain had tool results, so a business turn on which no call could be built was not checked; a stale figure from recalled memory could pass. (b) A percentage passed whenever its digits occurred anywhere in a result (for example, "30%" was sourced by the date `2026-09-30`).
* **No schema change was needed**: `memory_item` already stores metadata (JSONB), `source_type`, `source_ref`, versions and expiry. Agent DB head stays `0005`.
* Baseline (before any change, 11:01:01 → 11:03:42): **1238 passed, 18 skipped, 0 failed (1256 collected)** with PostgreSQL 16 `agentic_test` and Redis 7 db 15. `ruff check`: clean. `mypy --strict`: clean. `ruff format --check`: 33 files would be reformatted (pre-existing; the project uses `# fmt: skip`; untouched).

### 2. Gap analysis

| Brief item | Existing code | Missing → Phase 16 | Tests |
|---|---|---|---|
| A. Long-term memory: facts, preferences, procedures, dedup, expiry, deletion, tenant/permission-aware recall | all present (§ 3c, § 3d) | episodes recorded nothing about the business reads of a turn; recall did not say how an item is known | `test_episode_keeps_analyses_and_recall_labels_how_each_item_is_known`, `test_analysis_references_hold_no_figures_names_or_refs`, cross-session graph + PostgreSQL tests |
| A. Memory never overrides records | verifier on turns with results; golden `safety_memory_contradicts_erp` | verification of business turns without results | `test_graph_business_turn_without_any_read_cannot_state_a_figure`, `test_graph_memory_figure_never_overrides_the_comparison` |
| B. Customer purchase history | customer resolution and AMBIGUOUS handling only | **BLOCKED**: port method, record, fake data and rules (what a purchase is; cancellations, returns, credit notes) | `test_graph_purchase_history_and_product_sales_are_unsupported` |
| C. YoY sales totals | `sales_by_day`, `resolve_period`, rule gates | year periods, same-period-last-year range, comparison maths, missing/zero baseline | § 4 below |
| C. "Which products sold more" | — | **BLOCKED**: no product-level sales | as B |
| D. Historical tools | `business.daily_sales` | `compare_with` on the same tool (no new framework, capability or permission) | tool contract tests |
| E. Graph | planner → supervise → business domain → verify | intent field, prompt v5, composition/metrics, verification changes | graph tests |

### 3. What Phase 16 implemented

| Item | Result | Where |
|---|---|---|
| Year periods `this_year` (to date) and `last_year`, calendar years | ✅ rule `year_is_calendar_year` | `business/dates.py`, `business/rules.py` |
| Same period last year: same calendar dates, 29 Feb → 28 Feb, same days for to-date periods | ✅ rule `same_period_last_year_by_date` | `dates.same_period_last_year` |
| Comparison: both sides via `sales_by_day`, same deadline/retry/tenant/record checks; Decimal change, % half-up to 0.1 | ✅ MOCK VERIFIED | `service._sales_comparison`, `_summary`, `_compare` |
| Missing history ≠ zero; zero baseline → no %; mixed currency → no change; both empty → NOT_FOUND; comparison without period → VALIDATION_ERROR `PERIOD_REQUIRED` | ✅ | same, `results.Reason.PERIOD_REQUIRED` |
| Tool: `business.daily_sales` gains `compare_with` (closed token); planner intent `comparison` | ✅ | `tools/business.py`, `business/intent.py`, `understand_request.system.v5.md` |
| Comparison metrics (`baseline_total_sales`, `baseline_document_count`, `sales_change_amount`, `sales_change_percent`), figure kind `percent`, rendering with both ranges, basis notice and gap reasons | ✅ | `business/metrics.py`, `business/composition.py` |
| Episodes keep `analyses` (capability, status, period, comparison range, adapter, `test_data`, `retrieved_at`), never figures/names/refs/tenant | ✅ MOCK + VERIFIED LOCALLY (PostgreSQL 16) | `business/focus.analysis_references`, `memory/episodic.py`, `agents/agent.py` |
| Recall labels every item with `basis` and `recorded_at` | ✅ | `memory/models.py` (`memory_basis`, `to_prompt`) |
| Every business-domain turn verified (also without results) | ✅ | `agents/edges.py` |
| Percentages must equal a computed percentage (or the user's) | ✅ | `grounding.sourced_percentages` |
| Sales replies may not speak of profit/margin/earnings ("not profit" allowed) | ✅ | `grounding.claims_profit` |

**Questions that are supported** (fake ERP, TEST data): "Is month ki sales pichhle saal isi month se compare karo" (this month to date vs the same dates last year) · "Pichhle mahine ki sales pichhle saal se compare karo" · "Is saal ki sales pichhle saal ke isi period se" (year to date) · today / yesterday / this week / last week / last month / last year against the same period one year earlier · "Pichhle saal ki sales kitni thi?" (calendar year). "Pichhle saal ke comparison mein sales kitni badhi?" without a period gets a clarification ("Please name the period to compare…").

**Not supported (BLOCKED, answered as unsupported):** "Rahul Gupta ne pichhle saal kitne products kharide?", "… kitni baar purchase kiya?", "Is customer ki previous purchase history batao", "Last year ke same period mein kaunse products zyada bike?".

**Data and coverage limits, stated in every comparison reply:** sales are the ERP's recorded totals as the adapter's mapping defines them (tax, discounts, returns and cancellations belong to the mapping and are NOT VERIFIED for the real ERP); never profit; internal company data only, not market performance. A period without day records has no total: missing history and a period without sales cannot be told apart, so no change is computed. With the fake ERP, every figure is TEST data.

**Memory persistence behaviour:** analysis references live in episodic memory (PostgreSQL `memory_item`, RLS by tenant, user-private, 90-day expiry, erased by `forget_me` / thread deletion, recorded only with `memory.write`, recalled only with `memory.read`). They let a later session know an analysis was done (period, comparison range, source), never its figures, which are always read again. Long-term facts, knowledge and procedures are unchanged.

### 4. Changed behaviour of existing code (deliberate, reviewed)

* **Business-domain turns without tool results are now verified.** A reply without figures is unchanged; a reply with unsourced figures is replaced by "The business data could not be retrieved." No existing test depended on the old behaviour (full suite green).
* **Percentages are checked against computed percentages.** No existing result had a percentage field, so a percentage in a business reply was only "sourced" by coincidence before; such a reply is now replaced.
* **Sales replies mentioning profit, margin or earnings are replaced** (negated forms allowed).
* `daily_sales` accepts `compare_with`; `DatePeriod` gains `this_year` / `last_year` (gated by `year_is_calendar_year`). Phase 11 periods behave as before.
* The fake adapter declares two more TEST rules. The live adapter declares none (ERP contract mechanism, ADR-063).
* `Episode` gains optional `analyses`; an episode without business reads keeps its old metadata shape. Recall entries gain `basis` and `recorded_at`.
* Planner prompt v5 (v4 kept; the registry serves the latest): year periods, `compare_with`, `comparison`, new metrics, purchase history and product sales listed as unsupported.
* **Test changed (exact, nothing removed or weakened):** `tests/test_business_tools.py::test_no_tool_accepts_identity_scope_or_sql_arguments` allow-lists `compare_with` on `business.daily_sales` only, and **adds** the assertion that its schema is a closed `const` token (or null), so it can never carry free text.

### 5. Tests run and exact results (2026-10-10)

| Run | Result |
|---|---|
| Baseline before Phase 16 (code as found, incl. Phase 15) | 1238 passed, 18 skipped, 0 failed (1256 collected; 11:01:01 → 11:03:42) |
| **Final full suite** (PostgreSQL 16 `agentic_test` + Redis 7 db 15; no ERP) | **1302 passed, 18 skipped, 0 failed (1320 collected = 1256 + 64 new; 2 min 4 s, 11:20:27 → 11:22:31, no host stall)** |
| New tests | 64: `tests/test_historical_business.py` 63 · `tests/integration/test_historical_memory_postgres.py` 1 |
| Skipped | the same 18 as the baseline: live LLM (2), live OIDC/Keycloak (14), real-ERP contract suite (1, Q1), POSIX signals on Windows (1) |
| Targeted runs during development | business, memory, supervisor, graph, evaluation (golden 36/36), ERP contract suites: 586 passed, 1 skipped |

A first rerun of the baseline (11:04:37) showed 1 failure, `NameError: SalesComparisonData`: Phase 16 edits had started while it ran. That run is discarded; the clean baseline above is the one taken before any edit.

**Lint and type-check:** `ruff check src tests scripts`: All checks passed! · `mypy` (strict, src): Success, 168 files · `ruff format --check`: the same 33 pre-existing files as the baseline, none of them Phase 16 code.

**Brief § 4 checklist:** (1) supported query from fixtures ✅ · (2) amounts, documents, days separate ✅ · (3) equivalent periods incl. 29 Feb ✅ · (4) zero and missing baseline ✅ · (5) cancellations/returns: only the mapping's rules; the comparison adds none and is rule-gated ✅ · (6) ambiguous customer names: existing AMBIGUOUS path unchanged (Phase 10/11 tests); no customer-history capability exists to test it on · (7) memory across sessions, in-memory and PostgreSQL ✅ · (8) outdated memory never overrides records ✅ · (9) tenant isolation (service, memory recall, PostgreSQL RLS) ✅; warehouse scope: sales are not warehouse-scoped in the read layer, existing stock-scope tests pass · (10) unauthorized: no sales permission → nothing read; another tenant's memory never recalled ✅ · (11) injected tenant/SQL arguments and an unregistered `sql.execute` refused ✅ · (12) ERP outage, missing rules, missing timezone, malformed records, embedder failure (existing) ✅ · (13) existing suites green ✅.

### 6. Database impact

* **ERP: NONE. Not contacted.** `ERP_ADAPTER` defaults unchanged (`none`); tests use `fake`. `inventoryai-postgres` stayed `Exited (0)` before and after every run; `inven-logic` and `new_agent` were not read.
* **Agent DB: no migration** (head stays `0005`). The integration test wrote episodic rows for unique throw-away tenants (`p16-*`) in the project's own `agentic_test` database, as earlier phases did.

### 7. Files

**Created:** `tests/test_historical_business.py`, `tests/integration/test_historical_memory_postgres.py`, `src/agentic_platform/prompts/templates/understand_request.system.v5.md`.

**Modified:** `business/dates.py`, `business/rules.py`, `business/models.py`, `business/results.py`, `business/service.py`, `business/intent.py`, `business/metrics.py`, `business/composition.py`, `business/grounding.py`, `business/focus.py`, `tools/business.py`, `agents/domains/business.py` (description), `agents/edges.py`, `agents/nodes/verify_business_response.py`, `agents/agent.py`, `memory/models.py`, `memory/episodic.py`, `tests/test_business_tools.py` (§ 4). Docs: `PROJECT_STATUS.md`, `ARCHITECTURE.md` (§ A.19, § A.20), `ARCHITECTURE_DECISIONS.md` (ADR-065, ADR-064 noted as missing), `docs/BUSINESS_INTEGRATION.md` (§ 11), `docs/SCHEMA_INTEGRATION.md` (rules register).

### 8. Known limitations

1. **Customer purchase history and product-level sales: BLOCKED** (Q1, Q27).
2. Real ERP NOT VERIFIED; on `ERP_ADAPTER=postgres` every comparison answers RULE_NOT_VERIFIED until the contract confirms `daily_sales` and the four rules it needs.
3. Missing history and zero sales are indistinguishable without an ERP coverage statement; no change is computed for a side without records.
4. "Year" is the calendar year by rule; a financial-year comparison (April–March) needs its own rule and periods (Q28).
5. A year period reads up to 366 day records per side (bounded by the range); the deterministic rendering shows totals, not days.
6. Number grounding still treats a plain number that occurs in a date or reference as sourced (Phase 10 limit). Percentages are now exempt from that.
7. Analysis references are recalled by similarity to the episode's one-line summary (hashing embedder in development); relevance is best-effort, as for all recall.
8. Production LLM behaviour (whether Claude fills `comparison` reliably) NOT VERIFIED (Q22); planner behaviour is scripted in tests.
9. Phase 15 is undocumented (§ 3n.1); Phase 16 did not review it.

### 9. External inputs still required

Q1 (with the new rules `year_is_calendar_year` and `same_period_last_year_by_date` for the ERP owner to confirm) · Q27 customer purchase-history semantics · Q28 calendar vs financial year · Q22 live model · Q4 data policy · the Phase 15 owner: ADR-064 and its status section.

### 10. Next step (not started)

Phase 16 stops here; Phase 17 has not been started. Recommendation in the completion report: first document Phase 15 (ADR-064); then either the ERP contract (Q1) so the existing capabilities, including the comparison, can be verified live, or, if the owner supplies purchase-history semantics (Q27), a customer-sales capability through the ADR-063 contract, with fake-ERP fixtures defined from those confirmed semantics.

## 3o. Phase 15: production hardening and operational readiness (implemented 2026-10-05; documented 2026-10-10 in Phase 17)

**Scope of this section** (Phase 17 brief, 2026-10-10): document the existing Phase 15 code and tests, and write ADR-064. Documentation only: no source, test, model, migration, environment, Docker or ERP-adapter change, and no ERP contact. No Phase 15 brief is in the repository, so this section describes what the code does, not what was asked for. Details: ADR-064, ARCHITECTURE.md § A.19, OPERATIONS_RUNBOOK.md § 20.

### 1. Audit findings (before editing)

* **The Phase 16 report's claims match the repository.** Phase 15 code exists and cites ADR-064: 15 references in 10 source files, 33 "Phase 15" comments in `src/`, 3 test files, the 3 backup scripts, the gateway snippet and both env templates. ADR-064 had an index row marked "Not documented" and no body. No other decision uses the number, so it was free. Before this change, § 1 marked Phase 15 "in the code, not documented", not "not started". Only § 3m.10 still said "Phase 15 has not been started" (now annotated).
* **Phase 15 is wider than the brief's four items.** Besides shutdown, the turn cap, build information and the ERP readiness component, the files written 2026-10-05 17:25 → 18:16 also add: safe default response headers (and matching gateway `proxy_hide_header`); production policy errors for DEBUG logging and `DATABASE_STATEMENT_TIMEOUT_MS=0`; the `Exporter` boundary and `OBSERVABILITY_EXPORTER`; `constraints.txt` with a digest-pinned base image; Agent DB backup, restore and verify scripts (`deploy/backup/`); and a deployment smoke test (`deploy/smoke/`).
* **Documentation the code already pointed to was missing:** OPERATIONS_RUNBOOK.md § 20 (cited by the backup scripts) and the startup note in ARCHITECTURE.md § A.19 (cited by `services/readiness.py`). Both now exist.
* **Stale operational statement:** runbook § 12 said the API "exits 0" on SIGTERM. Since Phase 15, uvicorn 0.54.0 (pinned) re-raises the caught signal, so the exit status is the signal's (143 in a container), as `api/__main__.py` states. § 12 is corrected. Phase 7's historical result (§ 3f, "exit 0") is left as recorded.
* **No evidence that the smoke test or the backup scripts ever ran.** There is no `deploy/smoke/results/`, and no smoke container or image exists. Their repository tests are static checks of the files.

### 2. What Phase 15 implemented (verified by reading the code)

| Item | Behaviour | Code |
|---|---|---|
| Bounded shutdown | After `API_GRACEFUL_SHUTDOWN_SECONDS`, the steps `in_flight_requests` (≤ ½ budget) → `maintenance` (in-process only) → `redis` → `database` → `erp` (if configured) → `observability` run within `API_SHUTDOWN_CLEANUP_SECONDS` (10). Each step gets ≥ 0.5 s; an overrun is abandoned (`SHUTDOWN_STEP_TIMEOUT`); errors are recorded; the sequence never raises. Logs `shutdown.step`, `shutdown.completed`, and `api.stopped {shutdown}` | `services/lifecycle.py`, `api/runtime.py`, `api/app.py`, `agents/dependencies.closers` |
| Worker shutdown | Same sequence (without in-flight and maintenance steps) | `maintenance/__main__.py` |
| In-flight counting | Middleware counts each request until it has finished or unwound | `api/middleware.py` |
| Concurrent-turn cap | `API_MAX_CONCURRENT_TURNS` (32; 0 = unlimited) per process; above it, 503 `SERVER_BUSY`, retryable, not queued; on agent turn, conversation start/continue/resume, approval approve/reject; held only after authentication and rate limiting | `services/lifecycle.TurnSlots`, `api/dependencies.turn_slot`, `core/errors.ServerBusyError`, `observability/taxonomy.py` |
| Build information | version + `APP_BUILD_COMMIT` / `APP_BUILD_TIME` (shape-validated, else `unknown`); `--version`, `api.started`, `/ready` | `core/build.py`, `api/__main__.py`, `Dockerfile` |
| ERP readiness component | never gates, never connects: `none` → `not_configured`; fake → `ok`; live gate `verified`/`pending`/`unverified`/`refused` → `ok`/`degraded`/`not_configured`/`misconfigured` | `services/readiness.py`, `business/ports.py`, `business/service.py`, `business/adapters/postgres.py` |
| Startup logging | `api.started` + `api.startup.dependencies`, never blocking | `api/app.py` |
| Default headers | `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` unless set by the route; the gateway hides them from the upstream | `api/middleware.py`, `deploy/gateway/snippets/proxy.conf` |
| Production policy | errors: `LOG_LEVEL_DEBUG` (was a warning), `DATABASE_STATEMENT_TIMEOUT_DISABLED`; warning: `TURN_CONCURRENCY_UNBOUNDED` | `core/production.py` |
| Exporter boundary | `Exporter` protocol, none shipped; `OBSERVABILITY_EXPORTER` accepts only `none`; flush bounded by `OBSERVABILITY_FLUSH_TIMEOUT_SECONDS` | `observability/hub.py`, `core/config.py` |
| Image reproducibility | `constraints.txt` pins runtime dependencies; digest-pinned base; installers removed; build args | `constraints.txt`, `Dockerfile` |
| Agent DB backup tooling | backup (`pg_dump -Fc` + manifest; refuses a non-Agent DB, exit 3), restore into a new empty DB only (exit 4 otherwise), read-only verify | `deploy/backup/*.sh` |
| Deployment smoke test | isolated compose project `agentic-smoke`, LLM stub, 12 check groups | `deploy/smoke/` |
| Configuration templates | new variables documented | `.env.example`, `deploy/env/production.env.example` (§ 7 lists them) |

### 3. Verification status

| Concern | Status |
|---|---|
| Shutdown sequence, turn cap, build info, readiness `erp`, headers, policy, exporter boundary | UNIT VERIFIED (`tests/test_operational_readiness.py`, `tests/test_production_unit.py`) |
| Cancelled turn recorded as failed (never replayed), no pooled-connection leak, pools reconnect after shutdown, readiness on the real stack | VERIFIED LOCALLY: PostgreSQL 16 `agentic_test` + Redis 7 db 15, app role (`tests/integration/test_lifecycle_postgres.py`) |
| Worker SIGTERM exit | NOT VERIFIED on this machine: the POSIX test is skipped on Windows |
| Image, compose hardening, `constraints.txt`, backup-script guards | STATIC CHECK ONLY (tests read the files) |
| Deployment smoke test (`deploy/smoke/run_smoke.py`) | **NOT RUN**: no recorded result |
| Agent DB backup → restore → verify | **NOT RUN** |
| Production platform, capacity for `API_MAX_CONCURRENT_TURNS`, exporter | BLOCKED (Q3) |

### 4. Changed behaviour of existing code (as found; reviewed, not changed, in Phase 17)

* `LOG_LEVEL=DEBUG` in production now stops the process (was a warning); `tests/test_production_unit.py` was updated accordingly in Phase 15.
* Every response gains `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` unless the route set them.
* Turn routes can answer 503 `SERVER_BUSY`.
* After SIGTERM, the API exits with status 143 (uvicorn re-raises the signal), not 0.
* `services/database_health.py` uses the shared `abandon_after` helper.

### 5. Tests run and exact results (2026-10-10, Phase 17)

Commands (Git Bash; `TEST_DATABASE_URL` / `TEST_DATABASE_APP_URL` = owner / app role on `agentic_test`, `TEST_REDIS_URL` = Redis db 15):

```
.venv/Scripts/python -m pytest tests/test_operational_readiness.py tests/integration/test_lifecycle_postgres.py tests/test_production_unit.py -rs -p no:cacheprovider -o addopts=""
.venv/Scripts/python -m pytest -rs -v -p no:cacheprovider -o addopts=""
```

| Run | Result |
|---|---|
| Phase 15 targeted (88 collected: 40 + 4 + 44) | **87 passed, 1 skipped** in 17.86 s (11:29:34 → 11:29:54). Skip: `test_worker_exits_cleanly_on_sigterm` (POSIX signals) |
| Full suite, first attempt | **Discarded**: stalled (21 s CPU in about 11 min, no progress); stopped at 11:41:13. Probably a host stall (known on this machine); no result taken from it |
| **Full suite** (PostgreSQL 16 + Redis 7; no ERP) | **1302 passed, 18 skipped, 0 failed** (1320 collected; 124.32 s, 11:41:18 → 11:43:24). Same counts as the Phase 16 final run. Skips: live OIDC/Keycloak 14, live LLM 2, real-ERP contract suite 1 (Q1), POSIX signals 1 |

Not run in Phase 17: `ruff`, `mypy` (no code changed), the deployment smoke test, the backup scripts, any container build.

### 6. Database impact

None. No migration (head stays `0005`). The integration tests wrote throw-away rows to the project's own `agentic_test` database, as in earlier phases. **ERP: not contacted.** `inventoryai-postgres` was `Exited (0)` before and after the runs; Phase 17 did not start it.

### 7. Files (Phase 17)

Modified, documentation only: `PROJECT_STATUS.md` (header, § 1, § 3m.10 annotation, this § 3o, § 7), `ARCHITECTURE_DECISIONS.md` (ADR-064 index row and record, header), `ARCHITECTURE.md` (status line, § A.19), `OPERATIONS_RUNBOOK.md` (header, § 12, § 14 rows, new § 20), `GO_LIVE_CHECKLIST.md` (header, Operations and Testing rows). § 3n (Phase 16) is unchanged.

### 8. Known limitations and open items

1. The deployment smoke test and the backup → restore → verify path have never run. Run `deploy/smoke/run_smoke.py` (Docker) before relying on them.
2. `API_MAX_CONCURRENT_TURNS=32` and `API_SHUTDOWN_CLEANUP_SECONDS=10` are defaults, not values sized against a production deployment (Q3).
3. No metrics/tracing exporter exists (Q3).
4. The original Phase 15 brief is not in the repository; ADR-064 records the implemented decision, reconstructed from code and comments.

### 9. Next step (not started)

Phase 17 stops here. The open items from § 3n.9 are unchanged (Q1, Q27, Q28, Q22, Q4). The Phase 15 owner item is closed, except running the smoke test (§ 3o.8.1).

## 3p. Phase 18: ERP access contract preparation (2026-10-10) — documentation only, real ERP NOT CONTACTED

**Scope** (stakeholder brief, 2026-10-10): audit the existing ERP access contract and prepare an implementation-ready specification for a later real ERP integration. This is an audit and contract-preparation phase. Not allowed: connecting to or modifying the real ERP, starting `inventoryai-postgres`, reading ERP credentials, ERP migrations, fake customer-purchase records, implementing customer purchase history or product-sales analytics, changing business behaviour without a confirmed defect, Phase 19. The deliverables are in [docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 5–§ 11 and ADR-066 (Proposed).

### 1. What was inspected

The documents: this file (header, § 1, § 3m, § 3n, § 3o, § 9, § 10), ARCHITECTURE.md (status line, § A.18, § A.20), ARCHITECTURE_DECISIONS.md (index; ADR-063, ADR-064, ADR-065), GO_LIVE_CHECKLIST.md (header and ERP rows), OPERATIONS_RUNBOOK.md (outline and § 19), docs/BUSINESS_INTEGRATION.md, docs/SCHEMA_INTEGRATION.md, docs/erp/*. The code: `business/` (capabilities, ports, models, context, rules, dates, service, metrics, erp_contract, erp_gate, adapters fake/postgres/sql_guard), `tools/business.py`, `agents/routing.py`, `agents/supervisor.py`, planner prompt v5, `core/config.py` (`ErpSettings`). The tests: the business, historical, ERP-contract, ERP-boundary, inspection and readiness suites, and the contract suite with its fixtures. The open-question register (§ 9). No Agent DB migration exists for ERP data (head `0005`; the ERP has its own settings and is never migrated).

### 2. Findings

* **The phase brief matches the repository.** ADR-064 and ADR-065 exist; the Phase 16 counts (1302 passed, 18 skipped) are recorded in § 3n.5 and § 3o.5.
* **Six capabilities, all FAKE-TESTED, none with a live reader.** `python -m scripts.erp_readiness` (offline) still reports BLOCKED, A–T missing, exit 3.
* **Not implemented at all:** customer sales history, product-level sales, customer payment history, supplier records, purchase orders, GRNs, purchase invoices, outlet stock as distinct from warehouses, tax and accounting reads. Only refusals are tested (`unsupported`).
* **Sales, customer and invoice reads are tenant-wide; only stock reads apply warehouse scope.** This has been by design since Phase 10, and nothing in the repository confirms it is acceptable to the owner. It is raised as **Q29**; no code was changed.
* **Activation dependency:** warehouse resolution (`find_warehouses` / `get_warehouse`) is served only by a `warehouse_stock` reader, but `product_stock` uses it for its warehouse filter. This fails closed (RULE_NOT_VERIFIED) and is accounted for in the activation order (ERP_CONTRACT.md § 10).
* **Adding a capability changes Q1 item S for every contract** (`missing_inputs` requires every capability to be mapped). This must be decided before the first new capability (ADR-066).
* **There is no history-coverage statement**, so "no sales" and "no data" stay indistinguishable. Raised as **Q30**.
* **No defect was found that requires a code change.**

### 3. Deliverables

| Deliverable | Where |
|---|---|
| 1. Verified ERP capability matrix | ERP_CONTRACT.md § 5 |
| 2. Future integration contract specification | ERP_CONTRACT.md § 6; ADR-066 (Proposed) |
| 3. Unresolved questions and owner decisions | ERP_CONTRACT.md § 7; § 9 below (Q29, Q30 new; Q1, Q7, Q8, Q9, Q27, Q28 annotated) |
| 4. Capabilities testable now with the fake ERP | ERP_CONTRACT.md § 8 |
| 5. Capabilities blocked until the contract or owner decisions | ERP_CONTRACT.md § 9 |
| 6. Ordered plan for the real ERP integration | ERP_CONTRACT.md § 10 (mechanics stay in OPERATIONS_RUNBOOK.md § 19) |
| 7. Statement that no real ERP connection was attempted | ERP_CONTRACT.md § 11; § 5 below |

### 4. Tests run and exact results (2026-10-10)

Command (Git Bash, from the repository root; no `TEST_DATABASE_URL`, `TEST_REDIS_URL` or `ERP_*` variable set):

```
.venv/Scripts/python -m pytest tests/test_business_service.py tests/test_business_tools.py tests/test_business_graph.py tests/test_business_reasoning_unit.py tests/test_business_reasoning_graph.py tests/test_business_reasoning_security.py tests/test_erp_boundary_unit.py tests/test_erp_contract_unit.py tests/test_erp_inspection_unit.py tests/test_historical_business.py tests/test_operational_readiness.py tests/erp_contract -rs -p no:cacheprovider -o addopts=""
.venv/Scripts/python -m scripts.erp_readiness
```

| Run | Result |
|---|---|
| Baseline, before any edit (12:01:38 → 12:01:59) | **480 passed, 1 skipped** in 19.00 s. Skip: `tests/erp_contract/test_real_erp_contract.py` (Q1 inputs missing) |
| Readiness gate, offline | `ERP READINESS: BLOCKED`, items A–T missing, exit 3 |
| After the documentation edits (12:11:51 → 12:12:12) | **480 passed, 1 skipped** in 19.13 s (same skip); readiness again BLOCKED, exit 3; `inventoryai-postgres` still `Exited (0)` |

Documentation check: every edited file was compared with its pre-edit copy. Only the Phase 18 lines differ, and line endings are unchanged (LF). Every `ERP_CONTRACT.md § n` reference resolves to an existing section, and Q29, Q30 and ADR-066 exist where they are cited.

Not run: the full suite, the integration tests that need the project's test PostgreSQL and Redis, `ruff` and `mypy`. No source or test changed. The full-suite result of record stays 1302 passed, 18 skipped, 0 failed (§ 3o.5).

### 5. Database and ERP impact

* **ERP: none. No connection was attempted.** `inventoryai-postgres` was `Exited (0)` before and after and was not started. `inven-logic`, `new_agent` and the schema atlas were not read. No `ERP_*` variable or credential was set or read. The readiness tool ran offline only (no `--live`).
* **Agent DB: none.** No migration (head stays `0005`), and no test that writes to it was run.

### 6. Files (Phase 18)

Modified, documentation only: `docs/erp/ERP_CONTRACT.md` (status, new § 5–§ 11), `ARCHITECTURE_DECISIONS.md` (header, ADR-066 index row and record), `PROJECT_STATUS.md` (header, § 1, this § 3p, § 9 Q1/Q7/Q8/Q9/Q27/Q28 annotations, new Q29 and Q30, § 10 note), `ARCHITECTURE.md` (status line), `docs/BUSINESS_INTEGRATION.md` (header, § 9, § 11.4 pointers), `docs/SCHEMA_INTEGRATION.md` (status line, two rules-register rows), `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md` (status line). No source, test, configuration, environment, Docker or migration file changed.

### 7. Unresolved blockers

Q1 (A–T) · Q27 · Q28 · Q29 (new) · Q30 (new) · Q7 · Q8 · Q9 · Q4 · Q3, as detailed in ERP_CONTRACT.md § 7. Q22 (live model) is unchanged and independent of the ERP contract.

### 8. Next step (not started)

Phase 18 stops here; Phase 19 has not been started. The next useful input is the ERP owner's answer to Q1, together with Q7, Q29 and Q8, so the six existing capabilities can be activated in the order of ERP_CONTRACT.md § 10. New capabilities wait for Q27, Q9 and Q30.

## 3q. Phase 19: warehouse scope of sales, customer and invoice reads (2026-10-10) — BLOCKED on owner decision Q29, no code change

Security phase. Its brief: investigate the Phase 18 finding that sales, customer and invoice reads may ignore warehouse scope, and fix it only if the repository establishes the intended policy. **It does not.** No authorization change was made. Real ERP not contacted.

### 1. Request path (verified in code)

`tools/business.py` (strict `extra="forbid"` input; principal from `require_principal()`) → `BusinessReadService.<capability>` → `_run`: permission re-check, then `BusinessContext.for_principal(principal, resolver)` → `ErpReadPort` → adapter → record re-validation and tenant check → totals in code.

* **Tenant and warehouse scope originate only in trusted code.** `tenant_id` comes from the authenticated `Principal`. Scope comes from the `WarehouseScopeResolver`, by default `PermissionScopeResolver`: `business.warehouse.all` → ALL, otherwise NONE. No other resolver is wired in `src/`. None of the six tool inputs has a tenant, scope or permission field, and unknown fields are rejected (`test_business_tools.py`, `test_business_reasoning_security.py`). The model cannot set or widen either one.
* **Stock (reference behaviour).** `product_stock`, `warehouse_stock` and `low_stock_products` refuse an empty scope (UNAUTHORIZED / `WAREHOUSE_SCOPE_EMPTY`). The adapter filters by scope, and the service filters again *before* summing. A named warehouse outside the scope resolves as NOT_FOUND.
* **Sales (`daily_sales`: day, period, `compare_with`), `customer_outstanding` and `customer_open_invoices`.** They check only the capability permission. `ctx.warehouse_scope` is passed to the port but never read on these paths: not by the service, not by `FakeErpAdapter`, and the live adapter has no reader. `SalesRecord`, `OutstandingRecord` and `InvoiceRecord` have **no warehouse attribute**, so there is nothing to filter on. The historical comparison reads both periods through the same `_sales_days` with the same context, so it neither widens nor narrows scope relative to a plain period read.
* **Probe** (throw-away script outside the repository, fake ERP, standard test data): with `business.sales.read` and `business.customer.read`, callers with scope ALL, NONE and LISTED {Godown 1} all received the same `daily_sales` total, the same outstanding balance and the same 3 open invoices. For the same NONE and LISTED callers, `warehouse_stock("Godown 2")` returned UNAUTHORIZED / `WAREHOUSE_SCOPE_EMPTY` and NOT_FOUND respectively.

### 2. Leakage channels checked

| Channel | Finding |
|---|---|
| Tool arguments | no tenant, scope or permission field; extra fields rejected (existing tests) |
| Cache | business results are not cached (`cache/` holds no business data) |
| Long-term memory | episodes keep `analysis_references` only: capability, status, period, source. No figure, name, ref or tenant id (`business/focus.py`). Memory is scoped by `MemoryScope(tenant_id, user_id)` |
| Thread focus | refs and names only, no figures. Re-resolved inside the caller's tenant (and, for warehouses, scope) on every turn |
| Retries | `_run` retries the same call with the same context within one deadline |
| Answer checking | `verify_business_response` grounds the reply in this turn's tool results only |
| Error messages | out-of-scope stock is NOT_FOUND, indistinguishable from missing. Adapter errors carry no data |

No cross-tenant path was found. The only scope gap is the one under review: within a tenant, non-stock reads ignore warehouse scope.

### 3. Policy evidence

Nothing in the repository states that sales or customer figures must be warehouse-scoped, or which roles may see company-wide figures. What it does state:

* ADR-059 / `context.py`: scope is decided by `business.warehouse.all`. The stock-only filtering point is documented in `ports.py` ("Stock reads receive the caller's `WarehouseScope`").
* `capabilities.py` documents the operator example `"sales_viewer": ["business.customer.read", "business.sales.read"]`, *without* `business.warehouse.all`. The test role catalogue (`tests/business_helpers.py`) uses the same grant. Failing closed on scope would remove those users' access.
* ERP_CONTRACT.md § 3 lists the customer capabilities' warehouse boundary as "n/a" and the sales boundary as TO BE SUPPLIED. § 5 finding 1 and ADR-066 record tenant-wide behaviour as by design since Phase 10, and say the six capabilities keep it until Q29.
* Q8 (business role catalogue) is open, so no role is known to need company-wide figures or to be forbidden them.

Without an authoritative policy, either change would be speculative. Requiring scope ALL would silently withdraw documented access. Filtering by warehouse is impossible without warehouse attribution, which needs ERP semantics (Q1 I) that nobody has confirmed.

### 4. Decision required from the owner (Q29)

Asked of the business owner, the ERP application owner and a security reviewer:

1. **Policy.** For `daily_sales` (including periods and the year-over-year comparison), `customer_outstanding` and `customer_open_invoices`, choose one:
   * **(a)** company-wide for anyone holding the capability permission: today's behaviour, which then becomes documented policy;
   * **(b)** only for callers with `business.warehouse.all`, and UNAUTHORIZED for everyone else. Implementable now as a small service-level check, but it withdraws access from `sales_viewer`-style grants;
   * **(c)** filtered to the caller's warehouses. This needs answers to items 2 and 3.
   The answer may differ for sales and for customers.
2. **Attribution (only for c).** Which ERP field attributes a sales document, invoice or customer balance to a warehouse or outlet? How do multi-warehouse invoices, and customer balances built from documents of several warehouses, behave?
3. **Assignment source.** Where does a user's warehouse list come from (ERP, identity-provider claim or platform configuration)? Today no `WarehouseScopeResolver` produces LISTED in production, so every caller is ALL or NONE.

**Safe interim behaviour supported by the current code:** these three capabilities are granted only through `AUTH_ROLE_CATALOG_JSON` (no built-in role, Q8). An operator who wants warehouse-limited users to see no company-wide figures can simply not grant them `business.sales.read` or `business.customer.read`. The live adapter answers nothing until Q1, so no real ERP figure can leak today.

**After the decision:** (a) is a documentation change. (b) adds a check in the three service methods, plus a reason code and regression tests (authorized, unauthorized, cross-tenant, scope change between turns, comparison). (c) needs the attribution fields in the records, adapter filtering, a filter in the service before any total, and a resolver. The live reader in every case waits for Q1.

### 5. Verification

```
.venv/Scripts/python -m pytest tests/test_business_service.py tests/test_business_tools.py tests/test_business_graph.py tests/test_business_reasoning_unit.py tests/test_business_reasoning_graph.py tests/test_business_reasoning_security.py tests/test_erp_boundary_unit.py tests/test_erp_contract_unit.py tests/test_erp_inspection_unit.py tests/test_historical_business.py tests/test_operational_readiness.py tests/erp_contract -rs -p no:cacheprovider -o addopts=""
```

| Run | Result |
|---|---|
| Baseline, before any edit (12:16:59 → 12:17:20) | **480 passed, 1 skipped** in 18.77 s. Skip: `tests/erp_contract/test_real_erp_contract.py` (Q1 inputs missing) |
| After the documentation edits (12:18:56 → 12:19:17) | **480 passed, 1 skipped** in 18.79 s (same skip). `python -m scripts.erp_readiness` (offline): exit 3, BLOCKED |

No regression tests were added: no behaviour changed, and a test asserting tenant-wide reads would freeze a policy that nobody has decided. Not run: the full suite, integration tests that need the test PostgreSQL and Redis, `ruff`, `mypy`. Unit tests passing is not evidence of production security.

### 6. Database and ERP impact

* **ERP: none.** No connection was attempted. `inventoryai-postgres` was `Exited (0)` and was not started. No `ERP_*` variable or credential was set or read (`ERP_ADAPTER` unset, so the default is `none`).
* **Agent DB: none.** No migration was run.

### 7. Files (Phase 19)

Modified, documentation only: `PROJECT_STATUS.md` (header, § 1 row, this § 3q, § 9 Q29 annotation) and `docs/erp/ERP_CONTRACT.md` (§ 7 Q29 row annotation). No source, test, configuration or migration file changed.

### 8. Next step (not started)

Phase 19 stops here. The next step is the owner's answer to Q29 (§ 3q.4), ideally together with Q8. With (b), the fix is a small, testable phase against the fake ERP. With (c), it also needs Q1 I and an attribution field.

## 3r. Phase 20: proposed owner decisions for Q29 (2026-10-10) — documentation only, proposals not approvals

> These are proposed owner decisions recorded for review. No owner approval has been provided or inferred. Runtime authorization behavior remains unchanged. Q8 remains unresolved. No production or real ERP access is authorized by this documentation.

### 1. Proposals and statuses

The options are those of § 3q.4, item 1. As § 3q.4 allows, sales and customers are two separate proposals. Approving one does not approve the other.

| Decision | Proposed option | Status | Capabilities affected |
|---|---|---|---|
| Q29, sales read scope | **(a)** company-wide for authorized users: a holder of `business.sales.read` sees company-wide sales, not filtered by warehouse | **PROPOSED — OWNER APPROVAL PENDING** | `daily_sales` (day, period, `compare_with`) |
| Q29, customer outstanding and invoice read scope | **(a)** company-wide for authorized users: a holder of `business.customer.read` sees company-wide outstanding balances and invoices | **PROPOSED — OWNER APPROVAL PENDING** | `customer_outstanding`, `customer_open_invoices` |
| Q8, role permissions | none | **PENDING — OWNER DECISION REQUIRED** | which roles or users receive any `business.*` permission |

The proposals cover only these three existing capabilities. They do not cover payments, purchase documents or the new document-level capabilities of ADR-066, which keep the safe rule "scope ALL only" (ERP_CONTRACT.md § 6.1). Q29 items 2 and 3 (warehouse attribution, source of warehouse assignment) are not needed for option (a) and stay open.

### 2. Proposed vs approved vs implemented

* **Proposed** (the state today): an option is written down for the owner to accept or reject. It authorizes nothing.
* **Approved**: the business owner, the ERP application owner and a security reviewer (the Q29 approvers, ERP_CONTRACT.md § 7) explicitly confirm the option, separately for sales and for customer/invoice reads. None has done so, and approval is not inferred from this record, from the brief that asked for it, or from today's behaviour.
* **Implemented**: the running system enforces the approved policy. Option (a) happens to match today's code (§ 3q.1). That is **current behaviour, not approved policy**: until approval it remains an undecided security question, and nothing here makes it operational on a real ERP.

**Q29 does not grant permissions.** Option (a) decides only *what a permission holder sees*. *Who* holds `business.sales.read` or `business.customer.read` is Q8. No built-in role grants either, and no role definition, role grant, warehouse assignment or permission mapping is proposed here.

### 3. Prerequisites before any code or configuration change

1. Explicit owner approval of each Q29 proposal, recorded by the three approvers. If (a) is approved, the change is documentation: Q29 closes for that area and ERP_CONTRACT.md § 3 and § 7 record the policy. A regression test pinning company-wide reads may then be added (Phase 19 deliberately added none). If (b) or (c) is chosen instead, § 3q.4 applies.
2. Q8: the role catalogue, i.e. which roles receive `business.sales.read`, `business.customer.read` and `business.warehouse.all`. Until then no role is changed and grants stay explicit operator configuration (`AUTH_ROLE_CATALOG_JSON`).
3. For any live answer: Q1 (A–T) and Q4, unchanged. `ERP_ADAPTER` stays unset (default `none`).

### 4. What was done

Read-only audit, then documentation edits. The workspace is not a git repository (Q2), so there was no branch, status or uncommitted-change view. Unrelated work was protected by editing only the lines listed below and comparing each file with a pre-edit copy. No Q29 or Q8 status had changed since Phase 19. Modified, documentation only: `PROJECT_STATUS.md` (header, § 1 row, this § 3r, § 9 Q8 and Q29 annotations) and `docs/erp/ERP_CONTRACT.md` (§ 7 Q29 and Q8 row annotations). No source, test, configuration, migration or seed file changed. No container, database, ERP, credential or environment file was touched. No tests were run (no behaviour changed). The repository has no established documentation check.

### 5. Next step (not started)

The owner (with the ERP application owner and a security reviewer) accepts or rejects each Q29 proposal separately, and answers Q8.

## 3s. Phase 21: owner decision readiness and safety gate (2026-10-10) — documentation only, nothing approved

Re-checked the decision records and prepared the reviewer handoff ([docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 12). No owner approval has been supplied, and none is inferred from Phase 20's proposals, from project notes or from current code behaviour.

### 1. Readiness by decision

"Implemented" means behaviour in this repository; "real ERP" means evidence from a run against the real ERP.

| Item | Proposed | Owner-approved | Implemented | Verified against the real ERP |
|---|---|---|---|---|
| Q29 sales scope | yes: option (a), Phase 20 (§ 3r) | **no** (PROPOSED — OWNER APPROVAL PENDING) | today's code is tenant-wide, which matches (a), but that is not approved policy. Evidence: fake ERP only (§ 3q.1) | **no**: no live reader |
| Q29 customer outstanding / invoice scope | yes: option (a), Phase 20, separate proposal | **no** (PROPOSED — OWNER APPROVAL PENDING) | as above | **no** |
| Q8 role permissions | **no**: none proposed, deliberately | **no** (PENDING — OWNER DECISION REQUIRED) | provisional platform roles only. No built-in role grants a `business.*` permission; operators grant them through `AUTH_ROLE_CATALOG_JSON` | **no** (not applicable until roles exist) |
| Q1 access contract A–T | **no**: no contract supplied (the template is empty) | **no** | contract model, validator, gate and readiness tool exist (Phase 14, ADR-063); no mapping or reader | **no**: `erp_readiness` offline → BLOCKED, exit 3, **all 20 items A–T missing** (re-run 2026-10-10) |
| Q4 business data to the LLM provider | **no** | **no** | the design sends SUCCESS data to the model to phrase the reply (BUSINESS_INTEGRATION.md § 9.5); live answers wait | **no** |

Other documented prerequisites for live ERP access, all open: Q7, Q3, Q26, Q22 (live model), a passing read-only security inspection (LIVE_ERP_VERIFICATION_CHECKLIST.md § 3), owner-supplied contract test records with two real test tenants. Q27, Q28 and Q30 are also needed before the history and period steps (ERP_CONTRACT.md § 10).

### 2. What was done

Read `PROJECT_STATUS.md`, `docs/erp/ERP_CONTRACT.md`, ADR-066, `docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md`, `docs/BUSINESS_INTEGRATION.md` § 9, `docs/SCHEMA_INTEGRATION.md`, `GO_LIVE_CHECKLIST.md` and `scripts/erp_readiness.py`. Confirmed that Phase 20's records are intact. Ran `python -m scripts.erp_readiness --json` without `--contract` or `--live`: it reads no connection variable and connects to nothing. Exit 3. The workspace is still not a git repository and was not initialized (Q2). Each edited file was compared with a pre-edit copy.

Modified, documentation only: `PROJECT_STATUS.md` (header, § 1 row, this § 3s) and `docs/erp/ERP_CONTRACT.md` (header line, new § 12). No source, test, configuration, migration or seed file changed. No container, database, ERP, credential or environment file was touched. No tests were run (no behaviour changed).

### 3. Next step (not started)

The reviewers complete ERP_CONTRACT.md § 12. Recorded answers then go through the existing paths: § 3r.3 for Q29, and ERP_CONTRACT.md § 10 for Q1.

## 3t. Phase 22: InvenLogic real ERP integration (2026-10-30) — readers verified on a TEST replica, fake ERP deleted, live ERP NOT CONTACTED

Details of the mapping, the rules and the go-live steps: [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md). Decision record: ADR-067.

### 1. Owner decisions (2026-10-30)

The company owner confirmed that the *Inven Logic Schema Atlas* (PostgreSQL 18.6, six service databases, read 2026-10-29) is the company's **real ERP**, and asked to:

| # | Request | Result |
|---|---|---|
| a | integrate it | done in code; verified on a TEST replica; the live ERP waits for § 3t.4 |
| b | build tools the LLM calls to read data from it | six readers behind the existing six READ tools (§ 3t.2) |
| c | delete the fake ERP completely | done: `adapters/fake.py` and its settings deleted; tests use a test database instead (owner's choice) |
| d | use the local Ollama LLM | the provider exists since 2026-10-29; findings in § 3t.5 |
| e | test everything | § 3t.3 |

For the business rules the owner said "adopt your proposed defaults". They are recorded as confirmed rules in the contract (§ 3t.6); the accountant should re-confirm them.

This also answers the earlier record that the InvenLogic database was an unconfirmed candidate (ADR-063 context, § 3m): the owner has now confirmed it. Q1 is no longer "nothing supplied": the business rules, statuses, mappings and tenant isolation are in the contract; the connection items are still missing (§ 3t.4).

### 2. What was delivered

| Area | Change |
|---|---|
| Readers | New `src/agentic_platform/business/adapters/invenlogic.py`, reader set `invenlogic_v1`: one reader per capability (`daily_sales`, `customer_outstanding`, `customer_open_invoices`, `product_stock`, `warehouse_stock`, `low_stock_products`) over the `master_data`, `sales` and `inventory` databases. Reviewed `ErpQuery` constants only; names from `master_data` are read with a second query and joined in code (no cross-database foreign keys). A document status outside the contract raises the new `ErpMalformedError` → `DEPENDENCY_ERROR` / `MALFORMED_RESPONSE`; it is never guessed |
| Fake ERP | `adapters/fake.py` **deleted**. `ErpSettings.adapter` is now `none` or `postgres`; `fake_tenant_ids` removed; the `ERP_FAKE_ADAPTER` production check removed. `deploy/smoke` no longer uses the fake (it runs the `postgres` boundary without a contract) |
| Settings | New `ERP_DATABASE_URLS`: JSON object, contract database key → URL. The settings use `hide_input_in_errors`, so a malformed value is never echoed |
| Contract (`business/erp_contract.py`) | New `databases` (one identity per database key), `RelationRef.database`, `reader_set`, `currency`, `TenantIsolation.policy_function`, column role `match` (searched, never returned). Validation refuses a contract that confirms both year rules |
| Security gate (`business/erp_gate.py`) | Runs per database. With `policy_function` it checks that RLS policies call the function and, with `FUNCTION_QUERY`, that the function reads the confirmed setting |
| Adapter (`adapters/postgres.py`) | One `ReadOnlyErpDatabase` per database key; the adapter is `verified` only when every database is |
| Periods | New rule `year_is_financial_year_april`; `dates.py` resolves Indian financial years (1 April – 31 March); `service._period_span` requires exactly one year rule (calendar or financial), otherwise RULE_NOT_VERIFIED |
| Wiring | `business/factory.py` builds multi-database adapters and registers the InvenLogic readers; `agents/dependencies.build_dependencies(..., erp_port=)` accepts a prebuilt port (tests) |
| Production policy (`core/production.py`) | Checks every `ERP_DATABASE_URLS` entry: not the Agent DB, no development password; the contract's databases must match the URL keys |
| Readiness tool | `scripts/erp_readiness.py` accepts `ERP_INSPECT_DATABASE_URLS` (JSON key → URL) and checks each database |
| CLI | `python -m agentic_platform` no longer crashes printing ₹ to a redirected cp1252 Windows console (the character is replaced) |
| Contract | [docs/erp/invenlogic_contract.json](docs/erp/invenlogic_contract.json), status DRAFT, all six capabilities DRAFT |
| TEST replica | `docker/erp-test/`: PostgreSQL 18.6 with the Atlas tables, columns, types and forced RLS, and the read-only role `agentic_erp_reader` (port 127.0.0.1:5434; [README](docker/erp-test/README.md)). TEST rows only |
| Tests | `tests/erp_db.py` (replica helpers), `tests/business_helpers.py` (`ErpFaults`, fault injection replacing the fake's), `tests/erp_contract/test_invenlogic_contract.py`, `tests/test_invenlogic_unit.py`, `tests/integration/test_invenlogic_replica.py`. Tests that need the replica are skipped without `TEST_ERP_ADMIN_URL` / `TEST_ERP_READER_URL` |

### 3. Verification

| Check | Where | Result |
|---|---|---|
| ERP contract suite | TEST replica | **18/18** |
| `python -m scripts.erp_readiness --live` | TEST replica, three databases | **READY_FOR_ACTIVATION** for `master_data`, `sales` and `inventory` |
| Production factory path (real settings → factory → gate → readers) | TEST replica | outstanding **45000.00**; today's sales **125000.00** (7 documents); stock read |
| `python -m scripts.erp_readiness --contract docs/erp/invenlogic_contract.json` (offline) | no connection | **BLOCKED**, missing **A, B, C, D, E, F, G, T** only (before Phase 22: all 20 items A–T) |
| Full test suite | local | 1373 passed, 35 skipped, 0 failed with PostgreSQL 16, Redis 7 and the InvenLogic TEST replica (PostgreSQL 18.6); without the replica 966 passed, 442 skipped, 0 failed (baseline before Phase 22: 1326 passed, 18 skipped). ruff and mypy clean |
| Ollama end-to-end (business question through the local model) | local | 5 Hindi/English business questions through the CLI with Ollama qwen3:8b (LLM_OLLAMA_NUM_CTX=4096) and the InvenLogic TEST replica: 5/5 completed, every ERP read SUCCESS; outstanding ₹45,000, today's sales ₹1,25,000, iPhone 15 stock 15 pcs, low stock iPhone 3/5 and Galaxy 0/2 all correct; for "pending bills dikhao" the model picked customer_outstanding instead of customer_open_invoices (right total, no bill list). 2.4-8.1 min per turn on CPU |

The **live company ERP was NOT contacted**: no host, role or credentials have been supplied. The figures above are TEST rows on the replica, not company data.

### 4. Blocked: what the live ERP still needs

- **Q1 items A, B, C, D, E, F, G, T**: owner confirmation and intended-ERP confirmation (A, G), host, database names and PostgreSQL version of the three databases (B, C, D) and their Flyway versions (T), and the read-only role and its credentials through the secret store (E, F).
- **Assumptions to confirm on the live ERP:** the RLS setting name (`invenlogic.company_id` is the replica's value; the gate refuses the connection if the live function reads another setting), and the document status values (`POSTED`, `CANCELLED`, `DRAFT`, `DISCARDED`; run `SELECT DISTINCT status` on the four document tables).
- **Q29** (warehouse scope of sales and customer reads) and **Q8** (role permissions) remain open owner decisions.
- **Performance indexes** on `(company_id, customer_id)` and `(company_id, invoice_date)` are an ERP-owner action; the agent never creates indexes.

### 5. Ollama finding (local model)

The provider (`llm/ollama_provider.py`) was integrated on 2026-10-29; `.env` has `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen3:8b` (models in `D:\OllamaModels`, binary in `D:\Ollama`). Today, with `LLM_OLLAMA_NUM_CTX=16384` (the `.env` value) and with 8192, the model **failed to load**: llama-server ran out of memory allocating the KV cache (2.4 GB / 1.2 GB) with about 5.5 GB of 15.4 GB RAM free. 4096 loads. One turn takes about **5 minutes** on this CPU-only machine. The `.env` was **not** changed (the runs used environment overrides). Recommendation to the owner: free RAM (other Docker stacks are running) or set a smaller `LLM_OLLAMA_NUM_CTX`. OPERATIONS_RUNBOOK § 7, step 6.

### 6. Rules adopted (owner: "adopt your proposed defaults")

Daily sales are POSTED invoices' `grand_total` (GST inclusive) by invoice date, credit notes not deducted (gross); outstanding is open invoice and opening-bill balances minus advances and unapplied credit notes; stock is `quantity_on_hand` over all batches; low stock is at or below an active reorder rule's `min_qty`. Time zone Asia/Kolkata, weeks from Monday, calendar months, Indian financial years, INR. Full table: INVENLOGIC_MAPPING.md § 3.

### 7. Known gaps

- Capabilities not covered: product-wise sales, a customer's purchase history, payment history, purchases and suppliers, GST/accounting reads (ADR-066 path).
- `source.adapter` in the model's prompt context is now `"postgres"` (it was `"fake"`). It is a backend label and carries no data.
- A model reply that wrongly calls real data "TEST data" is not flagged by the reply verifier.

### 8. Owner's next steps

Follow [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6: supply A–G and T in the contract, have the DBA create the read-only role, confirm the RLS setting and statuses, run `erp_readiness --live` until it exits 0, mark the contract and capabilities VERIFIED, run the contract suite against dedicated TEST records in the ERP, then configure `ERP_ADAPTER=postgres` with `ERP_DATABASE_URLS` from the secret store. Also decide Q29 and Q8, and the Ollama memory setting (§ 3t.5).

## 3u. Routing repair, customer counts and live chat testing (2026-11-25/26) — TEST replica only

Owner request in the project chat: fix the agent's wrong routing, make the chat's example questions work, add 200 test customers and test live. Approved plan items A–F (2026-11-25).

### 3u.1 Root causes found (live logs)

* "Mere database me kitne user hain?" / "Aaj kitne customer aaye?": the planner must file an uncovered read under a reserved domain (`UnavailablePart.domain`), picked `purchase` (or `knowledge`), and the Supervisor answered "not available: purchase orders and other purchase actions".
* No capability counted customers. Login users live in `auth_db`, which is never read; the ERP records no registration date.
* Local-model tool calls: unprefixed tool names, invented arguments, model-computed dates ("kal" → wrong day), shortened names ("Godown" for "Main Godown"), clarification asked although the name was given.
* Model replies that mislabelled counts ("overdue: 99" for 99 open bills, 163 overdue) or denied data that had been read ("koi jaankari nahi").
* `invenlogic.open_bills` summed payments with a subquery per bill; `receivable_allocation` has no index on the bill columns, so 200 customers took 4–6 s and timed out under load.

### 3u.2 Changes

* Supervisor: a `read` part is never named by its domain (`READ_NOT_AVAILABLE_MESSAGE` lists what can be asked); prompt `understand_request.system.v6` and `supervisor.routing.v1` say uncovered reads are `unsupported` with no routing parts.
* New capability `customer_count` (permission `business.customer.read`, contract mapping DRAFT like the others, rule `customer_count_definition`): active/inactive customers without cash-sale placeholders; with a period/day, different customers billed (posted invoices, counter sales and cash-sale placeholders counted as bills only). Questions about *new* customers/registrations are answered "not recorded" instead of a count.
* Business domain: tool-name prefix resolution, invalid plans give way to the typed intent, user period words replace model dates ("aaj tak" stays a total), shortened names narrowed to the user's words, planner clarification dropped only when the user named the record.
* Verification: deterministic text for all-customer overdue lists and for replies that state none of the data read.
* `open_bills` rewritten with CTEs (same rows and amounts; contract suite passes): 0.012 s.
* Chat page: results as tables, two new example questions. Tests: `tests/test_customer_count.py` and regressions in the graph, supervisor and tool tests; `test_production_unit` no longer reads a developer `.env` at collection time.

### 3u.3 Test data

`scripts/seed_erp_demo.py --company-id 900000002 --customers 200` (TEST replica only, idempotent per company): 200 customers "… (TEST)", 196 active, 4 inactive, unique names, plus one cash-sale placeholder; company 900000001 keeps 100. Verified by read-only SQL.

### 3u.4 Live verification (2026-11-26, Ollama qwen3:8b, company 900000002)

20 questions through `/api/v1/agent/turn`, each checked against read-only SQL: all 20 answered correctly (customer counts 196/4, sales yesterday ₹2,55,978.72, this month ₹40,69,640.82, year comparison, low stock 8, Main Godown 22 products, overdue 163 of 195 open, Rahul Gupta not found, user count and new registrations refused honestly). Today's sales: no record (the TEST data was seeded the day before). 1–12 min per turn on CPU.

### 3u.5 Open

Hindi wording of the local model is sometimes wrong ("मशीन" for "महीना"); figures are checked, words are not. A short question without a name ("Godown ka stock dikhao") is answered with a clarification question. mypy cannot run (Windows Application Control). 25 source files were not ruff-formatted before this work and are unchanged.

## 4. Database changes (Phase 2)

| Change | Where | Notes |
|---|---|---|
| Role `agentic_owner` | Docker init | Owns schema; migrations only. Superuser in the local Docker image only |
| Role `agentic_app` | Docker init | LOGIN, no superuser/createdb/createrole; CONNECT + TEMPORARY; USAGE on `public`; default SELECT/INSERT/UPDATE/DELETE on tables and USAGE/SELECT on sequences created by `agentic_owner` |
| `REVOKE ALL ON DATABASE … FROM PUBLIC`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC` | Docker init | Both databases |
| Database `agentic` | Docker | Application database |
| Database `agentic_test` | Docker init | Integration tests only |
| Table `alembic_version` | Migration `0001` (baseline) | Alembic bookkeeping only |
| Business tables | — | **None.** None existed, and none were invented |

## 5. Files created (Phase 2)

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Local PostgreSQL 16 (healthcheck, volume, ports) |
| `docker/postgres/init/01-roles.sh` | Owner/app roles, grants, test database |
| `.gitattributes` | Keep `*.sh` LF-only |
| `alembic.ini` | Alembic CLI config (no credentials) |
| `src/agentic_platform/db/__init__.py` | Public API of the data layer |
| `src/agentic_platform/db/base.py` | `Base`, naming convention, `TimestampMixin` |
| `src/agentic_platform/db/models.py` | Model registry for Alembic (empty) |
| `src/agentic_platform/db/engine.py` | `Database`: engine, pool, sessions, disposal |
| `src/agentic_platform/db/unit_of_work.py` | Transaction boundary |
| `src/agentic_platform/db/repository.py` | `Repository[Model]` base |
| `src/agentic_platform/db/errors.py` | Driver exception → platform error translation |
| `src/agentic_platform/db/health.py` | `DatabaseHealthRepository` (read-only probes) |
| `src/agentic_platform/db/migrations/{__init__,env}.py`, `script.py.mako` | Alembic environment and template |
| `src/agentic_platform/db/migrations/versions/20261001_0001_baseline.py` | Baseline revision |
| `src/agentic_platform/services/database_health.py` | `DatabaseHealthService` |
| `src/agentic_platform/tools/database.py` | `system.database_health` tool |
| `tests/test_db_unit.py` | 37 unit tests (config, errors, health, wiring, SQL guard, migrations) |
| `tests/integration/{__init__,test_postgres}.py` | 9 PostgreSQL integration tests |

## 6. Files modified (Phase 2)

| Path | Change |
|---|---|
| `pyproject.toml` | Added `sqlalchemy[asyncio]`, `asyncpg`, `alembic`; packaged migration files; `postgres` pytest marker; mypy excludes migration versions |
| `.env.example` | Database, docker-compose and test variables |
| `src/agentic_platform/core/config.py` | `DatabaseSettings`, `normalise_postgres_url`, `AppSettings.database` |
| `src/agentic_platform/core/errors.py` | `DatabaseError` hierarchy |
| `src/agentic_platform/agents/dependencies.py` | Optional `Database`; registers `system.database_health` when configured |
| `src/agentic_platform/agents/agent.py` | `aclose()`; `run()` releases pooled connections after each call |
| `src/agentic_platform/__main__.py` | `--db-health`; interactive mode uses a single event loop |
| `tests/conftest.py` | Isolates `DATABASE_URL` / `DATABASE_MIGRATION_URL` from unit tests |
| `ARCHITECTURE.md` | Status header; new § A.5 |
| `ARCHITECTURE_DECISIONS.md` | ADR-014 status; ADR-022 to ADR-026 |
| `PROJECT_STATUS.md` | This file |

Phase 1 files are listed in ARCHITECTURE.md § A.4.

## 7. Environment variables

Real environment variables override `.env`. All are optional.

**Application, logging, LLM, agent (Phase 1)**

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | `development` / `test` / `production` |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Logging level; `json` or `text` (stderr) |
| `LLM_PROVIDER` / `LLM_MODEL` | `anthropic` / `claude-opus-5-5` | Provider adapter and model |
| `LLM_MAX_TOKENS` / `LLM_EFFORT` | `16000` / `medium` | Output cap; default effort |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `120` / `2` | Per-request timeout; SDK retries |
| `LLM_ANTHROPIC_FALLBACKS` | `default` | Server-side refusal fallback (`default` / `off`) |
| `ANTHROPIC_API_KEY` | unset | Credential (or `ANTHROPIC_AUTH_TOKEN` / `ant auth login`) |
| `AGENT_RECURSION_LIMIT` / `AGENT_MAX_TOOL_CALLS_PER_TURN` | `25` / `5` | Graph and tool budgets |
| `AGENT_MAX_INPUT_CHARS` / `AGENT_MAX_HISTORY_MESSAGES` | `8000` / `20` | Input limit; memory window |
| `AGENT_CHECKPOINTER` | `auto` | Phase 3. `auto` (PostgreSQL if `DATABASE_URL` is set, else memory) / `postgres` / `memory` / `none` |
| `AGENT_THREAD_LOCK_TTL_SECONDS` / `AGENT_THREAD_LOCK_WAIT_SECONDS` | `30` / `2` | Phase 3. Thread lock lease (auto-renewed) and how long a second turn waits before `THREAD_BUSY` |
| `AGENT_DOMAIN_TIMEOUT_SECONDS` | `120` | Phase 12. Deadline for one domain handler run (all its planned tool calls); exceeding it is a retryable `DOMAIN_TIMEOUT` |

**Database (Phase 2)**

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | unset (database disabled) | Runtime connection, **application role**. `postgres://`, `postgresql://` or `postgresql+asyncpg://`; anything else is rejected |
| `DATABASE_MIGRATION_URL` | falls back to `DATABASE_URL` | Schema-owner connection for Alembic only |
| `DATABASE_POOL_SIZE` | `5` | Persistent connections per process |
| `DATABASE_MAX_OVERFLOW` | `5` | Extra connections under burst |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `10` | Wait for a free connection, then `DATABASE_UNAVAILABLE` |
| `DATABASE_POOL_RECYCLE_SECONDS` | `1800` | Replace connections older than this |
| `DATABASE_POOL_PRE_PING` | `true` | Check connections before use |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | TCP/auth connect timeout |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `15000` | Server-side per-statement limit (`0` = none) |
| `DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS` | `60000` | Kills sessions idle inside a transaction |
| `DATABASE_SSL_MODE` | unset | `disable` / `prefer` / `require` / `verify-ca` / `verify-full` |
| `DATABASE_APPLICATION_NAME` | `agentic_platform` | Shown in `pg_stat_activity` |
| `DATABASE_HEALTH_TIMEOUT_SECONDS` | `3` | Health-check time limit |
| `DATABASE_ECHO_SQL` | `false` | Log SQL (debug only; may log data) |

**Redis (Phase 3)**

| Variable | Default | Meaning |
|---|---|---|
| `REDIS_URL` | unset (process-local fallbacks) | `redis://` or `rediss://`; for example `redis://:password@host:6379/0` |
| `REDIS_KEY_PREFIX` | `agentic` | Prefix of every key (`[A-Za-z0-9_.-]`) |
| `REDIS_MAX_CONNECTIONS` | `20` | Pool size per process |
| `REDIS_SOCKET_TIMEOUT_SECONDS` / `REDIS_CONNECT_TIMEOUT_SECONDS` | `2` / `2` | Command and connect timeouts |
| `REDIS_HEALTH_TIMEOUT_SECONDS` | `2` | Health-check limit |
| `REDIS_CACHE_DEFAULT_TTL_SECONDS` | `300` | Default cache TTL |
| `REDIS_TEMPORARY_STATE_TTL_SECONDS` | `3600` | Default temporary-state TTL |
| `REDIS_IDEMPOTENCY_CACHE_TTL_SECONDS` | `3600` | Fast-path TTL (capped by the PostgreSQL record's expiry) |
| `REDIS_CLIENT_NAME` | `agentic_platform` | Shown in `CLIENT LIST` |

**Memory (Phase 4)**

| Variable | Default | Meaning |
|---|---|---|
| `MEMORY_ENABLED` | `true` | Turn agent memory on/off |
| `MEMORY_DEFAULT_TENANT_ID` / `MEMORY_DEFAULT_USER_ID` | `default` / `local` | Tenant/user of the local development identity (Phase 5) |
| `MEMORY_EMBEDDING_PROVIDER` | `hashing` | `hashing` (local, lexical) or `voyage` (semantic) |
| `MEMORY_EMBEDDING_MODEL` | provider default (`voyage-3.5`) | Changing it requires re-ingesting |
| `MEMORY_EMBEDDING_DIMENSIONS` | `256` | Hashing embedder only |
| `MEMORY_EMBEDDING_TIMEOUT_SECONDS` | `30` | Voyage HTTP timeout |
| `VOYAGE_API_KEY` | unset | Required for `voyage` |
| `MEMORY_RECALL_K` / `MEMORY_RECALL_MIN_SCORE` | `5` / `0.2` | Hits per section; minimum cosine similarity |
| `MEMORY_RECALL_TIMEOUT_SECONDS` | `5` | Recall budget per turn |
| `MEMORY_CONTEXT_MAX_CHARS` | `4000` | Size of recalled context in the prompt |
| `MEMORY_RECORD_EPISODES` | `true` | Record an episode per finished turn |
| `MEMORY_EPISODIC_RETENTION_DAYS` | `90` | Episode expiry |
| `MEMORY_WORKING_TTL_SECONDS` | `3600` | Working-memory TTL |
| `MEMORY_MAX_CONTENT_CHARS` | `20000` | Max fact / procedure size |
| `MEMORY_CHUNK_CHARS` / `MEMORY_CHUNK_OVERLAP_CHARS` | `1200` / `150` | Document chunking |
| `MEMORY_AGENT_FACT_WRITES` | `off` | Phase 5. `approval`: offer the `memory.remember_fact` tool; every write needs the user's approval |

**Identity and approvals (Phase 5)**

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_TOKEN_SECRET` | unset (token sign-in disabled) | HMAC key for development tokens (at least 32 characters) |
| `AUTH_TOKEN_ISSUER` / `AUTH_TOKEN_MAX_TTL_SECONDS` | `agentic-platform-dev` / `43200` | Expected issuer; longest accepted token lifetime |
| `AUTH_ALLOW_LOCAL_IDENTITY` | unset = allowed unless `APP_ENV=production` | Local development identity (CLI flags, no principal) |
| `AUTH_LOCAL_ROLES` | `["member"]` | Roles of the local identity (JSON list) |
| `AUTH_ROLE_CATALOG_JSON` | unset (built-in roles) | `{"role": ["permission", ...]}` replacing the role catalogue |
| `APPROVAL_TTL_SECONDS` | `86400` | Validity of a pending approval |
| `APPROVAL_MIN_RISK` | `high` | Read tools at or above this risk need approval (`low`/`medium`/`high`/`never`); write tools always do |
| `AGENTIC_TOKEN` | unset | CLI only: token used when `--token` is not given |

**HTTP authentication and identity provider (Phase 6)**

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_PROVIDER` | unset = `dev_token` when `AUTH_TOKEN_SECRET` is set outside production | `dev_token` or `oidc`. Production must use `oidc`: the API refuses to start otherwise |
| `AUTH_TOKEN_AUDIENCE` | unset | Optional `aud` of development tokens (issued with it, required when verifying) |
| `AUTH_OIDC_ISSUER` | unset | Expected `iss`; base URL for discovery. Required for `oidc` |
| `AUTH_OIDC_AUDIENCE` | unset | Expected `aud` (this API). Required for `oidc` |
| `AUTH_OIDC_JWKS_URL` | discovered | Signing keys. https in production |
| `AUTH_OIDC_ALGORITHMS` | `["RS256"]` | Allowed asymmetric algorithms (RS/PS 256-512, ES256/384); HS* and `none` are refused |
| `AUTH_OIDC_USER_CLAIM` / `AUTH_OIDC_TENANT_CLAIM` / `AUTH_OIDC_ROLES_CLAIM` | `sub` / `tenant_id` / `roles` | Top-level claim names |
| `AUTH_OIDC_ROLE_MAP_JSON` | unset | `{"external": ["platform_role"]}`; unset = external names that are catalogue roles |
| `AUTH_OIDC_JWKS_CACHE_SECONDS` / `AUTH_OIDC_HTTP_TIMEOUT_SECONDS` / `AUTH_OIDC_LEEWAY_SECONDS` | `600` / `5` / `30` | Key cache, key-fetch timeout, clock skew |

**HTTP API and maintenance (Phase 6)**

| Variable | Default | Meaning |
|---|---|---|
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | `python -m agentic_platform.api` |
| `API_DOCS_ENABLED` | unset = on unless production | `/docs` and `/openapi.json` |
| `API_MAX_BODY_BYTES` | `1048576` | Larger bodies → 413 |
| `API_RUN_MAINTENANCE` | `false` | Run the maintenance scheduler inside the API process (development only) |
| `MAINTENANCE_BATCH_SIZE` | `500` | Rows per batch (one transaction) |
| `MAINTENANCE_MAX_BATCHES` | `200` | Batches per job per run |
| `MAINTENANCE_INTERVAL_SECONDS` | `3600` | Local scheduler interval (`--loop`, in-process scheduler) |
| `MAINTENANCE_JOB_LOCK_TTL_SECONDS` | `300` | Per-job lock lease (one runner per job) |

**Production readiness (Phase 7)**

| Variable | Default | Meaning |
|---|---|---|
| `APP_SECRETS_DIR` | unset | Directory of secret files named like the variables (`/run/secrets/DATABASE_URL`); env vars and `.env` win |
| `LLM_ANTHROPIC_BASE_URL` | unset (SDK default) | LLM endpoint override (egress gateway); https in production |
| `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` | unset (no bound) | Longest accepted `exp - iat`; recommended ≤ 900 |
| `API_GRACEFUL_SHUTDOWN_SECONDS` | `30` | SIGTERM: time for in-flight requests |

**Go-live hardening (Phase 8)**

| Variable | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_ENABLED` | unset = on in production, off elsewhere | Per-caller limits in the API (ADR-053) |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed window |
| `RATE_LIMIT_TURNS_PER_WINDOW` / `_APPROVAL_DECISIONS_PER_WINDOW` / `_WRITES_PER_WINDOW` / `_READS_PER_WINDOW` | `20` / `60` / `60` / `600` | Per tenant+user; `0` = class off. Provisional (Q20) |
| `RATE_LIMIT_FAIL_OPEN` | `true` | Redis down: allow (logged) or refuse (503) |
| `API_PROXY_HEADERS` / `API_FORWARDED_ALLOW_IPS` | `true` / `127.0.0.1` | Apply `X-Forwarded-*` only from these proxies; `*` refused in production |
| `DATABASE_COMMAND_TIMEOUT_SECONDS` | `20` | Client-side bound per database command (ADR-055) |
| `AGENTIC_MIGRATE_NON_EMPTY_DATABASE` | unset | Migration job only: `1` allows migrating a non-empty database without `alembic_version`. Never for an ERP/system-of-record database (ADR-056) |
| `GATEWAY_*` (gateway container) | see `docker-compose.yml` | `GATEWAY_UPSTREAM`, `GATEWAY_RESOLVER`, `GATEWAY_SERVER_NAME`, `GATEWAY_HTTPS_PORT`, `GATEWAY_REQUESTS_PER_SECOND` (20), `GATEWAY_BURST` (40), `GATEWAY_TURNS_PER_MINUTE` (30), `GATEWAY_MAX_CONNECTIONS_PER_IP` (20), `GATEWAY_TURN_TIMEOUT_SECONDS` (300), `GATEWAY_INTERNAL_CIDR` |
| `LOADTEST_*` (load-test overlay only) | see `deploy/loadtest/compose.loadtest.yml` | stub latency, pool sizes, raised limits |

**Production hardening (Phase 15, ADR-064; listed in Phase 17)**

| Variable | Default | Meaning |
|---|---|---|
| `API_SHUTDOWN_CLEANUP_SECONDS` | `10` (max 120) | After `API_GRACEFUL_SHUTDOWN_SECONDS`: budget for the bounded shutdown sequence (unwind cancelled requests, close Redis, Agent DB and ERP pools, flush). Orchestrator grace period > graceful + cleanup |
| `API_MAX_CONCURRENT_TURNS` | `32` | Agent turns at once per API process; one more → 503 `SERVER_BUSY` (retryable). `0` = unlimited (production warning). A protection bound, not a capacity figure |
| `OBSERVABILITY_EXPORTER` | `none` | Only `none` exists (Q3); other values are refused at startup |
| `OBSERVABILITY_FLUSH_TIMEOUT_SECONDS` | `5` | Shutdown: time for exporters to flush |
| `APP_BUILD_COMMIT` / `APP_BUILD_TIME` | `unknown` | Image build arguments (hex SHA / ISO UTC timestamp); other shapes become `unknown`. Shown by `--version`, `api.started`, `/ready` |

**Local infrastructure and tests (not read by the application)**

| Variable | Default | Used by |
|---|---|---|
| `POSTGRES_OWNER_PASSWORD` / `POSTGRES_APP_PASSWORD` | `agentic_owner_dev` / `agentic_app_dev` | `docker-compose.yml` (first start of an empty volume only) |
| `POSTGRES_PORT` | `5432` | `docker-compose.yml` host port |
| `TEST_DATABASE_URL` | unset → integration tests skipped | Owner role on `agentic_test` (must be a real env var; tests ignore `.env`) |
| `TEST_DATABASE_APP_URL` | unset → privilege test skipped | App role on `agentic_test` |
| `REDIS_PASSWORD` / `REDIS_PORT` | `agentic_redis_dev` / `6379` | `docker-compose.yml` |
| `TEST_REDIS_URL` | unset → Redis integration tests skipped | For example `redis://:agentic_redis_dev@localhost:6379/15`; each test uses a random key prefix |
| `KEYCLOAK_PORT` / `KEYCLOAK_ADMIN_PASSWORD` | `8080` / `keycloak_admin_dev` | `docker compose --profile idp-test` (reference provider, verification only) |
| `RUN_LIVE_LLM_TESTS` | unset → live LLM tests skipped | `1` = real (billed) requests in `tests/live/test_llm_live.py` |
| `LIVE_KEYCLOAK_URL` / `LIVE_KEYCLOAK_ADMIN_PASSWORD` | unset → skipped | Keycloak reference run of `tests/live/test_oidc_live.py` |
| `LIVE_OIDC_ISSUER` / `_AUDIENCE` / `_ACCESS_TOKEN` (+ `_TENANT_CLAIM`, `_ROLES_CLAIM`, `_ROLE_MAP_JSON`, `_EXPECTED_TENANT`, `_ALGORITHMS`) | unset → skipped | Provider-neutral live OIDC checks against a candidate provider |

## 8. Known issues and limitations

1. **No live LLM run yet.** There are no Anthropic credentials on this machine. The adapter is tested only against a stub client. The first live run should confirm structured outputs and the `server-side-fallback-2026-07-01` beta (otherwise set `LLM_ANTHROPIC_FALLBACKS=off`).
2. **Windows Application Control blocks some compiled Python extensions:** `psycopg[binary]` (bundled libpq DLLs) and `charset_normalizer` (harmless `RequestsDependencyWarning`). The data layer therefore uses **asyncpg** (ADR-022). *Phase 3: resolved for checkpointing by our own asyncpg-based saver (ADR-027). We now maintain that saver, so LangGraph upgrades need a compatibility check (run the integration tests).*
3. **Local Docker credentials are development defaults** and the Docker `agentic_owner` is a superuser. Production needs managed secrets, a non-superuser owner role, `DATABASE_SSL_MODE=verify-full`, backups and PITR. None of this is set up.
4. **Role/init script runs only on an empty volume.** Changing passwords or grants later requires `docker compose down -v` (destroys local data) or manual SQL.
5. **PgBouncer (transaction pooling) is not supported as configured.** asyncpg prepared statements need `statement_cache_size=0` there.
6. **Engine is bound to one event loop.** `Agent.run()` disposes the pool after every call, so it does not pool across calls. Long-lived callers must use `arun()` in one loop and `aclose()` on shutdown (the CLI does).
7. **No retry helper for transient database errors.** Errors carry `retryable`, but callers decide. Writes must never be retried without idempotency keys (ADR-012).
8. *(Resolved in Phase 5: tenant and actor recorded in `audit_log`; checkpoint, lock and turn-idempotency keys namespaced by tenant; thread ownership checked on every access. ADR-016 itself still awaits Q7.)* **No tenant scoping yet** (ADR-016 pending Q7). `audit_log.tenant_id` exists but is always NULL. Idempotency keys and checkpoint `thread_id`s are not namespaced by tenant or user, and nothing authorises a thread on resume. Anyone who knows a thread id can continue it. This needs a principal (proposed Phase 5).
9. **Integration tests share the `agentic_test` database** and are not safe to run in parallel (they migrate up/down and create/drop `it_widgets`). `audit_log` rows written by tests accumulate (append-only by design) until a test runs `downgrade base`.
10. Still open from Phase 1: *(Phase 5: permissions exist and write tools run through approval)* no auth or permissions, so all `write` tools are refused; first-draft prompts with no eval set; not a git repository (Q2). *(Audit, idempotency and durable checkpointing were done in Phase 3.)*
11. **Phase 3 limitations:**
    * No retention or pruning for checkpoints, audit rows or expired idempotency records. `IdempotencyService.purge_expired()` exists but no scheduler calls it (Q11).
    * The checkpoint saver is async-only (sync `graph.get_state()` raises; use `aget_state`). `alist` loads blobs and writes for each checkpoint.
    * The audit log is append-only against DML, but a superuser can drop the triggers. There is no hash chaining or off-host shipping.
    * Audit failures do not block read tools (they are logged at ERROR). Write tools must fail closed when they arrive.
    * Actors are recorded as `agent` with no id until authentication exists.
12. **Phase 3, part B limitations:**
    * *(Resolved in Phase 5, ADR-039.)* **Thread ownership is not checked.** Anyone with a thread id can read its status, continue it or resume it. Ids are not namespaced by tenant or user (proposed Phase 5).
    * **No "abandon pending turn" operation.** A thread whose turn can never complete stays blocked; start a new thread.
    * **Redis is a hard dependency for turns once configured.** The thread lock fails closed by design.
    * **Locks are not fencing tokens.** A turn that stalls past its lease is not aborted (lost-lock detection only logs). PostgreSQL constraints remain the final guard for business data.
    * **Without Redis, locks are per process.** Running several processes without `REDIS_URL` loses the one-turn-per-thread guarantee.
    * **Extra latency:** sync durability adds a checkpoint write per node, and the pending-turn guard adds a checkpoint read per turn.
    * **No production caller yet** for `Cache` and `TemporaryStateStore` (no memory or UI features exist yet).
    * **Local Redis uses a dev password and no TLS.** Production needs `rediss://`, managed secrets, ACLs and network isolation.
    * *(Resolved in Phase 5: `approval_gate`.)* **The production graph has no `interrupt()` node yet.** Interrupt resume is proven with a test-only handler.

13. **Phase 4 limitations:**
    * **Default embeddings are lexical** (`HashingEmbedder`): recall matches words, not meaning. Production needs `MEMORY_EMBEDDING_PROVIDER=voyage` (or another adapter); the Voyage adapter has not made a live call.
    * *(Resolved in Phase 5: scope derived from the principal.)* **Memory scope is caller-asserted**, not authenticated; anyone calling the API can claim any tenant/user until identity exists.
    * **No ANN index**: vector search is an exact scan within the tenant (fine for thousands of rows per tenant, not millions).
    * **RLS guards against application bugs**, not a compromised application (it sets its own session variables).
    * **Episodes store an LLM-written summary of each request** (possible PII) for 90 days by default; no scheduled purge yet.
    * *(Resolved in Phase 5: tenant-namespaced keys + RLS; `AuthorizedMemory.history` checks ownership.)* **Checkpoints are not tenant-bound**: `ConversationMemory.history` and `CheckpointMemory` read by thread id alone.
    * **Recall latency**: one embedding call plus up to five store reads per turn, bounded by `MEMORY_RECALL_TIMEOUT_SECONDS`.
    * The pgvector extension must be available on the production PostgreSQL service.

14. **Phase 5 known issues and security limitations:** see §3d items 13 and 14.
15. **Phase 6 known limitations:** see §3e item 20. *(Phase 6 also resolved: Phase 3's "no scheduler calls `purge_expired()`" and Phase 4's "no scheduled purge yet" for documented expiry; retention of data without an expiry rule is still open, Q11.)*
16. **Phase 7 known limitations:** see §3f item 14. *(Phase 7 resolved: Known issue 1 partially (the real endpoint was reached; a successful completion still needs credentials); Known issue 3 is now enforced in production by the configuration policy (development passwords, runtime-as-owner, TLS warnings).)*
17. **Phase 8 known limitations:** see §3g item 12. *(Phase 8 resolved: rate limiting and TLS termination (Phase 6/7 limitation) at the deployment-neutral gateway and in the API.)*
18. **Phase 12 known limitations:** see §3k item 8. *(Phase 12 resolved: §3j limitation 7, checkpoints now use an explicit type allow-list instead of LangGraph's permissive default.)*

## 9. Open questions (unchanged unless noted)

1. Is there an existing codebase, database or API elsewhere (ERP/Tally/SAP) to integrate with or replace? *(Phase 2 found no database; please confirm none exists elsewhere.)* *(Phase 8: the stakeholder brief states that an existing ERP database is the system of record. The repository has no connection to it, and the migration job now refuses any non-empty database it does not own (ADR-056). Its location, owner and access rules are needed before any integration phase.)* *(Phase 9: still unanswered and now **the main blocker**. The access contract fields are listed in docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md § 2. A local `inven-logic` project with an `inventoryai-postgres` container exists on the development machine. Please confirm whether it reflects the production ERP schema. It was not touched.)* *(Phase 18: still the main blocker; readiness reports A–T missing (exit 3). What each item changes, who confirms it and the safe behaviour until then: docs/erp/ERP_CONTRACT.md § 7. Not contacted.)*
2. Git remote location; may we `git init` here?
3. Deployment target and data residency? *(Phase 8: still open; a deployment-neutral package exists: image, TLS gateway, env templates, runbooks, go-live checklist. Manifests, certificates, log shipping, alerts and backups wait for this answer.)* *(Now also: managed PostgreSQL service, for example RDS / Azure Database / Cloud SQL / on-prem, and its PostgreSQL version.)* *(Phase 9: still open; DEPLOYMENT TARGET: BLOCKED.)*
4. LLM provider/model and whether external SaaS LLM calls are allowed for business data; LangSmith?
5. Confirm FastAPI (SQLAlchemy/Alembic now in use, ADR-014). Frontend technology? *(Phase 6: FastAPI is now in use for the HTTP API; the frontend, its origins (CORS) and session handling are still open.)*
6. Identity provider? *(Phase 7: options and requirements in §3f.3; the adapter is verified against a reference Keycloak; the production choice is still needed.)* *(Phase 5: needed to replace the development tokens; the `Authenticator` protocol is ready.)* *(Phase 6: an OIDC/JWT adapter exists. Needed: the provider (Keycloak, Entra ID, Okta, Auth0, company SSO), its issuer and audience, which claim carries the tenant, how roles/groups map to platform roles, and whether its tokens are JWTs with top-level claims.)* *(Phase 9: still open; production IdP BLOCKED.)*
7. Single or multi-company / multi-GSTIN / multi-branch (ADR-016)? *(Needed before the first business table.)* *(Phase 18: it also decides the ERP tenant mapping (Q1 H) and ownership rule (Q1 K), and blocks tax and accounting reads.)*
8. Role catalogue and approval rules (RBAC, HITL)? *(Phase 5: provisional platform roles `member`, `knowledge_manager`, `approver`, `auditor`, `tenant_admin`; business roles and approval rules still needed.)* *(Phase 18: also which roles receive the `business.*` read permissions, and the proposed `business.purchase.read`; ADR-066.)* *(Phase 20: **PENDING — OWNER DECISION REQUIRED.** The Q29 proposals grant nothing; no role grant is proposed (§ 3r).)*
9. Purchase workflow details (statuses, partial GRNs/settlements, matching rules, cancellations, GST placement). *(Phase 18: this blocks every supplier and purchase read: purchase orders, GRNs, purchase invoices, supplier history (docs/erp/ERP_CONTRACT.md § 6.6).)*
10. May agents ever write without approval? *(Phase 5: no; every write tool needs approval.)*
11. Data retention and PII rules for conversation, long-term and episodic memory? *(Now also database backup retention.)* *(Phase 6: the maintenance framework applies existing expiry only. Still undefined, so not scheduled: LangGraph checkpoints, the audit log (including tenant-less `auth.failed` and `maintenance.run` rows), decided/expired approval requests and their summaries, conversation ownership rows.)*
12. Can IT allow-list psycopg's DLLs on developer machines (Known issue 2)? *(No longer blocking: asyncpg saver, ADR-027.)*
13. Production Redis: managed service (ElastiCache / Azure Cache / Memorystore / self-hosted), TLS, ACLs, and whether one instance may be shared across environments (key prefixes allow it)?
14. **New:** Embedding provider for production (Voyage AI or another), data-residency constraints for sending knowledge text to it, and the model/dimension to index?
15. Who may write tenant-shared memory (knowledge documents, procedures, shared facts)? *(Phase 5: provisionally `memory.shared.write`, granted to `knowledge_manager` and `tenant_admin`; please confirm.)*
16. **New:** Conversation sharing and administrator access: may users share a conversation, and may an administrator read, delete or erase another user's conversations and memory (for example for right-to-erasure requests)? Today only the owner can.
17. **New:** Separation of duties for future business approvals: which operations need a different approver than the requester, and must approvers be re-checked at execution time?
18. **New (Phase 6):** May an approver's decision resume the requester's paused turn automatically (for example a worker resuming as the requester), or must the requester always resume it, as today?
19. **New (Phase 6):** Session and token lifetime requirements: is token revocation (logout, disabled users) required before go-live, and what access-token lifetime does the identity provider issue? *(Phase 7: strategy in ADR-048: short tokens + `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS`, no immediate revocation; still needs the business answer.)*
20. **New (Phase 6):** Rate limits and abuse protection per user/tenant, and where they live (API gateway or application)? *(Phase 8: both layers implemented (ADR-053): per address at the gateway, per tenant+user in the API. The values are provisional configuration; the business limits (per user, per tenant, LLM budget) are still needed.)*
21. **New (Phase 6):** Which scheduler runs maintenance in production (cron, Kubernetes CronJob, cloud scheduler), and how often? *(Phase 7: either works; a scheduled one-shot job is recommended, hourly by default; the choice follows Q3.)*
22. **New (Phase 7):** Who provides the production Anthropic credential (organisation/workspace, API key vs workload identity federation), and may business data be sent to the API (Q4)? The first live smoke test waits for it. *(Phase 8: still no credential; the live smoke test remains NOT VERIFIED.)* *(Phase 9: still no credential; CLAUDE LIVE VERIFICATION: BLOCKED.)*
23. **New (Phase 7):** Is an egress gateway or proxy mandatory for outbound LLM traffic (`LLM_ANTHROPIC_BASE_URL`)?
24. **New (Phase 8):** Must a retried *first* turn (no `thread_id`) be deduplicated? Today an `idempotency_key` deduplicates per conversation only, so a client that loses the response of a new-conversation turn and retries creates a second conversation. Options: clients name the conversation first (no change), or the server derives the conversation from (tenant, user, key).
25. **New (Phase 8):** Expected traffic (users, turns per hour, peak concurrency) for sizing replicas, pools and the LLM rate limit, and who owns production on-call (runbook escalation).
26. **New (Phase 9):** The Phase 9 brief treats an ERP read layer, typed business tools, a supervisor, inventory intelligence and a business query agent as existing. They are not in this repository. May they be built as a separate phase once Q1 (ERP access) and the business rules (docs/SCHEMA_INTEGRATION.md § 4) are supplied? Or is there another repository that contains them? *(Phase 10: the foundation was approved and built (§ 3i): contracts, fake ERP, read-only boundary. Each capability's live mapping and rule still need the ERP owner (Q1) before it can answer.)* *(Phase 11: the reasoning foundation on top was built against the fake ERP (§ 3j). The live adapter must also map `sales_by_day` and the new filters, and declare only the business rules the ERP owner confirms.)*

27. **New (Phase 16):** Customer purchase history: which documents count as a customer's purchase (invoices, cash memos, orders?), how cancellations, returns and credit notes are treated, whether "how many products" means units, distinct products or both, and which ERP relations hold sales lines. Needed before a customer-sales capability can exist (BLOCKED in Phase 16). *(Phase 18: still open; it also blocks product-level sales. To be confirmed by the business owner (sales and accounts) for the meaning and by the ERP application owner for the relations. A proposed read contract is in docs/erp/ERP_CONTRACT.md § 6.2–§ 6.3; until Q27 is answered these questions stay `unsupported`, with no fake records.)*
28. **New (Phase 16):** Does "pichhle saal" / "this year" mean the calendar year or the financial year (April–March), and should comparisons use the same calendar dates or the same weekdays? Phase 16 implements calendar year and same dates as named rules the ERP owner must confirm. *(Phase 18: still open; to be confirmed by the business owner (finance). Until then the live adapter answers year periods and comparisons with RULE_NOT_VERIFIED.)*
29. **New (Phase 18):** Are sales, invoices, customer balances, payments and purchase documents attributed to a warehouse or outlet in the ERP, and must a user with a limited warehouse scope see only their own? Where does a user's warehouse assignment come from (ERP, identity provider or platform)? Today only stock reads apply warehouse scope; any holder of `business.sales.read` or `business.customer.read` sees tenant-wide figures. To be confirmed by the business owner, the ERP application owner and a security reviewer. Until then the six existing capabilities keep their documented behaviour, and any new document-level capability answers only callers with scope ALL (docs/erp/ERP_CONTRACT.md § 6.1, § 7). *(Phase 19: confirmed in code and against the fake ERP; the decision options and affected paths are in § 3q.4. It now blocks a change, not just documentation.)* *(Phase 20: option (a), company-wide for authorized users, proposed separately for sales and for customer outstanding / invoices. Both are **PROPOSED — OWNER APPROVAL PENDING**, so Q29 is still open (§ 3r).)*
30. **New (Phase 18):** From which date is the ERP's history complete (migration cut-over, archiving, purging), and can the ERP state it? Without it, "no sales" and "no data" cannot be told apart, and a year-over-year figure on partial history would be wrong. To be confirmed by the ERP application owner. Until then a period without records has no total and no change is computed (ADR-065).

## 10. Next phase (proposal; NOT started)

*(Phase 18 note, 2026-10-10: Phase 19 has not been started. The ordered ERP integration plan is in docs/erp/ERP_CONTRACT.md § 10. It starts with the owner's answers to Q1, Q7, Q29 and Q8; new capabilities wait for Q27, Q9 and Q30. The notes below are kept for history.)*

Phase 12 is complete as the Supervisor / orchestration foundation (§3k). Phase 13 has not been started. Live routing needs Q22 (observe Claude's classification of the §3k.4 examples); live business answers still need Q1. *(Earlier note kept below for history.)* Phase 11 is complete as the database-independent reasoning foundation (§3j). Phase 12 had not been started at that point. Live business answers still need Q1 (mapping, rules, tenant mechanism, business time zone), Q4 and Q26 per capability (§3j.10). *(Earlier notes kept below for history.)* Phase 10 is complete as a foundation (§3i). Phase 9 is **NOT COMPLETE / BLOCKED** (§3h). Unblock it with Q1, Q22, Q6 and Q3, in that order (§3h.10). The ERP read layer and business tools need a separate build phase (Q26). Phase 10 has not been started. *(Earlier notes kept below for history.)* Phase 8 is complete (§3g); go-live is blocked on Q3, Q6 and Q22 (GO_LIVE_CHECKLIST.md). *(Phase 7 note kept below for history.)* Phase 7 is complete (§3f). Recommended: **go-live hardening** once Q3 (deployment target), Q6 (identity provider) and Q22 (LLM credential) are answered (§3f.17). Other options (carried over from the Phase 6 proposal; option 1 was delivered as Phase 7 except the items that need those answers):

1. **Production identity and deployment** (recommended if go-live is near): choose the identity provider (Q6) and verify the OIDC adapter against it, decide token lifetime and revocation (Q19), container images and process model for the API and the maintenance worker (Q21), managed PostgreSQL/Redis with TLS and secrets (Q3, Q13), rate limits (Q20), and the first live Claude run (Known issue 1).
2. **Tool System:** a catalogue of read and write tools on the Phase 5 approval boundary, exposed through the existing API.
3. **Supervisor and domain-agent framework** (ADR-003): routing to domain subgraphs, still without business logic until the business documentation exists.
4. **Memory quality:** production embeddings (Q14), an HNSW index, automatic summaries.
5. **Purchase domain:** still waits for business documentation (Q9) and the role and approval rules (Q8, Q17).

Superseded Phase 6 proposal (kept for history): FastAPI service, OIDC/JWT authenticator, request ids, health/readiness, scheduled maintenance. All delivered in §3e except rate limits (Q20) and retention decisions (Q11).
