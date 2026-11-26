# Operations Runbook

> **For:** operators and on-call engineers running the agentic platform.
> **Scope:** the platform only (API, maintenance worker, migration job, gateway). There are no business agents.
> **Last updated:** 2026-10-30 (Phase 22: InvenLogic is the real ERP and the fake ERP is deleted; § 7 Ollama memory note, § 15, § 16 and § 19 updated for the multi-database InvenLogic ERP. Before that, 2026-10-10, Phase 17: § 20 and the § 12/§ 14 updates document Phase 15). Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) § A.11–A.19. Business layer: [docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md). Go-live list: [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md).

The deployment target is not chosen yet (PROJECT_STATUS Q3). Commands below are given for the reference Docker Compose stack. Each runbook says what the step must achieve, so it can be translated to the chosen platform (Kubernetes, a container service, VMs).

## Safety rules (always)

1. **Never delete a PostgreSQL volume or database** to "fix" anything. Never run `docker compose down -v`. Never drop or recreate the Agent DB.
2. **Never point the migration job at another database** (for example the ERP / system-of-record database). The job refuses a non-empty database without `alembic_version` (ADR-056). Never set `AGENTIC_MIGRATE_NON_EMPTY_DATABASE=1` to get past that error. Recheck the URL instead.
3. **Never paste secrets into tickets, chat or logs.** The application never prints them; `--check-config` reports setting names only.
4. **Never disable authentication, RLS or audit triggers** to restore service. Development authentication cannot run in production (the process refuses to start).
5. Redis may be restarted or flushed **only** with the procedure in § 6; it holds no durable data, but flushing it drops in-flight locks.
6. **The ERP database is read only for this platform.** Never run Alembic, `create_all`, seed scripts, or any INSERT, UPDATE, DELETE or DDL against it, not even "just to test". Connect to it only with the ERP read-only role: `scripts/inspect_erp_security.py` (§ 15) and the business read layer (`ERP_ADAPTER=postgres`, § 16), which runs every statement in a read-only transaction. Never set `ERP_ADAPTER=fake` outside development: it serves TEST data (production refuses it).

## Components and signals

| Component | Command | Health signal | Logs (JSON, stderr) |
|---|---|---|---|
| gateway | nginx (`deploy/gateway`) | `GET /gateway-health` on port 8080 | `gateway.request` lines (no query strings, no headers) |
| api (N replicas) | `python -m agentic_platform.api` | liveness `GET /health`; readiness `GET /ready` (port 8000) | `http.request.finished`, `auth.*`, `agent.turn.*`, `llm.call.*`, `ratelimit.*`, `supervisor.*` (§ 17) |
| worker | `python -m agentic_platform.maintenance` (one-shot) or `--loop` | exit code; `maintenance.run` audit rows | `maintenance.job.*` |
| migrate | `alembic upgrade head` | exit code | Alembic INFO lines |

Correlation: the gateway logs `request_id` and `api_request_id`; the API logs its own `request_id` and the gateway's as `client_request_id`. Grep either id to join both logs. Every API response carries `X-Request-ID`.

---

## 1. API deployment

**Goal:** new API version serving, with readiness gating traffic.

1. Build and tag the image (`docker build -t agentic-platform:<tag> .`).
2. Gate the configuration of the **target environment** (must exit 0; warnings are listed):
   ```sh
   docker run --rm --env-file <rendered env> -v <secrets dir>:/run/secrets:ro \
       agentic-platform:<tag> python -m agentic_platform.api --check-config
   ```
3. Run the migration job first (§ 3) if the release contains migrations; wait for exit 0.
4. Roll out replicas. Each must report `/ready` 200 before it receives traffic. Readiness probe timeout: 5 s (above `DATABASE_HEALTH_TIMEOUT_SECONDS`=3).
5. Smoke test through the gateway: `/health` 200; `/api/v1/me` with a real token returns the expected tenant and roles; one harmless turn ("Hello") returns 200.
6. Watch for 5 minutes: 5xx rate, `llm.call.failed`, `auth.failed`, `ratelimit.exceeded`.

Requirements: `API_FORWARDED_ALLOW_IPS` lists the gateway's addresses; `RATE_LIMIT_ENABLED=true`; `AUTH_PROVIDER=oidc`; `REDIS_URL` set (several replicas share thread locks).

## 2. Maintenance deployment

**Goal:** expiry jobs run on schedule with the same image.

* Preferred: a scheduled one-shot job, `python -m agentic_platform.maintenance`, hourly (`MAINTENANCE_INTERVAL_SECONDS` equivalent). Give it a **deadline** (for example 30 minutes) in the scheduler: a hung database can hold a job (ADR-055).
* Alternative: one long-running `--loop` worker with a stop grace period of 60 s.
* Gate it like the API: `python -m agentic_platform.maintenance --check-config` (the worker needs no LLM key and no identity provider).
* Overlapping or duplicate runs are safe: per-job Redis lock (busy → `skipped`) and `FOR UPDATE SKIP LOCKED`. Verified in Phase 8: a held lock gives `skipped` for that job only.
* The API never needs the worker to serve; expiry is also enforced at read time.

## 3. Migration deployment

**Goal:** the Agent DB at the new schema head, before new API replicas start.

1. Confirm the target: `DATABASE_MIGRATION_URL` names the **Agent DB** with the schema-owner role. Never the ERP database.
2. Optional DBA review: `alembic upgrade head --sql` prints the SQL without connecting.
3. Run the one-shot job (`alembic upgrade head`) with the owner URL. Only this job receives the owner URL.
4. Concurrent jobs are serialized by an advisory lock; a second job waits and then does nothing.
5. Exit 0 → continue the deployment. Non-zero → stop the rollout; the old API keeps serving (its `/ready` reports `degraded` only if the schema is now ahead of it, which additive migrations avoid).
6. If the job prints **`Refusing to migrate: the target database has tables but no alembic_version table`**, the URL points at a database this application does not own. Stop and correct the URL. Do not override.

The API never migrates on start. Readiness stays 503 while the schema is behind.

## 4. Rollback

* **Application:** redeploy the previous image tag (same configuration). Migrations are kept backward compatible for one release (additive first, removals later), so the previous version runs on the new schema.
* **Schema:** only by an operator, only for reversible migrations, after checking the data impact: `alembic downgrade -1` with the owner URL. Take a backup or PITR marker first.
* **Configuration:** revert the changed variables or secrets and restart the affected processes (settings are read at start).
* **Never** roll back by restoring an old volume over the live database, and never by recreating it.

## 5. PostgreSQL failure

**Symptoms:** `/ready` 503 with `database: unavailable` (`DATABASE_UNAVAILABLE` or `DATABASE_TIMEOUT`); requests 503 `DATABASE_UNAVAILABLE` with `Retry-After: 2`; gateway 504 `GATEWAY_TIMEOUT` for requests that were already waiting on a hung connection.

Behaviour verified in Phase 8 (frozen server): readiness answered 503 in 3.0 s; token-only `/me` kept working; new database work failed in about 5 s; everything recovered **without restarting** the API once the database answered again.

1. Check the database service (managed console, connectivity from the API network, `pg_isready`).
2. Check connections: `SELECT count(*), state FROM pg_stat_activity WHERE datname = '<agent db>' GROUP BY state;` versus `replicas × (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)` + workers.
3. **Pool exhaustion** (503 `DATABASE_UNAVAILABLE` with "pool timeout" in the API log, database healthy): scale API replicas, or raise `DATABASE_POOL_SIZE` within the server's `max_connections`; look for slow queries (`statement_timeout` 15 s cancels them).
4. Failover or maintenance: wait; the API reconnects by itself (`pool_pre_ping`). Restart API replicas only if they stay not-ready for minutes after the database is healthy.
5. Never point the API at another database to "get back up".

## 6. Redis failure

**Symptoms:** `/ready` 503 with `redis: unavailable` (`CACHE_UNAVAILABLE` / `CACHE_TIMEOUT`); agent turns 503 `CACHE_TIMEOUT` / `CACHE_UNAVAILABLE` (thread locks fail closed); reads keep working (rate limits fail open, about +2 s per request while Redis times out); `ratelimit.unavailable` warnings.

1. Check the Redis service (memory, `maxmemory-policy noeviction`, connectivity, credentials).
2. **OOM** (`CACHE_UNAVAILABLE ... out of memory`): raise memory; do not switch to an evicting policy (it would evict locks).
3. Redis holds no durable data. A restart loses in-flight locks and fast-path entries only; PostgreSQL remains the source of truth. If a restart is needed, restart it; do not flush a shared instance (other environments use other `REDIS_KEY_PREFIX`es).
4. Recovery needs no API restart (verified).
5. If the Redis pool is exhausted under load ("Too many connections"), raise `REDIS_MAX_CONNECTIONS` (default 20 per process; Phase 8 load reached 20 at 40 concurrent requests per replica) or add replicas.

## 7. LLM provider failure

**Symptoms:** turns 503 `LLM_UNAVAILABLE` + `Retry-After` (rate limit, overload, timeout, network) or 502 `LLM_AUTH_ERROR` / `LLM_ERROR` / `LLM_OUTPUT_ERROR`; `llm.call.failed` logs with status and provider request id; `/ready` stays 200 (LLM is DEGRADED, not DOWN).

1. `LLM_AUTH_ERROR`: the credential is invalid or revoked. Rotate it (§ 10). Do not retry in a loop.
2. `LLM_UNAVAILABLE` with HTTP 429 in `llm.call.failed`: account rate limit. Lower `RATE_LIMIT_TURNS_PER_WINDOW` temporarily or raise the provider limit.
3. 5xx / 529 / timeouts: check the provider status page; the SDK already retried `LLM_MAX_RETRIES` times. Approvals, memory, history and audit keep working.
4. With an egress gateway (`LLM_ANTHROPIC_BASE_URL`), check it first.
5. Never log prompts or responses to debug; use the provider request id from `llm.call.failed`.
6. **Local Ollama (`LLM_PROVIDER=ollama`): the model does not load.** The context size `LLM_OLLAMA_NUM_CTX` sets the KV cache the server must allocate, on top of the model itself, and it must fit in free RAM. On the development machine (15.4 GB RAM, about 5.5 GB free, CPU only) `qwen3:8b` failed to load with 16384 (the `.env` value) and with 8192: llama-server ran out of memory allocating the KV cache (2.4 GB / 1.2 GB). 4096 loaded. Either free RAM (stop Docker stacks you do not need) or set a smaller `LLM_OLLAMA_NUM_CTX`, and check that the prompt still fits. On that machine one turn takes about 5 minutes; keep `LLM_TIMEOUT_SECONDS` above that (Phase 22, PROJECT_STATUS § 3t.5).

## 8. Identity provider failure

**Symptoms:** 503 `IDENTITY_PROVIDER_UNAVAILABLE` for valid tokens (signing keys cannot be fetched); `auth.provider_unavailable` logs. Replicas with cached keys keep authenticating until `AUTH_OIDC_JWKS_CACHE_SECONDS` expires (verified: warm cache 200, cold cache 503, forged tokens 401 in both cases).

1. Check the provider's discovery and JWKS URLs from the API network.
2. **Do not restart API replicas during an IdP outage**: a restart empties the key cache and turns a partial outage into a full one.
3. Never switch `AUTH_PROVIDER` or add development tokens to get around it (production refuses to start with them anyway).
4. Key rotation at the provider: new `kid`s are fetched automatically (at most one refresh per 30 s); old-key tokens stay valid while the old key is published.
5. Revocation: a disabled user's existing token stays valid until it expires (≤ `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS` + leeway; ADR-048).

## 9. Certificate renewal

* The gateway reads `/etc/nginx/certs/tls.crt` and `tls.key` (mounted from the platform's certificate store or secret manager; never from the repository).
* Renew before expiry (alert at 30 and 7 days). Replace both files atomically, then reload without downtime: `nginx -s reload` in the gateway container (or roll the gateway).
* Check: `openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 -noout -dates -subject`; TLS 1.0/1.1 must be refused.
* Development certificates come from `deploy/gateway/dev-certs.sh` and are valid for 30 days on `localhost` only.

## 10. Secret rotation

Secrets: `ANTHROPIC_API_KEY`, `DATABASE_URL` (runtime role password), `DATABASE_MIGRATION_URL` (owner password), `REDIS_URL` (password), the gateway's TLS key, any OIDC client secret used by **clients** (the API itself needs none).

1. Create the new credential beside the old one (second key, new database role password with both valid during the change, Redis ACL user or `requirepass` change during a window).
2. Update the secret manager entry (env var or file under `APP_SECRETS_DIR`).
3. Restart processes in order: worker, API replicas (rolling). Connections are built at start, so a restart is required (Known limitation).
4. Verify: `--check-config` exit 0, `/ready` 200, one smoke turn.
5. Revoke the old credential. Check logs never contained it (they never print secrets; confirm with a grep for a distinctive part of the **old** value in your log store).

## 11. Rate-limit changes

* API limits per verified user (`RATE_LIMIT_*`) and gateway limits per address (`GATEWAY_*`) are configuration. Current values are provisional (Q20).
* Change: set the variable, restart API replicas (or reload the gateway). Values are per window (`RATE_LIMIT_WINDOW_SECONDS`, default 60).
* Signs a limit is too tight: `ratelimit.exceeded` logs for normal users; gateway `status 429` lines from one office NAT address (raise `GATEWAY_REQUESTS_PER_SECOND`/`GATEWAY_BURST`, the API limit still protects per user).
* Emergency (abuse or cost spike): lower `RATE_LIMIT_TURNS_PER_WINDOW`; to stop turns entirely without touching authorization, set it to 1 and the window to 3600. Never use rate limits to block a specific user: disable the user at the identity provider.
* Rate limits never grant access; changing them cannot expose data.

## 12. API restart

* SIGTERM: the API stops accepting connections, lets in-flight requests finish for up to `API_GRACEFUL_SHUTDOWN_SECONDS` (30), then runs the bounded shutdown sequence within `API_SHUTDOWN_CLEANUP_SECONDS` (10; § 20), logs `api.stopped`, and exits with the signal's status (143 in a container; since Phase 15, uvicorn re-raises the signal). Before Phase 15 it exited 0. Keep the orchestrator grace period above the sum of both values (for example 45 s for 30 + 10). Verified in Phase 8: a turn in flight during SIGTERM completed with 200.
* Roll replicas one at a time; readiness gates traffic. The gateway re-resolves the API address (new containers get new addresses) and never marks its upstream down by itself (`max_fails=0`).
* Run **at least two replicas** in production: replacing a single API container leaves nothing listening for a few seconds (verified locally: instant 503 `SERVICE_UNAVAILABLE` from the gateway during the gap, then normal service).
* A client whose request was cut can retry safely **if it sent `thread_id` and the same `idempotency_key`**: the stored answer is replayed (verified). A first turn without `thread_id` cannot be deduplicated (see PROJECT_STATUS Q24).
* Do not restart replicas during an identity-provider outage (§ 8).

## 13. Worker restart

* One-shot jobs: nothing to restart; the next schedule runs. A failed run exits 1 with `partial`/`failed` statuses and is retried by the next run.
* `--loop` worker: SIGTERM → `maintenance.stop_requested` → the current batch commits → exit 0 (verified). Then start it again.
* A job stuck on a hung database is bounded by the scheduler deadline (§ 2); the job lock expires after `MAINTENANCE_JOB_LOCK_TTL_SECONDS` (300) so the next run can proceed.

## 14. Health and readiness diagnosis

| Observation | Meaning | Action |
|---|---|---|
| `/health` fails | process dead or wedged | orchestrator restarts it; check the last logs for `config.production.*` or crashes |
| `/ready` 503, `database: degraded` | schema behind or ahead of this version | run the migration job (§ 3) or roll back the image (§ 4) |
| `/ready` 503, `database: unavailable` | database unreachable, hung or pool starved | § 5 |
| `/ready` 503, `redis: unavailable` | Redis unreachable or timing out | § 6 |
| `/ready` 200, `llm: not_configured` | no LLM credential (production would not start) | configuration error outside production |
| `/ready` 200 but turns 502/503 `LLM_*` | provider problem; degraded by design | § 7 |
| 503 `IDENTITY_PROVIDER_UNAVAILABLE` | signing keys unavailable | § 8 |
| 429 `RATE_LIMITED` with `X-Request-ID` | API per-user limit | § 11 |
| 429 `RATE_LIMITED` without `X-Request-ID` | gateway per-address limit | § 11 |
| 503 `SERVICE_UNAVAILABLE` / 504 `GATEWAY_TIMEOUT` from the gateway | no healthy API replica / slow upstream | check API readiness and logs by `api_request_id` |
| process exits 2 at start | production policy refused the configuration | run `--check-config`; fix the listed settings |
| 503 `SERVER_BUSY` with `X-Request-ID` | this API process already runs `API_MAX_CONCURRENT_TURNS` turns (Phase 15) | § 20 |
| `/ready` 200, `erp: degraded` / `misconfigured` | the live ERP gate has not run yet / refused the connection; the ERP never gates readiness | § 20, § 19 |

`/ready` is not exposed publicly through the gateway (internal networks only); probe the API container directly.

## 15. ERP read-only access verification

**When:** before the first live ERP read, after any change to the ERP read-only role, its grants or the ERP's RLS policies, and after ERP upgrades (Flyway). Checklist: [docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md). Status at Phase 9: **BLOCKED** (no ERP access contract, Q1). Status at Phase 22 (2026-10-30): the real ERP is InvenLogic, three databases (`master_data_db`, `sales_db`, `inventory_db`); its contract exists ([docs/erp/invenlogic_contract.json](docs/erp/invenlogic_contract.json)), but host, role and credentials (A–G, T) are not supplied yet, so this step has not run against the live ERP. **Run it once per database**, with the read-only role's URL and `--expect-database` of that database, or use `erp_readiness --live` with `ERP_INSPECT_DATABASE_URLS`, which runs this inspector for every database (§ 19).

1. Get the database name, host and read-only role from the ERP owner (checklist § 2). Load the URL from the secret store into the environment of this one command. Never put it in a file, a ticket or shell history:

   ```powershell
   $env:ERP_INSPECT_DATABASE_URL = "<from the secret store>"   # postgresql://<ro role>@<host>:<port>/<db>?sslmode=verify-full
   .venv\Scripts\python -m scripts.inspect_erp_security --expect-database <db>
   Remove-Item env:ERP_INSPECT_DATABASE_URL
   ```

2. Read the result:

   | Exit / output | Meaning | Action |
   |---|---|---|
   | `REFUSED: ... Agent DB` / `alembic_version` | the URL points at the Agent DB | fix the URL; never "adopt" the database |
   | `REFUSED: connected database is 'x', expected 'y'` | wrong database or wrong `--expect-database` | confirm with the ERP owner |
   | `NOT INSPECTED: <ErrorType>` | network, TLS, authentication or timeout | check firewall/VPN, `sslmode`, role password; the message deliberately omits host and user |
   | exit 1, a `FAIL` line | the role can write, run DDL, bypass RLS or owns objects | **stop**: the ERP DBA must fix the role (checklist § 2 "required role shape"); do not connect the platform |
   | exit 0 with `WARN` lines | no blocker; each WARN needs a written decision from the ERP owner (tables without RLS, owner-rights views, sequences, SECURITY DEFINER functions, Flyway) | record the decision in the checklist |
   | exit 0, `VERDICT: PASS` | role is read only and least privilege | attach the `--json` output to the checklist |

3. What the tool will never do: write, create, alter, lock (lock timeout 1 s), read business rows, or run longer than 10 s per statement. It refuses to start against the Agent DB. INSERT, UPDATE and DELETE are only *planned* (`EXPLAIN`), and the server refuses the plan for a read-only role (`42501`).
4. A FAIL after an ERP release usually means a broad `GRANT ... ON ALL TABLES` or a new default privilege. Report it to the ERP DBA. The platform's own configuration can't fix it.

## 16. Business read layer (ERP adapter)

**Modes** (`ERP_ADAPTER`): `none` (default; business questions are `unsupported`) or `postgres` (read-only boundary). With `postgres`, a capability answers only through a reader registered for a VERIFIED contract mapping; otherwise UNAVAILABLE / RULE_NOT_VERIFIED. The InvenLogic readers (`invenlogic_v1`, Phase 22) are in the code; the InvenLogic contract is still DRAFT, so on a live ERP every capability answers RULE_NOT_VERIFIED until § 19 is completed. *(The former `fake` mode, TEST data for development, was deleted on 2026-10-30 by owner decision; `ERP_ADAPTER=fake` is now an invalid setting. Development and tests use the TEST replica in `docker/erp-test/`.)*

**Enabling `postgres`** (only after § 15 passes): for a single-database ERP, put the read-only role's URL in the secret file `ERP_DATABASE_URL` and set `ERP_EXPECTED_DATABASE`; for InvenLogic (several databases), put `ERP_DATABASE_URLS` in the secret store instead, a JSON object of contract database key → URL (`{"master_data": "...", "sales": "...", "inventory": "..."}`). Set `ERP_SSL_MODE=verify-full` and run `--check-config`. It refuses any URL naming the Agent DB, a missing expected name (single database), development passwords, and `ERP_DATABASE_URLS` keys that differ from the contract's databases (`ERP_CONTRACT_DATABASES_MISMATCH`). A malformed `ERP_DATABASE_URLS` is reported without echoing its value. Use `127.0.0.1` or the server's IP rather than `localhost`: on Windows, `localhost` can try IPv6 first and stall about 2 s per new connection when PostgreSQL listens on IPv4 only. Grant `business.*` permissions through `AUTH_ROLE_CATALOG_JSON`; no built-in role has them.

| Signal | Meaning | Action |
|---|---|---|
| `erp.identity.refused` (error) | wrong database, the Agent DB, a session that isn't read only, or a superuser/BYPASSRLS role | **stop**: fix the URL or the role with the ERP DBA; the layer serves nothing until fixed (restart after fixing) |
| `erp.write_or_permission_refused` (error) | the ERP refused a statement (read-only or privilege) | a code defect or a revoked grant: open an incident, don't widen the role |
| `business.read.rejected` with `TENANT_MISMATCH` (error) | an adapter returned another tenant's record; the answer was discarded | security incident: disable the adapter (`ERP_ADAPTER=none`), investigate the mapping |
| `business.read.completed` status `UNAVAILABLE` reason `ERP_TIMEOUT` / `ERP_UNAVAILABLE` | ERP slow or unreachable | check network/VPN and ERP load; the agent already says the data couldn't be retrieved |
| `business.response.ungrounded` (warning) | the model wrote a figure without a source; the reply was replaced | expected occasionally; a spike means a prompt/model regression |
| `business.response.rejected` (warning, Phase 11) | a draft was replaced by the deterministic rendering; `fields` say why: `unsourced_figures`, `hedged`, `action_claim`, `requested_figure_unavailable` | occasional is expected. A spike in `action_claim` or `hedged` means a prompt/model regression. Many `requested_figure_unavailable` mean users ask for figures whose ERP rule is not verified (see below) |
| business answers say "the business rule behind it is not verified yet" / reads end `RULE_NOT_VERIFIED` | a derived figure (overdue, week/month sales, "today") needs a rule the adapter does not vouch for, or `ERP_BUSINESS_TIMEZONE` is unset | expected until the ERP owner confirms the rule (docs/SCHEMA_INTEGRATION.md § 4). **Never** set `ERP_BUSINESS_TIMEZONE` or declare a rule to make the message go away |

**Phase 11 notes.** Relative dates ("aaj", "this month") are resolved by the service from the clock and `ERP_BUSINESS_TIMEZONE`. The fake adapter's time zone and rules were TEST definitions (the fake was deleted in Phase 22; InvenLogic's are in its contract: Asia/Kolkata, Indian financial years). Follow-up questions use a per-conversation focus that holds references only, never figures. Every turn reads the ERP again. Nothing in Phase 11 writes to the ERP or to the Agent DB schema (no migration).

**Never**: give the business layer the ERP application's role, raise `ERP_STATEMENT_TIMEOUT_MS` to work around slow queries without the ERP owner, or point production settings at the TEST replica (`docker/erp-test/`, TEST rows only).

| Signal (Phase 22) | Meaning | Action |
|---|---|---|
| `business.read` `result_status=DEPENDENCY_ERROR`, `reason=MALFORMED_RESPONSE` | a document the read relies on has a status the contract does not list (or a referenced master-data row is missing); the value is never guessed | run `SELECT DISTINCT status` on the document tables with the ERP owner and correct the contract (INVENLOGIC_MAPPING.md § 3) |

## 17. Supervisor and domain routing (Phase 12)

Every turn goes through the Supervisor (`supervise` node, ADR-061). It picks the domain (`general`, or `business` when an ERP adapter is configured) from the planner's classification, or answers itself with a fixed message when the request needs a domain that does not exist yet (`purchase`, `sales`/`inventory` writes, `accounting`, `gst`, `knowledge`, `notification`). It runs no tool, reads no data and decides no permission: the domain, the tool registry and the business service still check the caller's permissions. Nothing here writes to the ERP or the Agent DB schema.

| Signal | Meaning | Action |
|---|---|---|
| `supervisor.decided` (info) | the routing decision: `outcome` (ROUTE, CLARIFY, UNSUPPORTED, UNSUPPORTED_CAPABILITY), `domain`, `reason`, `requested_action`, `unavailable` (`domain:action`), `follow_up`, `topic_changed`, `rerouted_from` | none. Codes only: no user text, names or figures. Use `reason` counts to see what users ask for that does not exist (`not_available`, `reserved_domain`) |
| `supervisor.turn.completed` (info) | the turn's end: `domain_outcome` (SUCCESS, CLARIFICATION_REQUIRED, UNAUTHORIZED, DEPENDENCY_ERROR, VALIDATION_ERROR, FAILED, UNSUPPORTED), `fixed_reply`, `notice_appended`, `action_claim_replaced` | a rise in `DEPENDENCY_ERROR` for `business` = ERP trouble (§ 16). A rise in `UNAUTHORIZED` = role catalogue / permission change. `action_claim_replaced` should be rare: a spike means a prompt/model regression |
| `reason: invalid_domain` | the planner returned a domain key that does not exist; the user got "I can't handle this request here" and nothing ran | occasional is harmless. A spike means a prompt/model regression (check `understand_request.system` / `supervisor.routing` versions) |
| `reason: domain_not_connected` | a business question while `ERP_ADAPTER=none` | expected until the ERP is connected (Q1) |
| error `ORCHESTRATION_FAILED` / `ORCHESTRATION_LIMIT_EXCEEDED` (`node.failed`) | the routing contract was broken: no or invalid decision in state, a second decision or domain run in one turn, a routed domain that is not registered | **a defect or tampered checkpoint, never load.** The turn failed cleanly and nothing ran twice. Open an incident with the `request_id`; do not edit checkpoints |
| error `DOMAIN_TIMEOUT` (retryable) | one domain run took longer than `AGENT_DOMAIN_TIMEOUT_SECONDS` (default 120) | check the dependency behind the domain (ERP § 16, LLM § 7). Do not raise the timeout above the thread lock's renewal budget without review |

**Known limits.** The planner still sees the tool descriptions of every domain the user may use (one LLM call classifies and plans); execution is restricted per domain. Two domains never run in one turn: the unsupported part of a mixed request is reported ("Note: part of your request was not done …"), or the user is asked first when the request mixes a read with sending something. No business write, reorder, purchase order, WhatsApp/SMS or accounting/GST action exists.

## 18. Observability, diagnostics and reliability signals (Phase 13)

Every turn emits typed **observability events** (logger `agentic_platform.observability`, field `"kind": "observability"`, ADR-062). They carry ids, codes, counts, durations and flags only: never user text, replies, memory, ERP records, tool argument values, tenant or user ids. They are **not** the audit log: for who-did-what (security), use `GET /api/v1/audit` / `--audit`; audit stays authoritative.

**Diagnosing one request.** Take the `request_id` (the `X-Request-ID` response header, or the error body's `request_id`).

* From shipped JSON logs (`LOG_FORMAT=json`): `python -m agentic_platform.observability.diagnose --request-id <id> --file agent.log` (or pipe logs to stdin; `--json` for machine output). It prints the turn tree: planner → supervisor (domain, decision) → domain (tools, business reads, attempts) → verification (grounded/replaced) → response status → total duration, plus model calls, retries, timeouts and checkpoint counts.
* On a developer machine: `python -m agentic_platform --trace "..."` prints the same tree to stderr after the turn.
* The in-process buffer keeps the last `OBSERVABILITY_TRACE_BUFFER_REQUESTS` (default 200) requests per process; it is lost on restart. Logs are the durable source.

| Signal (event / metric) | Meaning | Action |
|---|---|---|
| `turn.failed` with `error_category` | a turn ended failed; the category says why (see the table below) | follow the category's row |
| `turn.rejected` (`validation`, `authentication`, `authorization`, `not_found`) | the call was refused before or instead of a turn | a spike of `authentication` = token/IdP issue (§ 8); of `authorization` / `not_found` = role catalogue change or probing; check the audit log |
| `timeout` (`attr.scope`: `business_read`, `tool`, `domain`) | a deadline fired; the operation **failed** (it cannot return success afterwards) | `business_read` = ERP slow (§ 16); `tool` = a tool's own limit; `domain` = `AGENT_DOMAIN_TIMEOUT_SECONDS` exceeded. Repeated `business_read` then `tool` timeouts on one request = a dependency ignoring cancellation: investigate the driver |
| `retry.attempted` (`attr.operation=business_read`) | the ERP was unreachable and the read was retried within its deadline | occasional is fine. Sustained = ERP connectivity problem (§ 16) |
| `retry.exhausted` | all `ERP_READ_MAX_ATTEMPTS` failed (or the deadline left no room); the user got "could not be retrieved" | ERP outage (§ 16). Do not raise attempts to hide an outage |
| `llm.call.failed` (`retryable_dependency`) | the model provider failed after the SDK's own retries (`LLM_MAX_RETRIES`) | provider incident or rate limit (§ 7). The graph adds no retries of its own |
| `verification.result` with `attr.replaced=true` | a business reply was replaced by the deterministic answer (unsourced figure, hedge, action claim, figure unavailable) | normal at a low rate; a spike = prompt/model regression |
| `checkpoint.save_failed` / `checkpoint.load_failed` | the checkpoint store failed (`dependency_unavailable`) or the data could not be (de)serialized (`serialization`, `checkpoint`) | store outage: § 5/§ 14. `CHECKPOINT_UNREADABLE` / `CHECKPOINT_SERIALIZATION_FAILED`: a defect or a corrupted row; open an incident with the request id; never edit checkpoints by hand |
| `checkpoint.rejected` (`CHECKPOINT_TYPE_REJECTED`) | a checkpoint held a type outside the allow-list; it was **not** constructed | a tampered row or a missing allow-list entry after a code change: incident |
| `authz.denied` | an authorization refusal (also in the audit log, which is authoritative) | as for audit `authz.denied` |

**Error categories** (`error_category` on events; computed from the stable error code; the API error body is unchanged): `validation`, `authentication`, `authorization`, `not_found`, `ambiguity`, `dependency_unavailable`, `timeout`, `retryable_dependency`, `non_retryable_dependency`, `model`, `tool`, `domain` (routing contract broken: `ORCHESTRATION_*`, a defect, never load), `checkpoint`, `serialization`, `configuration`, `internal`.

**Metrics.** In-process only (`Agent.metrics.snapshot()`): turns by outcome and duration, stage durations and failures, routing decisions, domain runs, tool calls/durations, LLM calls/latency/tokens, retries, timeouts, checkpoint durations/failures, business reads by status, grounding (grounded/replaced), security refusals. Labels never contain tenant, user, customer, invoice, amount, thread or request ids. No exporter is configured (Q3); until one is, count the event log lines in the log platform.

**Configuration.** `OBSERVABILITY_LOG_EVENTS` (default true: one log line per event; set false to keep only metrics/traces), `OBSERVABILITY_TRACE_BUFFER_REQUESTS` (default 200), `ERP_READ_MAX_ATTEMPTS` (default 2; 1 disables the retry), `ERP_READ_RETRY_BACKOFF_SECONDS` (default 0.1). Retry and deadline policy: ADR-062.

**Pre-release regression checks** (no API key, no network): `python -m tests.evaluation.run` (scorecard of the scripted evaluation dataset; exit code 1 on any failure), `pytest tests/evaluation tests/test_failure_injection.py`, and `python -m tests.perf.benchmark` for a local latency baseline (not a capacity figure). After a reviewed behaviour change: `python -m tests.evaluation.run --update-golden` and review the golden diff.

## 19. Real ERP activation and deactivation (Phase 14)

**Current state (Phase 22, 2026-10-30): BLOCKED at the input gate on A, B, C, D, E, F, G, T.** The owner confirmed InvenLogic as the real ERP. Its contract ([docs/erp/invenlogic_contract.json](docs/erp/invenlogic_contract.json), DRAFT) holds the mappings and the owner-adopted rules, and the six readers (`invenlogic_v1`) exist and pass on the TEST replica; the connection items (owner confirmation, hosts, databases, version, read-only role, credentials) are missing. Until the contract is VERIFIED, `ERP_ADAPTER=postgres` answers every business question with UNAVAILABLE / RULE_NOT_VERIFIED. Don't point the platform at any other database, including `inventoryai-postgres` ([docs/erp/ERP_CONTRACT.md](docs/erp/ERP_CONTRACT.md), ADR-063, ADR-067). *(Phase 14 text, superseded: "No ERP access contract (Q1) exists, so the live adapter has no mapping.")*

**InvenLogic activation (multi-database): follow [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6.** It is the procedure below with these differences:

* The contract has `databases` (`master_data`, `sales`, `inventory`) instead of one `database`. The DBA creates the read-only role in **all three** databases (`SELECT` only on the tables in INVENLOGIC_MAPPING.md § 1; never `auth_db`, `purchase_db` or `accounting_db`).
* Discovery uses `ERP_INSPECT_DATABASE_URLS`, a JSON object of database key → URL from the secret store, instead of `ERP_INSPECT_DATABASE_URL`. Every check runs for every database; `--live` reports each one.
* Steps 4 and 5: there is nothing to write. The readers are already in the code and register themselves for each mapping marked VERIFIED. Confirm the RLS setting name and the status values first (INVENLOGIC_MAPPING.md § 2, § 3).
* The agent is configured with `ERP_DATABASE_URLS` (same keys) instead of `ERP_DATABASE_URL` / `ERP_EXPECTED_DATABASE`, `ERP_BUSINESS_TIMEZONE=Asia/Kolkata`, and each user's tenant id = their InvenLogic company id.
* Use `127.0.0.1` or the server's IP, not `localhost`, in every URL (§ 16).

```powershell
$env:ERP_INSPECT_DATABASE_URLS = "<from the secret store>"   # {"master_data": "postgresql://...", "sales": "...", "inventory": "..."}
.venv\Scripts\python -m scripts.erp_readiness --contract docs\erp\invenlogic_contract.json --live
Remove-Item env:ERP_INSPECT_DATABASE_URLS
```

**Check the gate at any time (offline, nothing connects):**

```powershell
.venv\Scripts\python -m scripts.erp_readiness                                   # BLOCKED: lists Q1 items A-T
.venv\Scripts\python -m scripts.erp_readiness --contract <path>\erp_contract.json
```

| Exit | State | Meaning |
|---|---|---|
| 3 | `BLOCKED` | access items missing (A, B, C, E, F, G, H) or the contract is invalid. Nothing may connect |
| 4 | `READY_FOR_DISCOVERY` | access items present, contract not VERIFIED: the read-only inspection may run |
| 0 | `READY_FOR_ACTIVATION` | contract VERIFIED (and, with `--live`, every live check passed) |
| 1 / 2 | live failure / refused | a security finding or gate problem (1); unreachable, wrong database or Agent DB URL (2) |

**Activation (in order; stop at the first failure, never work around a check):**

1. Contract: the ERP owner fills and signs `erp_contract.json` (no secrets). Store it with the deployment configuration and review it like code.
2. Credential: the ERP DBA creates the read-only role (OPERATIONS_RUNBOOK § 15, checklist § 2). Put its URL in the secret store, never in a file or chat.
3. Discovery, read-only: load `ERP_INSPECT_DATABASE_URL` from the secret store for this shell only, then run `python -m scripts.erp_readiness --contract <file> --live`. It runs the Phase 9 inspector (stops on any FAIL), then the adapter gate (privileges, version, relations, columns, types, tenant/warehouse isolation). It reads the catalog only; no business row. Remove the variable afterwards.
4. Owner sign-off: set `status` to VERIFIED (and each mapping that's ready). Run step 3 again: it must exit 0.
5. Readers: engineers add one reviewed capability reader per VERIFIED mapping (InvenLogic: already written, `adapters/invenlogic.py`) and run the contract suite against the ERP's dedicated test records (`ERP_CONTRACT_TEST_CONTRACT`, `ERP_CONTRACT_TEST_FIXTURES`, `ERP_INSPECT_DATABASE_URL`). Query plans are reviewed with the ERP owner; any index is created by the ERP owner, never by this platform.
6. Configure: `ERP_ADAPTER=postgres`, `ERP_CONTRACT_PATH`, `ERP_DATABASE_URL` (secret store) and `ERP_EXPECTED_DATABASE` (= contract), or for a multi-database contract `ERP_DATABASE_URLS` (secret store; keys = contract `databases`), `ERP_BUSINESS_TIMEZONE` (= contract), `ERP_SSL_MODE=verify-full`. Run `python -m agentic_platform.api --check-config`. Production refuses to start on `ERP_CONTRACT_MISSING`, `ERP_CONTRACT_INVALID`, `ERP_CONTRACT_NOT_VERIFIED`, `ERP_CONTRACT_DATABASE_MISMATCH`, `ERP_CONTRACT_DATABASES_MISMATCH` or `ERP_TIMEZONE_MISMATCH`.
7. Watch: `business.read` events (§ 18) by `attr.capability` / `attr.result_status`; `erp.identity.refused` (error) means the gate refused the connection. Treat that as a security incident, not an outage.

**Deactivation / rollback (no data to migrate; the platform stores no ERP records):** set `ERP_ADAPTER=none` and restart the API. Business questions then get the "business data is not connected" reply, and nothing reaches the ERP. To keep the domain but stop one capability, remove its reader (or set its mapping back to DRAFT) and redeploy: that capability returns RULE_NOT_VERIFIED. Revoke or rotate the ERP role's credential in the secret store if the deactivation follows a security finding.

| Signal | Meaning | Action |
|---|---|---|
| `erp.identity.refused` with problems (`write privileges`, `owns relations`, `not found`, `type ...`, `row-level security`) | the gate refused the ERP connection; nothing was read | the ERP role or schema changed. Stop, compare with the contract, ask the ERP DBA. Never grant the platform more rights |
| `business.read` `result_status=UNAVAILABLE`, `reason=RULE_NOT_VERIFIED` | no VERIFIED reader for that capability | expected until Q1. After activation, check the reader and the contract mapping |
| `ERP_CONTRACT_*` at startup | the contract is missing, invalid, unverified or inconsistent with settings | fix the contract or settings; do not start without it |

## 20. Shutdown, turn capacity, build identity and Agent DB backup (Phase 15)

Design: ADR-064, ARCHITECTURE.md § A.19. Status: PROJECT_STATUS § 3o. **The deployment smoke test and the backup scripts below have no recorded run yet.** Rehearse them (`deploy/smoke/run_smoke.py` runs both) before relying on them.

**Shutdown.** SIGTERM → in-flight requests finish for up to `API_GRACEFUL_SHUTDOWN_SECONDS` → the rest are cancelled (a cancelled turn is recorded as failed, its thread lock released; a retry with the same idempotency key is not answered as a replayed success) → the bounded sequence within `API_SHUTDOWN_CLEANUP_SECONDS`: `in_flight_requests`, `maintenance` (in-process only), `redis`, `database`, `erp`, `observability`. Set the orchestrator grace period above `API_GRACEFUL_SHUTDOWN_SECONDS + API_SHUTDOWN_CLEANUP_SECONDS`. The worker (`python -m agentic_platform.maintenance`) closes its connections through the same sequence.

| Signal | Meaning | Action |
|---|---|---|
| `shutdown.step` with `status: timeout` (`SHUTDOWN_STEP_TIMEOUT`) | that close hung and was abandoned; the process still stopped on time | check the named dependency (§ 5 database, § 6 Redis, § 19 ERP). Nothing to clean up: the server drops the connections |
| `shutdown.step` with `status: error` | that close raised; later steps still ran | look at `error_code`; same as above |
| `api.stopped` with `shutdown: degraded` | at least one step timed out or failed | as above; the next start is unaffected (pools reconnect lazily) |
| exit status 143 after SIGTERM | normal stop (the signal's status) | none |

**Concurrent-turn cap.** `API_MAX_CONCURRENT_TURNS` (default 32) agent turns per API process. One more gets 503 `SERVER_BUSY` (retryable) at once, never queued. It applies to agent turn, conversation start/continue/resume, and approval approve/reject. `api.turns.busy` (warning) is logged each time. Occasional 503s under a burst are the bound working; clients retry. Sustained 503s mean too little capacity: add API replicas rather than raising the cap. Raise the cap only after a load test of the real deployment (Q3). The database pool, LLM rate limits and memory must support the new value. `0` disables the cap (production warns `TURN_CONCURRENCY_UNBOUNDED`).

**Build identity.** `python -m agentic_platform.api --version` prints `{version, commit, build_time}` without any configuration. The same values are on the `api.started` log line and in `/ready` under `build`. `unknown` means the image was built without `--build-arg APP_BUILD_COMMIT=... --build-arg APP_BUILD_TIME=...` (Dockerfile header), or with a value of the wrong shape.

**ERP on `/ready`.** `checks.erp` is always `ready: true`: an ERP problem never takes the API out of rotation, and business reads fail closed on their own. `not_configured` (`ERP_ADAPTER=none`, or `ERP_NO_VERIFIED_CAPABILITY`) is expected until Q1. `degraded` / `ERP_NOT_YET_VERIFIED` means the live gate has not run yet (it runs on the first business read). `misconfigured` / `ERP_SAFETY_VIOLATION` means the gate refused the connection: treat it as a security incident (§ 19).

**Production refusals added in Phase 15:** `LOG_LEVEL_DEBUG` (use INFO; diagnose with the request id and § 18) and `DATABASE_STATEMENT_TIMEOUT_DISABLED`. In every environment, an `OBSERVABILITY_EXPORTER` other than `none` is a settings error at startup.

**Agent DB backup and restore** (`deploy/backup/`, POSIX `sh` + PostgreSQL client tools). **Agent DB only:** never point these scripts at an ERP. Connection comes from the libpq environment (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` or `PGPASSFILE`, `PGSSLMODE`); never pass a password on the command line.

1. Backup, as the schema owner (row-level security does not filter the owner; the API keeps serving): `PGDATABASE=<agent db> agent_db_backup.sh <out.dump>`. This writes `<out.dump>` and `<out.dump>.manifest`. Exit 3 = not the Agent DB (a platform table or `alembic_version` is missing); exit 4 = the output already exists (a backup is never overwritten).
2. Restore **into a new, empty database**: `agent_db_restore.sh <out.dump> <new_db>`. The roles `AGENT_DB_OWNER_ROLE` (default `agentic_owner`) and `AGENT_DB_APP_ROLE` (default `agentic_app`) must exist, along with the `vector` extension (or a role allowed to create it). Exit 2 = bad target name or unreadable dump; exit 3 = not an Agent DB dump or a role is missing; exit 4 = the target already holds tables.
3. Verify, read-only: `agent_db_verify.sh <out.dump.manifest> <new_db> [--strict]` checks the migration revision, the tables and their RLS flag, the audit triggers, and that the runtime role can connect. `--strict` also requires equal row counts. Exit 0 = verified, 1 = a difference.
4. Point the application at the verified database (configuration change, then the API restart in § 12). Never restore over the live database, and never drop the old one as part of a recovery (safety rule 1).

The dump holds every tenant's conversations, memory and audit trail: its encryption, transfer and retention are the deployment's job (Q3). A managed PostgreSQL service's own backups and PITR (ARCHITECTURE.md § A.11) stay the primary mechanism; these scripts are for portable copies and restore rehearsals.
