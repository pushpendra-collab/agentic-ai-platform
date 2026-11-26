# Architecture Decision Records

> Format: lightweight ADRs. Status is one of **Accepted** (given by stakeholder or a non-negotiable principle), **Proposed** (recommended in Phase 0, awaiting confirmation) or **Superseded**.
> **Last updated:** 2026-10-30 (Phase 22: ADR-067 Accepted, InvenLogic readers and removal of the fake ERP; dated notes on ADR-059, ADR-063, ADR-064 and ADR-066; see PROJECT_STATUS § 3t. Before that, 2026-10-10, Phase 18, documentation only: ADR-066 Proposed, the conventions for future ERP read capabilities; see PROJECT_STATUS § 3p. Before that, Phase 17: ADR-064 written down from the Phase 15 code and tests (§ 3o); Phase 16: ADR-065)

| # | Decision | Status |
|---|---|---|
| ADR-001 | Treat the project as greenfield | Accepted (verified by inspection) |
| ADR-002 | LangGraph as the agent orchestration framework | Accepted |
| ADR-003 | Supervisor + domain-agent subgraphs | Partly implemented (Phase 12: deterministic Supervisor over the planner's classification, ADR-061; no domain subgraphs yet) |
| ADR-004 | LLM never has direct database/SQL access | Accepted |
| ADR-005 | Layered backend: Tool → Service → Repository → DB | Accepted |
| ADR-006 | PostgreSQL is the source of truth | Accepted |
| ADR-007 | Redis only for ephemeral/rebuildable state | Accepted (implemented in Phase 3, ADR-030) |
| ADR-008 | pgvector for semantic memory; never a source of truth | Accepted (implemented in Phase 4, ADR-034) |
| ADR-009 | PostgreSQL-backed LangGraph checkpointing | Accepted (Phase 3, implemented per ADR-027; tenant-namespaced keys and ownership since Phase 5, ADR-039) |
| ADR-010 | Human-in-the-loop approval for write tools by default | Accepted (implemented in Phase 5, ADR-040) |
| ADR-011 | Central tool registry with permission + audit wrapper | Accepted (permission, risk and approval steps added in Phase 5, ADR-038/040) |
| ADR-012 | Mandatory idempotency keys for write operations | Partially implemented (store ADR-029, fast-path ADR-030; approved write executions are idempotent per approval since Phase 5, ADR-040) |
| ADR-013 | Agents act as the user (no super-user agent identity) | Accepted (implemented in Phase 5, ADR-037) |
| ADR-014 | Python + FastAPI + SQLAlchemy/Alembic stack | Accepted (FastAPI in use since Phase 6, ADR-043) |
| ADR-015 | Modular monolith with vertical domain slices | Proposed |
| ADR-016 | Multi-tenancy readiness (`tenant_id` everywhere) | Proposed (pending Q7); applied to memory (Phase 4) and to conversations, approvals, audit and checkpoints (Phase 5, ADR-041) |
| ADR-017 | Phase 1 narrowed to the agent foundation (no DB/Redis/API) | Accepted (stakeholder instruction) |
| ADR-018 | Own `LLMProvider` interface; Anthropic is the first adapter | Accepted (Phase 1) |
| ADR-019 | Domain handlers behind `process_request` as the domain-agent extension point | Accepted (Phase 1) |
| ADR-020 | Per-turn state fields are overwritten and reset each turn | Accepted (Phase 1) |
| ADR-021 | Python package name `agentic_platform` | Accepted (provisional) |
| ADR-022 | SQLAlchemy 2 async + asyncpg as the only PostgreSQL driver stack | Accepted (Phase 2) |
| ADR-023 | Unit of Work is the only transaction boundary; repositories never commit | Accepted (Phase 2) |
| ADR-024 | Separate schema-owner and application database roles | Accepted (Phase 2) |
| ADR-025 | Alembic migrations, packaged with the code, linear history | Accepted (Phase 2) |
| ADR-026 | Database is optional at runtime; health reports ok/degraded/unavailable | Accepted (Phase 2) |
| ADR-027 | Own asyncpg-based LangGraph checkpoint saver instead of the psycopg one | Accepted (Phase 3, stakeholder choice) |
| ADR-028 | Append-only `audit_log`, enforced by database triggers; tool calls audited | Accepted (Phase 3) |
| ADR-029 | Durable idempotency records with leases and claim tokens | Accepted (Phase 3; scheduled purge since Phase 6, ADR-044) |
| ADR-030 | Redis layer: one backend contract, in-memory fallback, TTL on every key, fail-open vs fail-closed per use | Accepted (Phase 3) |
| ADR-031 | One turn at a time per thread via a distributed lock | Accepted (Phase 3) |
| ADR-032 | Thread model and resume: sync durability, pending-turn guard, `aresume` | Accepted (Phase 3) |
| ADR-033 | Memory architecture: manager → memory types → ports → adapters | Accepted (Phase 4) |
| ADR-034 | One `memory_item` table, pgvector, untyped vectors + model column, tenant RLS | Accepted (Phase 4) |
| ADR-035 | Embedding abstraction: local hashing default, Voyage adapter | Accepted (Phase 4) |
| ADR-036 | Agent recall before each turn, episodes after; memory never fails a turn | Accepted (Phase 4) |
| ADR-037 | Authenticated `Principal`; replaceable authenticators; dev tokens and local identity | Accepted (Phase 5; amended by ADR-043: OIDC provider, no local identity over HTTP) |
| ADR-038 | Platform permission model, role catalogue and one `Authorizer`, enforced in code | Accepted (Phase 5) |
| ADR-039 | Conversation ownership: one owner, no sharing, tenant-namespaced storage keys | Accepted (Phase 5; sharing is open question Q16) |
| ADR-040 | Human-in-the-loop approvals: `approval_request`, `approval_gate`, safe resume | Accepted (Phase 5; amended by ADR-046) |
| ADR-041 | Tenant RLS on conversations, approvals, audit log and checkpoints | Accepted (Phase 5) |
| ADR-042 | Security audit trail: actor model, correlation, no secrets, fail-closed writes | Accepted (Phase 5) |
| ADR-043 | HTTP API (FastAPI) as a thin boundary; bearer authentication through a replaceable identity-provider interface | Accepted (Phase 6) |
| ADR-044 | Scheduled maintenance: bounded, idempotent jobs for existing expiry rules; scheduler-agnostic runner | Accepted (Phase 6) |
| ADR-045 | Server-generated correlation ids, one error envelope, liveness vs readiness | Accepted (Phase 6) |
| ADR-046 | Requester-only approvals; an executed approval never runs again | Accepted (Phase 6; amends ADR-040) |
| ADR-047 | Production configuration policy enforced at process start | Accepted (Phase 7) |
| ADR-048 | Token lifecycle: short-lived JWT access tokens, no API-side revocation; bounded lifetime | Accepted (Phase 7; lifetime value depends on Q6/Q19) |
| ADR-049 | Secrets: environment variables or secret files; no secret manager SDK in the application | Accepted (Phase 7) |
| ADR-050 | Deployment: one image, three process roles (API, worker, migration job); managed PostgreSQL and Redis | Accepted (Phase 7; target platform still Q3) |
| ADR-051 | Identity provider: verified against a reference OIDC implementation; production selection still open | Accepted (Phase 7; selection is Q6) |
| ADR-052 | LLM failures are translated without provider payloads; LLM outage degrades, never gates readiness | Accepted (Phase 7) |
| ADR-053 | Two-layer rate limiting: per address at the gateway, per verified caller in the API (Redis) | Accepted (Phase 8; limit values provisional, Q20) |
| ADR-054 | Deployment-neutral TLS gateway (nginx) in front of the API; authentication stays in the API | Accepted (Phase 8; target platform still Q3) |
| ADR-055 | Hung dependencies: readiness has a hard caller-side deadline; client-side DB command timeout | Accepted (Phase 8) |
| ADR-056 | The migration job refuses a non-empty database it does not own (Agent DB / system-of-record separation) | Accepted (Phase 8) |
| ADR-057 | Go-live gate: evidence-based checklist; nothing marked done without a run | Accepted (Phase 8) |
| ADR-058 | ERP access is verified read only from the live catalog by an operator tool before any integration; the application gets no ERP connection until the access contract exists | Accepted (Phase 9; ERP access blocked, Q1) |
| ADR-059 | Business read layer: typed capabilities behind an ERP read port, deterministic result statuses, fake adapter for tests, read-only PostgreSQL boundary without a schema mapping, figure grounding of replies | Accepted (Phase 10; live ERP NOT VERIFIED, Q1) |
| ADR-060 | Business query reasoning on deterministic results: typed intent, service-resolved dates, rule-gated derived figures, composition, reference-only focus | Accepted (Phase 11; real ERP NOT VERIFIED, Q1) |
| ADR-061 | Supervisor: a deterministic routing node over the planner's classification; closed domain catalogue with reserved future domains; one decision and one domain run per turn; common domain result contract; checkpoint type allow-list | Accepted (Phase 12; fake ERP only) |
| ADR-062 | Observability: typed in-process events correlated request → turn → span (separate from audit), operational error taxonomy, redaction by construction, strict deadlines, opt-in retry for ERP reads only, bounded id-free metrics, deterministic evaluation with golden snapshots | Accepted (Phase 13; fake ERP only) |
| ADR-063 | ERP integration only through an owner-confirmed contract (Q1), a fail-closed security gate on effective privileges, schema and isolation, database-layer tenant binding, and per-capability activation | Accepted (Phase 14; real ERP BLOCKED at the input gate) |
| ADR-064 | Production hardening and operational readiness: bounded shutdown sequence, per-process concurrent-turn cap (`SERVER_BUSY`), build information, an ERP readiness component that never gates or connects, stricter production policy, an exporter boundary without an exporter, pinned image dependencies, Agent DB backup scripts | Accepted (Phase 15, implemented 2026-10-05; record written in Phase 17 from the code and tests; deployment smoke test and backup scripts NOT RUN) |
| ADR-065 | Historical sales comparison as a rule-gated extension of `daily_sales` (no new capability or ERP read); long-term business memory as analysis references and provenance labels, never figures; every business-domain reply verified, percentages matched to computed ones, no "profit" for sales | Accepted (Phase 16; fake ERP only; customer purchase history and product-level sales BLOCKED, Q1) |
| ADR-066 | Future ERP read capabilities (customer sales history, product sales, payment history, supplier and purchase reads, history coverage) extend the existing contract, port, service and tool conventions; no parallel framework; document-level reads fail closed to scope ALL until warehouse attribution is decided (Q29) | Proposed (Phase 18; specification only, nothing implemented; needs Q1, Q8, Q9, Q27, Q29, Q30) |
| ADR-067 | InvenLogic (owner-confirmed real ERP) read through six reviewed capability readers over three of its databases; multi-database contract, gate and adapter; Indian financial-year rule; the fake ERP deleted, tests on a PostgreSQL 18.6 TEST replica | Accepted (Phase 22, 2026-10-30; verified on the TEST replica; live ERP NOT CONTACTED, A–G and T missing) |

---

### ADR-001: Treat the project as greenfield
**Context:** On 2026-10-01 the project directory was inspected. It contained zero files, and no git repository, backend, frontend, DB config or tests.
**Decision:** There is no existing infrastructure to reuse, so Phase 1 starts from scratch.
**Consequences:** If an existing codebase is found elsewhere, this ADR is superseded and the inspection must be repeated before any implementation.

### ADR-002: LangGraph as the agent orchestration framework
**Context:** Stakeholder requirement.
**Decision:** Use LangGraph (StateGraph, subgraphs, conditional edges, `interrupt()`, checkpointers, store).
**Consequences:** We get explicit, inspectable, resumable graphs and first-class HITL. Python is the natural language (most mature LangGraph SDK). LangChain packages are used only where they add value (model and tool abstractions). We avoid deep coupling.

### ADR-003: Supervisor + domain-agent subgraphs
**Context:** Many business modules (Purchase, Sales, Inventory, Accounting, GST, ...) with distinct tools and rules.
**Decision:** A top-level supervisor routes to per-domain agent subgraphs. Each agent sees only its own tools. The routing decision is a structured output, and low-confidence routing goes to a clarification step.
**Alternatives:** (a) a single agent with all tools, rejected because tool overload degrades accuracy and permissions get harder to reason about; (b) the prebuilt `langgraph-supervisor` package, which is acceptable later but a hand-built router gives tighter control over routing, guards and audit.
**Consequences:** Modules can be added independently. Cross-domain requests need the supervisor to sequence agents, and this must be tested.

### ADR-004: LLM never has direct database/SQL access
**Context:** Security requirement. The LLM output is untrusted (hallucination, prompt injection).
**Decision:** No SQL-execution tool, no text-to-SQL against production, and no DB credentials exposed to the model. The only path is LLM → LangGraph → registered Tool → Business Service → Repository → DB.
**Consequences:** Every capability must be built as an explicit tool. Ad-hoc analytics will later need a safe design (for example parameterised, whitelisted report services or a read-only, row-level-secured analytics replica). That is a future ADR.

### ADR-005: Layered backend: Tool → Service → Repository → DB
**Decision:** Tools are thin adapters. Business rules live only in services. Repositories do data access only. The REST API and the agent tools call the **same** services.
**Consequences:** Business rules are enforced identically for humans and AI. Rules are testable without an LLM.

### ADR-006: PostgreSQL is the source of truth
**Decision:** All transactional and master data, audit logs, checkpoints and long-term memory live in PostgreSQL. Schema is managed through Alembic migrations only.

### ADR-007: Redis only for ephemeral/rebuildable state
**Decision:** Redis is used for cache, locks, rate limits, idempotency fast-path and short-lived session state. Flushing Redis must never lose business data.

### ADR-008: pgvector for semantic memory; never a source of truth
**Decision:** Start with pgvector inside the existing PostgreSQL (one fewer system to operate, transactional consistency, and tenant filtering through SQL). Vector hits are pointers that must be re-validated against PostgreSQL before use in decisions.
**Alternatives:** Qdrant/Weaviate/Pinecone, to revisit if scale or latency requires it. The `SemanticMemory` interface keeps the store swappable.

### ADR-009: PostgreSQL-backed LangGraph checkpointing
**Decision:** Use the LangGraph Postgres checkpointer for durable, resumable threads. The in-memory saver is used only in tests. `thread_id` is namespaced by tenant and user and is authorised on resume.

### ADR-010: Human-in-the-loop approval for write tools by default
**Decision:** Every tool with `kind=write` requires explicit user approval through `interrupt()` unless a documented business rule exempts it. Approvals are audited.
**Consequences:** Safer by default, at the cost of more friction. Exemptions need business sign-off.

### ADR-011: Central tool registry with permission + audit wrapper
**Decision:** All tools are registered centrally with metadata (permission, kind, approval, idempotency, timeout). The registry wrapper performs these steps in order: permission check → validation → idempotency → execute → audit → metrics. Tools cannot be invoked outside the registry.

### ADR-012: Mandatory idempotency keys for write operations
**Context:** Graph resumes, retries and duplicate user actions can re-execute writes.
**Decision:** Every write command carries an idempotency key. The result is stored durably in PostgreSQL, with a Redis fast-path. A replay returns the stored result.

### ADR-013: Agents act as the user
**Decision:** The agent executes with the authenticated user's principal and permissions. There is no privileged "agent" account. The principal comes from auth and is never from LLM output.

### ADR-014: Python + FastAPI + SQLAlchemy/Alembic stack
**Status:** Proposed. Confirm with the team, because there is no existing stack to stay consistent with. *(Phase 6: FastAPI is in use for the HTTP API, ADR-043. All parts of this ADR are now in the code.)*
**Rationale:** Matches LangGraph's primary SDK and gives one language for agents and services. Async-capable, with mature tooling.

### ADR-015: Modular monolith with vertical domain slices
**Decision:** Start with one deployable backend organised as independent domain packages with clear boundaries (domains talk through service interfaces, not each other's tables).
**Consequences:** Simple to operate now. Domains can be extracted into services later if needed.

### ADR-016: Multi-tenancy readiness
**Status:** Proposed, pending an answer on whether the platform serves multiple companies or GSTINs.
**Decision (provisional):** Include `tenant_id` in business tables, memory namespaces, thread IDs and the audit log from day one. Adding it later is expensive.

### ADR-017: Phase 1 narrowed to the agent foundation
**Context:** PROJECT_STATUS.md §8 (Phase 0) recommended a broad Phase 1 including Docker Compose, PostgreSQL, Redis, FastAPI and auth. The stakeholder's Phase 1 instruction (2026-10-01) limited Phase 1 to the LangGraph agent foundation and excluded PostgreSQL, Redis and vector storage.
**Decision:** Phase 1 delivers the agent runtime only: config, logging, errors, LLM abstraction, prompts, tools/services skeleton, typed state, graph, entry point and tests. The other §8 items move to later phases.
**Consequences:** No persistence, checkpointing is in-memory only, and there is no principal, audit or idempotency store. Because HITL, permissions and idempotency do not exist yet, the tool registry **refuses every `write` tool** (see ADR-010, ADR-012).

### ADR-018: Own `LLMProvider` interface; Anthropic is the first adapter
**Context:** The LLM provider is still an open question (PROJECT_STATUS Q4). Nodes must not depend on a vendor SDK.
**Decision:** Nodes depend on `agentic_platform.llm.LLMProvider` (`generate_text`, `generate_structured`). Adapters map vendor errors to the `LLMError` hierarchy. The first adapter uses the official `anthropic` SDK with model `claude-opus-5-5`, structured output via `messages.parse(output_format=<Pydantic model>)`, and the server-side refusal fallback (`fallbacks="default"`, which can be turned off with `LLM_ANTHROPIC_FALLBACKS=off`). The provider is chosen by `LLM_PROVIDER` through `llm/factory.py`.
**Alternatives:** LangChain chat-model classes. Rejected for now because they add a second abstraction layer and a dependency surface without a current need. They remain an option behind the same interface.
**Consequences:** Adding a provider means one adapter plus one `register_provider` call. Transient-error retries happen inside the SDK (`LLM_MAX_RETRIES`); the graph does not retry.

### ADR-019: Domain handlers as the domain-agent extension point
**Decision:** `process_request` dispatches to the `DomainHandler` registered under the domain chosen by `understand_request`. Phase 1 registers only `general` (no business logic). Unknown domains, and the reserved key `unsupported`, produce an explicit `unsupported` result, and the reply says the capability is not available yet. A future Purchase/Sales/... agent is a `DomainHandler` (typically wrapping a compiled subgraph) plus a registry entry. The top-level graph does not change.
**Relation to ADR-003:** This is the minimal form of the supervisor routing. `understand_request` produces the structured routing decision, and the registry replaces a hard-coded edge per domain.

### ADR-020: Per-turn state fields are overwritten and reset each turn
**Context:** ARCHITECTURE.md §B.3.2 proposed append reducers for `tool_results` and `errors`. With a checkpointer, append reducers would carry the previous turn's errors and results into the next turn and break error routing.
**Decision:** Only `messages` accumulates across turns (`add_messages`). All other fields are per-turn: the owning node writes the full value, and `initial_turn_state()` resets them at the start of every turn. The graph is strictly sequential, so overwrite is safe. `state_version` (currently 1) is bumped on incompatible changes.
**Consequences:** If parallel branches are added later, the fields they write need reducers again.

### ADR-021: Python package name `agentic_platform`
**Context:** ARCHITECTURE.md §B.2 used `src/platform/`, but `platform` shadows the Python standard-library module of the same name.
**Decision:** Use `src/agentic_platform/`. Rename freely before external consumers exist.

### ADR-022: SQLAlchemy 2 async + asyncpg as the only PostgreSQL driver stack
**Context:** The agent runtime is async. On this machine a Windows Application Control policy blocks psycopg 3's bundled `libpq` DLLs (`psycopg[binary]`). asyncpg's extension module loads.
**Decision:** Use SQLAlchemy 2.x (`AsyncEngine`, `AsyncSession`) with the asyncpg driver for both runtime and Alembic. Database URLs are normalised to `postgresql+asyncpg://`. Any other scheme or driver is rejected at configuration time, so no second database system can be introduced by configuration (ADR-006).
**Consequences:** The LangGraph PostgreSQL checkpointer (`langgraph-checkpoint-postgres`, ADR-009) is built on psycopg 3. Adopting it needs either an allow-list entry for psycopg's DLLs or an asyncpg-based checkpointer. *(Resolved in Phase 3: asyncpg-based saver, ADR-027.)* asyncpg uses server-side prepared statements, so a transaction-mode PgBouncer in front of PostgreSQL needs `statement_cache_size=0` (not configured yet).

### ADR-023: Unit of Work is the only transaction boundary
**Decision:** Services open `async with database.unit_of_work(read_only=...)`. The unit commits on clean exit, rolls back on any exception, and translates driver errors into `DatabaseError` subclasses by SQLSTATE. Repositories (`Repository[Model]`) do data access only: they flush but never commit or roll back, and they never accept SQL text from callers. Read paths use `read_only=True`, which issues `SET TRANSACTION READ ONLY`, so PostgreSQL itself rejects writes there.
**Consequences:** One service command maps to one transaction. Errors are classified once, at one boundary (`retryable` flags transient classes). Writes are never retried automatically (ADR-012).

### ADR-024: Separate schema-owner and application roles
**Decision:** `agentic_owner` owns the schema and is used only by Alembic (`DATABASE_MIGRATION_URL`). `agentic_app` is used at runtime (`DATABASE_URL`). It has CONNECT plus default privileges for SELECT/INSERT/UPDATE/DELETE on tables the owner creates, and no CREATE/ALTER/DROP. Every connection also sets `statement_timeout` and `idle_in_transaction_session_timeout`.
**Consequences:** Even a fully compromised application process (or a prompt-injected agent) cannot change the schema. This is enforced by PostgreSQL and verified by an integration test. In the local Docker image the owner is also a superuser. Production must create a non-superuser owner.

### ADR-025: Alembic migrations, packaged with the code
**Decision:** Migrations live in `src/agentic_platform/db/migrations` (`script_location = agentic_platform.db:migrations`), so the running application can compare its expected head with the database's revision (health check). History is linear (one head, enforced by a test). Revision `0001` is an empty baseline: **no tables are created until a domain's business rules are documented.** Online runs take a PostgreSQL advisory lock so concurrent deploys cannot migrate at the same time. `alembic upgrade head --sql` produces reviewable SQL without a connection.
**Deviation:** ARCHITECTURE.md §B.2 placed migrations at the repository root.
**Consequences:** Every migration must have a docstring and a working downgrade. Production rollbacks should prefer a forward fix.

### ADR-026: Database is optional at runtime; three-level health
**Decision:** With `DATABASE_URL` unset, the agent runs without database tools. With it set, `system.database_health` is registered. `DatabaseHealthService` reports `ok` (reachable, schema at head), `degraded` (reachable, migrations pending or ahead), `unavailable` or `not_configured`. It runs in a read-only unit of work under `DATABASE_HEALTH_TIMEOUT_SECONDS` and never raises.
**Consequences:** Readiness checks (Phase 3+ API) should treat only `ok` as ready.

### ADR-027: Own asyncpg-based LangGraph checkpoint saver
**Context:** ADR-009 calls for PostgreSQL checkpointing. The official `langgraph-checkpoint-postgres` needs psycopg 3, whose DLLs Application Control blocks on developer machines, and it would add a second driver (ADR-022). On 2026-10-01 the stakeholder chose an asyncpg-based saver.
**Decision:** `agents/checkpointer.py` `PostgresCheckpointSaver` implements LangGraph's `BaseCheckpointSaver` (async API: `aget_tuple`, `alist`, `aput`, `aput_writes`, `adelete_thread`) on the existing `Database` / `UnitOfWork`. Storage lives in `db/checkpoints.py`, in three tables with the same shape as LangGraph's own saver, prefixed `agent_`: `agent_checkpoints`, `agent_checkpoint_blobs`, `agent_checkpoint_writes`. Values are LangGraph serializer output (type + bytes). Metadata is JSONB so `alist(filter=...)` can use `@>`. Checkpoint ids use `COLLATE "C"`, so ordering matches LangGraph's string ordering. Reads run in read-only transactions. Writes follow LangGraph's rules: regular pending writes are never overwritten, and special writes (error, interrupt, resume) are. `AGENT_CHECKPOINTER` (`auto` / `postgres` / `memory` / `none`) selects the saver. `auto` uses PostgreSQL when `DATABASE_URL` is set.
**Consequences:** One driver, it works on locked-down machines, and it is tested against real PostgreSQL, including `interrupt()` / `Command(resume=...)` across a fresh engine. The trade-off: we maintain this code ourselves. The sync API raises `NotImplementedError` (the agent is async-only). `get_delta_channel_history`, `aprune`, `acopy_thread` and `adelete_for_runs` use base-class behaviour or are unimplemented. `alist` loads blobs and writes for each checkpoint. **Not done:** tenant/user namespacing of `thread_id` and authorisation on resume (needs a principal, ADR-013), and checkpoint retention or pruning. A future LangGraph checkpoint-format change needs a compatibility check against this saver.

### ADR-028: Append-only audit log
**Decision:** The `audit_log` table holds: timestamp, nullable `tenant_id` (ADR-016 pending), actor type and id, action, tool name, status (`ok` / `error` / `denied`), error code, a SHA-256 `input_hash` (never the input itself), `entity_refs`, approval decision and approver, `request_id`, `thread_id`, and small `details`. Triggers reject UPDATE, DELETE and TRUNCATE for every role, including the owner (SQLSTATE 42501 → `DatabasePermissionError`). `AuditService.record` writes in its own unit of work, or in a caller's (`uow=`) so the entry commits atomically with a business change. When a database is configured, `ToolRegistry` audits every call, including refused, invalid and unknown ones. Request and thread ids come from the log context.
**Consequences:** If audit fails during a **read** tool call, the failure is logged and the call still succeeds. Write tools must fail closed when they arrive. Until authentication exists, actors are recorded as `agent` with no id. A superuser can still drop the triggers: tamper-evidence (hash chaining or off-host shipping) is not implemented. Retention is not defined (Q11).

### ADR-029: Durable idempotency records
**Decision:** The `idempotency_record` table is keyed by (`scope`, `idempotency_key`) and also stores `request_hash`, `status` (`in_progress` / `completed` / `failed`), the stored `response`, a lease (`locked_until`), `expires_at` and a random `claim_token`. `IdempotencyService.claim` either returns a new claim or replays a completed response. It raises `IdempotencyKeyReuseError` when the key is reused with a different request, and `IdempotencyInProgressError` (retryable) while a live lease is held. Failed attempts, expired leases (a crashed worker) and expired records can be claimed again. `complete` and `fail` succeed only for the current claim token (`IdempotencyLeaseLostError` otherwise), and they accept the caller's unit of work, so the result commits with the business write (exactly-once). All time checks use the database clock.
**Consequences:** No tool uses it yet, because write tools are still refused until permissions and HITL exist. The Redis fast-path in ADR-012 is not built. Expired rows need a scheduled `purge_expired()`, which has no scheduler yet. Keys are not tenant-scoped, so callers must make keys globally unique (for example, derived from `thread_id`).

### ADR-030: Redis layer
**Context:** The stakeholder's Phase 3 asks for Redis for temporary and fast-access use cases, with PostgreSQL remaining the source of truth (ADR-006, ADR-007).
**Decision:** The package `cache/` contains:
* **A `KeyValueBackend` contract** (get, set with TTL and NX, delete, ttl, expire, atomic compare-and-delete, compare-and-expire, ping), with two implementations. `RedisBackend` uses redis-py's asyncio client: a lazy client per event loop, a pool, timeouts, a key prefix, and Lua scripts for the compare operations. `InMemoryBackend` is the fallback when `REDIS_URL` is unset and is the double for unit tests. A single contract test suite runs against both.
* **Higher-level abstractions, written once against the contract**, each with an explicit failure policy:
  * `Cache` (cache-aside) **fails open**.
  * `TemporaryStateStore` **fails closed** (it raises).
  * `LockManager` **fails closed**.
  * The idempotency fast-path **fails open** to PostgreSQL.
* **Rules:**
  * Every key has a TTL; the backend rejects writes without one.
  * Values are JSON.
  * Error messages never contain keys or values.
  * Only `redis://` and `rediss://` URLs are accepted.
  * hiredis is not used (compiled DLLs are blocked on developer machines).
* **Local infrastructure:** Redis 7 with no persistence and `noeviction`.

**Consequences:** Redis is optional. Without it the platform runs on one process with local fallbacks, so locks are not shared. With it, cross-process locks and the fast-path work. A Redis outage degrades the cache and the fast-path but stops agent turns, because their thread lock fails closed. The cache and temporary state have no production caller yet; they exist for upcoming memory and UI features.

### ADR-031: One turn at a time per thread
**Context:** Two concurrent turns on the same thread would each load the same checkpoint and write diverging histories (a fork). The UI, retries or two server processes can cause this.
**Decision:** `Agent.arun` and `aresume` hold the distributed lock `thread:<id>`. The lease is `AGENT_THREAD_LOCK_TTL_SECONDS` and is renewed every TTL/3 while the turn runs. A second caller waits up to `AGENT_THREAD_LOCK_WAIT_SECONDS`, then gets `failed` / `THREAD_BUSY` (retryable). If the lock backend is unreachable, the turn fails (`CACHE_UNAVAILABLE`) instead of running unguarded.
**Consequences:** Turns on one thread are serialized across processes when Redis is configured. If a holder stalls past its lease (a long GC pause, say), the lock can be lost. This is detected and logged, but the stalled turn is not aborted. Checkpoint writes are idempotent per checkpoint id, which limits the damage.

### ADR-032: Thread model and workflow resume
**Decision:**
* **Thread ids:** 1-128 characters from `[A-Za-z0-9_.:-]`, validated at the entry point (`agents/threads.py`).
* **Durability:** turns run with `durability="sync"` when a checkpointer is configured, so every finished node is persisted before the next one starts.
* **Pending turns:** a thread whose latest checkpoint has pending nodes (crashed or interrupted) rejects new input with `ThreadPendingError`.
* **Resume:** `Agent.aresume(thread_id, resume_value=None)` continues a crashed turn with `ainvoke(None)`, or an interrupted one with `Command(resume=value)`. A value is required for an interrupt and rejected otherwise.
* **Responses:** gain `status="interrupted"` with `interrupts`, and `replayed` for idempotent replays. A crash with saved progress returns `TURN_INCOMPLETE` (retryable).
* **Turn idempotency:** caller-supplied keys (`arun(idempotency_key=...)`) use the scope `agent.turn`, with the key `<thread_id>:<key>`. This needs the database.
* **Node wrappers:** they re-raise `GraphBubbleUp`, so `interrupt()` works inside nodes and domain handlers.

**Consequences:**
* Sync durability adds one checkpoint write per node to turn latency.
* The pending-turn guard costs one checkpoint read per turn.
* A thread whose turn can never complete (for example, a bug in a node that always crashes) stays blocked. There is no "abandon pending turn" operation yet; start a new thread.
* Thread ids are not yet tied to an owner. Anyone who knows an id can read its status or resume it. This needs a principal (Phase 5).

### ADR-033: Memory architecture
**Context:** Phase 4 asks for working, conversation, long-term, semantic, episodic and procedural memory plus checkpoint state, in the layering Agent → Memory Manager → Memory Type → Storage Adapter, with replaceable storage.
**Decision:**
* **Manager:** `memory/manager.py` `MemoryManager` is the agent's only entry point (recall, record_turn, forget, purge).
* **Memory types:** each type is a class with its own rules (`memory/*.py`).
* **Ports:** types depend only on `memory/ports.py`: `MemoryStore`, `VectorStore`, `Embedder` and `ConversationSource`. Working memory uses `cache.TemporaryStateStore`.
* **Adapters:** PostgreSQL+pgvector, in-memory, checkpointer, and the embedders.
* **Swappability:** a shared contract suite (`tests/memory_contract.py`) runs against both store adapters, so a new adapter is accepted only if it behaves identically.
* **Reuse:** conversation memory reads the checkpointer rather than copying messages, and procedural memory reuses `PromptRegistry` for built-in procedures.

**Consequences:** Replacing pgvector with a dedicated vector database means implementing `VectorStore` (and keeping vectors in sync); no memory type changes. Without a database the same types run on the in-memory adapter (process-local).

### ADR-034: Durable memory storage
**Decision:**
* **One table, `memory_item`** (migration 0003). It carries `kind`, `tenant_id` (required), `user_id` (`''` = tenant-shared), namespace/key/version, thread, content, JSONB metadata, source type/ref, `embedding vector`, `embedding_model`, timestamps and `expires_at`.
* **Natural keys:** partial unique indexes provide them for long-term facts, procedure versions and conversation summaries.
* **pgvector:** the extension is created by the migration.
* **Vectors:** stored without a fixed dimension. Every search filters on `embedding_model` and `vector_dims`, so different models never mix.
* **Row-level security:** the runtime role sees only rows where `tenant_id = current_setting('app.tenant_id')`. The adapter sets this at the start of every transaction, in addition to explicit filters. `app.memory_maintenance` allows the cross-tenant retention purge.
* **Driver:** a custom SQLAlchemy `Vector` type exchanges vectors as text, so neither numpy nor pgvector-python is needed (Application Control).
* **Local infrastructure:** PostgreSQL now runs the `pgvector/pgvector:pg16-trixie` image, which shares the Debian base of `postgres:16`, so the existing volume was kept.

**Consequences:**
* There is no ANN (HNSW/IVFFlat) index yet, because untyped vectors cannot be indexed generically. Exact search per tenant is fine at current volumes. Production should add a per-model expression index, for example `((embedding::vector(1024))) WHERE embedding_model = 'voyage:…'`, once a model is chosen.
* RLS protects against application bugs (a missing filter), not against a compromised application, which controls its own session settings.
* Managed PostgreSQL must offer pgvector.

### ADR-035: Embedding abstraction
**Decision:** `Embedder` port (`model_id`, `dimensions`, `embed_documents`, `embed_query`).
* `HashingEmbedder` is local, deterministic and lexical (feature-hashed words and word pairs). It is the default: it needs no network or key, and it makes tests reproducible.
* `VoyageEmbedder` calls Voyage AI (Anthropic's recommended embeddings provider). It batches by 128 and maps 429/5xx/timeouts to `EmbeddingUnavailableError` (retryable) and other failures to `EmbeddingError`.

Configure with `MEMORY_EMBEDDING_PROVIDER` / `MEMORY_EMBEDDING_MODEL` / `VOYAGE_API_KEY`.
**Consequences:**
* With the hashing embedder, retrieval matches words, not meaning ("car" ≠ "automobile"). Production needs a semantic provider.
* The Voyage adapter is tested against a mocked HTTP transport only (no key on this machine); its default model name `voyage-3.5` must be confirmed before use.
* Changing the model requires re-ingesting, because old vectors stay but are not searched.

### ADR-036: Memory in the agent loop
**Decision:**
* **Scope:** `Agent.arun/aresume(..., scope=MemoryScope)`. The default comes from `MEMORY_DEFAULT_TENANT_ID` / `MEMORY_DEFAULT_USER_ID`, and the scope is never taken from LLM output.
* **Recall:** before a new turn, the agent recalls memory within a timeout. The size-bounded result goes into the per-turn state field `memory_context` (`STATE_VERSION` 2) and into the answer context; prompt `generate_response.system.v2` defines how the model must treat it.
* **Episodes:** after completed, clarification and unsupported turns, the agent records an episode. Failed and interrupted turns are not recorded.
* **Failure isolation:** recall sections fail independently, and memory failures never fail a turn.
* **Writes:** long-term facts, knowledge and procedures are written through the manager API (CLI `--ingest` for documents). The LLM has no memory-writing tool.

**Consequences:**
* Recall happens once per turn, before routing. `understand_request` does not see memory yet.
* Recall adds one embedding call and three vector searches per turn.
* Episodes contain an LLM-written summary of the user's request, which may include personal data. Retention is 90 days by default, and a final PII policy is still pending (Q11).

### ADR-037: Authenticated principal and replaceable authentication
**Context:** Phase 5 asks that identity come from authentication, not from the caller or the LLM. No identity provider has been chosen (Q6), and the only entry points are the `Agent` API and the CLI.
**Decision:**
* **`security.Principal`** (user, tenant, roles, resolved permissions, auth method, session id) is the only source of tenant and user downstream. It is immutable and holds identifiers and permissions only, never credentials.
* **`IdentityResolver`** turns verified claims into a principal. Clients can name roles at most; the server resolves roles to permissions. A token that carries a `permissions` claim gains nothing, and unknown roles grant nothing.
* **`Authenticator` protocol** (`authenticate(credential) -> Principal`) has one development implementation, `HmacTokenAuthenticator`: compact HMAC-SHA256 tokens (`AUTH_TOKEN_SECRET`, at least 32 characters) with issuer, issue and expiry times, a maximum lifetime and a session id. A production OIDC/JWT authenticator implements the same protocol.
* **`LocalIdentity`**: a trusted local operator (CLI flags, scripts) may assert tenant, user and roles. It is allowed outside production only (`AUTH_ALLOW_LOCAL_IDENTITY` overrides). `Agent` uses it when no principal is passed, which keeps scripts and tests simple; in production that call is refused.
* **Propagation:** `Agent` binds the principal with `security.acting_as` (a contextvar) for the whole call, so nodes, tool handlers and the checkpoint saver see the same identity. Graph state stores only `actor = {tenant_id, user_id}`, never permissions.
* `Agent.arun` / `aresume` no longer take the Phase 4 `scope=` parameter: a memory scope can no longer be passed in.

**Consequences:**
* No caller-asserted scope remains on production paths.
* Permissions are fixed for the life of a request. Re-authentication picks up role changes, which is what makes the re-check on resume meaningful.
* The development token is not an identity provider: there is no key rotation, revocation list or refresh token.

### ADR-038: Permission model and authorization layer
**Decision:**
* **Twelve platform permissions** (`security/permissions.py`): `conversation.read/write/delete`, `memory.read/write/delete`, `memory.shared.write/delete`, `tool.execute`, `approval.request`, `approval.decide` and `audit.read`. Permission names are strings, so future domain permissions (for example `purchase.order.create`) need no code change here.
* **Roles are additive and provisional:** `member`, `knowledge_manager`, `approver`, `auditor` and `tenant_admin`. They are platform roles, not business roles. `AUTH_ROLE_CATALOG_JSON` replaces them; the real catalogue is open question Q8.
* **One `Authorizer`** (`require`, `require_tenant`, `require_owner`, `deny`). The agent entry point, the tool registry, `AuthorizedMemory`, the approval service and the audit reader all use it. Another tenant's or user's resource raises the same "not found" error as a missing one.
* **Enforcement points** (ARCHITECTURE.md § B.6):
  * tool visibility (`ToolRegistry.specs(principal=)`);
  * tool execution (`ToolRegistry.execute*`);
  * the service layer (`AuthorizedMemory`, `ApprovalService`), which is the final authority.

  No prompt takes part in any decision.
* Permissions are resolved once, at authentication, so each check is a set lookup with no per-check query (no N+1).

**Consequences:** Adding a domain agent means declaring `required_permission` on its tools and granting that permission to roles. Denials are audited (`authz.denied`).

### ADR-039: Conversation ownership
**Context:** Phase 4 left checkpoints keyed by a caller-chosen thread id. Anyone who knew an id could continue, read or delete any conversation.
**Decision:**
* **Ownership record:** `conversation_thread (tenant_id, thread_id) → owner_user_id`. It is created on a thread's first turn by one race-free upsert (`ON CONFLICT ... DO UPDATE ... WHERE owner = excluded.owner`).
* **Storage keys:** checkpoints, the thread lock and turn idempotency keys use `<tenant_id>/<thread_id>`. `/` is not allowed in ids, so no caller can name another tenant's key, and two tenants may use the same public id.
* **Rules:** only the owner may continue, resume, read the status of, or forget a conversation (`conversation.write/read/delete`). The existing architecture defines neither sharing nor administrator access, so both stay open (question Q16) instead of being guessed. Neither exists today.
* **No existence oracle:** another user's or tenant's thread behaves exactly like a missing one (`CONVERSATION_NOT_FOUND`, `exists=false`).
* **Forget** removes the checkpoints, summary, working memory and episodes, cancels the thread's open approvals, deletes the ownership row, and is audited.

**Consequences:**
* Conversations stored before Phase 5 (keys without a tenant prefix, no ownership row) are unreachable. They were development data.
* Each turn writes one ownership row; the upsert also records `updated_at`.

### ADR-040: Human-in-the-loop approvals
**Decision:**
* **Policy in code** (`security/policy.py`):
  * every `write` tool needs approval (ADR-010);
  * so does any tool flagged `requires_approval`, or at or above `APPROVAL_MIN_RISK` (`low`, `medium` or `high`; default `high`).

  An `ApprovalPolicy` names the approver permission, whether self-approval is allowed, the TTL and a reason code. It holds no business thresholds.
* **`approval_request`** stores:
  * tenant and requester;
  * the workflow: `thread_id` plus the turn's `request_id`, which is also the audit correlation id;
  * the operation name and the SHA-256 of its validated arguments (never the arguments themselves);
  * the required and approver permissions, and the risk;
  * a human-readable summary (at most 1000 characters);
  * status, decider, note and timestamps.

  Lifecycle: `pending → approved → executed`, `pending → rejected | expired | cancelled`, and `approved → expired | cancelled`. Every transition is a compare-and-set, audited in the same transaction.
* **Flow:**
  1. The registry validates the call, checks the permission, creates the request and returns `pending_approval`.
  2. `process_request` stores a `PendingAction` (validated arguments plus their hash) in state.
  3. `approval_gate` calls `interrupt()` (the Phase 3 mechanism).
  4. A human decides through `ApprovalService.decide` (CLI `--approve` / `--reject`).
  5. `Agent.aresume` refuses to resume while the request is still pending.
* **Safe resume:**
  1. The principal comes from authentication, and the gate checks that it is the turn's actor.
  2. `execute_approved` re-validates the approval: tenant, requester, workflow, operation, argument hash, approved, unexpired and unused.
  3. It **re-checks the permission with the principal's current roles**.
  4. It claims the `IdempotencyService` key `approval.execute:<tenant>/<approval>`, so the operation runs at most once.
  5. It executes, then marks the request `executed` and audits it.
* **First protected operation:** `memory.remember_fact` writes a private long-term fact and needs `memory.write`. Self-approval is allowed because it is the user's own memory. The tool is registered only with `MEMORY_AGENT_FACT_WRITES=approval` (default `off`), which keeps the Phase 4 rule that the agent never writes long-term facts on its own.

**Consequences:**
* Approval never grants a permission.
* A paused turn blocks its thread until it is resumed. An expired request resumes into an `APPROVAL_EXPIRED` result.
* A crash after execution but before the idempotency record completes is retried once the lease expires: at-least-once within that window, as in ADR-029.
* "Edit before approve" (the `edited` audit decision) is not implemented.

### ADR-041: Tenant isolation in the database (RLS)
**Decision:** migration `0004` adds row-level security on top of the explicit `tenant_id` filters.
* `conversation_thread` and `approval_request`: only rows of the tenant in `app.tenant_id` are visible (`app.maintenance` lets cross-tenant jobs such as expiry through).
* `audit_log`:
  * tenants read only their own rows;
  * tenant-less system rows (failed sign-ins) are visible to maintenance only;
  * an insert carries the bound tenant or no tenant;
  * UPDATE, DELETE and TRUNCATE are **revoked** from every non-owner role, and the Phase 3 triggers stay.
* `agent_checkpoints`, `agent_checkpoint_blobs` and `agent_checkpoint_writes`: the key prefix (`split_part(thread_id, '/', 1)`) must be the bound tenant. The saver binds the **request principal's** tenant, so a wrong key computed by application code can be neither seen nor written.
* `Database.unit_of_work(tenant_id=...)` binds the tenant for each transaction (`set_config(..., is_local)`). For read transactions the binding is folded into the read-only setup statement, so it costs no extra round trip. Memory (Phase 4) uses the same path.

**Consequences:**
* Measured cost on this machine (Docker on Windows, about 2.3 ms per round trip): a turn issues 29 statements instead of 18. The median turn takes about 140 ms instead of about 110 ms, a difference of 25-35 ms; run-to-run noise is about ±15 ms.
* Most of that cost is the binding in LangGraph's per-node checkpoint write transactions, which cannot safely be folded into another statement.
* RLS protects against application bugs, not against a compromised application, which sets its own session variables (ADR-034).

### ADR-042: Security audit trail
**Decision:**
* `security/audit.py` builds every security event from the principal: `tenant_id` and `actor_id`, `request_id` and `thread_id` from the log context, and entity references (approval id, memory id, document). That links request → graph → node → approval → tool → result.
* **Actor model:**
  * `actor_type="user"` for the user's own actions (approval decisions, memory API writes);
  * `actor_type="agent"`, with the user's id as `actor_id`, for actions the agent takes for the user (tool calls, agent memory writes), per ADR-013;
  * `actor_type="system"` for maintenance (expiry).
* **Events:**
  * sign-in and authorization: `auth.failed`, `authz.denied`;
  * memory: `memory.fact.create/update/delete`, `memory.document.write/delete`, `memory.procedure.write/delete`, `memory.summary.write`, `memory.user.forget`;
  * conversations: `conversation.forget`;
  * approvals: `approval.requested/decided/cancelled/expired`, `operation.executed/rejected`;
  * tools: the Phase 3 `tool.call`, which now carries tenant and actor.
* **Never recorded:** tokens, secrets, tool arguments or memory content; content appears only as a SHA-256 `input_hash`. A failed sign-in carries a reason code but no tenant, because nothing about the caller is trusted.
* **Failure policy:** approval and memory-write events are written fail-closed (approval events atomically with the state change). Denials are recorded best-effort.
* **Reading** the audit trail needs `audit.read` and is tenant-scoped (CLI `--audit`).

**Consequences:**
* Without a database there is no audit log; events are only logged, as in Phase 3.
* Automatic per-turn steps (recall, episode recording) are not audited individually.
* There is no hash chaining or off-host shipping yet.

### ADR-043: HTTP API and authentication boundary
**Context:** Phase 6 asks for an HTTP entry point around the existing agent and services, and for an authentication boundary that a real identity provider can plug into. No HTTP framework existed; ADR-014 proposed FastAPI. No identity provider has been chosen (Q6).
**Decision:**
* **Framework:** FastAPI on uvicorn (plain install, no compiled extras), one process per event loop. `create_app()` builds one `Agent`, one authenticator and the services at startup; configuration errors stop the process at startup.
* **Thin boundary:** handlers map requests and responses only. They call the `Agent`, the Phase 5 services and two small application services (`ApprovalWorkflow`, `AuditReader`). They never touch repositories, sessions or SQL, and contain no role or permission logic: the Phase 5 `Authorizer` decides inside the services.
* **Authentication:**
  * only `Authorization: Bearer` is read;
  * the local development identity is never accepted over HTTP;
  * request bodies reject unknown fields, so identity cannot be supplied in a body.
* **Identity-provider interface:** `IdentityProvider.verify(token) -> ExternalIdentity`, then `IdentityMapper` (`RoleMapping` → role catalogue) → `Principal`, wrapped as an `Authenticator` (ADR-037). External claims never become permissions.
* **Providers:**
  * `dev_token`: the Phase 5 HMAC tokens, now with an optional audience; refused in production;
  * `oidc`: `OidcJwtProvider` for OIDC/OAuth2 JWT access tokens (JWKS, asymmetric algorithms only, `exp`/`nbf`/`iat`/`iss`/`aud`, configurable claim names, discovery, key rotation). PyJWT + `cryptography` are an optional extra, imported only for `oidc`.
* **Auditing:** a missing credential is a 401 without an audit row (probes must not fill the append-only log); every presented-but-invalid credential is audited with a reason code.
**Alternatives:**
* A hand-written ASGI layer: rejected; FastAPI gives validation and OpenAPI for free and was already proposed.
* A specific vendor SDK: rejected; it would hard-code a provider before Q6 is answered.
* Accepting the local identity over HTTP in development: rejected; any client could claim any identity.
**Consequences:**
* A provider is integrated by configuration (or by one new `IdentityProvider` class for non-JWT tokens). Nothing downstream changes.
* The OIDC adapter is **not verified against a real provider**. Only top-level claims are read. There is no revocation, introspection or refresh: tokens are valid until they expire.
* Turns are synchronous HTTP requests: no streaming, and the client waits for the LLM.

### ADR-044: Scheduled maintenance
**Context:** Phase 4/5 left `purge_expired()` / `expire_due()` with no scheduler (Q11), and those operations were unbounded single statements. Phase 6 asks for a reusable, observable, retry-safe maintenance framework that does not lock the platform into a scheduler.
**Decision:**
* **`MaintenanceJob.run_batch(limit)`:** one transaction, at most `limit` rows, oldest expiry first, `FOR UPDATE SKIP LOCKED`, only rows that are still due.
* **`MaintenanceRunner`:**
  * repeats batches until one is short or `MAINTENANCE_MAX_BATCHES` is reached;
  * holds a per-job lock (`maintenance:<job>`; busy → `skipped`);
  * stops a job at its first failed batch (`partial` / `failed`) and continues with the other jobs;
  * reports `JobRunReport` to the log and to `audit_log` (`maintenance.run`, system actor, run id as `request_id`).
* **Scheduling is outside the runner:**
  * a local `MaintenanceScheduler` loop;
  * `python -m agentic_platform.maintenance` for cron, container or cloud jobs;
  * the CLI's `--memory-purge`;
  * optionally inside the API process (`API_RUN_MAINTENANCE`, development only, off by default).
* **Jobs apply existing expiry only:** `memory.expired`, `approvals.expired`, `idempotency.expired`. Data without a documented retention rule gets no job.
**Consequences:**
* Any scheduler can drive it safely.
* Concurrent runs are harmless: tested with two runners on PostgreSQL.
* Checkpoints, the audit log, decided approvals and conversation rows keep growing until Q11 is answered.
* `maintenance.run` rows are tenant-less, so only operators can read them.

### ADR-045: Correlation, errors, health and readiness
**Decision:**
* **Request id:** the server always generates the request id. It is returned as `X-Request-ID`, bound as the log `request_id`, passed to the agent as the turn's request id, and so reaches graph state, tool calls, approval requests and audit rows. A client `X-Request-ID` is logged as `client_request_id` only. Approvals bind (thread, request id), so client-chosen ids could let two turns share one.
* **Resume:** a resumed turn keeps its original request id.
* **Errors:** one envelope, `{"error": {code, message, retryable, request_id, details?}}`, with the platform error code and its public message.
  * Status mapping: 401, 403, 404 (missing or not visible, identical for other tenants), 409, 410 (expired approval), 413, 422, 502 (LLM), 503 (dependency), 500.
  * A pure ASGI middleware catches anything unhandled and answers a generic 500.
  * No stack traces, SQL or input echoes reach clients.
* **Health and readiness:**
  * `/health` is liveness and touches no dependency;
  * `/ready` uses the existing health services: database `ok` (migrated) or not configured outside production; Redis `ok` or not configured; an authenticator. It reports statuses, latencies and codes only.
* **Logging:** one `http.request.finished` line per request (method, path, status, duration). Never headers, query strings or bodies.
**Consequences:** Container orchestration can use `/health` and `/ready` directly. Readiness queries both stores on every probe (not cached).

### ADR-046: Requester-only approvals; single execution after purge
**Context:** Building the approval API exposed two Phase 5 gaps.
1. `memory.remember_fact` names `memory.write` as its approver permission so users can approve their own facts. Every member holds `memory.write`, so any member of the tenant could list, read (the summary contains the fact) and decide another user's request.
2. `ToolRegistry.execute_approved` relied on the idempotency record alone to prevent a second run. Once that record expired (24 h) or was purged (ADR-044), a replayed execution of an `executed` approval would claim a fresh key and run again.
**Decision:**
* **Requester-only approvals:**
  * `ApprovalPolicy.requester_only` (implies self-approval) is persisted as `approval_request.requester_only` (migration `0005`, with a check constraint);
  * such a request is visible to, and decidable by, its requester only; approvers and tenant administrators get "not found";
  * `memory.remember_fact` uses it.
* **Single execution:** an approval whose status is `executed` never runs again. A fresh idempotency claim on it is failed, and the call returns `APPROVAL_ALREADY_USED`; a stored result is still replayed.
**Consequences:**
* Private approvals cannot be decided by anyone else, which matches the Phase 5 intent ("the user approves their own fact").
* The approval state, not the idempotency record's lifetime, is now the final guard against double execution.
* Existing rows keep `requester_only = false`. Development data only: in-flight memory approvals created before `0005` stay visible to members until they expire (24 h).

### ADR-047: Production configuration policy enforced at process start
**Context:** Phase 6 refused development tokens in the HTTP API, but nothing stopped a production process from starting with a development database password, no Redis (per-process locks with two processes), SQL echo, in-process maintenance or no LLM credential. Mistakes like these surface only under load or in an incident.
**Decision:**
* `core/production.py` `check_configuration(settings, role)` returns errors and warnings for `APP_ENV=production`, per process role (`api`, `worker`). It reads settings and variable names only, never connects and never prints a value.
* **Errors** (the process does not start, exit 2): a development token secret or local identity; `AUTH_PROVIDER` not `oidc`, incomplete or non-https OIDC (API); no database; the runtime connecting as the schema owner; SQL echo; a development password from `.env.example`; no Redis or Redis without authentication; no LLM credential or a non-https LLM endpoint (API); in-process maintenance (API).
* **Warnings** (logged as `config.production.warning`): weak database TLS, Redis without TLS, unbounded token lifetime, leeway above 60 s, public API docs, DEBUG logs, lexical embeddings, owner URL given to the API.
* Enforced by the supported entry points (`python -m agentic_platform.api`, `python -m agentic_platform.maintenance`). `--check-config` prints the report as JSON (exit 1 on errors) for deployment pipelines.
* Development tokens are now refused by **every** entry point in production: `build_token_authenticator` returns nothing there, so the CLI cannot accept them either.
* The image defaults to `APP_ENV=production`, so a container started without configuration refuses to run instead of running in development mode.
**Alternatives:** validating inside `AppSettings` (rejected: tests and tools build production-like settings deliberately, and a validator cannot know the process role); warnings only (rejected: the listed errors are unsafe or non-functional).
**Consequences:** `create_app()` itself still only refuses development authentication (unchanged, tests rely on building apps directly); embedding the app in another ASGI server must call `enforce_production_policy` first. Production requires Redis.

### ADR-048: Token lifecycle and revocation
**Context:** Phase 6 left token lifetime and revocation open (Q19). The API verifies self-contained JWT access tokens locally (signature, `exp`), which is what keeps authentication free of per-request network calls (about 2-3 ms per request).
**Decision:**
* **Access tokens are short-lived and issued by the identity provider.** The API never issues, refreshes, stores or logs them. Refresh tokens belong to the client and the provider (authorization code + PKCE for user clients); the API never sees them.
* **No API-side revocation list and no per-request introspection.** A token is accepted until `exp` (+ `AUTH_OIDC_LEEWAY_SECONDS`). Verified against Keycloak: a disabled user gets no new token, but a token issued before stays valid until it expires; a removed role disappears with the next token.
* **The exposure window is bounded by configuration:** `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` rejects tokens whose `exp - iat` exceeds it (a misconfigured provider cannot issue day-long tokens). Recommendation: the provider issues access tokens of 5-15 minutes and the API bound matches it (warning in production when unset).
* **Permission changes:** role assignments at the provider take effect with the next token; changes to the role catalogue (`AUTH_ROLE_CATALOG_JSON`) take effect on restart; permissions are resolved per request, so nothing is cached beyond the token. The approval path re-checks the requester's current permissions at execution (ADR-040).
* **Immediate revocation is not guaranteed.** If go-live requires it (for example for a disabled employee), the options are: provider introspection for every request (adds a network call), a `sid`/`jti` deny-list in Redis fed by provider back-channel logout events, or shorter tokens. None is built until Q19 is answered.
**Consequences:** worst-case access after disabling a user = remaining token lifetime + leeway (with the recommended settings, at most about 15.5 minutes). This depends on the provider chosen (Q6).

### ADR-049: Secrets and configuration boundary
**Context:** No secret manager has been selected (deployment target Q3). The application must not depend on one vendor, and secrets must never be in images, the repository, logs or audit rows.
**Decision:**
* Configuration stays environment-driven (pydantic-settings). Additionally, `APP_SECRETS_DIR` names a directory from which any setting can be read as a file named like its variable (`/run/secrets/DATABASE_URL`). This is the delivery format of Docker and Kubernetes secrets, Vault Agent and the cloud secret-store CSI drivers. Precedence: environment, `.env`, secret files.
* No secret manager SDK in the application; the platform mounts or injects the values.
* Secret settings are `SecretStr` (masked in reprs); the production policy report and readiness never include values; `.dockerignore` keeps `.env*` out of the build context; the image has no credentials.
* Templates: `.env.example` (development, local dev passwords only), `deploy/env/staging.env.example` and `deploy/env/production.env.example` (placeholders only).
**Consequences:** any secret manager works without code changes. Rotation of database/Redis passwords requires a process restart (connections are built at start).

### ADR-050: Deployment architecture
**Context:** Phase 6 produced the processes but no deployable artifact. Docker existed for PostgreSQL and Redis only. The deployment target (Q3) is not chosen.
**Decision:**
* **One image** (`Dockerfile`, Python 3.11 slim, non-root uid 10001, no secrets, `.[oidc]` installed) and **three process roles**:
  * **api**: `python -m agentic_platform.api` (default command), horizontally scalable, stateless; liveness `GET /health`, readiness `GET /ready`; graceful SIGTERM (`API_GRACEFUL_SHUTDOWN_SECONDS`);
  * **worker**: `python -m agentic_platform.maintenance` either as a scheduled one-shot job (preferred: cron/CronJob/cloud scheduler, exit code = result) or `--loop` (one long-running worker); graceful SIGTERM (the current batch commits);
  * **migrate**: `alembic upgrade head` as a one-shot job **before** a new API version starts, with the schema-owner URL (only this job gets it). Never run on API startup.
* **PostgreSQL 16 + pgvector** and **Redis 7** are external, managed services in production (persistent storage, backups/PITR, TLS). The compose file keeps local versions and adds the app roles behind the `app` profile without changing the existing services.
* **Concurrency:** API replicas share Redis thread locks; several workers or overlapping schedules are safe because of the per-job Redis lock plus `FOR UPDATE SKIP LOCKED`; concurrent migration jobs are serialized by Alembic's advisory lock (ADR-025).
* **Rollback:** application rollback = deploy the previous image (schema changes are backward compatible within one release); schema rollback = `alembic downgrade -1` by an operator, only for reversible migrations and after checking the data impact. Never `docker compose down -v`.
**Consequences:** the platform can run on any container orchestrator. Exact manifests (Kubernetes, ECS, App Service, ...) wait for Q3.

### ADR-051: Identity provider verification and selection status
**Context:** Phase 7 asks for a real identity provider. None is specified by the stakeholders (Q6), and choosing one is an organizational decision (existing directory, SSO, cost, data residency).
**Decision:**
* No production provider is selected by this phase. The adapter stays provider-neutral (OIDC discovery + JWKS + top-level claims + role map).
* The adapter is **verified against a real OIDC implementation**: Keycloak 26.4 run locally as a reference provider (`docker compose --profile idp-test`, `tests/live/test_oidc_live.py`). Real discovery, JWKS caching, RS256 signatures, key rotation, expiry, issuer/audience, ID-token rejection, tampering, algorithm confusion, role mapping, missing tenant, disabled users and role changes, and the HTTP API inside the container.
* No nested-claim support was added: Keycloak (like Entra ID app roles, Okta and Auth0 custom claims) can emit a top-level `roles` claim and a top-level tenant claim through mappers. Nested parsing (`realm_access.roles`) stays out until a selected provider cannot be configured that way.
* Requirements any chosen provider must meet: signed JWT access tokens (RS/PS/ES), discovery or a JWKS URL over https, an audience for this API distinct from client ids, an admin-controlled tenant claim, roles or groups in a top-level claim, and short access-token lifetimes.
* `tests/live/test_oidc_live.py` has a provider-neutral mode (`LIVE_OIDC_*`) to verify the chosen provider before go-live.
**Consequences:** "verified against a real provider" means a reference provider, not the production one; the production provider still needs its own run of the live tests.

### ADR-052: LLM failure handling and degraded operation
**Context:** No live Claude call had ever run. Phase 7 makes the first live calls and needs controlled behaviour for invalid keys, outages, timeouts, rate limits and malformed responses.
**Decision:**
* The LLM stays behind `LLMProvider` (ADR-018); the provider and model come from configuration; `LLM_ANTHROPIC_BASE_URL` allows an egress gateway (https in production).
* SDK exceptions are translated with a controlled detail: exception type, HTTP status, provider error type. Never the provider message (it can echo request content), the model output (a structured-output validation error quotes it) or a stack trace; translated errors are not chained. Each failure is logged once as `llm.call.failed` with status and provider request id.
* Malformed responses (non-JSON body, wrong shape, schema mismatch) are `LLM_OUTPUT_ERROR`, not internal errors.
* HTTP mapping (unchanged): retryable (429, 5xx, 529, timeout, connection) → 503 with `Retry-After`; auth/permission → 502 `LLM_AUTH_ERROR`; other → 502. The SDK retries `LLM_MAX_RETRIES` times first.
* **Degraded, not down:** the LLM never gates readiness (`/ready` reports `llm` by configuration only, never with a provider call). Without the LLM, turns fail with a controlled error while approvals, memory, audit, conversation history and authentication keep working. In production a missing credential stops the API at startup (ADR-047).
**Consequences:** found and fixed three gaps (non-JSON body and wrong shape became `INTERNAL_ERROR`; a structured-output parse failure logged the model output). The first successful live completion still waits for credentials.

### ADR-053: Two-layer rate limiting
**Context:** Phase 7 left rate limiting open (Q20: limits per user/tenant, gateway or application). Agent turns cost LLM tokens; every endpoint touches PostgreSQL. Before authentication there is no trustworthy identity, and after it the gateway cannot verify one without duplicating the identity layer.
**Decision:**
* **Gateway (per client address, before authentication):** nginx `limit_req` (all requests, and separately agent-turn POSTs) and `limit_conn`, answering 429 with the API's error envelope and code `RATE_LIMITED`. It absorbs floods of unauthenticated or invalid-token requests, which would otherwise also fill the append-only audit log with `auth.failed` rows.
* **API (per verified caller, after authentication):** `services/rate_limit.py` `RateLimiter`, a route dependency on every `/api/v1` route. Key = `ratelimit:<kind>:<sha256(tenant, user)>` from the `Principal` only; fixed windows counted with an atomic `INCR` + TTL (`KeyValueBackend.increment`), shared by all replicas through Redis. Classes: `turn` (start/continue/resume), `approval_decision`, `write`, `read`. 429 `RATE_LIMITED`, `retryable`, `Retry-After` = seconds to the end of the window.
* **Never authorization:** the limiter only counts and refuses; it runs after authentication and before the handler, and every admitted request is authorized exactly as before. Unauthenticated requests are not counted (they are refused first). Authenticated requests are counted even when validation later refuses them.
* **Configuration, not code:** `RATE_LIMIT_*` and `GATEWAY_*`. The defaults (20 turns, 60 decisions, 60 writes, 600 reads per user per minute; 20 requests/s per address) are provisional technical guards, not business limits. On by default in production only; the production policy warns when they are off.
* **Redis outage:** fail open by default (`ratelimit.unavailable` logged; the gateway limit still applies); `RATE_LIMIT_FAIL_OPEN=false` refuses instead. Turns need Redis for the thread lock and fail closed regardless.

**Alternatives:** gateway-only limits (rejected: cannot key on the verified user, and NAT puts many users behind one address); per-tenant budgets (deferred until Q20 names them; a tenant-level class can be added without changing callers); sliding windows (rejected for now: fixed windows are one round trip and enough for abuse protection).
**Consequences:** one extra Redis round trip per authenticated request. A burst of up to twice the limit is possible across a window boundary. Limit changes need a restart (settings are read at start). With Redis down and fail-open, each request waits up to `REDIS_SOCKET_TIMEOUT_SECONDS` for the counter (observed +2 s).

### ADR-054: Deployment-neutral TLS gateway
**Context:** Production traffic must use HTTPS; Phase 7 had no TLS, request-size or connection limits in front of the API. The deployment target (Q3) is still open, so the gateway must not assume a cloud.
**Decision:**
* `deploy/gateway/`: an nginx configuration (unprivileged image, uid 101) rendered from `GATEWAY_*` variables, plus snippets. It terminates TLS (1.2/1.3, Mozilla intermediate ciphers, certificate mounted at `/etc/nginx/certs`), redirects HTTP with 308, sets security headers (HSTS, nosniff, `frame-ancestors 'none'`, no-store), limits bodies to 1 MiB (= `API_MAX_BODY_BYTES`), sets header/body timeouts and per-address limits (ADR-053), gives agent turns (only) a long read timeout, and restricts `/ready` to internal networks. Gateway-generated errors use the API envelope.
* **Forwarding:** the gateway replaces `X-Forwarded-For/-Proto/-Host` (never appends a client's chain) and passes its own request id as `X-Request-ID`, which the API logs as `client_request_id` (correlation without trusting client ids, ADR-045). uvicorn applies forwarded headers only from `API_FORWARDED_ALLOW_IPS`; `*` is a production error.
* **Upstream address resolved at run time** (`server ... resolve`, nginx >= 1.27.3): found by the restart drill, where a recreated API got a new address and the gateway kept answering 503. **`max_fails=0`**: nginx never marks its single upstream down; with the default, one refused connect during an API start became 10 s of instant 503s (load re-run). Health is decided by `/ready` and the orchestrator.
* **No identity at the gateway:** the `Authorization` header is forwarded untouched and never logged; the gateway makes no tenant or role decision. The API still verifies every token and permission (ADR-043).
* **No streaming:** the API has no SSE endpoint (turns are synchronous, ADR-043). Responses are buffered; a future streaming route needs `proxy_buffering off` and its own timeouts.
* On a managed platform the same responsibilities can move to its load balancer or ingress; `GO_LIVE_CHECKLIST.md` lists them so none is lost.

**Consequences:** `docker compose --profile gateway` runs it locally with a self-signed development certificate (`deploy/gateway/dev-certs.sh`, git-ignored). Certificate issuance and renewal belong to the platform (Q3).

### ADR-055: Hung dependencies
**Context:** The Phase 8 failure drill froze PostgreSQL (reachable but not answering: a hung process, a stalled failover, or a network that drops packets). `/ready` itself hung for more than 90 s, so an orchestrator would never have learned that the replica could not serve. Cause: asyncpg turns a timeout or cancellation into a PostgreSQL CancelRequest and waits for the server to acknowledge it, so `asyncio.timeout` cannot return before the driver unwinds.
**Decision:**
* The database readiness probe runs as its own task; the caller stops waiting at `DATABASE_HEALTH_TIMEOUT_SECONDS`, reports `DATABASE_TIMEOUT`, and leaves the cancelled probe to unwind in the background (verified: 503 in 3.0 s during the freeze).
* `DATABASE_COMMAND_TIMEOUT_SECONDS` (default 20 s, above the 15 s statement timeout) sets asyncpg's client-side `command_timeout`, which bounds commands on a responsive server whose statement does not finish.
* A fully hung server can still hold the requests that already had a pooled connection. They are bounded by the pool (at most `pool_size + max_overflow`); every other request gets 503 `DATABASE_UNAVAILABLE` after the connect or pool timeout, and clients get the gateway's 504 after its read timeout. Recovery needs no restart.

**Consequences:** during a hang, abandoned probes may occupy pool connections until the server answers, which makes other requests fail faster (desired). Orchestrator readiness probes should use a timeout above `DATABASE_HEALTH_TIMEOUT_SECONDS` (for example 5 s).

### ADR-056: The migration job never migrates a database it does not own
**Context:** The Agent DB is separate from any system of record (for example an existing ERP database). Nothing in the application connects to another database, but the migration job takes its URL from configuration, and a wrong URL would create extensions, tables and grants in someone else's database.
**Decision:** before migrating, `db/migrations/env.py` checks the target. If it has no `alembic_version` table and contains any user table, view or materialized view outside the system schemas (extension-owned tables excepted), the job stops with `ForeignDatabaseError`, naming up to five tables, before Alembic creates anything. `AGENTIC_MIGRATE_NON_EMPTY_DATABASE=1` overrides it for a deliberate adoption only. The application keeps exactly one database setting pair (`DATABASE_URL`, `DATABASE_MIGRATION_URL`), both for the Agent DB.
**Consequences:** verified against a scratch database shaped like a system of record: no `alembic_version`, no extension, the existing table and row untouched. Existing Agent DBs (with `alembic_version`) and empty new ones migrate as before.

### ADR-057: Go-live gate
**Context:** Phase 8 must leave a go-live checklist, but the production identity provider, deployment target and Anthropic credential are not available.
**Decision:** `GO_LIVE_CHECKLIST.md` lists every go-live item with its evidence (a test, a command and its output, or a document). An item is checked only when it was run in this repository; items that need the production environment say exactly what to run there. The production configuration gate (`--check-config`) and the template test (`tests/test_deployment_unit.py`) are the machine-checkable part.
**Consequences:** "verified locally" and "verified in production" stay distinct; the open decisions (Q3, Q6, Q20, Q22) block specific checkboxes, not the whole list.

### ADR-058: Verify the ERP role read only before any integration; no ERP connection in the application yet
**Context:** The Phase 9 brief asks to connect the platform to the existing ERP PostgreSQL database read only and to verify a business read layer, business tools and a business query agent against it. The repository has none of these (no read layer, no business tools, no `scripts.inspect_erp_security`, no Schema Atlas), and no ERP access contract was supplied (Q1). The ERP must never be altered, migrated, seeded or written.
**Decision:**
* `scripts/inspect_erp_security.py` is an **operator tool, not part of the application**. It's outside the package and image, and isn't imported by the API or the worker. It reads one URL from `ERP_INSPECT_DATABASE_URL` and must be told the expected database name. It refuses the Agent DB in two ways: by endpoint before connecting, and by `alembic_version` after.
* It runs with `default_transaction_read_only=on`, in one READ ONLY transaction that is always rolled back, with 10 s statement and 1 s lock timeouts. It reads only the catalog and `flyway_schema_history`.
* **Write refusal is proven without writing.** INSERT, UPDATE and DELETE are planned with `EXPLAIN`. PostgreSQL checks table privileges when it starts the executor, even for EXPLAIN-only statements, but those statements are allowed in a read-only transaction, never execute and fire no triggers. A read-only role gets `42501`. DDL is proven impossible from the catalog: no CREATE on the database or any schema, and no owned objects. A rolled-back trial write was rejected: on a misconfigured role it would actually run, fire statement triggers and advance sequences.
* Verdict rules: superuser, BYPASSRLS, CREATEDB/CREATEROLE/REPLICATION, elevated or write-all memberships, ownership, any table write privilege, sequence UPDATE, schema or database CREATE, or an accepted write plan → FAIL (exit 1). Missing evidence, such as tables without RLS, owner-rights views, sequence USAGE, SECURITY DEFINER functions, no visible tenant setting or no Flyway table, is a WARN that the ERP owner must decide. It is never a PASS.
* The application keeps exactly one database setting pair (Agent DB, ADR-056). An ERP connection setting, the read layer and the typed business tools are added only after the access contract (Q1) and the business rules exist, in a separate phase. They aren't written against guessed tables.

**Alternatives:** add `ERP_DATABASE_URL` to the application settings now (rejected: unverifiable, and it would invite use before the contract exists); prove refusals by executing writes in a rolled-back transaction (rejected, see above); `has_table_privilege` only (kept, but the server's own refusal is stronger evidence, so both are reported).
**Consequences:** verified against a throw-away PostgreSQL 16 database (SELECT-only role passes; superuser owner, a single `GRANT INSERT`, the Agent DB and a wrong name are all detected or refused; rows, sequence and catalog unchanged). Its result against the real ERP is unknown until Q1 is answered.

### ADR-059: Business read layer foundation, schema-safe and read only
**Context:** Phase 10 asks for the foundation of the business layer while the ERP access contract and schema are still missing (Q1). Nothing may be invented: no ERP table, column, rule or tenant mechanism. The LLM must never reach SQL, choose identity or scope, or be the source of a business figure.
**Decision:**
* **Boundary:** LangGraph → `BusinessDomainHandler` (domain `business`, READ tools only) → typed tool (`tools/business.py`) → `BusinessReadService` → `ErpReadPort` → adapter. The port has no write method. The tools have no tenant, user, scope, permission, table, column or SQL argument (inputs reject unknown fields).
* **Capabilities:** six, one per reference question (customer outstanding, open invoices, daily sales, product stock, warehouse stock, low stock), each with one `business.*` permission. No built-in role grants them (Q8); operators grant them through `AUTH_ROLE_CATALOG_JSON`. The existing `Principal` and `ToolRegistry` checks are reused, and the service checks again.
* **Deterministic results:** `BusinessResult` with a closed `BusinessStatus` set (SUCCESS, NOT_FOUND, AMBIGUOUS, UNAVAILABLE, UNAUTHORIZED, VALIDATION_ERROR, DEPENDENCY_ERROR) and reason codes. Only SUCCESS carries data. Out-of-tenant and out-of-scope records look exactly like missing ones. Every adapter record is re-validated and must name the caller's tenant, or the whole answer is discarded.
* **Scope:** warehouse scope comes from the principal: `business.warehouse.all` grants all warehouses, otherwise none (fail closed). A `WarehouseScopeResolver` port is there for the unknown ERP/IdP assignment mechanism.
* **Adapters:** `fake` serves deterministic `TEST_*` data with failure injection (refused in production by the policy and by the factory). `postgres` is the read-only boundary: it reuses `Database`/`UnitOfWork(read_only=True)`/`translate_db_error` with its own engine (`default_transaction_read_only=on`, statement timeout), an identity check before the first read (expected name, not the Agent DB, read only, not superuser/BYPASSRLS), and `ErpQuery` constants that pass `check_read_only_sql`. It has **no schema mapping**, so every capability answers UNAVAILABLE / RULE_NOT_VERIFIED without connecting. `none` (default) registers nothing, so behaviour is unchanged from Phase 9.
* **ERP settings** live in their own `ErpSettings` (`ERP_*`), never in `DatabaseSettings`. Alembic and the Agent DB can't see them. Production refuses an ERP URL that names the Agent DB. This amends ADR-058: connection code now exists, but it's off by default and verified only on throw-away databases.
* **Grounding:** a deterministic `verify_business_response` node after `generate_response`, for business turns only. Any number in the reply that's in no business result and not in the user's message replaces the reply with a rendering of the results. An AMBIGUOUS result ends the turn as `needs_clarification`. Memory therefore can't override ERP truth. The prompt (`generate_response.system.v3`) states the same rule.
* **Observability:** a `business.read` audit row and log line per read, with metadata only (capability, status, reason, adapter, duration, test_data). That's in addition to the registry's `tool.call`.

**Alternatives:** an LLM-generated SQL tool (rejected: it's the risk being removed); a supervisor/multi-agent graph now (rejected: one domain handler is enough, and the `DomainRegistry` stays the extension point); an `ERP_DATABASE_URL` inside `DatabaseSettings` (rejected: Alembic would see it); validating figures only by prompt (rejected: not enforceable).
**Consequences:** the business layer can be demonstrated and tested end to end against TEST data. Live answers need Q1 (mapping, rules, tenant mechanism), Q4 (business data goes to the LLM provider) and Q26. Figures written in words aren't detected by the grounding check. Business turns take one more graph step.
**Note (2026-10-30, Phase 22):** the `fake` adapter described above was deleted by owner decision; `ERP_ADAPTER` is now `none` or `postgres`, and tests run against a PostgreSQL TEST replica. The `postgres` adapter now has a schema mapping (the InvenLogic contract) and readers. See ADR-067.

### ADR-060: Business query reasoning on deterministic results, database-independent
**Context:** Phase 11 asks the agent to reason over business results: relative dates, metrics, lists, aggregation, ambiguity, not-found, follow-ups and multi-step reads. The real ERP, its schema, its business rules and its time zone are still unknown (Q1), so everything must work against the fake ERP and leave only the adapter mapping for later. The LLM must not compute dates or figures, decide business rules, or turn a failure into an answer.
**Decision:**
* **Typed intent, untrusted.** `RequestUnderstanding.business_query` (`BusinessQueryIntent`) describes capability, operation, entity, entity references (name or exact ref), invoice number, a `DatePeriod` token or an explicit date, the requested metric, filters, follow-up and write-request flags. It has no identity, scope, permission, table, column or SQL field. Naming a warehouse asks for it; scope still decides. When the planner gives no tool call, the business handler compiles the intent into exactly one call (`tool_call_for`), validated against the tool's strict input. A write request compiles to nothing.
* **Same six capabilities, narrower inputs.** No new capability, tool or permission. Optional typed arguments: `daily_sales.period` (today, yesterday, this/last week, this/last month), `customer_open_invoices` without a customer (all customers) or with `invoice_number`, `product_stock.warehouse_*`, `low_stock_products.product_*`. The port gains `sales_by_day` and optional filters. Payment history and customer counts have no capability and stay `unsupported`.
* **Dates are resolved by the service** from the injected clock and the business time zone into a half-open `BusinessDateRange` (start inclusive, end exclusive, `last_day` stated; current periods run to date). Without `ERP_BUSINESS_TIMEZONE`, relative dates are RULE_NOT_VERIFIED. With the fake adapter, the zone is labelled `timezone_source="TEST"`, never a claim about the ERP.
* **Business rules gate derived figures.** `BusinessRule` names the rules a derived figure needs (business time zone, overdue = due date before the business date, period sales = sum of days, weeks start on Monday). An adapter declares the rules its mapping vouches for (`ErpReadPort.verified_rules`): the fake declares TEST rules, and postgres declares none. A figure whose rule does not hold is not computed. It is named in `rules_not_verified`, or the read answers RULE_NOT_VERIFIED. Rules applied inside a read (what is open, which documents count) stay with the mapping.
* **The service computes; composition selects.** Totals, overdue sums, paid and gross sums and period sums are computed in `BusinessReadService`, and only from complete, single-currency lists. `metrics.measure` selects a figure from typed data or returns a `MetricGap` with the reason. `compose()` builds a `BusinessResponseContext` (figures, normalised records, filters applied to service-computed flags, date range, source, warnings, typed derivation steps, deterministic status messages and safe clarifications with display names only). The writer receives this instead of raw payloads (`generate_response.system.v4`).
* **Verification rejects, deterministic rendering replaces.** A draft is replaced by `render_context()` when it has an unsourced figure (digits, "45k", "1.25 lakh", "forty-five thousand"), hedges ("maybe", "shayad"), claims a business action happened, or when the asked-for figure is unavailable (a sourced number could otherwise stand for the wrong thing). Write requests are verified too. Hedge and claim detection are regex lists, not NLP; they fail closed.
* **Follow-ups through a reference-only focus.** `AgentState.business_focus` (state v4) keeps the last capability, entity refs and display names, period token and AMBIGUOUS candidates. It is checkpointed with the thread, shown to the planner (`understand_request.focus`), and never holds figures or reaches long-term memory. Every turn reads the ERP again; a ref from the focus resolves only inside the caller's tenant and scope.
* **Multi-step stays bounded.** One plan per turn, run in order within `AGENT_MAX_TOOL_CALLS_PER_TURN`. Resolve → retrieve → filter → aggregate happens inside one typed read. No re-planning loop; AMBIGUOUS still takes a second user turn.

**Alternatives:** a re-planning loop (rejected: unbounded and not needed for six capabilities); letting the LLM pass ISO dates for "this month" (rejected: dates would be model-made); computing overdue in the prompt (rejected: a business rule in the model); an NLP number parser dependency (rejected: a small deterministic parser covers the forms that matter, and its limits are documented); a new capability per question form (rejected: the brief keeps six, and each new one needs an ERP mapping).
**Consequences:** the reasoning layer is complete and verified against TEST data. A live adapter must map `sales_by_day` and the new filters, and declare only the rules the ERP owner confirmed. Grounding still cannot tell which sourced number a sentence refers to, except where a requested figure is missing; lone small number words and Hindi number words are not read. `prompts`: `understand_request.system.v3`, `understand_request.focus.v1` and `generate_response.system.v4` are the new defaults; earlier versions are unchanged.

### ADR-061: Supervisor as a deterministic routing node over the planner's classification
**Context:** Phase 12 asks for a Supervisor that decides which domain handles a request (GENERAL, BUSINESS, or none), handles unsupported and multi-domain requests, cannot loop, cannot reach data or tools, cannot decide authorization and cannot change a domain's answer. ADR-003 proposed an LLM router returning a `RouteDecision`, with agents able to hand back to the supervisor. ADR-019 made `understand_request` the minimal form: one structured LLM call that classifies the request *and* plans its tool calls.
**Decision:**
* **The Supervisor is a deterministic graph node (`supervise`), not a second LLM call.** The planner's single structured call still classifies (`domain`, `business_query`) and plans. It can now also name the parts of a request that need a domain that does not exist (`RequestUnderstanding.routing: RoutingHints`, untrusted). `Supervisor.decide()` turns that into a typed `SupervisorDecision` (outcome, domain, reason, confidence, clarification flag, requested action, unavailable parts, routing metadata). It has no field for a tenant, user, permission, SQL, table or tool argument, and unknown fields are refused. The `Supervisor` receives only a callable returning the registered domain names. It has no LLM, tool registry, database, ERP port or principal, so it cannot run anything or grant anything.
* **Closed domain catalogue** (`agents/routing.py`). Implemented domains are `general` and `business` (the latter registered only with an ERP adapter). Reserved future domains are `purchase`, `sales`, `inventory`, `accounting`, `gst`, `knowledge` and `notification`. `DomainRegistry.register` refuses a reserved key, so an unimplemented domain cannot be activated with a stub. Reading sales figures and stock remains `business`.
* **Rules:** (1) one routing decision and one domain run per turn (`MAX_ROUTING_DECISIONS_PER_TURN = MAX_DOMAIN_RUNS_PER_TURN = 1`, enforced in `supervise` and in the domain router `process_request`); there is no edge back to `supervise`. (2) A typed business read capability wins over a `general` or reserved label (only if `business` is registered). (3) The planner's clarification passes through. (4) A registered domain is routed. With an unavailable `write` part it is a partial route: the read runs, and `finalize_turn` appends a fixed "not done" notice. With an unavailable `send` part, the Supervisor asks first, because the read only exists to be delivered. (5) `unsupported` without named parts keeps the Phase 1 explained-unsupported reply; with named parts it gets a fixed reply. (6) A reserved domain, `business` without an ERP, and any unknown key get a fixed reply and nothing runs. The previous turn's domain is recorded (`previous_domain`, `topic_changed`) but never decides the route.
* **Common domain result contract.** `DomainResult` gains `outcome` (SUCCESS, CLARIFICATION_REQUIRED, UNSUPPORTED, UNAUTHORIZED, DEPENDENCY_ERROR, VALIDATION_ERROR, FAILED), `next_action`, `errors` (codes) and `metadata` (counts). Tool-level outcomes come from `with_tool_outcome`. The business domain maps its own `BusinessResult` statuses (`business_outcome`: NOT_FOUND is a truthful SUCCESS, AMBIGUOUS is CLARIFICATION_REQUIRED). The Supervisor reads only the domain-level outcome.
* **Finalization never overrides domain truth.** `finalize_turn` writes the Supervisor's fixed replies (which contain no figures, no hedges and no action claims, all tested), appends the partial-route notice, and records `domain_outcome` and the carried `last_domain`. It replaces a reply only when a partial *general* reply claims the missing action happened; business replies are already verified by `verify_business_response`.
* **Checkpoint safety.** Phase 12 state (`orchestration`, `last_domain`) is plain JSON (state v5). All platform checkpointers now use `checkpoint_serializer()`, a `JsonPlusSerializer` with an explicit `allowed_msgpack_modules` list (the five state models plus four Phase 11 enums). Any other type in a checkpoint is blocked, not imported. A strict-mode test runs representative turns and fails on any blocked or unlisted type.
* **Domain deadline.** A domain run has `AGENT_DOMAIN_TIMEOUT_SECONDS` (default 120) in addition to the per-tool timeouts; exceeding it is a retryable `DOMAIN_TIMEOUT` failure.

**Alternatives:** a separate LLM routing call before the planner (rejected for now: one more model round trip per turn, roughly doubling planner latency and cost, and a second classification that can disagree with the plan; the decision contract stays the same if one is added later); a keyword classifier (rejected by the brief and brittle); agents handing back to the supervisor for sequencing, as ADR-003 sketched (rejected: the brief forbids loops, and no current request needs two domains to *run*); the prebuilt `langgraph-supervisor` package (rejected: tighter control over guards and audit, as in ADR-003); using `confidence` to force clarification (not done: the planner already asks when it is unsure; confidence is recorded for observability).
**Consequences:** Single-domain requests run exactly one domain handler, verified by test. Every turn takes two more graph steps (`supervise`, `finalize_turn`) and, with a checkpointer, two more checkpoint writes; there is no extra LLM call. The planner still sees the tool descriptions of every domain the user may use, because it plans in the same call; execution is restricted per domain by the handler and the registry. Multi-domain *execution* (two domains in one turn) is not supported: the second part is reported or clarified. A future domain is activated by implementing its handler and moving its key out of `RESERVED_DOMAINS`. Prompts: `understand_request.system.v4` and `supervisor.routing.v1` are the new defaults; earlier versions are unchanged.

### ADR-062: In-process observability events, an operational error taxonomy, strict deadlines and an opt-in retry policy
**Context:** Phase 13 asks that every agent turn be observable, diagnosable, measurable and reproducible (which node failed, which domain ran, how long each stage took, whether a model call, tool run, retry or timeout happened, whether a reply was grounded or replaced, whether authorization stopped it), without connecting a real ERP, without business writes, without a second LLM routing call, and without logging secrets, prompts, memory, ERP records or tool argument values. The platform already had structured JSON logging with a `log_context` contextvar (Phase 1), request ids (ADR-045), an authoritative audit log (ADR-013, Phase 5) and a typed error hierarchy with public codes. Two latent defects were found while designing it: on Python 3.11, `asyncio.wait_for` returns the inner result when the inner coroutine swallows the cancellation, and `asyncio.timeout` exits without raising in the same case, so a deadline that fired could still yield a "successful" tool or domain result; and tool-validation failure details quoted the offending input values into logs.
**Decision:**
* **Events extend the existing logging model; no separate tracing stack.** `observability/events.py` defines a closed set of typed events (`ObsEvent`, `EventName`, `Stage`). A contextvar `TraceContext` carries `request_id` (unchanged, ADR-045), a new `turn_id` (one graph execution: a run or a resume of that request) and the current `span_id`, so nested operations get `parent_span_id` without passing ids around. `emit()` delivers to in-process sinks only: one JSON log line per event, a metrics registry, and a bounded trace buffer of recent requests. It never raises and never does I/O beyond the log handler.
* **Observability is not audit.** `ObsEvent.kind = "observability"`; it has no tenant, user, actor or input-hash field. The audit log stays the authoritative, append-only record of security actions; observability never writes audit rows. An authorization refusal produces both an audit row and an `authz.denied` event carrying less (permission name and reason code only).
* **Redaction by construction.** Event metadata is reduced to numbers, booleans and short identifier-like tokens; any free text becomes `[redacted]` and secret-named keys are dropped. Every formatted log line additionally passes a pattern scrubber (bearer tokens, Authorization headers, API keys, JWTs, passwords/secrets in key=value or JSON form, URL credentials, also inside tracebacks).
* **One operational error taxonomy over the existing codes.** `ErrorCategory` (validation, authentication, authorization, not_found, ambiguity, dependency_unavailable, timeout, retryable/non_retryable dependency, model, tool, domain, checkpoint, serialization, configuration, internal) is computed deterministically from the stable error code, then the exception type. It is used by events, metrics, retry and diagnostics. The exception hierarchy and the public error contract (`PublicError`, HTTP error body) are unchanged; two checkpoint errors are added (`CHECKPOINT_UNREADABLE`, `CHECKPOINT_SERIALIZATION_FAILED`) for failures that previously surfaced as uncategorised crashes.
* **Strict deadlines.** `strict_deadline` replaces `asyncio.timeout` / `asyncio.wait_for` for domain runs, tool calls and business reads: once the deadline fired, the block ends in `TimeoutError` even if the inner code swallowed the cancellation and returned. The work runs in the caller's task and is cancelled there; nothing is left running in the background. Nested deadlines (read < tool < domain) back each other up when a dependency ignores cancellation repeatedly.
* **Retry is opt-in per operation.** Only business **reads** are retried automatically, only when the ERP is unreachable (`dependency_unavailable`), up to `ERP_READ_MAX_ATTEMPTS` (default 2) with deterministic exponential backoff, and all attempts share the read's single deadline (a retry that would end after it is not started). Timeouts, data errors, tool calls, domain runs, graph nodes, approved executions, checkpoint/audit/memory writes are never retried here. LLM transient failures keep being retried inside the provider SDK only (`LLM_MAX_RETRIES`), so calls are never multiplied.
* **Metrics are declared, bounded and id-free.** Every metric has fixed label keys (an undeclared key raises), label values are short lowercase tokens, and each metric keeps at most 200 series (overflow folds into `other`). No tenant, user, customer, invoice, amount, thread or request label exists. No exporter is bundled; events are also log lines an aggregator can count.
* **Instrumentation adds no behaviour.** `ObservedLLM` wraps the provider (one event per call; no extra call, no retry). Checkpoint savers are instrumented on the instance (their class, and `isinstance` checks, are unchanged), and the LangGraph serde listener reports types blocked by the Phase 12 allow-list, which is not widened.
* **Evaluation is deterministic and wording-free.** A scripted-model dataset over the real graph, Supervisor, registry, business service and grounding (fake ERP) asserts structured outcomes and named safety invariants; a golden file pins the wording-free record (stages, decision, tool/business statuses, retries, timeouts, verification, error code/category, writes).

**Alternatives:** OpenTelemetry tracing and a Prometheus exporter (deferred: they need a collector and a deployment target, Q3; the event model maps onto spans and counters when chosen); retrying LLM calls in the graph (rejected: multiplies SDK retries and cost); retrying tool calls or domain runs (rejected: not provably idempotent, may include protected actions); an HTTP diagnostics/metrics endpoint (rejected for now: roles are tenant-scoped and there is no platform-operator role, so a global view could leak cross-tenant volumes; diagnostics stay operator tooling: `Agent.diagnose`, CLI `--trace`, and rebuilding from JSON logs); exact-text golden replies (rejected: brittle; only safety-critical substrings are asserted).
**Consequences:** Every turn emits roughly 25–40 events (most checkpoint events at DEBUG); one extra log line per stage at INFO when `OBSERVABILITY_LOG_EVENTS=true` (the Phase 1–12 log lines are kept for existing dashboards). A timed-out domain, tool or read can no longer report success. A transiently unreachable ERP costs one short backoff inside the same deadline instead of an UNAVAILABLE answer. Metrics and traces are per process and reset on restart. Python's cancellation model still allows code that ignores cancellation forever to delay the *innermost* deadline; the next deadline out ends the operation, and the result is a failure, never a success.

### ADR-063: The ERP is integrated only through an owner-confirmed contract, a fail-closed security gate and per-capability activation
**Context:** Phase 14 may implement the real ERP read adapter only if Q1 (the ERP access contract: owner, database, read-only role, credentials delivery, intended-ERP confirmation, tenant and warehouse isolation, time zone, business rules, invoice statuses, authorization expectations, mappings, schema version) is supplied and verified. It is not: no owner, contract, credential or schema exists for this repository, and the candidate databases seen earlier (`inventoryai-postgres`, the `new_agent` schema atlas) are explicitly not approved sources. The Phase 10 adapter already fails closed (no mapping → RULE_NOT_VERIFIED, no connection); its connection check covered database name, Agent DB, read-only session, superuser and BYPASSRLS, but not effective write privileges, ownership, schema conformance or tenant isolation.
**Decision:**
* **Q1 is a typed, reviewed document**, `ErpContract` (`business/erp_contract.py`), loaded from `ERP_CONTRACT_PATH` (JSON, kept with deployment config, never in the repository, never containing credentials). It maps every Q1 item (A–T) to a field and every capability to a 15-item mapping (sources, columns with catalog types and roles, joins as text, tenant/warehouse boundary, status/date/time-zone/null/ambiguity behaviour, rules, permission). Validation refuses unknown capabilities, a permission that differs from the platform's, identifiers that are not plain SQL identifiers, columns that fill no platform record field (data minimization), rules or status values the owner did not confirm, non-IANA time zones and anything credential-like. `status` is DRAFT until complete and confirmed.
* **The gate verifies effective privileges, not configuration.** Before the first read, `ReadOnlyErpDatabase.verify()` reads the catalog: no write privilege on any relation, no CREATE on the database or a schema, owns no relation, no elevated membership, no CREATEDB/CREATEROLE/REPLICATION (in addition to the Phase 10 checks). With a contract it also checks the PostgreSQL major version, every mapped relation, column and exact type, SELECT on each, and the confirmed tenant isolation (the tenant column on every mapped relation, or RLS enabled with a policy that reads the confirmed setting) and warehouse column. Any problem refuses the connection. Privilege names are bound parameters, so the statement guard's keyword rule still holds. The operator inspector (ADR-058) stays the full report; the readiness tool runs it first.
* **Every business read is bound to the principal's tenant by the database layer**, not by reader discipline: without a contract no business read runs; `mechanism=column` overwrites the reserved `:tenant` parameter with the principal's tenant and refuses a query without it; `mechanism=rls_session_setting` sets the confirmed setting per read-only transaction (`set_config(..., true)`, the one statement outside the guard; its name comes from the validated contract).
* **Capabilities are activated one at a time.** `PostgresErpAdapter` answers a port method only through a reader registered for its capability, and `register_reader` refuses unless the contract is VERIFIED and that mapping is VERIFIED. Readers are explicit code (reviewed `ErpQuery` constants, record fields only); SQL is never generated from the contract, the LLM or user input. No reader exists until Q1.
* **Production refuses** `ERP_ADAPTER=postgres` without a loadable, VERIFIED contract whose database and time zone match `ERP_EXPECTED_DATABASE` / `ERP_BUSINESS_TIMEZONE`. Development defaults stay `ERP_ADAPTER=none`.
* **One contract suite for every adapter** (`tests/erp_contract/`): 18 structured checks through the real `BusinessReadService`; it runs against the fake ERP now and is wired to run against the real ERP with owner-supplied test fixtures once Q1 is closed.

**Alternatives:** inferring the schema from `inventoryai-postgres` or the schema atlas (rejected: not confirmed as the ERP, and table names do not define business rules); a generic SQL builder driven by the contract (rejected: SQL must stay explicit and reviewable per capability); trusting configuration flags for read-only status (rejected: effective privileges are read from the catalog); YAML contracts (not chosen: JSON needs no new dependency).
**Consequences:** The real ERP stays NOT CONNECTED; every business question on `ERP_ADAPTER=postgres` answers UNAVAILABLE / RULE_NOT_VERIFIED, as before. When Q1 arrives, the path is mechanical and fail-closed: contract → readiness (`--live`) → readers per VERIFIED mapping → contract suite against the real ERP → production configuration. Indexes or views the readers need are ERP-owner actions; the platform never changes the ERP schema.
**Note (2026-10-30, Phase 22):** the owner confirmed the InvenLogic database (the "schema atlas" candidate above) as the company's real ERP. Superseded statements: "No reader exists until Q1" (the six InvenLogic readers now exist; they still register only for VERIFIED mappings) and "it runs against the fake ERP now" (the fake was deleted; the suite runs against a PostgreSQL 18.6 TEST replica). The contract model, gate and readiness tool were extended for several databases. Everything else in this record stands. See ADR-067.

### ADR-064: Production hardening and operational readiness: bounded shutdown, a concurrent-turn cap, build information and an ERP readiness component that never gates
**Context:** The Phase 15 code and tests were written on 2026-10-05, after the Phase 14 documentation, and cite this ADR, but the record was never written. No Phase 15 brief is in the repository. This record was written in Phase 17 (2026-10-10, documentation only) from the code, its comments and its tests. It states what is implemented, not new intent. The gaps it addresses, as the code states them: closing Redis, Agent DB and ERP pools at shutdown had no bound, so a hung dependency could keep a stopping process alive; any number of agent turns could run at once in one process; nothing identified the running release; readiness did not report the ERP adapter; DEBUG logging in production was only a warning; and there was no backup tooling for the Agent DB.
**Decision:**
* **Bounded, ordered shutdown** (`services/lifecycle.py`, `api/runtime.py`). After uvicorn's graceful phase (`API_GRACEFUL_SHUTDOWN_SECONDS`, Phase 7), the app runs `run_shutdown` within `API_SHUTDOWN_CLEANUP_SECONDS` (default 10, at most 120). The steps run in order: `in_flight_requests` (wait until cancelled requests have unwound, at most half the budget), `maintenance` (only when the scheduler runs in-process), `redis`, `database`, `erp` (only when configured), then `observability` (exporter flush in a daemon thread, bounded by `OBSERVABILITY_FLUSH_TIMEOUT_SECONDS`). Each step gets at least 0.5 s. A step that overruns is **abandoned, not awaited**, and recorded as `timeout` (`SHUTDOWN_STEP_TIMEOUT`); a failing step is recorded as `error`, and the next step still runs. The sequence never raises. It logs one `shutdown.step` line per step and one `shutdown.completed`, and the report (`ok` / `degraded`) goes on `api.stopped`. The HTTP middleware counts in-flight requests. Closed pools reconnect lazily. The maintenance worker closes its connections through the same sequence. After SIGTERM, uvicorn (0.54, pinned) re-raises the signal, so the API exits with the signal's status (143 in a container).
* **A per-process concurrent-turn cap** (`TurnSlots`, `api/dependencies.turn_slot`). At most `API_MAX_CONCURRENT_TURNS` (default 32; 0 = unlimited) agent turns run at once in one API process. The cap covers `POST /api/v1/agent/turn`, conversation start, continue and resume, and approval approve and reject. One more is refused **at once** with 503 `SERVER_BUSY` (retryable; error category `retryable_dependency`), never queued. The slot dependency comes after authentication and rate limiting, so unauthenticated or rate-limited requests never hold one. The value is a protection bound, not a capacity figure.
* **Build information** (`core/build.py`): package version plus `APP_BUILD_COMMIT` and `APP_BUILD_TIME`, baked into the image as build arguments. Values that do not match the expected shape (a hex SHA, an ISO UTC timestamp) become `unknown`. Nothing reads git at run time. It is shown by `python -m agentic_platform.api --version` (no configuration needed), on `api.started` and in `/ready`.
* **The ERP is a readiness component that never gates and never connects** (`services/readiness.erp_check`). `ERP_ADAPTER=none` reports `not_configured`. The fake adapter reports `ok` (TEST data). The live adapter reports its security-gate state (ADR-063) from memory only, through the optional `ErpReadPort` lifecycle methods `gate_state()` and `aclose()`: `verified` → `ok`, `pending` → `degraded` / `ERP_NOT_YET_VERIFIED`, `unverified` → `not_configured` / `ERP_NO_VERIFIED_CAPABILITY`, `refused` → `misconfigured` / `ERP_SAFETY_VIOLATION`. The component is always `ready`. At startup the app logs `api.started` and one `api.startup.dependencies` line (the readiness report) without blocking the start.
* **Production policy additions** (`core/production.py`): `LOG_LEVEL=DEBUG` is an error (it was a warning), `DATABASE_STATEMENT_TIMEOUT_MS=0` is an error, and `API_MAX_CONCURRENT_TURNS=0` is a warning (`TURN_CONCURRENCY_UNBOUNDED`).
* **Safe default response headers** (`api/middleware.py`): `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on every response unless the route set its own. The TLS gateway hides these two from the upstream and sends its own, so clients never receive duplicates.
* **An exporter boundary without an exporter** (`observability.hub.Exporter`). `OBSERVABILITY_EXPORTER` accepts only `none`; any other value is refused at startup, not ignored. A failing or hanging exporter cannot fail a turn or block shutdown. Which backend to use waits on the deployment target (Q3).
* **Reproducible image.** `constraints.txt` pins every runtime dependency exactly, and the Dockerfile installs with it. The base image is pinned by digest. pip, setuptools and wheel are removed from the runtime image. The Dockerfile sets `STOPSIGNAL SIGTERM` and runs as uid 10001.
* **Agent DB backup tooling** (`deploy/backup/`). `agent_db_backup.sh` writes a `pg_dump -Fc` and a manifest, and refuses a database without `alembic_version` or a platform table (exit 3), so it cannot be pointed at an ERP. `agent_db_restore.sh` restores only into a new, empty database (a non-empty target exits 4). `agent_db_verify.sh` compares a restored copy with the manifest, read-only. None of the scripts uses destructive flags, and credentials come from the libpq environment.
* **A deployment smoke test** (`deploy/smoke/`): its own compose project with throw-away PostgreSQL and Redis, the release image in its three roles, an LLM stub, and 12 named check groups (image, configuration gate, startup, authentication, turns, logs, resource limits, graceful shutdown, restart, worker, backup → restore → verify, ERP candidate untouched).

**Alternatives** (as recorded in the code comments): queueing turns over the cap (rejected: a burst would pile up requests that hold connections while they wait); awaiting a hung close (rejected: it keeps a stopping process alive); `asyncio.to_thread` for the exporter flush (rejected: executor threads are joined at interpreter exit); letting the ERP gate readiness (rejected: it would take history, approvals and general questions out of rotation for an ERP problem; business reads already fail closed); reading git at run time (rejected: the image has no git or repository); shipping a Prometheus or OpenTelemetry exporter now (deferred: needs Q3); a separate startup probe (not needed: the server listens only after startup has finished).
**Consequences:** A stopping API process is bounded by `API_GRACEFUL_SHUTDOWN_SECONDS` + `API_SHUTDOWN_CLEANUP_SECONDS` (plus the 0.5 s step minimums), so the orchestrator's grace period must exceed that sum. A hung pool close is abandoned, and the stop reports `degraded`. A turn cut by the shutdown deadline is recorded as failed and never replayed as a success (integration-tested on PostgreSQL 16). Bursts above the cap get 503 `SERVER_BUSY` per process, and the default of 32 is not sized from any production load test (Q3). Debug logging is unavailable in production. `/ready` shows the ERP state without depending on it. **Not verified:** the deployment smoke test and the backup scripts have no recorded run (no `deploy/smoke/results/`); their repository tests are static checks of the files only. No migration (head stays `0005`), and the ERP is not contacted.
**Note (2026-10-30, Phase 22):** "The fake adapter reports `ok` (TEST data)" no longer applies: the fake was deleted (ADR-067). With several ERP databases, the live adapter reports `verified` only when every database's gate passed, and `refused` when any was refused.

### ADR-065: Historical sales comparison and long-term business memory without new ERP reads or stored figures
**Context:** The Phase 16 brief asks for long-term business memory, customer purchase history, year-over-year comparisons and historical analytics tools, but only where the existing business-read interface and fake ERP can supply authorized records. The audit found: `ErpReadPort` has `sales_by_day` (day totals and document counts) and nothing at customer-transaction or product-line level; the fake ERP has four days of TEST sales near "today" and no history; memory (Phase 4/5) already offers versioned long-term facts, episodes, knowledge and procedures, all tenant-scoped, permission-checked and audited, and recall is labelled "not a source of truth". `verify_business_response` checks every figure against tool results, but only on business turns that had tool results, and a percentage could pass because its digits occur in a date.
**Decision:**
* **No new capability and no new port method.** "Same period last year" is a deterministic extension of `daily_sales`: `compare_with="same_period_last_year"` plus a relative `period` (now also `this_year` / `last_year`). The service reads both sides with `sales_by_day` under the same deadline, retry, tenant and record validation, and computes totals, change and percentage with Decimal (percentage half-up to 0.1). The live adapter's gate is untouched: `sales_by_day` still needs the VERIFIED `daily_sales` mapping, and the six-capability ERP contract is unchanged.
* **Every definition is a named rule.** `year_is_calendar_year` (not a financial year) and `same_period_last_year_by_date` (same calendar dates, 29 February → 28 February, same days for to-date periods) join the existing timezone/sum-of-days/week rules. The fake declares them as TEST rules; the live adapter declares only what a VERIFIED contract confirms (the Phase 14 mechanism), so a comparison answers RULE_NOT_VERIFIED until the ERP owner confirms them.
* **Missing history is never zero.** A side without any day record has no total; no change is computed against it (`BASELINE_NO_RECORDS` / `CURRENT_NO_RECORDS`); a zero baseline gives the amount change but no percentage (`ZERO_BASELINE`); different currencies give no change. Both sides empty is NOT_FOUND. The reply states both date ranges, the basis and the limits.
* **Customer purchase history and product-level sales comparisons are not implemented.** They need port methods, record shapes and business rules (what a customer purchase is; cancellations, returns, credit notes) that neither the ERP contract nor the fake ERP provides. Inventing them would mean inventing ERP semantics. The planner routes them to `unsupported`.
* **Long-term business memory stores references, not figures.** An episode of a business turn keeps `analyses`: capability, status, the date range asked about (and the comparison range), adapter, `test_data`, `retrieved_at`. No amounts, counts, names, entity refs or tenant ids. Recall labels each item with `basis` (`stated_by_user`, `approved_by_user`, `document`, `interaction_summary`, `unconfirmed`) and `recorded_at`, so confirmed facts and inferred summaries are distinguishable. Storage, retention (90-day episodes), erasure, RLS and audit are the existing ones; no migration.
* **Verification closes two gaps.** Every turn routed to `business` is verified, with or without tool results, so a figure from the model or from recalled memory cannot pass on a turn that read nothing. A percentage in a reply must equal a percentage field the service computed (or one the user wrote). A sales reply that speaks of profit, margin or earnings is replaced (negations such as "not profit" are allowed).

**Alternatives:** a seventh capability `sales_comparison` (rejected: it would change the Phase 14 contract and readiness for no new ERP read); fake customer-transaction fixtures to make purchase-history tests pass (rejected by the brief's own rule and because they would define ERP semantics nobody confirmed); storing comparison figures in memory for faster follow-ups (rejected: memory is never business truth; figures are re-read); an LLM-written memory summary (rejected: not needed, and not verifiable).
**Consequences:** Year-over-year sales totals and document counts work against the fake ERP (MOCK VERIFIED) and are fail-closed on the live adapter. Customer purchase history and "which products sold more" stay unsupported until Q1 supplies the mappings and rules (new capabilities then go through the ADR-063 contract). A business-domain reply on a turn without results can no longer contain figures; if it tried, the deterministic "could not be retrieved" text replaces it.

### ADR-066: Future ERP read capabilities extend the existing contract (Proposed)
**Context:** Phase 18 audited the ERP access contract (ADR-063) against the code and tests. Six capabilities exist and are tested against the fake ERP. None has a live reader. Customer sales history, product-level sales, customer payment history, supplier and purchase reads, outlet stock and history coverage do not exist at all: there is no port method, record, rule or fake record for any of them. The stakeholder wants them later, against the real ERP. The audit also found that only stock reads apply warehouse scope; that `missing_inputs` treats Q1 item S as complete only when *every* capability is mapped; and that lists are bounded by `limit + 1` / `truncated`, with no cursors. Details: [docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 5–§ 10.
**Decision (proposed; nothing implemented):**
* **Same building blocks, no parallel framework.** Each new read is one `Capability` with one permission, `ErpReadPort` methods, a `*Record` / `*Data` pair, a `CAPABILITY_RECORDS` entry (data minimization), a `METHOD_CAPABILITIES` entry, a reader registered only for a VERIFIED mapping, and one READ tool. Inputs, identifiers, dates, tenant binding, provenance, errors, retry, audit and grounding follow the existing code (ERP_CONTRACT.md § 6.1).
* **Semantics come from the owner first, fixtures second.** No capability, rule or fake record is written before the owner confirms what it means (Q27 for customer sales and product sales, Q1 N for payments, Q9 for purchases, Q30 for coverage). Fake fixtures are then built from the confirmed semantics, never the other way round.
* **Document-level reads fail closed on scope.** Until Q29 says whether sales, payment and purchase documents belong to a warehouse or outlet, a new document-level capability answers only callers with scope ALL. The six existing capabilities keep their documented behaviour.
* **Customer sales and supplier purchases are separate capabilities with separate permissions** (proposed: `business.sales.read` / `business.customer.read` for the customer side, a new `business.purchase.read` for the supplier side; Q8 decides the grants).
* **History needs coverage.** A history result states how far back the ERP vouches for its data (`coverage_start`, `complete_through`) once Q30 defines it. Until then a range without records has no total, as in ADR-065.
* **Before the first new capability,** decide whether Q1 item S means "all capabilities" or "the capabilities to activate". Adding a capability must not silently make every existing contract incomplete.

**Alternatives:** a generic "ERP report" tool taking entity and filter names (rejected: SQL and semantics must stay explicit and reviewable per capability, as in ADR-063); fake purchase-history fixtures now (rejected: they would define ERP semantics nobody confirmed, and the Phase 16 and 18 briefs forbid it); answering document-level reads tenant-wide for scope-limited users (rejected until Q29: fail closed).
**Consequences:** Nothing changes in the running system. When the owner answers, each capability is a bounded, reviewable change through the ADR-063 path. The open questions grow by two (Q29, Q30). The status becomes Accepted when the first of these capabilities is approved for implementation.
**Note (2026-10-30, Phase 22):** the context above ("Six capabilities exist and are tested against the fake ERP. None has a live reader.") is superseded: the six capabilities now have InvenLogic readers (ADR-067), tested on a TEST replica, and the fake ERP was deleted. The future capabilities this record covers are still not implemented, and this record is still Proposed. For InvenLogic their source tables are known (`sales_invoice_line`, `sales_receipt`, `purchase_db`, `accounting_db`; INVENLOGIC_MAPPING.md § 7), but no capability, permission or rule exists for them. "Fake fixtures are then built from the confirmed semantics" now means TEST rows in the replica.

### ADR-067: InvenLogic multi-database readers; fake ERP removed
**Context:** On 2026-10-30 the company owner confirmed that the *Inven Logic Schema Atlas* (PostgreSQL 18.6, six service databases, one per service, no foreign keys across databases; read 2026-10-29) describes the company's real ERP. ADR-063 had treated it as an unconfirmed candidate. The owner asked to integrate it, to give the LLM tools that read from it, to use the local Ollama model, to test everything, and to **delete the fake ERP completely** (tests use a test database instead). For the business rules the owner said "adopt your proposed defaults". The ADR-063 machinery assumed one ERP database per contract; the six existing capabilities read from three InvenLogic databases (`master_data_db`, `sales_db`, `inventory_db`). Every InvenLogic table has `company_id`, with RLS forced through `company_id = invenlogic_company_id()`. Host, read-only role and credentials have not been supplied.
**Decision:**
* **One reader per existing capability, no new capability.** `business/adapters/invenlogic.py` (reader set `invenlogic_v1`) implements `daily_sales`, `customer_outstanding`, `customer_open_invoices`, `product_stock`, `warehouse_stock` and `low_stock_products` as reviewed `ErpQuery` constants. Readers register only for VERIFIED mappings (ADR-063 unchanged). A reader that needs names reads them from `master_data` with a second query and joins in code. The contract names the reader set (`reader_set`), so a contract cannot activate readers written for another ERP.
* **Multi-database contract, gate and adapter.** The contract gains `databases` (key → identity; exclusive with `database`), `RelationRef.database`, `currency`, `TenantIsolation.policy_function` and the column role `match` (searched to find an entity by name, never returned). `PostgresErpAdapter` holds one `ReadOnlyErpDatabase` per key, and the security gate runs per database. With `policy_function`, the gate checks that the RLS policies call the function and that the function reads the contract's setting (`FUNCTION_QUERY`); otherwise it refuses the connection. Settings gain `ERP_DATABASE_URLS` (JSON key → URL; `hide_input_in_errors`), production checks each URL (not the Agent DB, no development password) and that its keys match the contract's databases, and `scripts/erp_readiness.py` takes `ERP_INSPECT_DATABASE_URLS`.
* **Tenant = InvenLogic company id.** The adapter sets the RLS setting per read transaction, and every statement also filters `company_id` itself. A tenant id that is not a company id is refused (`ERP_SAFETY_VIOLATION`).
* **Owner-adopted rules in the contract.** Gross, GST-inclusive sales of POSTED invoices; outstanding net of advances and unapplied credit notes; stock over all batches; low stock against active reorder rules; Asia/Kolkata; INR. New rule `year_is_financial_year_april` (Indian financial year); the service requires exactly one year rule, and the validator refuses a contract that confirms both.
* **Unknown values fail, never map by guess.** A document status outside the contract raises `ErpMalformedError` → `DEPENDENCY_ERROR` / `MALFORMED_RESPONSE`.
* **The fake ERP is deleted.** `adapters/fake.py`, `ERP_ADAPTER=fake`, `fake_tenant_ids` and the production `ERP_FAKE_ADAPTER` check are gone; `ERP_ADAPTER` is `none` or `postgres`. Tests run the real SQL, gate and RLS on a PostgreSQL 18.6 TEST replica of the Atlas tables (`docker/erp-test/`), with fault injection in `tests/business_helpers.ErpFaults`. The deployment smoke test uses the `postgres` boundary without a contract.

**Alternatives:** keeping the fake for development (rejected by the owner: delete completely); one database URL with cross-database views or `dblink` (rejected: it needs objects in the ERP, and the platform never changes the ERP schema); one contract per database (not chosen: one capability reads from more than one database, for example invoices in `sales` and customer names in `master_data`); trusting the ERP's RLS alone (rejected: every statement also filters `company_id`); mapping unknown statuses to a default (rejected: a wrong status changes sales and outstanding figures silently); reading `auth_db`, `purchase_db` or `accounting_db` now (rejected: no capability needs them, and `auth_db` holds credentials).
**Consequences:** On the TEST replica, the contract suite passes 18/18, `erp_readiness --live` reports READY_FOR_ACTIVATION for all three databases, and the production factory path reads outstanding, today's sales and stock. Offline readiness against the contract is BLOCKED only on A, B, C, D, E, F, G, T (before: all 20). The **live ERP is not contacted** until the owner supplies those items and the steps in [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6 pass. The RLS setting name and the status values are assumptions until confirmed on the live ERP; Q29 and Q8 stay open; indexes on `(company_id, customer_id)` and `(company_id, invoice_date)` are ERP-owner actions. Tests that need the replica are skipped without it, so a run without Docker covers less. The model's prompt context now shows `source.adapter: "postgres"` instead of `"fake"` (a backend label, no data). A model reply that wrongly calls real data "TEST data" is not caught by the reply verifier. Product-wise sales, purchase history, payments, purchases/suppliers and GST stay unimplemented (ADR-066).
