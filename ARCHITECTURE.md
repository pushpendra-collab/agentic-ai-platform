# Architecture

> **Status:** Phases 1-8 implemented: agent foundation (§ A.4), PostgreSQL (§ A.5), Redis and agent persistence (§ A.6, § A.7), agent memory (§ A.8), identity, authorization and human approval (§ A.9), HTTP API, identity-provider boundary and scheduled maintenance (§ A.10), production readiness and deployment (§ A.11), go-live hardening: gateway, TLS, rate limits, failure handling (§ A.12). Phases 10-12 against a fake ERP only: business read layer (§ A.14), business query reasoning (§ A.15), Supervisor and domain routing (§ A.16). Phase 13: observability, reliability and agent evaluation (§ A.17). Phase 14: ERP integration readiness, real ERP BLOCKED at the input gate (Q1) (§ A.18). Phase 15: production hardening and operational readiness, documented in Phase 17 (§ A.19, ADR-064); deployment smoke test and backup scripts not run. Phase 16: historical sales comparison and long-term business memory, fake ERP only; customer purchase history BLOCKED (§ A.20). Phase 18 (documentation only): ERP access contract audit and preparation, no architecture change; the capability matrix and future read contract are in [docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md) § 5–§ 10 (ADR-066, Proposed). Real ERP NOT VERIFIED. Phase 22 (2026-10-30): the owner confirmed InvenLogic as the real ERP; six capability readers over three of its databases, a multi-database contract, gate and adapter, and the Indian financial-year rule; the fake ERP was deleted and tests use a PostgreSQL 18.6 TEST replica (§ A.18 note, ADR-067, [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md)). The live ERP is NOT CONTACTED (Q1 A–G, T). Where Phases 10–16 below say "fake ERP", that is their historical test basis. The rest of Part B is still design only.
> **Last updated:** 2026-10-30
> **Related:** [PROJECT_STATUS.md](PROJECT_STATUS.md) · [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md)

This document has two strictly separated parts:

1. **Part A: Current implementation** describes what exists today, verified by inspection.
2. **Part B: Target architecture** describes what we plan to build. It is a design, not a description of code.

Where Part B mentions business behaviour, it uses only what has been documented by the business. Anything not yet documented is marked **TBD (needs business documentation)**. It is never filled in by assumption.

---

## Part A: Current implementation

### A.1 Repository inspection (2026-10-01)

| Area | Finding |
|---|---|
| Repository contents | **Empty.** 0 files, 0 directories, no hidden files. |
| Version control | **Not a git repository.** |
| Backend | None |
| Frontend | None |
| APIs | None |
| Database / schema / migrations | None |
| PostgreSQL configuration | None in the project. `psql` is not installed on this machine. |
| Redis configuration | None in the project. `redis-server` / `redis-cli` are not installed. |
| Authentication / authorization | None |
| Services / models / repositories | None |
| Environment configuration (`.env`, settings) | None |
| Tests | None |
| Documentation | None (these Phase 0 files are the first). |
| Neighbouring folders | `Desktop/Check point` holds media files and utility `.exe` files. It is not related to this project. No other candidate codebase was found. |

### A.2 Developer machine (as inspected)

| Tool | Status |
|---|---|
| Python | 3.11.9 (global interpreter; no LangGraph, LangChain, FastAPI, SQLAlchemy, Redis or psycopg packages installed) |
| Node.js / npm | v24.21.0 / 12.0.2 |
| Git | 2.55.0 |
| Docker | 29.8.0 installed. **The daemon was not running** at inspection time, so no containers were listed. |
| PostgreSQL / Redis | Not installed locally. These can be run through Docker. |
| uv / poetry | Not installed |
| OS | Windows 11 Pro Education |

### A.3 Conclusion

There is **no existing infrastructure to reuse or duplicate**. The project is greenfield. If an existing codebase lives somewhere else (another folder, a remote repository or a company Git server), it was not provided. That is the first open question in [PROJECT_STATUS.md](PROJECT_STATUS.md). If the codebase turns up, this Part A must be redone before Phase 1.

### A.4 Phase 1 implementation (2026-10-01)

Package `src/agentic_platform/` (ADR-021). Each layer calls only the layer below it:

```
agents/agent.py          Agent.arun()/run(): validates input, invokes graph, returns AgentResponse
  ↓
agents/graph.py          StateGraph(AgentState): nodes + static/conditional edges (edges.py)
  ↓
agents/nodes/*.py        understand_request · process_request · generate_response · handle_error
  ↓                      (LLM via llm.LLMProvider; prompts via prompts.PromptRegistry)
agents/domains/*.py      DomainRegistry → DomainHandler ("general" only in Phase 1)
  ↓
tools/registry.py        ToolRegistry.execute: availability → kind policy → validation → timeout → output check
  ↓
services/clock.py        ClockService (the only service; non-business)
```

Graph:

```
START → understand_request ─┬─(clear)──────────────► process_request ─┬─► generate_response ─┬─► END
                            ├─(needs clarification)───────────────────┘ ↑                      │
                            └─(error)──► handle_error ◄──(error)────────┴──────────(error)─────┘
                                              └──► END
```

| Concern | Implementation | Differs from Part B? |
|---|---|---|
| State | `agents/state.py` `AgentState` TypedDict, `STATE_VERSION = 1` | Per-turn fields overwrite instead of append (ADR-020). `principal`, `pending_action`, `working_memory` not added yet (no auth/HITL). |
| Routing | `understand_request` returns structured `RequestUnderstanding` (intent, domain, summary, clarification, planned tool calls) | Minimal supervisor form (ADR-019); since Phase 12 the `supervise` node decides the route from it (§ A.16, ADR-061) |
| LLM | `llm/base.py` interface, `llm/anthropic_provider.py` adapter, `llm/factory.py` selection | ADR-018 |
| Prompts | `prompts/templates/<name>.v<N>.md`, strict `$var` rendering, latest version by default | — |
| Tools | `tools/base.py` contract, `tools/registry.py`, `tools/system.py` (`system.current_time`) | Write tools refused; no permission/idempotency/audit yet (ADR-017) |
| Errors | `core/errors.py` hierarchy with `code` / `public_message` / `retryable` | — |
| Logging | `core/logging.py` JSON or text logs, contextvars for `request_id` / `thread_id` / `node` | No tracing/metrics yet |
| Config | `core/config.py` pydantic-settings, env + `.env` | — |
| Checkpointing | Optional; `InMemorySaver` in tests. *Phase 3: PostgreSQL saver, see § A.6* | — |

### A.5 Phase 2 implementation: PostgreSQL foundation (2026-10-01)

No PostgreSQL existed before Phase 2 (verified: no DB code, config, containers or local install), so nothing was reused. Exactly one database system exists: PostgreSQL 16.

```
Agent ─► Tool (system.database_health) ─► Service (DatabaseHealthService)
                                              │ opens
                                              ▼
                                   UnitOfWork (read_only) ── one transaction, error translation
                                              │ session
                                              ▼
                                   Repository (DatabaseHealthRepository / Repository[Model])
                                              │ SQLAlchemy expressions / fixed statements
                                              ▼
                       Database (AsyncEngine + QueuePool, asyncpg) ─► PostgreSQL 16
```

| Concern | Implementation |
|---|---|
| Configuration | `core/config.py` `DatabaseSettings` (`DATABASE_*`); URL normalised to `postgresql+asyncpg://`; non-PostgreSQL URLs rejected |
| Connection management and pooling | `db/engine.py` `Database`: one `AsyncEngine` per process; `QueuePool` (`pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`, `pool_pre_ping`); per-connection `statement_timeout`, `idle_in_transaction_session_timeout`, `application_name`, UTC; optional SSL mode |
| Sessions and transactions | `db/unit_of_work.py` `UnitOfWork`: commit/rollback, `read_only` transactions, lazy connection |
| Repository abstraction | `db/repository.py` `Repository[Model]`: get, get_required, list_page (capped at 500), count, add, delete; never commits |
| ORM conventions | `db/base.py` `Base` (constraint naming convention), `TimestampMixin`; `db/models.py` imports models for Alembic. **No tables defined.** |
| Error handling | `db/errors.py` `translate_db_error` maps SQLSTATE to `DatabaseUnavailableError` / `DatabaseTimeoutError` / `DatabaseConcurrencyError` / `DatabaseConflictError` / `DatabaseIntegrityError` / `DatabaseReadOnlyError` / `DatabasePermissionError`; details exclude SQL and parameters |
| Migrations | Alembic, `db/migrations/` (async env, advisory lock), `alembic.ini`; revision `0001` = empty baseline |
| Health | `services/database_health.py` + `db/health.py`; CLI `--db-health`; agent tool `system.database_health` |
| Least privilege | `docker/postgres/init/01-roles.sh`: `agentic_owner` (migrations) vs `agentic_app` (DML only) |
| Local infrastructure | `docker-compose.yml`: `postgres:16`, databases `agentic` and `agentic_test` |

**SQL safety (ADR-004), enforced in layers:** no tool accepts SQL or SQL fragments (guarded by a test over every registered tool); repositories build statements with SQLAlchemy and accept typed arguments only; read paths use read-only transactions; the runtime role cannot run DDL; statement timeouts bound every query.

Database changes in Phase 2: roles `agentic_owner` and `agentic_app`, databases `agentic` and `agentic_test` (local Docker only), and the Alembic bookkeeping table `alembic_version` (revision `0001`). No business tables.

### A.6 Phase 3, part A: durable state, audit, idempotency (2026-10-01)

Scope (first Phase 3 delivery, 2026-10-01): a PostgreSQL checkpointer built on asyncpg, plus the audit and idempotency tables with their repositories and services. Permissions, principal and the HITL approval gate are **not** part of Phase 3, so write tools are still refused. The stakeholder later defined Phase 3 as "Redis and agent persistence"; this part covers its checkpoint items, and § A.7 covers the rest.

```
Agent.arun ─► compiled graph ─► PostgresCheckpointSaver (agents/checkpointer.py)
                                   │ UnitOfWork per call (reads: read-only)
                                   ▼
                       CheckpointRepository (db/checkpoints.py) ─► agent_checkpoints / _blobs / _writes

ToolRegistry.execute ─► … ─► result ─► AuditService.record ─► AuditRepository ─► audit_log (append-only)

(future write tools) ─► IdempotencyService.claim/complete/fail ─► IdempotencyRepository ─► idempotency_record
```

| Concern | Implementation |
|---|---|
| Checkpointer | `PostgresCheckpointSaver`: async `BaseCheckpointSaver` on asyncpg (ADR-027). Selected by `AGENT_CHECKPOINTER` through `create_checkpointer`; `Agent.from_settings` and the CLI use it. CLI `--thread <id>` resumes a stored conversation. |
| Checkpoint storage | `db/checkpoints.py`: `agent_checkpoints` (serialized checkpoint + JSONB metadata), `agent_checkpoint_blobs` (channel values per version), `agent_checkpoint_writes` (pending writes, including interrupts/resumes) |
| Audit | `db/audit.py` (`AuditLogEntry`, `AuditRepository`: append/list only) and `services/audit.py` (`AuditService`, `AuditEvent`). Triggers make `audit_log` append-only for every role (ADR-028). `ToolRegistry(auditor=...)` records every tool call with an input hash. |
| Idempotency | `db/idempotency.py` (`IdempotencyRecord`, `IdempotencyRepository`, `NewClaim`) and `services/idempotency.py` (`IdempotencyService`): claim / replay / reuse detection / lease / claim token / TTL / purge (ADR-029). Not wired to any tool yet. |
| Hashing | `core/hashing.py` `stable_hash`: canonical-JSON SHA-256 for audit input hashes and idempotency request hashes |
| Errors | `IdempotencyError` family in `core/errors.py` |
| Migration | `0002`: five platform tables, indexes, the append-only trigger function and triggers. A test checks that the models and the migration match (no drift). |

Database changes in Phase 3: tables `agent_checkpoints`, `agent_checkpoint_blobs`, `agent_checkpoint_writes`, `audit_log`, `idempotency_record`; function `audit_log_reject_change()`; triggers `audit_log_no_update_delete` and `audit_log_no_truncate`. The runtime role gets DML through the existing default privileges, and the triggers block UPDATE/DELETE on `audit_log`. Still no business tables.

### A.7 Phase 3, part B: Redis, thread model, resume (2026-10-01)

Scope (stakeholder definition of Phase 3, "Redis and agent persistence"): Redis connection, configuration and service abstraction; cache; temporary state; idempotency support; distributed locks; conversation/thread identification; workflow resume; persistence error handling. LangGraph checkpointing was already delivered in part A (§ A.6) and is reused, not rebuilt.

#### Where each kind of data lives

| Data | Store | Why |
|---|---|---|
| Business data (future domains), audit log, idempotency records of record | **PostgreSQL** | Source of truth, transactional (ADR-006) |
| Conversation / graph state (checkpoints, pending writes, interrupts) | **PostgreSQL** (`agent_checkpoint*`) | Must survive restarts and Redis flushes; needed to resume (ADR-027) |
| Cache (cache-aside reads) | **Redis** | Fast, rebuildable from the source; fails open |
| Temporary state (scratch, drafts, progress) | **Redis** | Short-lived by definition; mandatory TTL |
| Distributed locks (one turn per thread) | **Redis** | Shared by all processes; short leases with renewal |
| Idempotency fast-path (completed results) | **Redis** | Replay without a PostgreSQL round trip; PostgreSQL still decides on any doubt |

Every Redis key has a TTL and the prefix `<REDIS_KEY_PREFIX>:`. Losing Redis loses nothing that matters: caches refill, locks expire, idempotency falls back to PostgreSQL. The local Redis runs with no persistence and `maxmemory-policy noeviction` (writes fail loudly rather than silently evicting locks).

#### Redis layer

```
cache/backend.py     KeyValueBackend contract ─┬─ InMemoryBackend   (no REDIS_URL: this process only)
cache/redis.py       RedisConnection (pool, lazy, ─┴─ RedisBackend    (REDIS_URL: shared; prefix; Lua compare-and-*)
                     dispose per event loop)
cache/errors.py      redis-py exceptions → CacheError family (no keys/values in messages)
        ▲
        ├── cache/cache.py            Cache: cache-aside JSON, namespaces, FAILS OPEN
        ├── cache/temporary_state.py  TemporaryStateStore: scoped, TTL, size cap, FAILS CLOSED
        ├── cache/locks.py            LockManager: SET NX PX + token, renewal, compare-and-delete, FAILS CLOSED
        └── services/idempotency.py   fast-path for completed results, FAILS OPEN to PostgreSQL
services/redis_health.py  RedisHealthService (ok / unavailable / not_configured); CLI --redis-health
```

`build_cache_runtime(settings.redis)` picks the backend. Every abstraction is written once against `KeyValueBackend`, and both backends pass the same contract test suite (`tests/cache_contract.py`).

#### Checkpoint and resume architecture

```
Agent.arun(text, thread_id?, idempotency_key?)                 Agent.aresume(thread_id, resume_value?)
  │ validate thread id (threads.py)                              │ validate thread id
  ▼                                                              ▼
LockManager.hold("thread:<id>")  ── busy → failed THREAD_BUSY (retryable)
  │
  ├─ idempotency claim (agent.turn, "<thread>:<key>") ── replay → stored AgentResponse (replayed=true)
  ├─ pending-turn guard: checkpoint.next non-empty → ThreadPendingError       (aresume: next empty → NothingToResumeError)
  ▼                                                                            interrupted → Command(resume=value)
graph.ainvoke(input, {"thread_id": id}, durability="sync")                     crashed     → ainvoke(None): continue
  │   every node's result is checkpointed before the next node starts
  ▼
PostgresCheckpointSaver ─► agent_checkpoints / _blobs / _writes
  │
  ▼
AgentResponse: completed | needs_clarification | unsupported | failed | interrupted(+interrupts)
```

* **Thread identification** (`agents/threads.py`): one `thread_id` per conversation, 1-128 characters of `[A-Za-z0-9_.:-]`. It keys the checkpoint, the thread lock and turn idempotency keys. The agent generates a random id if the caller has none. It is not yet bound to a tenant or user (proposed Phase 5). *Phase 5: done. Storage keys are `<tenant>/<thread>` and each thread has an owner (§ A.9).*
* **Resume after a crash:** with `durability="sync"`, a turn that dies mid-way (process killed, node crash, lost connection) leaves the last finished step checkpointed. If the process survives, the response is `failed` with `TURN_INCOMPLETE` (retryable). `aresume` continues from the next step. Finished steps, including their LLM and tool calls, are not repeated.
* **Resume after an interrupt:** a node that calls `interrupt(value)` pauses the turn. The response is `interrupted`, with the interrupt values. `aresume(thread_id, resume_value=...)` resumes it with `Command(resume=...)`. Node error wrappers let LangGraph's control-flow exceptions (`GraphBubbleUp`) through. The production graph has no interrupting node yet; the HITL approval gate (proposed Phase 5) will be the first.
* **Pending-turn guard:** a thread with an unfinished turn refuses new input (`THREAD_HAS_PENDING_TURN`), because starting a new turn would silently abandon the paused one.
* **Thread status:** `athread_status` reports exists / pending / interrupted / next nodes / interrupts / message count. CLI: `--thread <id> --status`.

#### Persistence error handling

| Failure | Behaviour |
|---|---|
| Checkpoint store (PostgreSQL) down during a turn | `failed` with the database error code (`DATABASE_UNAVAILABLE`, retryable); details only in logs |
| Turn crashed with progress saved | `failed` / `TURN_INCOMPLETE` (retryable): resume the thread |
| Thread lock held by another turn | `failed` / `THREAD_BUSY` (retryable) after `AGENT_THREAD_LOCK_WAIT_SECONDS` |
| Redis down: locks | `failed` / `CACHE_UNAVAILABLE` (retryable). Fail closed: no turn runs without its lock |
| Redis down: cache | Miss / no-op, logged (`cache.degraded`). Fail open |
| Redis down: temporary state | `CacheUnavailableError` raised to the caller |
| Redis down: idempotency fast-path | Ignored, logged; PostgreSQL answers |
| Idempotency key reused with a different input | `failed` / `IDEMPOTENCY_KEY_REUSED` |
| Invalid thread id, pending turn, nothing to resume | `AgentInputError` subclasses raised (caller mistakes) |

### A.8 Phase 4 implementation: agent memory (2026-10-01)

```
Agent.arun(text, scope=MemoryScope(tenant_id, user_id))
  │  recall before the graph runs        record after a finished turn
  ▼                                      ▼
MemoryManager  (memory/manager.py)  recall · record_turn · forget_thread · forget_user · purge_expired
  │
  ├─ WorkingMemory        working.py       ─► TemporaryStateStore ─► Redis | in-memory      (TTL)
  ├─ ConversationMemory   conversation.py  ─► ConversationSource ─► LangGraph checkpointer
  │                                        └► MemoryStore (summary record)
  ├─ LongTermMemory       long_term.py     ─► MemoryStore + VectorStore + Embedder
  ├─ SemanticMemory       semantic.py      ─► chunking ─► Embedder ─► MemoryStore + VectorStore
  ├─ EpisodicMemory       episodic.py      ─► MemoryStore + VectorStore + Embedder        (expires)
  ├─ ProceduralMemory     procedural.py    ─► PromptRegistry (built-in) + MemoryStore (versions)
  └─ CheckpointMemory     checkpoints.py   ─► LangGraph checkpointer (read / delete)
                 │ ports.py: MemoryStore · VectorStore · Embedder · ConversationSource
                 ▼
Adapters: PostgresMemoryStore (PostgreSQL + pgvector, RLS) | InMemoryMemoryStore
          HashingEmbedder (local, lexical) | VoyageEmbedder (semantic, HTTP)
          CheckpointConversationSource
```

| Memory type | Implemented as | Storage | Lifetime |
|---|---|---|---|
| Working / short-term | Per-thread JSON scratch (`WorkingMemory`); plus the recent-message window of graph state | Redis (`state:wm:<owner-hash>:<thread>`), in-memory fallback | `MEMORY_WORKING_TTL_SECONDS` (1 h), renewed on write |
| Conversation | History read from the checkpointer; one stored summary per thread | Checkpointer (PostgreSQL) + `memory_item` `conversation_summary` | Until the thread is deleted |
| Long-term | Facts and preferences by (namespace, key), upserted, embedded for recall | `memory_item` `long_term` + pgvector | Indefinite, or per-fact TTL; delete / forget_user |
| Semantic | Chunked knowledge documents with source pointers; re-ingest replaces | `memory_item` `semantic` + pgvector | Until deleted / re-ingested |
| Episodic | One record per finished agent turn (summary, intent, outcome, tools), embedded | `memory_item` `episodic` + pgvector | `MEMORY_EPISODIC_RETENTION_DAYS` (90) |
| Procedural | Versioned procedures: built-in prompt files plus tenant-defined versions in the database | Prompt files + `memory_item` `procedural` | Versions kept until deleted |
| Checkpoint state | Read/delete view over LangGraph checkpoints | `agent_checkpoint*` (PostgreSQL) | Until the thread is deleted |

**Storage choices.**
* **PostgreSQL** holds durable, structured memory: one `memory_item` table, with a `kind` discriminator, partial unique indexes for natural keys, JSONB metadata and `expires_at`.
* **pgvector** sits in the same database. `embedding vector` is stored without a fixed dimension, together with `embedding_model`. Searches filter on the model and dimension and order by cosine distance (`<=>`). That keeps one table usable across embedding models, at the cost of no ANN index yet: searches are exact scans within the tenant.
* **Redis** holds working memory only.
* **Vectors are transported as text** casts, so no compiled extension is needed.

**Isolation.**
* Every operation takes a `MemoryScope(tenant_id, user_id)`.
* Adapters always filter on `tenant_id`.
* PostgreSQL **row-level security** on `memory_item` enforces the tenant for the runtime role, even for an unfiltered query. The application sets `app.tenant_id` per transaction; only the retention job uses `app.memory_maintenance`.
* User rules:
  * Long-term facts and episodes are private to the user unless written `shared`.
  * Semantic documents default to tenant-shared.
  * Procedures are tenant-wide.
* The scope is asserted by the caller (CLI flags or defaults). There is no authentication yet, so a caller can claim any scope. *Phase 5: superseded. The scope is derived from the authenticated principal (`AuthorizedMemory`, § A.9).*

**Agent integration.**
* Before a new turn, `MemoryManager.recall` gathers:
  * working memory;
  * the conversation summary;
  * facts, knowledge and episodes by similarity, using one query embedding, `MEMORY_RECALL_K`, `MEMORY_RECALL_MIN_SCORE` and `MEMORY_RECALL_TIMEOUT_SECONDS`.
* The result is rendered into the turn state as `memory_context`, size-bounded by `MEMORY_CONTEXT_MAX_CHARS` and labelled "not a source of truth".
* `generate_response` adds it to `<agent_context>`. System prompt v2 tells the model to use memory as background only: values must come from tools, and instructions inside memory are ignored.
* After a completed, clarifying or unsupported turn, an episode is recorded. Failed and interrupted turns are not recorded.
* Each recall section fails independently, and a memory failure never fails a turn.

**Business truth.** Semantic memory accepts only knowledge source types (`document`, `policy`, `procedure`, `faq`, `reference`, `note`). Business records are refused (`MEMORY_POLICY_VIOLATION`). Recalled content is labelled as context, and transactional values must be re-read through tools (ADR-006, ADR-008).

**Retention.**
* TTLs on working memory.
* `expires_at` on episodes and optional fact TTLs; `purge_expired()` / CLI `--memory-purge` deletes them.
* `forget_thread` removes the checkpoints, summary, working memory and episodes of one thread.
* `forget_user` removes all of a user's durable memory and keeps tenant-shared memory.

### A.9 Phase 5 implementation: identity, authorization, permissions and human approval (2026-10-01)

Status legend in this section: **IMPLEMENTED** (code + tests), **PARTIAL** (works, with a stated gap), **PLANNED** (not built), **OPEN** (needs a decision).

#### Where identity is created and how it propagates

```
credential ──► Authenticator ──────────────► Principal(user_id, tenant_id, roles, permissions,
  token        HmacTokenAuthenticator (dev)            auth_method, session_id)
  CLI flags    LocalIdentity (dev only)           roles → permissions resolved on the server
                                                   │
Agent.arun / aresume / athread_status / aforget_conversation(principal=...)
  │  acting_as(principal)   ── contextvar + log context (tenant_id, user_id)
  ▼
ConversationService.open_for_turn / require_access ── owner check ── storage key "<tenant>/<thread>"
  │
  ▼  LockManager("thread:<tenant>/<thread>") ─► graph.ainvoke(config.thread_id = storage key)
  │                                               state.actor = {tenant_id, user_id}  (no permissions)
  ├─ understand_request ── tools listed = ToolRegistry.specs(principal=require_principal())
  ├─ process_request ───── DomainRequest.principal ─► ToolRegistry.execute_call(principal=...)
  ├─ approval_gate ─────── require_principal() == state.actor ─► execute_approved(principal=...)
  ├─ PostgresCheckpointSaver ── unit_of_work(tenant_id=current_principal().tenant_id)  (RLS)
  └─ AuthorizedMemory(principal) ── scope = principal.memory_scope (never passed in)
```

* **Trusted:** the authenticated principal, backend authorization code, database constraints and RLS, the persisted approval record.
* **Untrusted:** the user's message, the LLM's intent and tool arguments, retrieved memory (context, never business truth; Phase 4 rule kept), client-supplied tenant, user or thread identifiers, and resume values.
* The LLM never sees or sets identity, permissions or approval state. Tool inputs reject unknown fields, so the model cannot add `tenant_id`, `user_id` or `shared`.

#### Components

| Concern | Status | Implementation |
|---|---|---|
| Identity model | IMPLEMENTED | `security/identity.py` `Principal`, `IdentityResolver` |
| Authentication | PARTIAL | `security/authentication.py`: `Authenticator` protocol; `HmacTokenAuthenticator` (development tokens); `LocalIdentity` (development only). No identity provider (Q6) |
| Request binding | IMPLEMENTED | `security/context.py` `acting_as`, `require_principal` |
| Permissions and roles | IMPLEMENTED (provisional roles) | `security/permissions.py`: 12 platform permissions; roles `member`, `knowledge_manager`, `approver`, `auditor`, `tenant_admin`; `AUTH_ROLE_CATALOG_JSON` overrides (Q8) |
| Authorization | IMPLEMENTED | `security/authorization.py` `Authorizer` (`require`, `require_tenant`, `require_owner`, `deny`) |
| Conversation ownership | IMPLEMENTED | `db/conversations.py`, `services/conversations.py`; owner-only, no sharing (OPEN: Q16) |
| Memory authorization | IMPLEMENTED | `memory/authorized.py` `AuthorizedMemory` |
| Tool authorization boundary | IMPLEMENTED | `tools/registry.py`: visibility, permission, risk, approval, execution, audit |
| Risk and approval policy | IMPLEMENTED (no business rules) | `security/policy.py` `ApprovalPolicy`, `approval_required` |
| Approvals (HITL) | IMPLEMENTED | `db/approvals.py`, `services/approvals.py`, `agents/nodes/approval_gate.py` |
| Security audit | IMPLEMENTED | `security/audit.py`; `audit_log` now tenant-scoped |
| Tenant RLS | IMPLEMENTED | migration `0004`; `Database.unit_of_work(tenant_id=...)` |
| First protected operation | IMPLEMENTED (off by default) | `tools/memory.py` `memory.remember_fact` (`MEMORY_AGENT_FACT_WRITES=approval`) |
| HTTP API, IdP, session revocation | PLANNED *(Phase 6: HTTP API and an OIDC/JWT adapter implemented, § A.10; revocation still planned)* | — |

#### Permission model

| Permission | Lets the holder | Default roles |
|---|---|---|
| `conversation.read` | read the status and history of own conversations | member |
| `conversation.write` | start, continue and resume own conversations | member |
| `conversation.delete` | forget own conversations | member |
| `memory.read` | recall own and tenant-shared memory | member |
| `memory.write` | write and update own private memory (also: receive automatic episodes) | member |
| `memory.delete` | delete own private memory, forget own memory | member |
| `memory.shared.write` | write tenant knowledge, shared facts, procedures | knowledge_manager |
| `memory.shared.delete` | delete them | knowledge_manager |
| `tool.execute` | run (read) tools through the agent | member |
| `approval.request` | ask for approval of a protected operation | member |
| `approval.decide` | approve or reject other users' requests | approver |
| `audit.read` | read the tenant's audit trail | auditor |

`tenant_admin` holds all twelve. Domain tools declare their own permission strings (`ToolSpec.required_permission`).

#### Conversation ownership

* A conversation belongs to one tenant and one user: the user who sent its first message (`conversation_thread`).
* Only the owner can continue, resume, read the status of, or forget it. There is no sharing and no administrator access (OPEN: Q16).
* Another user's or tenant's conversation behaves exactly like a missing one: `CONVERSATION_NOT_FOUND`, `exists=false`.
* Storage keys are `<tenant>/<thread>`, so the same public id in two tenants names two separate conversations.

#### Approval lifecycle and pause/resume

```
process_request ─► ToolRegistry.execute_call ── write / requires_approval / risk ≥ APPROVAL_MIN_RISK
                     │ permission ✓  input ✓
                     ▼
                   ApprovalService.request ──► approval_request(pending) + audit approval.requested
                     │                           (one transaction)
                     ▼
state.pending_action ─► approval_gate ─► interrupt({type: approval_required, approval_id, summary, ...})
                                              │ AgentResponse(status="interrupted")
human:  ApprovalService.decide(approver)  ────┤ pending → approved | rejected  (+ audit, CAS)
caller: Agent.aresume(thread, principal) ─────┤ refused while pending (APPROVAL_PENDING)
                                              ▼
approval_gate (re-runs) ─► principal == state.actor ─► ToolRegistry.execute_approved
   1 restore checkpoint (LangGraph)          5 approval: same tenant, requester, thread, turn,
   2 principal from authentication             operation, argument hash; approved; unexpired
   3 tenant: storage key + RLS               6 permission re-checked with current roles
   4 user: conversation owner + state.actor  7 idempotency claim approval.execute:<tenant>/<id>
                                             8 execute ─► approval executed + operation.executed audit
```

Rejected, expired, cancelled, stale or invalid approvals become an error tool result (`APPROVAL_REJECTED`, `APPROVAL_EXPIRED`, `APPROVAL_CANCELLED`, `APPROVAL_STALE`, `APPROVAL_INVALID`, `PERMISSION_DENIED`). They are audited as `operation.rejected`, nothing runs, and the reply explains it.

#### Database changes (migration `0004`)

| Object | Purpose |
|---|---|
| `conversation_thread` | PK (`tenant_id`, `thread_id`), `owner_user_id`, timestamps; index (`tenant_id`, `owner_user_id`, `updated_at`); length checks |
| `approval_request` | UUID PK; tenant, requester, workflow (`thread_id`, `request_id`), operation + hash, permissions, risk, summary, reason, `resource_refs`, status, decision, expiry, execution; 9 checks; indexes on (tenant, status, created), (tenant, thread), (tenant, requester, created), partial (expires_at) WHERE pending |
| RLS policies | `conversation_thread_tenant_isolation`, `approval_request_tenant_isolation`, `audit_log_tenant_read`, `audit_log_tenant_insert`, `agent_checkpoints*_tenant_isolation` (key prefix) |
| Privileges | UPDATE, DELETE, TRUNCATE on `audit_log` revoked from non-owner roles (restored on downgrade) |

#### Error handling

| Situation | Error (code) | Public message reveals |
|---|---|---|
| No credential / local identity disabled | `UnauthenticatedError` (`AUTHENTICATION_REQUIRED`) | nothing |
| Forged, malformed or expired token | `InvalidIdentityError` (`INVALID_CREDENTIALS`) | nothing (the reason is in the audit record only) |
| Identity without tenant | `MissingTenantError` (`TENANT_REQUIRED`) | — |
| Missing permission | `PermissionDeniedError` / tool `PERMISSION_DENIED` | that the action is not allowed |
| Another user's or tenant's conversation, approval or resource | `ConversationNotFoundError` / `ApprovalNotFoundError` / `CrossTenantAccessError` (`NOT_FOUND`) | the same as for a missing item |
| Resume by someone other than the turn's actor | `InvalidResumeContextError` | — |
| Approval pending, expired, rejected, stale, invalid, already decided | `Approval*Error` | the approval's state only |

`SecurityError` subclasses propagate from `Agent` like `AgentInputError`, so an API layer can map them to 401, 403 or 404.

#### Performance (measured; ADR-041)

On local Docker for Windows (about 2.3 ms per round trip), a turn issues 29 SQL statements instead of 18 and takes about 25-35 ms longer (median about 140 ms vs about 110 ms, noise ±15 ms). The extra statements are:

* one ownership upsert;
* one RLS binding per checkpoint write transaction.

Read transactions bind the tenant for free. Authorization checks need no queries.


### A.10 Phase 6 implementation: HTTP API, identity-provider boundary and scheduled maintenance (2026-10-02)

Status legend: **IMPLEMENTED** (code + tests), **PARTIAL** (works, with a stated gap), **PLANNED**, **OPEN**.

#### Request flow

```
HTTP request
  │ RequestContextMiddleware ── request id (server-generated) → log context, X-Request-ID,
  │                             1 MiB body limit, last-resort 500 envelope        (api/middleware.py)
  ▼
FastAPI router: /api/v1/* · /health · /ready                                       (api/routes/)
  │ get_principal ── Authorization: Bearer → Authenticator → Principal             (api/dependencies.py)
  │                  dev_token: HmacTokenAuthenticator (not in production)
  │                  oidc:      ProviderAuthenticator(OidcJwtProvider, IdentityMapper)
  ▼
handler (mapping only) ──► Agent · ConversationService · ApprovalWorkflow · AuthorizedMemory · AuditReader
                              │  Authorizer, ownership, approvals (Phase 5, unchanged)
                              ▼
                            LangGraph → tools → services → repositories → PostgreSQL (RLS) / Redis
```

| Concern | Status | Implementation |
|---|---|---|
| HTTP framework | IMPLEMENTED | FastAPI + uvicorn (ADR-014 now accepted); `api/app.py` `create_app`, `python -m agentic_platform.api` |
| Authentication boundary | IMPLEMENTED | bearer header only; local identity never over HTTP; missing → 401 (not audited), invalid → 401 (audited) |
| Identity-provider abstraction | IMPLEMENTED | `security/identity_provider.py`: `IdentityProvider`, `ExternalIdentity`, `RoleMapping`, `IdentityMapper`, `ProviderAuthenticator`, `build_authenticator` |
| Real IdP integration | PARTIAL | `security/oidc.py` `OidcJwtProvider` + `JwksCache`; tested with local keys and a stubbed JWKS, not with a real provider (OPEN Q6) |
| API authorization | IMPLEMENTED | handlers hold no role logic; the Phase 5 `Authorizer` decides inside the services and the agent |
| Agent and conversation API | IMPLEMENTED | `api/routes/agent.py`, `conversations.py` |
| Approval API | IMPLEMENTED | `api/routes/approvals.py`, `application/approvals.py` `ApprovalWorkflow` |
| Memory API | IMPLEMENTED | `api/routes/memory.py` (only `AuthorizedMemory` operations) |
| Audit API | IMPLEMENTED | `GET /api/v1/audit`, `application/audit.py` `AuditReader` |
| Errors | IMPLEMENTED | `api/errors.py`: one envelope, platform error → HTTP status |
| Health / readiness | IMPLEMENTED | `/health` (liveness), `/ready` (`services/readiness.py`) |
| Scheduled maintenance | IMPLEMENTED | `maintenance/`: jobs, runner, scheduler, worker |
| Token revocation, rate limits, CORS | PLANNED | — |

#### Layering

* `api/` depends on `agents`, `application`, `services`, `security`, `maintenance`; nothing depends on `api/`.
* `application/` composes services and the `Agent` for entry points (CLI and HTTP) and holds no rules: `ApprovalWorkflow` = `ApprovalService.decide` + `Agent.aresume`; `AuditReader` = `audit.read` check + tenant-scoped `AuditService` read.
* Handlers never open a unit of work or a repository. The only identity input is the `Principal` from `get_principal`.

#### Authentication and the identity provider

```
Bearer token ─► IdentityProvider.verify ─► ExternalIdentity ─► IdentityMapper ─► Principal
                signature (JWKS kid),      subject, tenant,     RoleMapping:       roles →
                alg allow-list, exp/nbf/   external roles,      external →         permissions
                iat, iss, aud, claims      session, expiry      platform roles     (RoleCatalog)
```

* Provider selection (`resolve_auth_provider`):
  * `AUTH_PROVIDER=oidc`;
  * `dev_token` (default when `AUTH_TOKEN_SECRET` is set outside production; **refused in production**);
  * nothing usable → the API does not start.
* `OidcJwtProvider`:
  * asymmetric algorithms only; symmetric JWKS keys ignored; key algorithm must match the token's;
  * discovery must name the configured issuer; https in production;
  * JWKS cache with TTL, refresh on unknown `kid` at most every 30 s;
  * an outage is `IDENTITY_PROVIDER_UNAVAILABLE` (503), not an invalid credential.
* Claims never carry permissions; roles go through `RoleMapping` and the role catalogue.

#### Correlation (ADR-045)

```
X-Request-ID (server) = log request_id = AgentResponse.request_id = state.request_id
   = tool.call / approval.* / operation.* / memory.* audit request_id
   = approval_request.request_id
```

* A client `X-Request-ID` is logged as `client_request_id` only. It is never reused, because approvals bind (thread, request id).
* A resumed turn keeps its original turn request id (approval binding). The resume call's HTTP id is in its response header and logs.
* Maintenance runs use their run id as `request_id`.

#### Error mapping

401 unauthenticated · 403 permission · 404 missing or not visible (cross-tenant identical) · 409 state/conflict · 410 approval expired · 413 body too large · 422 validation · 502 language model · 503 dependency/feature unavailable · 500 other. Body: `{"error": {"code", "message", "retryable", "request_id", "details"?}}`. Validation details carry location and message, never the submitted value. Failed agent turns return the `TurnResponse` with its `error`, under the mapped status.

#### Health and readiness

* `/health`: liveness only.
* `/ready`: database `ok` (at migration head) or, outside production, not configured; Redis `ok` or not configured; authenticator built. 503 otherwise.
* Statuses, latencies and error codes only.

#### Scheduled maintenance (ADR-044)

```
MaintenanceScheduler (dev loop) / cron / container / cloud job
  ▼
MaintenanceRunner ── lock "maintenance:<job>" ── batches (≤ batch size, ≤ max batches) ── JobRunReport
  ▼                                                                        ├─ log maintenance.job.*
MaintenanceJob.run_batch(limit) = one transaction, FOR UPDATE SKIP LOCKED   └─ audit maintenance.run
  ├─ memory.expired       MemoryManager.purge_expired_batch       (memory_item.expires_at)
  ├─ approvals.expired    ApprovalService.expire_due(limit)       (approval_request.expires_at)
  └─ idempotency.expired  IdempotencyService.purge_expired(limit) (idempotency_record.expires_at)
```

Jobs apply existing expiry only. Checkpoints, the audit log, decided approvals and conversation rows have no retention rule (OPEN Q11) and therefore no job. The API process runs the scheduler only with `API_RUN_MAINTENANCE=true` (development).

#### Security fixes (ADR-046)

* `ApprovalPolicy.requester_only` (migration `0005`, column `approval_request.requester_only`): private requests (the user's own memory facts) are visible to and decidable by their requester only.
* `ToolRegistry`: an `executed` approval is never run again, even after its idempotency record expired or was purged.

#### Performance (measured)

* HTTP adds about 6-11 ms median to a PostgreSQL-backed turn (about 145-148 ms direct vs about 154-156 ms through uvicorn on this machine).
* Authentication-only requests take about 2-3.5 ms.
* No authorization queries were added. "Continue conversation" performs one ownership read before the turn.

### A.11 Phase 7 implementation: production readiness and deployment (2026-10-02)

Status legend: **IMPLEMENTED** (code + tests), **VERIFIED** (exercised against the real thing), **PARTIALLY VERIFIED**, **PLANNED**, **OPEN QUESTION**.

#### Deployment architecture (ADR-050)

```
                    ┌──────────────── identity provider (OIDC; Q6 open) ────────────────┐
 clients ── TLS ──► load balancer ──► api × N  (python -m agentic_platform.api)          │
                                        │  /health liveness · /ready readiness           │
                                        │  JWKS fetched + cached ◄───────────────────────┘
                                        ├──► LLM provider (Anthropic API, https)
                                        ├──► PostgreSQL 16 + pgvector (managed; runtime role, RLS, TLS)
                                        └──► Redis 7 (managed; TLS + password; locks, fast-paths)
 scheduler ──► worker (python -m agentic_platform.maintenance [--loop]) ──► PostgreSQL, Redis
 deploy pipeline ──► migrate job (alembic upgrade head, owner role) ──► PostgreSQL   (before new api)
 secret manager ──► env vars or files in APP_SECRETS_DIR ──► every role
```

| Component | Status | Implementation |
|---|---|---|
| Application image | IMPLEMENTED, VERIFIED (built and run) | `Dockerfile`: one image, roles api / worker / migrate; non-root uid 10001; no secrets; defaults to `APP_ENV=production`; `.dockerignore` |
| Local/staging-shaped stack | IMPLEMENTED, VERIFIED | `docker-compose.yml` profile `app` (`migrate`, `api`, `maintenance`) on the existing `postgres`/`redis` services, whose definitions (config hashes) are unchanged |
| Production orchestration manifests | PLANNED | waits for the deployment target (Q3) |
| Managed PostgreSQL / Redis | PLANNED (requirements documented) | pgvector 0.8+, TLS `verify-full`, backups/PITR; Redis with TLS and authentication |

#### Process roles

| Role | Command | Scaling | Shutdown | Health |
|---|---|---|---|---|
| api | `python -m agentic_platform.api` | N replicas (Redis required) | SIGTERM → stop accepting, finish in-flight requests up to `API_GRACEFUL_SHUTDOWN_SECONDS`, close pools | `/health`, `/ready` |
| worker | `python -m agentic_platform.maintenance` (one-shot, scheduled; preferred) or `--loop` | any number: per-job Redis lock + `SKIP LOCKED` | SIGTERM → current batch commits, remaining batches/jobs wait for the next run | exit code (one-shot); logs + `maintenance.run` audit rows |
| migrate | `alembic upgrade head` | one job per deploy; concurrent runs serialized by an advisory lock | n/a | exit code |

#### Startup and dependency behaviour

| Dependency | Class | At startup | While running |
|---|---|---|---|
| Configuration (production policy) | REQUIRED | errors → exit 2, nothing served (ADR-047) | n/a |
| Identity provider configuration | REQUIRED (api) | missing/invalid → exit 2 | provider unreachable → 503 `IDENTITY_PROVIDER_UNAVAILABLE` (cached keys keep working until the cache expires) |
| PostgreSQL | REQUIRED | process starts (lazy connect); `/ready` 503 until reachable **and** at the migration head | 503 `DATABASE_UNAVAILABLE`, `Retry-After` |
| Pending migrations | REQUIRED | `/ready` 503 (`degraded`) until the migrate job ran; the API never migrates | n/a |
| Redis | REQUIRED in production | process starts; `/ready` 503 until reachable | turns 503 `CACHE_UNAVAILABLE` (thread lock fails closed); idempotency falls back to PostgreSQL |
| LLM | DEGRADED | production without a credential → exit 2; otherwise `/ready` reports `llm` but never gates | turns 502/503; approvals, memory, audit, history, auth keep working |
| Maintenance worker | OPTIONAL for serving | not needed by the API | expiry is still enforced at read time (memory recall filters `expires_at`, approvals check expiry, idempotency claims ignore expired rows); rows only accumulate |

#### Identity (ADR-048, ADR-051)

* Provider selection: **OPEN QUESTION (Q6)**. The adapter is unchanged in shape: OIDC discovery or JWKS URL, RS/PS/ES algorithms, `iss`, `aud`, `exp`/`nbf`/`iat`, top-level user/tenant/roles claims, role map, server-side permissions.
* **VERIFIED against a real OIDC implementation** (Keycloak 26.4 as a local reference provider): see PROJECT_STATUS § 3f.
* New: `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` bounds `exp - iat`.
* Token lifecycle: short-lived provider access tokens, refresh handled by the client and the provider, no API-side revocation (a token is valid until `exp` + leeway); role changes apply on the next token. Immediate revocation is not guaranteed (Q19).
* Nested role claims: not added (not required by the reference provider; top-level claims via mappers).

#### LLM (ADR-052)

```
Agent → LangGraph node → LLMProvider (llm/factory: LLM_PROVIDER) → AnthropicProvider → AsyncAnthropic
         (unchanged)       (unchanged)                               base_url: LLM_ANTHROPIC_BASE_URL
```

* Request shape checked against the current API reference: `beta.messages.create` / `beta.messages.parse(output_format=...)`, `output_config.effort`, `fallbacks="default"` with `server-side-fallback-2026-07-01`, model `claude-opus-5-5` (adaptive thinking by default).
* Failures translated without provider payloads, prompts or model output; logged once as `llm.call.failed`.
* Live status: the real endpoint was reached (an invalid key → 401 → API 502 `LLM_AUTH_ERROR`); a successful live completion is **NOT VERIFIED** (no credentials).

#### Secrets and configuration (ADR-049)

* Sources, in order: environment variables, `.env` (development only), secret files in `APP_SECRETS_DIR`.
* Environments: development (`.env.example`), staging and production (`deploy/env/*.env.example`, placeholders only; staging runs with `APP_ENV=production`).
* `--check-config` on both entry points prints the production report (codes and setting names only).

#### Observability

| Concern | Signal |
|---|---|
| Requests | `http.request.finished` (method, route, status, duration, request id) |
| Agent turns / LangGraph | `agent.turn.started/finished`, `node.completed/failed/interrupted` with request and thread ids |
| Tool calls, approvals | `tool.call`, `approval.*`, `operation.*` audit rows (append-only) with the request id |
| LLM | `llm.call.completed` (model, tokens, provider request id), `llm.call.failed` (code, status, provider request id) |
| Authentication | `auth.authenticated`, `auth.failed` (audited with a reason code), `auth.provider_unavailable`, `auth.oidc.jwks_refreshed` |
| Database / Redis | readiness checks, `DATABASE_*` / `CACHE_*` error codes in `http.request.error` |
| Maintenance | `maintenance.job.started/finished`, `maintenance.stop_requested`, `maintenance.run` audit rows |
| Configuration | `config.production.warning` at startup |

Never logged: bearer tokens, API keys, passwords, database URLs, prompts, model output, memory content, tool arguments (tests and live container logs checked).

### A.12 Phase 8 implementation: go-live hardening (2026-10-02)

Status legend: **IMPLEMENTED** (code + tests), **VERIFIED** (exercised against the real thing), **VERIFIED LOCALLY** (real components on the development machine, not the production environment), **NOT VERIFIED**, **OPEN QUESTION**.

#### Production request path

```
client ── HTTPS ──► gateway (nginx, deploy/gateway)                         ADR-054
                      TLS 1.2/1.3 · 308 redirect · security headers · 1 MiB body
                      per-address limit_req / limit_conn (429 RATE_LIMITED)  ADR-053
                      replaces X-Forwarded-* · X-Request-ID = gateway id
                      turns: long read timeout · /ready: internal only
                      upstream re-resolved at run time, never marked down (max_fails=0)
                      │  (Authorization forwarded untouched, never logged; no identity logic)
                      ▼
                    api × N (uvicorn: forwarded headers only from API_FORWARDED_ALLOW_IPS)
                      RequestContextMiddleware → get_principal (OIDC) → rate_limit(kind)
                      → handler → Authorizer / ownership / approvals (unchanged)
                      │ per-caller budget: Redis INCR, key = hash(tenant, user)
                      ▼
                    PostgreSQL (Agent DB only) · Redis · LLM provider · IdP JWKS
```

| Concern | Status | Implementation |
|---|---|---|
| TLS termination, redirect, headers, size/time limits | IMPLEMENTED, VERIFIED LOCALLY | `deploy/gateway/templates/default.conf.template`, `snippets/`; compose profile `gateway` |
| Trusted proxy handling | IMPLEMENTED | `API_PROXY_HEADERS`, `API_FORWARDED_ALLOW_IPS` → uvicorn; `*` refused in production |
| Rate limiting (gateway, per address) | IMPLEMENTED, VERIFIED LOCALLY | nginx `limit_req`/`limit_conn`, `GATEWAY_*` |
| Rate limiting (API, per verified caller) | IMPLEMENTED, VERIFIED LOCALLY | `services/rate_limit.py`, `api/dependencies.rate_limit`, `RATE_LIMIT_*` |
| Hung-database handling | IMPLEMENTED, VERIFIED LOCALLY | readiness hard deadline (`services/database_health._within`), `DATABASE_COMMAND_TIMEOUT_SECONDS` (ADR-055) |
| Agent DB / system-of-record separation | IMPLEMENTED, VERIFIED | migration guard `refuse_foreign_database` (ADR-056) |
| SSE / streaming | NOT APPLICABLE | no streaming endpoint exists (turns are synchronous, ADR-043) |
| Load-test kit | IMPLEMENTED | `deploy/loadtest/` (LLM stub, compose overlay, driver) |
| Production identity provider | OPEN QUESTION (Q6) | adapter provider-neutral; reference Keycloak only |
| Live Claude completion | NOT VERIFIED (no credential, Q22) | harness `tests/live/test_llm_live.py` unchanged |
| Deployment target manifests | OPEN QUESTION (Q3) | deployment-neutral package only |

#### Rate limiting (ADR-053)

| Layer | Key | Classes | Answer |
|---|---|---|---|
| gateway | client address (`$binary_remote_addr`) | all requests (`GATEWAY_REQUESTS_PER_SECOND` + `GATEWAY_BURST`), turn POSTs (`GATEWAY_TURNS_PER_MINUTE`), connections (`GATEWAY_MAX_CONNECTIONS_PER_IP`) | 429 `RATE_LIMITED`, `Retry-After: 1`, no `X-Request-ID` |
| API | `sha256(tenant_id, user_id)` of the verified principal | `turn`, `approval_decision`, `write`, `read` (`RATE_LIMIT_*_PER_WINDOW`, `RATE_LIMIT_WINDOW_SECONDS`) | 429 `RATE_LIMITED`, `Retry-After` = rest of the window, `X-Request-ID` |

Order inside the API: authentication (401 is not counted) → rate limit → validation and handler → authorization. Probes (`/health`, `/ready`) are never limited by the API.

#### Failure behaviour (verified locally, Phase 8 drills)

| Dependency failure | API behaviour | Recovery |
|---|---|---|
| PostgreSQL hung (frozen) | `/ready` 503 `DATABASE_TIMEOUT` in 3 s; new DB work 503 `DATABASE_UNAVAILABLE` in ~5 s; requests already holding a pooled connection wait until the gateway read timeout (504) | automatic, no restart |
| PostgreSQL pool exhausted | 503 `DATABASE_UNAVAILABLE`, `Retry-After: 2`; no 500s | immediate |
| Redis hung or stopped | `/ready` 503 `CACHE_*` in 2 s; turns 503 (thread lock fails closed); reads 200 (+2 s, rate limit fails open) | automatic |
| LLM unreachable | turns 503 `LLM_UNAVAILABLE` + `Retry-After`; `/ready` 200 (degraded) | automatic |
| IdP unreachable | warm key cache: tokens accepted, forged refused; cold cache: 503 `IDENTITY_PROVIDER_UNAVAILABLE`; malformed tokens 401 | automatic |
| API SIGTERM / recreate | in-flight turn completes (200), exit 0; gateway 503/504 JSON while no replica listens; follows the new address after recreate (3.1 s) | rolling restart, ≥ 2 replicas |
| Worker duplicate / failure / SIGTERM | held lock → `skipped`; unreachable DB → `failed`, exit 1; SIGTERM → `stop_requested`, exit 0 | next scheduled run |

#### Observability additions

| Signal | Where |
|---|---|
| `gateway.request` (request id, API request id, method, path without query, status, durations, TLS version) | gateway stdout |
| `ratelimit.exceeded` (kind, limit, retry-after, tenant, user) · `ratelimit.unavailable` (kind, code, fail_open) | API |
| `client_request_id` = gateway request id | every API log line of a proxied request |

Never logged by the gateway: `Authorization`, cookies, query strings, bodies. Verified in container logs (no `Bearer`, token fragments, client secret, passwords or API keys).

### A.13 Phase 9: production go-live verification and ERP integration readiness (2026-10-02)

Status legend as in A.12, plus **BLOCKED** (needs an input that has not been supplied) and **NOT IMPLEMENTED** (does not exist in the repository).

| Concern | Status | Notes |
|---|---|---|
| Production identity provider | BLOCKED (Q6) | adapter unchanged; reference Keycloak suite re-run green |
| Live Claude completion | BLOCKED (Q22) | invalid-key check against the real endpoint re-run green; smoke test skipped (no credential) |
| Deployment target | BLOCKED (Q3) | deployment-neutral package unchanged |
| ERP access contract | BLOCKED (Q1) | [docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md) |
| ERP read-only verification tool | IMPLEMENTED, VERIFIED on a throw-away database | `scripts/inspect_erp_security.py` (ADR-058) |
| ERP read layer, business tools, business query agent, supervisor, inventory intelligence | NOT IMPLEMENTED | assumed by the Phase 9 brief; not in this repository |

ERP boundary (target, unchanged from ADR-056 and ADR-058):

```
operator ──► scripts/inspect_erp_security ──(read-only role, READ ONLY txn, EXPLAIN probes)──► ERP PostgreSQL
                                                                                               ▲
api / worker ──► Agent DB only (DATABASE_URL / DATABASE_MIGRATION_URL)                          │  no connection
migrate job  ──► Agent DB only; refuses any foreign non-empty database ─────────────────────────┘  (ADR-056)
```

A later phase adds the ERP read path inside the application, after Q1 and the business rules exist: a typed tool → read service → ERP read-only role, with the tenant and warehouse scope taken from the principal. The LLM never sees SQL or computes figures.

### A.14 Phase 10: business read layer and capability foundation (2026-10-02)

Details: [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md), ADR-059. **Live ERP: NOT VERIFIED** (Q1).

```
START → understand_request → process_request ─(business)→ BusinessDomainHandler (READ tools only)
                                  │                          → ToolRegistry → business.* tool
                                  │                          → BusinessReadService → ErpReadPort
                                  │                          → fake (TEST data) | postgres (read-only, unmapped)
                             generate_response ─(business turn)→ verify_business_response → END
```

| Concern | Status | Where |
|---|---|---|
| Business domain boundary | IMPLEMENTED, MOCK VERIFIED | `agents/domains/business.py`, `tools/business.py` |
| Typed read contracts and result semantics | IMPLEMENTED, UNIT VERIFIED | `business/models.py`, `business/results.py` |
| ERP read port and adapters | IMPLEMENTED (fake: MOCK VERIFIED; postgres boundary: VERIFIED on a throw-away PostgreSQL, no mapping). *Phase 22: fake deleted; postgres has InvenLogic readers, verified on the TEST replica* | `business/ports.py`, `business/adapters/` |
| Read-only enforcement (7 layers) | IMPLEMENTED; layers 2–7 VERIFIED locally; layer 1 (ERP role) live NOT VERIFIED | `docs/BUSINESS_INTEGRATION.md` § 4 |
| Tenant / warehouse scope | IMPLEMENTED (platform side, fail closed); ERP mechanism UNKNOWN (Q1) | `business/context.py` |
| Figure grounding (memory never authoritative) | IMPLEMENTED, MOCK VERIFIED | `agents/nodes/verify_business_response.py`, `business/grounding.py` |
| Live ERP queries | NOT IMPLEMENTED (no verified schema). *Phase 22: IMPLEMENTED for InvenLogic, verified on the TEST replica; live ERP NOT CONTACTED; RULE_NOT_VERIFIED until the contract is VERIFIED* | `PostgresErpAdapter` → RULE_NOT_VERIFIED; `business/adapters/invenlogic.py` |

### A.15 Phase 11: business intelligence and query reasoning foundation (2026-10-03)

Details: [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md) § 10, ADR-060. Database-independent: built and verified against the fake ERP only. **Live ERP: NOT VERIFIED** (Q1). No new graph node, capability, permission, table or migration.

```
understand_request ── RequestUnderstanding.business_query (typed BusinessQueryIntent; untrusted)
   │                   + conversation focus (refs only) from the checkpoint
process_request ── BusinessDomainHandler: planned calls, or ONE call compiled from the intent
   │               (tool_call_for); READ only; bounded by AGENT_MAX_TOOL_CALLS_PER_TURN
   │   business.* tool ── BusinessReadService: period → BusinessDateRange (clock + zone),
   │                      filters (warehouse / product / invoice number / all customers),
   │                      derived figures only when their BusinessRule holds (else RULE_NOT_VERIFIED)
generate_response ── compose(results, intent) → BusinessResponseContext (figures, records,
   │                 date range, source, warnings, clarification) → LLM phrases it
verify_business_response ── reject the draft (figure forms, hedging, action claims, unavailable
                            asked-for figure) → render_context(); update business_focus
```

| Concern | Status | Where |
|---|---|---|
| Typed business query intent | IMPLEMENTED, UNIT + MOCK VERIFIED | `business/intent.py`, `agents/schemas.py` |
| Deterministic date ranges | IMPLEMENTED, UNIT VERIFIED (test time zone only; ERP zone unresolved) | `business/dates.py` |
| Business rules gate derived figures | IMPLEMENTED; fake declares TEST rules, postgres declares none | `business/rules.py`, `ErpReadPort.verified_rules` |
| Metrics (selection, never arithmetic in the LLM) | IMPLEMENTED, UNIT VERIFIED | `business/metrics.py` |
| Composition and deterministic rendering | IMPLEMENTED, UNIT + MOCK VERIFIED | `business/composition.py` |
| Conversation focus for follow-ups | IMPLEMENTED, MOCK VERIFIED (refs only, state v4) | `business/focus.py`, `AgentState.business_focus` |
| Grounding: word/short figure forms, hedges, action claims | IMPLEMENTED, UNIT + MOCK VERIFIED | `business/grounding.py`, `verify_business_response` |


### A.16 Phase 12: Supervisor and multi-domain orchestration foundation (2026-10-03)

Details: ADR-061, PROJECT_STATUS.md § 3k. Built and verified against the fake ERP only. **Real ERP: NOT VERIFIED** (Q1). No business write, no new capability, permission, table or migration.

```
START → understand_request ── LLM: RequestUnderstanding (domain, plan, business_query, routing hints)
          │                     untrusted; one structured call, as before
          ↓
        supervise ── Supervisor.decide() → SupervisorDecision (typed, JSON in state.orchestration)
          │            deterministic; sees only the registered domain names
          ├─ ROUTE / UNSUPPORTED ─► process_request (domain router: one run, AGENT_DOMAIN_TIMEOUT_SECONDS)
          │                           ├─ general  handler ── system/memory tools
          │                           └─ business handler ── business.* READ tools ── BusinessReadService
          │                         → [approval_gate] → generate_response → [verify_business_response]
          ├─ CLARIFY (planner) ───► generate_response (question already written)
          └─ fixed reply ─────────┐ (reserved / unknown domain, business not connected, read + send)
                                  ↓
        finalize_turn ── fixed reply | append "not done" notice | record domain_outcome, last_domain → END
        handle_error ── any error, including ORCHESTRATION_FAILED / _LIMIT_EXCEEDED, DOMAIN_TIMEOUT → END
```

| Concern | Status | Where |
|---|---|---|
| Supervisor (routing only; no data, tools, SQL, LLM or identity) | IMPLEMENTED, UNIT + MOCK VERIFIED | `agents/supervisor.py`, `agents/nodes/supervise.py` |
| Typed `SupervisorDecision` (no identity / permission / SQL / tool-argument field) | IMPLEMENTED, UNIT VERIFIED | `agents/supervisor.py` |
| Domain catalogue: implemented `general`, `business`; reserved `purchase`, `sales`, `inventory`, `accounting`, `gst`, `knowledge`, `notification` | IMPLEMENTED; registry refuses reserved keys | `agents/routing.py`, `agents/domains/base.py` |
| Planner routing hints (`RequestUnderstanding.routing`) | IMPLEMENTED; untrusted, closed | `agents/routing.py`, `agents/schemas.py` |
| Unsupported / multi-domain handling (fixed reply, partial route + notice, clarify on send) | IMPLEMENTED, MOCK VERIFIED | `supervisor.py`, `agents/nodes/finalize_turn.py` |
| Routing limits (1 decision, 1 domain run per turn; no edge back to `supervise`) | IMPLEMENTED, UNIT + MOCK VERIFIED | `supervise`, `process_request`, `agents/edges.py` |
| Common domain result contract (`outcome`, `next_action`, `errors`, `metadata`) | IMPLEMENTED | `agents/schemas.py`, `with_tool_outcome`, `business_outcome` |
| Follow-up / topic change (previous domain recorded, never decides) | IMPLEMENTED, MOCK VERIFIED | `RoutingMetadata`, `AgentState.last_domain` |
| Checkpoint-safe state (plain-JSON orchestration, state v5) and explicit checkpoint type allow-list | IMPLEMENTED, MOCK VERIFIED (strict-mode test) + VERIFIED LOCALLY (PostgreSQL 16 integration suite runs with it) | `agents/state.py`, `agents/serialization.py`, `agents/checkpointer.py` |
| Supervisor logs (`supervisor.decided`, `supervisor.turn.completed`: codes and counts only) | IMPLEMENTED, MOCK VERIFIED | `supervise`, `finalize_turn` |
| Separate LLM routing call, domain subgraphs, running two domains in one turn | NOT IMPLEMENTED (ADR-061 alternatives) | — |

Changes to existing nodes: `understand_request` appends the `supervisor.routing` prompt; `process_request` dispatches on the decision instead of the planner's label; `generate_response` passes the unavailable parts to the writer; turns that used to end after `generate_response` / `verify_business_response` now end through `finalize_turn`.

### A.17 Phase 13: observability, reliability and agent evaluation foundation (2026-10-05)

Details: ADR-062, PROJECT_STATUS.md § 3l, OPERATIONS_RUNBOOK.md § 18. Built and verified against the fake ERP only. **Real ERP: NOT VERIFIED** (Q1). No business write, no new capability, permission, table or migration. The topology of § A.16 is unchanged; Phase 13 instruments it.

```
request_id (API middleware / Agent.arun)          ── request.received, turn.rejected
  └─ turn_id (one graph execution: run or resume) ── turn.started → turn.completed | failed | interrupted
       ├─ planner span        (understand_request)  planner.started/completed/failed ─ llm.call.*
       ├─ supervisor span     (supervise)           supervisor.* + supervisor.decision
       ├─ domain span         (process_request)     domain.* ─ domain.run.* (strict deadline, timeout)
       │    └─ tool span      (ToolRegistry)        tool.* (strict deadline) ─ business.read ─ retry.*
       ├─ approval span       (approval_gate)       approval.*
       ├─ response span       (generate_response)   response.* ─ llm.call.*
       ├─ verification span   (verify_business_response) verification.* + verification.result
       ├─ finalization span   (finalize_turn)       finalization.*
       └─ error_handler span  (handle_error)        error_handler.*
  checkpoint.loaded / saved / load_failed / save_failed / rejected   (any saver, instance-instrumented)
  auth.succeeded / auth.failed (API) · authz.denied (Authorizer; audit row stays authoritative)
          │
          ▼  emit() → in-process sinks: JSON log line · MetricsRegistry · TraceBuffer (recent requests)
```

| Concern | Status | Where |
|---|---|---|
| Typed observability events (`ObsEvent`, closed `EventName`, `Stage`); `kind="observability"`, distinct from `AuditEvent` | IMPLEMENTED, UNIT VERIFIED | `observability/events.py` |
| Correlation: `request_id` → `turn_id` → `span_id`/`parent_span_id`, `thread_id`; contextvar, like `log_context` | IMPLEMENTED, MOCK VERIFIED | `observability/hub.py`, `agents/agent.py`, `agents/nodes/_common.py` |
| Redaction: allow-listed event metadata (tokens, numbers, flags only) + pattern scrubbing of every log line (bearer, API keys, JWT, passwords, URL credentials, tracebacks) | IMPLEMENTED, UNIT + MOCK VERIFIED | `core/redaction.py`, `core/logging.py` |
| Error taxonomy (16 categories) over existing codes; API contract unchanged | IMPLEMENTED, UNIT VERIFIED (every platform code mapped) | `observability/taxonomy.py` |
| Retry policy: ERP reads on `dependency_unavailable` only, shared deadline; LLM retries stay in the SDK; nothing else retried | IMPLEMENTED, UNIT + MOCK VERIFIED | `observability/retry.py`, `business/service.py` |
| Strict deadlines (a timed-out operation can never return success) for domain runs, tool calls, business reads | IMPLEMENTED, UNIT + MOCK VERIFIED | `observability/deadline.py` |
| Metrics: declared metrics, fixed label keys, bounded series, no id labels | IMPLEMENTED, UNIT VERIFIED; no exporter (Q3) | `observability/metrics.py` |
| LLM call events (stage, model, latency, tokens) without adding calls | IMPLEMENTED, MOCK VERIFIED | `observability/llm.py`, `agents/dependencies.py` |
| Checkpoint load/save events, classified failures, rejected-type events; allow-list unchanged | IMPLEMENTED, UNIT + MOCK VERIFIED | `observability/checkpoints.py` |
| Diagnostics: per-request tree from the trace buffer or JSON logs; `Agent.diagnose`, CLI `--trace`, `python -m agentic_platform.observability.diagnose` | IMPLEMENTED, UNIT + MOCK VERIFIED | `observability/diagnostics.py`, `observability/diagnose.py` |
| Evaluation dataset (36 scripted cases) + golden snapshots + failure injection + benchmark | IMPLEMENTED (test infrastructure) | `tests/evaluation/`, `tests/test_failure_injection.py`, `tests/perf/benchmark.py` |
| Metrics exporter, distributed tracing (OpenTelemetry), HTTP diagnostics endpoint | NOT IMPLEMENTED (ADR-062 alternatives) | — |

Changes to existing code: `instrumented_node` opens a stage span; `process_request` and the tool registry run under `strict_deadline` instead of `asyncio.timeout` / `asyncio.wait_for`; `BusinessReadService` retries an unreachable ERP within its one read deadline; tool-validation details keep error locations and types, not input values; `handle_error` records `ORCHESTRATION_FAILED` when reached without a recorded error; checkpoint savers raise `CHECKPOINT_UNREADABLE` / `CHECKPOINT_SERIALIZATION_FAILED` for non-platform failures.

### A.18 Phase 14: ERP integration readiness (2026-10-05) — real ERP BLOCKED at the input gate

Details: ADR-063, PROJECT_STATUS.md § 3m, OPERATIONS_RUNBOOK.md § 19, [docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md). **Q1 (the ERP access contract) has not been supplied, so no real ERP was contacted and no capability is mapped.** Phase 14 built what the input gate allows: the contract model, the security gate, tenant binding, capability activation, the readiness tool, production refusal and the contract test suite.

```
typed business tool → BusinessReadService (business semantics, unchanged)
   → ErpReadPort → PostgresErpAdapter
        ├─ capability readers: registered only for VERIFIED contract mappings (none exist)
        │     otherwise every method → UNAVAILABLE / RULE_NOT_VERIFIED, no connection
        └─ ReadOnlyErpDatabase
              verify(): identity (Phase 10) + effective privileges + [contract] version,
                        relations/columns/types, tenant & warehouse isolation  → fail closed
              fetch(query, params, tenant_id=<principal>): column → :tenant forced;
                        RLS → set_config(<contract setting>, tenant, true) per transaction
ERP_CONTRACT_PATH (JSON, no secrets) → erp_contract.load_contract → validate_contract
scripts/erp_readiness.py: offline Q1 gate → [--live] Phase 9 inspector → adapter gate
```

| Concern | Status | Where |
|---|---|---|
| Typed ERP contract (Q1 items A–T, 15-item capability mappings), validation (no SQL fragments, no credentials, data minimization, fixed permissions, confirmed rules, closed status list) | IMPLEMENTED, UNIT VERIFIED | `business/erp_contract.py` |
| Runtime security gate on effective privileges (writes, CREATE, ownership, elevated roles, CREATEDB/ROLE/REPLICATION) | IMPLEMENTED, UNIT + VERIFIED LOCALLY (throw-away PostgreSQL 16) | `business/erp_gate.py`, `adapters/postgres.py` |
| Contract vs catalog (version, relations, columns, types, tenant/warehouse isolation) | IMPLEMENTED, UNIT + VERIFIED LOCALLY | same |
| Tenant binding on every business read (column or RLS session setting) | IMPLEMENTED, UNIT + VERIFIED LOCALLY | `ReadOnlyErpDatabase.fetch` |
| Capability activation gated by VERIFIED mappings | IMPLEMENTED, UNIT VERIFIED | `PostgresErpAdapter.register_reader` |
| Readiness tool (input gate, live inspection + gate, no business rows) | IMPLEMENTED, UNIT + VERIFIED LOCALLY | `scripts/erp_readiness.py` |
| Production refuses `ERP_ADAPTER=postgres` without a VERIFIED, matching contract | IMPLEMENTED, UNIT VERIFIED | `core/production.py` |
| Contract test suite (18 structured checks) | PASSES against the fake ERP; real ERP skipped (Q1). *Phase 22: 18/18 on the InvenLogic TEST replica; the fake suite was deleted* | `tests/erp_contract/` |
| Real ERP schema mapping, capability readers, live query plans | **BLOCKED (Q1)**. *Phase 22: mapping (DRAFT) and readers done for InvenLogic; live checks and query plans BLOCKED on Q1 A–G, T* | `docs/erp/invenlogic_contract.json`, `business/adapters/invenlogic.py` |

**Phase 22 update (2026-10-30, ADR-067).** The diagram above now has readers: `register_invenlogic_readers` (reader set `invenlogic_v1`) registers one per VERIFIED mapping. With a contract that has `databases`, `PostgresErpAdapter` holds one `ReadOnlyErpDatabase` per key (`master_data`, `sales`, `inventory`; URLs from `ERP_DATABASE_URLS`), and `verify()` runs per database, also checking that the RLS policies call `TenantIsolation.policy_function` and that the function reads the contract's setting. `ERP_ADAPTER` is `none` or `postgres`; the fake adapter is gone. `scripts/erp_readiness.py` takes `ERP_INSPECT_DATABASE_URLS`. Verified on the TEST replica only: `--live` READY_FOR_ACTIVATION for all three databases. Go-live steps: [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6.

### A.19 Phase 15: production hardening and operational readiness (implemented 2026-10-05; documented 2026-10-10 in Phase 17)

Details: ADR-064, PROJECT_STATUS.md § 3o, OPERATIONS_RUNBOOK.md § 20. The Phase 16 audit (2026-10-10) found this code and its tests without documentation. Phase 17 wrote this section from the code and tests; no code changed.

```
SIGTERM → uvicorn: stop accepting; in-flight requests finish (API_GRACEFUL_SHUTDOWN_SECONDS)
        → uvicorn cancels the rest (a cancelled turn records failure, releases its lock)
        → app shutdown: ApiRuntime.aclose() = run_shutdown(steps, API_SHUTDOWN_CLEANUP_SECONDS)
             in_flight_requests (≤ ½ budget) → maintenance (in-process only) → redis
             → database → erp (if configured) → observability flush (daemon thread)
             each step ≥ 0.5 s; overrun = abandoned + `timeout`; error = recorded; next runs
        → `api.stopped {shutdown: ok|degraded}` → exit with the signal's status (143)

request → RequestContextMiddleware (in_flight +1, default headers) → auth → rate_limit
        → turn_slot (TurnSlots: ≤ API_MAX_CONCURRENT_TURNS, else 503 SERVER_BUSY) → turn
```

| Concern | Status | Where |
|---|---|---|
| Bounded, ordered shutdown; hung steps abandoned; never raises | IMPLEMENTED, UNIT VERIFIED | `services/lifecycle.py`, `api/runtime.py`, `agents/dependencies.closers` |
| Same sequence for the maintenance worker | IMPLEMENTED; runs in the worker entry-point test, but its steps are not asserted; the POSIX SIGTERM worker test is skipped on Windows | `maintenance/__main__.py` |
| Cancelled turn recorded as failed, never replayed; no pooled connection leaks; pools reconnect after shutdown | VERIFIED LOCALLY (PostgreSQL 16 + Redis 7, app role) | `tests/integration/test_lifecycle_postgres.py` |
| Per-process concurrent-turn cap → 503 `SERVER_BUSY` (retryable) on the six turn routes | IMPLEMENTED, UNIT VERIFIED | `api/dependencies.turn_slot`, routes `agent`, `conversations`, `approvals` |
| Build information (`--version`, `api.started`, `/ready`), shape-validated, no git at run time | IMPLEMENTED, UNIT VERIFIED | `core/build.py`, `Dockerfile` build args |
| ERP readiness component: never gates, never connects; gate state from memory | IMPLEMENTED, UNIT + VERIFIED LOCALLY (`not_configured`) | `services/readiness.erp_check`, `ErpReadPort.gate_state` / `aclose` |
| Default `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`; gateway hides the duplicates | IMPLEMENTED, UNIT VERIFIED (API); gateway config not run in Phase 15 | `api/middleware.py`, `deploy/gateway/snippets/proxy.conf` |
| Production policy: DEBUG refused, statement timeout 0 refused, unbounded turns warned | IMPLEMENTED, UNIT VERIFIED | `core/production.py` |
| Exporter boundary (none shipped; unknown exporter refused) | IMPLEMENTED, UNIT VERIFIED | `observability/hub.Exporter`, `OBSERVABILITY_EXPORTER` |
| Pinned runtime dependencies, digest-pinned base image, no installers in the image | IMPLEMENTED, STATIC CHECK ONLY (files inspected by tests; no image build recorded) | `constraints.txt`, `Dockerfile` |
| Agent DB backup / restore-into-new / verify scripts | IMPLEMENTED, **NOT RUN** (static checks of the scripts only) | `deploy/backup/` |
| Deployment smoke test (12 check groups, isolated compose project) | WRITTEN, **NOT RUN** (no `deploy/smoke/results/`) | `deploy/smoke/` |

**Startup.** The server listens only after the application's startup has finished: configuration validated, production policy enforced, runtime built. So the first successful `/health` means "started", and there is no separate startup probe. At startup the app logs `api.started` (environment and build information) and one `api.startup.dependencies` line with the readiness report. Neither blocks or fails the start; `/ready` keeps gating traffic.

### A.20 Phase 16: historical sales comparison and long-term business memory (2026-10-10)

Details: ADR-065, PROJECT_STATUS.md § 3n, [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md) § 11. **Fake ERP only (MOCK VERIFIED); customer purchase history and product-level sales are NOT implemented** (no port method, record shape or rule exists for them).

```
"Is mahine ki sales pichhle saal isi month se compare karo"
  planner → business_query{capability: daily_sales, period: this_month,
                           comparison: same_period_last_year}   (untrusted, typed)
  → tool_call_for → business.daily_sales {period, compare_with}  (closed tokens only)
  → BusinessReadService.daily_sales
       rules: business_timezone · sales_period_is_sum_of_days · same_period_last_year_by_date
              [+ week_starts_monday | year_is_calendar_year]      else RULE_NOT_VERIFIED
       span     = resolve_period(this_month, today)          2026-10-01 .. 2026-10-10
       baseline = same_period_last_year(span)                2025-10-01 .. 2025-10-10
       sales_by_day(span), sales_by_day(baseline)   (same port, deadline, tenant, validation)
       SalesComparisonData: totals, documents, change, change % (Decimal, half-up 0.1),
                            gaps (no records / zero baseline / mixed currency)
  → compose: figures + gaps + two sales_period records + basis notice
  → generate_response → verify_business_response (figures, percentages, hedges,
       action claims, profit claims; every business-domain turn)
  → episode.analyses = [{capability, status, period, compared_with, source}]  (no figures)
```

| Concern | Status | Where |
|---|---|---|
| Year periods (`this_year` to date, `last_year`), same-period-last-year range | IMPLEMENTED, UNIT VERIFIED | `business/dates.py` |
| Comparison computation, missing/zero baseline, currencies | IMPLEMENTED, MOCK VERIFIED | `business/service.py` (`_sales_comparison`, `_compare`) |
| Rules `year_is_calendar_year`, `same_period_last_year_by_date` | TEST rules in the fake; **NOT VERIFIED** for the ERP. *Phase 22: new `year_is_financial_year_april`; exactly one year rule must hold; the InvenLogic contract (DRAFT) confirms the financial year and `same_period_last_year_by_date`* | `business/rules.py`, `business/dates.py` |
| Tool contract (`compare_with`, closed token) | IMPLEMENTED | `tools/business.py` |
| Comparison metrics and rendering | IMPLEMENTED | `business/metrics.py`, `business/composition.py` |
| Percentage grounding, profit guard, verification without results | IMPLEMENTED | `business/grounding.py`, `agents/edges.py`, `agents/nodes/verify_business_response.py` |
| Analysis references in episodes; `basis` / `recorded_at` on recall | IMPLEMENTED, MOCK + VERIFIED LOCALLY (PostgreSQL 16) | `business/focus.py`, `memory/models.py`, `memory/episodic.py`, `agents/agent.py` |
| Customer purchase history, product-level sales comparison | **BLOCKED (Q1)**: routed to `unsupported` | planner prompt v5 |
| Agent DB schema | unchanged (head `0005`) | — |

---

## Part B: Target architecture (proposed)

### B.1 Layered request flow

```
User
 ↓
AI Assistant (chat UI / API client)
 ↓
API Gateway layer (FastAPI): authN, request ID, rate limit   (Phase 6: implemented except rate limits, § A.10)
 ↓
LangGraph Supervisor graph: intent routing, HITL, checkpointing
 ↓
Domain Agents (subgraphs): Purchase · Sales · Inventory · Accounting · GST · ...
 ↓
Tools: typed, registered, permission-checked, audited
 ↓
Business Services: business rules, validation, state transitions
 ↓
Repositories: data access only, no business logic
 ↓
PostgreSQL (source of truth) · Redis (ephemeral) · Vector store (semantic)
```

**Hard security rule (non-negotiable):** the LLM never receives a database connection, raw SQL, or an "execute query" tool. It can act only by calling registered tools. Tools call business services, services call repositories, and repositories talk to the database. See ADR-004.

The same business services also back the normal (non-AI) REST API, so the AI path and the UI/API path enforce **identical** business rules.

### B.2 Proposed repository layout

```
/
├── apps/
│   ├── api/                    # FastAPI app: HTTP routes, auth middleware, DI wiring
│   └── web/                    # Frontend (technology TBD; out of Phase 1 scope)
├── src/platform/               # Python package (name TBD)
│   ├── core/                   # config, logging, errors, ids, clock, tenancy context
│   ├── security/               # authN, RBAC, permission checks, principal model
│   ├── audit/                  # audit log writer (append-only)
│   ├── db/                     # SQLAlchemy engine/session, Unit of Work, base models
│   ├── cache/                  # Redis client, locks, idempotency store
│   ├── domains/
│   │   └── <domain>/           # purchase, sales, inventory, accounting, gst, ...
│   │       ├── models.py       # ORM models
│   │       ├── schemas.py      # Pydantic DTOs
│   │       ├── repository.py   # data access
│   │       ├── service.py      # business rules
│   │       └── api.py          # REST routes (non-AI path)
│   ├── agents/
│   │   ├── supervisor/         # supervisor graph, router
│   │   ├── domains/<domain>/   # domain agent subgraph + prompts
│   │   ├── state.py            # shared graph state schema
│   │   └── runtime.py          # graph compilation, checkpointer, store wiring
│   ├── tools/
│   │   ├── registry.py         # tool registry
│   │   ├── base.py             # tool contract (permission, idempotency, audit)
│   │   └── <domain>/           # domain tools (thin adapters over services)
│   ├── memory/                 # memory interfaces + adapters
│   └── observability/          # tracing, metrics, LLM telemetry
├── migrations/                 # Alembic
├── tests/                      # unit / integration / graph / eval
├── docker-compose.yml          # postgres(+pgvector), redis
└── docs/
```

Domains are **vertical slices**. Adding a new module (for example Reports) means adding one `domains/<x>` package, one tools package and one agent subgraph, plus a registry entry. No core changes should be needed.

### B.3 LangGraph design

#### B.3.1 Graph topology

```
            ┌──────────────┐
  START ──► │ load_context │  (principal, tenant, conversation memory)
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │  supervisor  │  (LLM router → structured RouteDecision)
            └──────┬───────┘
     conditional edge on RouteDecision.target
   ┌──────────┬──────────┬───────────┬──────────┬────────────┬───────────┐
   ▼          ▼          ▼           ▼          ▼            ▼           ▼
purchase   sales    inventory  accounting     gst        clarify      respond
 agent     agent      agent       agent      agent      (ask user)   (final)
(subgraph)  ...        ...         ...        ...           │            │
   └──────────┴──── back to supervisor ───────┘             ▼            ▼
                                                            END          END
```

* **Supervisor** is a hand-built node that returns a structured `RouteDecision` (target agent, reason, confidence). It does **not** call business tools itself.
* **Domain agents** are compiled **subgraphs**. Each has its own tool subset and prompt, and gets only the state slice it needs.
* **Bounded loops:** a `max_steps` / recursion limit plus a per-turn tool-call budget prevent runaway agent loops.
* **Low confidence or ambiguity** routes to `clarify` and never to a guess.

#### B.3.2 State

A single typed state schema (a `TypedDict` or a Pydantic model), versioned so that checkpoints stay readable after deployments.

| Field | Purpose | Reducer |
|---|---|---|
| `messages` | Conversation turns | append (`add_messages`) |
| `principal` | user id, tenant id, roles (from auth, **never from the LLM**) | overwrite, read-only to the LLM |
| `route` | latest `RouteDecision` | overwrite |
| `active_agent` | which domain agent is working | overwrite |
| `working_memory` | scratch facts for the current task (entities in focus, extracted fields) | merge |
| `pending_action` | a proposed write operation awaiting approval | overwrite |
| `tool_results` | structured results of tool calls this turn | append |
| `errors` | structured errors for recovery/reporting | append |
| `step_count` | loop guard | increment |
| `state_version` | schema version for checkpoint migration | constant |

Rules: state holds **references and summaries**, not full business records, because the database remains authoritative. The LLM never writes `principal`.

#### B.3.3 Nodes, edges and conditional edges

* **Nodes:** `load_context`, `supervisor`, `<domain>_agent` (subgraph), `tool_executor`, `approval_gate` (HITL), `clarify`, `respond`, `handle_error`.
* **Static edges:** `START → load_context → supervisor`, `respond → END`.
* **Conditional edges:**
  * `supervisor → {domain agent | clarify | respond}` on `route.target`
  * `agent → {tool_executor | approval_gate | supervisor | respond}` on whether the model requested a tool and whether that tool is a write
  * `tool_executor → {agent | handle_error}` on the result status
  * `handle_error → {agent (retry) | respond (give up gracefully)}` on error class and retry budget

#### B.3.4 Tools and the tool registry

Every tool is a thin, typed adapter over a business-service method. A tool definition declares:

| Attribute | Meaning |
|---|---|
| `name`, `description` | Shown to the LLM |
| `input_schema` / `output_schema` | Pydantic models with strict validation |
| `domain` | purchase / sales / ... |
| `kind` | `read` or `write` |
| `required_permission` | for example `purchase.po.create` (checked against the principal **before** execution) |
| `requires_approval` | write tools default to `true` (HITL) |
| `idempotent` | whether replay is safe; write tools require an idempotency key |
| `timeout`, `retry_policy` | execution limits |

The **ToolRegistry**:
* registers tools per domain and exposes `tools_for(agent, principal)`. That way an agent only *sees* the tools its user is allowed to use, which is defence in depth on top of the execution-time check
* wraps every call with: permission check → input validation → idempotency check → execute → audit log → output validation → metrics
* is the **only** path from the LLM to business logic

#### B.3.5 Human-in-the-loop (HITL)

* Write or irreversible tool calls create a `pending_action` and hit the `approval_gate` node, which uses LangGraph `interrupt()`. The graph pauses, and its state is persisted by the checkpointer.
* The UI shows the proposed action (a human-readable diff or summary). The user approves, edits or rejects it. The graph resumes with `Command(resume=...)`.
* The approver's identity and decision are written to the audit log.
* Which actions need approval, and from which roles, is **TBD (needs business documentation)**. The default is that every write needs approval.
* *Phase 5: the approval gate, its persistence, audit and safe resume are implemented (§ A.9). Approve/reject is implemented; "edit" is not.*

#### B.3.6 Checkpointing

* **PostgreSQL checkpointer** (`langgraph-checkpoint-postgres`) persists graph state per `thread_id` (one thread per conversation/task).
* Enables: resume after interrupt (HITL), crash recovery, multi-turn continuity and time-travel debugging.
* `thread_id` is namespaced by tenant and user, and is checked against the principal on every resume. *(Phase 5: namespaced by tenant; the user is checked through the ownership record, § A.9.)*
* Checkpoint retention/pruning policy: TBD.
* In-memory checkpointer only in unit tests.

### B.4 Memory architecture (original design; implemented in Phase 4, see § A.8 for what exists)

| Memory type | What it holds | Lifetime | Planned backing store | Planned interface |
|---|---|---|---|---|
| **Short-term** | Recent messages in the current turn/window | Current thread | Graph state (`messages`), trimmed/summarised | `ShortTermMemory.window(thread_id, max_tokens)` |
| **Working** | Task scratchpad: entities in focus, partially collected fields | Current task | Graph state (`working_memory`) and the checkpointer | `WorkingMemory.get/set/clear(thread_id, key)` |
| **Conversation** | Full history plus rolling summaries of a thread | Thread lifetime | PostgreSQL (checkpointer + summary table) | `ConversationMemory.history/summary(thread_id)` |
| **Long-term** | Durable user/tenant preferences and facts | Indefinite, with deletion | PostgreSQL (LangGraph `BaseStore` / `PostgresStore`) | `LongTermMemory.put/get/search(namespace, key)` |
| **Semantic** | Knowledge: SOPs, policy docs, product/master-data descriptions | Indefinite, versioned | Vector store (pgvector) | `SemanticMemory.search(query, filters, k)` / `ingest(doc)` |
| **Episodic** | Records of past agent runs and outcomes ("last time X, we did Y") | Retention TBD | PostgreSQL + embeddings in pgvector | `EpisodicMemory.record(episode)` / `recall(query)` |
| **Procedural** | How-to knowledge: prompts, tool-usage guidance, workflow playbooks | Versioned with code/config | Versioned files + PostgreSQL | `ProceduralMemory.get(agent, version)` |
| **Graph/checkpoint state** | Exact resumable execution state | Thread lifetime + retention | PostgreSQL checkpointer | LangGraph `BaseCheckpointSaver` |

Memory rules:
* All memory is **namespaced by tenant (and user where relevant)**. Retrieval must never cross tenants.
* Memory is **context, not truth**. Before any decision about a business document (PO, GRN, invoice, payment), the agent reads it fresh through a tool or service. It never relies on remembered values.
* PII and retention handling must be defined before long-term or episodic memory goes live.

### B.5 Data architecture

| Store | Role | Must NOT be used for |
|---|---|---|
| **PostgreSQL** | **Source of truth** for all business transactions and master data, plus audit logs, LangGraph checkpoints, long-term memory store and idempotency records of record | — |
| **Redis** | Cache, rate limits, distributed locks, short-TTL idempotency fast-path, ephemeral session/fast-access state, optional task queue | The only copy of any business data. Anything that cannot be rebuilt if Redis is flushed. |
| **Vector store** (proposed: **pgvector** inside PostgreSQL) | Embeddings for semantic retrieval: documents, knowledge, episodic recall | Transactions, balances, statuses, or any value used to make a business decision |

Principles:
* Business writes go through a **Unit of Work** (one DB transaction per service command).
* Every business table carries `tenant_id`, `created_at/by` and `updated_at/by`. Optimistic locking (`version` column) is used where documents can be edited concurrently.
* Schema changes go only through **Alembic** migrations.
* Vector rows carry `tenant_id` plus a pointer back to the source record or document, so results can be re-validated against PostgreSQL.

### B.6 Security

#### Authentication
* Proposed: OIDC/OAuth2 with JWT access tokens, validated at the API layer. The identity provider is **TBD** (company SSO? Keycloak? Auth0?).
* *Phase 6: the API layer validates bearer tokens through the identity-provider boundary; an OIDC/JWT adapter exists but the provider is still TBD (§ A.10).*
* Service-to-service calls use separate machine credentials.

#### Authorization and RBAC
* Permission strings follow the form `<domain>.<resource>.<action>`, for example `purchase.invoice.read`.
* Roles map to permission sets. The actual role catalogue is **TBD (needs business documentation)**.
* Enforcement happens in **three** places: tool visibility (registry), tool execution (registry wrapper), and the business service (final authority). The service layer check exists so that the REST path and the AI path cannot diverge.
* The agent always acts **as the user** (the user's principal and permissions), never as a super-user.
* *Phase 5: implemented with platform permissions and provisional roles (§ A.9). The real role catalogue and the identity provider are still open (Q6, Q8).*

#### LLM-specific controls
* No raw SQL tool, no DB credentials in the agent process environment beyond what the services use.
* Tool inputs are validated strictly. Unknown fields are rejected.
* Prompt-injection posture: text from tools or documents is treated as data. Writes need HITL approval.
* Secrets are kept out of prompts, logs and traces (redaction).

#### Audit logs
* Append-only `audit_log` table recording: timestamp, tenant, actor (user / agent+user / system), action, tool name, input hash/summary, result status, affected entity ids, approval decision and approver, `trace_id`, `thread_id`.
* Business documents additionally keep their own status history.

### B.7 Reliability

| Concern | Design |
|---|---|
| **Error handling** | Typed error hierarchy: `ValidationError`, `PermissionDenied`, `NotFound`, `Conflict`, `BusinessRuleViolation`, `TransientError`, `ExternalServiceError`. Tools return structured errors to the agent (the LLM can explain or recover). Internals and stack traces never reach the LLM or the user. |
| **Retry** | Only for `TransientError` (DB/Redis/LLM-provider timeouts, 429/5xx). Exponential backoff with jitter and capped attempts (LangGraph node `RetryPolicy` and tenacity in clients). **Never** auto-retry a non-idempotent write without an idempotency key. |
| **Idempotency** | Every write tool or command takes an `idempotency_key` (derived from thread_id + step + action hash, or client-supplied). Results are recorded in PostgreSQL (durable) with a Redis fast-path. A replay returns the original result. This protects against double-execution on graph resume, retries and user double-clicks. |
| **Concurrency** | Redis distributed locks for short critical sections. PostgreSQL row locks / optimistic `version` checks for document state transitions. |
| **Timeouts/limits** | Per-tool timeout, per-turn tool-call budget, graph recursion limit, LLM token budget. |

### B.8 Observability

* **Structured JSON logs** with `trace_id`, `tenant_id`, `user_id`, `thread_id`, `agent`, `tool`.
* **Tracing:** OpenTelemetry across API → graph → tool → service → DB. LLM-level tracing through LangSmith or an OpenTelemetry-compatible alternative (choice **TBD**: data residency or confidentiality may rule out SaaS).
* **Metrics:** request latency, tool success/error rates, retries, HITL approval/rejection rates, token usage and cost per tenant/agent.
* **Evaluation:** an offline eval set per agent (routing accuracy, tool-selection accuracy) run in CI once agents exist.

### B.9 Documented business workflow (reference only, not designed yet)

The only business workflow documented so far:

```
Purchase Order → GRN → Purchase Invoice → Unpaid → Settlement → Paid
```

Everything beyond that sequence is **TBD (needs business documentation)**. That includes field lists, validation rules, partial receipts, quantity/price matching between PO, GRN and invoice, partial payments, tax (GST) treatment, approval rules and cancellation/reversal. See the open questions in [PROJECT_STATUS.md](PROJECT_STATUS.md). The design above deliberately keeps these rules in the **business service layer**, so that when they are documented they are implemented once and enforced for both human and AI callers.

### B.10 Proposed technology stack (pending confirmation)

| Concern | Proposal | Status |
|---|---|---|
| Language | Python 3.11+ | Proposed (3.11.9 already installed) |
| Agent orchestration | LangGraph | **Decided by stakeholder** |
| API | FastAPI + Pydantic v2 | **In use** (Phase 6: FastAPI 0.142, uvicorn; § A.10) |
| ORM / migrations | SQLAlchemy 2.x + Alembic | **In use** (Phase 2) |
| Database | PostgreSQL 16+ with pgvector | **In use** (pgvector 0.8 since Phase 4; image `pgvector/pgvector:pg16-trixie`) |
| Cache / locks | Redis 7+ (redis-py, pure Python) | **In use** (Phase 3: cache, temporary state, locks, idempotency fast-path) |
| LLM provider | **TBD** | Open question |
| Frontend | **TBD** | Open question |
| Packaging | uv (or poetry) | Proposed |
| Local infra | Docker Compose | Proposed (Docker installed) |
| Testing | pytest, testcontainers (Postgres/Redis) | Proposed |
