# Go-Live Checklist

> **For:** the people who sign off go-live (engineering lead, operations, security).
> **Rule (ADR-057):** a box is checked only with evidence from a run. "Local" means real components on the development machine (Docker), not the production environment. Every unchecked box says what must happen and who decides.
> **Last updated:** 2026-10-30 (Phase 22: InvenLogic confirmed as the real ERP, readers built and verified on a TEST replica, fake ERP deleted; ERP rows updated; verdict unchanged). Before that, 2026-10-10 (Phase 17: Phase 15 rows). Details and raw results: [PROJECT_STATUS.md](PROJECT_STATUS.md) § 3g (Phase 8) and § 3h (Phase 9). Procedures: [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md). ERP: [docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md](docs/erp/LIVE_ERP_VERIFICATION_CHECKLIST.md).

**Verdict: NOT READY FOR GO-LIVE** (re-checked in Phase 9, 2026-10-02). Four inputs are still missing: the production identity provider (Q6), the deployment target (Q3), the production Anthropic credential (Q22) and the ERP access contract (Q1). None of them was supplied for Phase 9, so none was chosen, faked or marked verified. A business go-live also needs the ERP read layer, business tools and business rules. They **don't exist in this repository yet** (PROJECT_STATUS § 3h). Everything the platform can prove without these inputs has been proven locally. *Update 2026-10-30 (Phase 22): the read layer, the six business tools and the owner-adopted business rules now exist for the company's real ERP, InvenLogic, and pass on a TEST replica (PROJECT_STATUS § 3t). Q1 is partly supplied: the contract (DRAFT) still misses A, B, C, D, E, F, G, T, and the live ERP has not been contacted. Q6, Q3 and Q22 are unchanged. The verdict stays NOT READY.*

| Status legend | Meaning |
|---|---|
| VERIFIED | evidence from a run against the real component (local where stated) |
| NOT VERIFIED | could be run, has not been |
| BLOCKED | needs an input that has not been supplied (question number given) |
| NOT APPLICABLE | the feature does not exist and is not in scope |

## Machine-checkable gates

Run these against the **production** configuration and environment before the switch:

```sh
# 1. Configuration gate (exit 0 required), API and worker
docker run --rm --env-file <prod env> -v <secrets>:/run/secrets:ro <image> python -m agentic_platform.api --check-config
docker run --rm --env-file <prod env> -v <secrets>:/run/secrets:ro <image> python -m agentic_platform.maintenance --check-config
# 2. Identity provider (provider-neutral live tests)
LIVE_OIDC_ISSUER=... LIVE_OIDC_AUDIENCE=... LIVE_OIDC_ACCESS_TOKEN=... pytest tests/live/test_oidc_live.py -m live_oidc
# 3. Claude (about four short, harmless requests; billed)
RUN_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/live/test_llm_live.py -m live_llm -s
# 4. Repository gates
pytest            # with TEST_DATABASE_URL, TEST_DATABASE_APP_URL, TEST_REDIS_URL
ruff check src tests scripts && mypy --strict && mypy --strict scripts
# 5. ERP read-only role (exit 0 required; OPERATIONS_RUNBOOK.md § 15)
ERP_INSPECT_DATABASE_URL=... python -m scripts.inspect_erp_security --expect-database <erp db>
#    InvenLogic (three databases; docs/erp/INVENLOGIC_MAPPING.md § 6): exit 0 required
ERP_INSPECT_DATABASE_URLS='{"master_data": "...", "sales": "...", "inventory": "..."}' python -m scripts.erp_readiness --contract <verified contract> --live
```

## Identity

| | Item | Evidence / what remains |
|---|---|---|
| [ ] | Production IdP selected | **Open (Q6).** Organisational decision: Entra ID / Okta / Auth0 / Cognito / Keycloak. Requirements: PROJECT_STATUS § 3f.3 |
| [ ] | Real login verified (production IdP) | Needs Q6. Run gate 2 with a token from the production tenant |
| [x] | Token validation verified (reference IdP) | Keycloak 26.4 local: signature, JWKS discovery and caching, key rotation, exp, iat, max lifetime, wrong iss/aud, ID token refused, alg none/HS256 refused, malformed, missing tenant. `tests/live/test_oidc_live.py` (Phase 7, re-run Phase 8) |
| [ ] | Token validation verified (production IdP) | Needs Q6 (gate 2) |
| [x] | Role mapping verified (reference IdP) | mapped, unmapped and forged roles; role removed takes effect with the next token; disabled user gets no new token |
| [ ] | Role mapping verified (production IdP) | Needs Q6 and the production role catalogue (Q8) |
| [x] | Client cannot set tenant, user, roles or permissions | 422 for body fields; headers (`X-Tenant-ID`, `X-User-ID`, `X-Forwarded-For`) ignored through the gateway (Phase 8 run) |

## LLM

| | Item | Evidence / what remains |
|---|---|---|
| [ ] | Real Claude credential configured | **Open (Q22):** who provides it (organisation/workspace, key vs workload identity), and may business data be sent (Q4) |
| [ ] | Live Claude smoke test passed | Not run: no credential on this machine. Gate 3 |
| [x] | LLM failure handling verified | invalid key against the real endpoint → 502 `LLM_AUTH_ERROR`, key not logged (live test); 429/5xx/529/timeout/connection/malformed/invalid structured output through the real SDK (`tests/test_llm_failures.py`); provider down in the deployed stack → 503 `LLM_UNAVAILABLE`, rest of the API working (Phase 8 drill) |

## Security

| | Item | Evidence / what remains |
|---|---|---|
| [x] | Production auth enforced | image without configuration exits 2; dev token secret, `AUTH_PROVIDER=dev_token`, local identity → refused (unit tests + container run, Phase 8) |
| [x] | RLS verified | 7 RLS tables on the development DB; integration suites with the runtime role (`tests/integration/test_security_postgres.py`) |
| [x] | Tenant isolation verified | cross-tenant 404 identical to missing; tenant only from the token (integration + live OIDC over HTTP) |
| [x] | Secrets protected | not in image (scan), not in repository templates (test), not in logs (gateway + API container logs grepped), not in `--check-config` output; secret files via `APP_SECRETS_DIR` (container run) |
| [x] | Audit protected | `audit_log_no_update_delete`, `audit_log_no_truncate` triggers present; append-only tests pass |
| [x] | Approval replay blocked | executed approvals never run again; requester-only approvals (Phase 5/6 suites pass) |
| [x] | Rate limits never grant access | `tests/test_rate_limit_unit.py` (403 still 403 under budget; 401 before limiting) |
| [ ] | Production TLS certificate from a trusted CA | Needs Q3 (who issues and renews it). Local run used a self-signed development certificate |
| [ ] | Penetration test / external security review | Not done. Recommended before handling business data |

## Infrastructure

| | Item | Evidence / what remains |
|---|---|---|
| [x] | Docker image verified | non-root uid 10001; no private keys or `.env` in the image; production default; `HEALTHCHECK`; API / worker / migrate modes run (Phase 8 container runs) |
| [x] | API deployment verified (local) | compose `app` + `gateway` profiles; migrate exit 0 → API ready → traffic through TLS gateway |
| [ ] | API deployment verified (production platform) | Needs Q3; manifests for the chosen platform |
| [x] | Maintenance worker verified (local) | duplicate run → `skipped`; DB failure → exit 1; SIGTERM → exit 0 |
| [ ] | Maintenance schedule on the production platform | Needs Q3/Q21 (cron, CronJob or cloud scheduler) with a job deadline |
| [x] | PostgreSQL persistent (local) | volume `agentic_ai01102026_pgdata` kept since 2026-10-01; no `down -v`; container never recreated |
| [ ] | Managed PostgreSQL 16 + pgvector ≥ 0.8, TLS `verify-full`, backups + PITR | Needs Q3 |
| [x] | Redis available (local) | Redis 7, password, `noeviction`; outage behaviour verified |
| [ ] | Managed Redis with TLS (`rediss://`) and ACL | Needs Q3/Q13 |
| [x] | TLS enabled (gateway) | TLS 1.2/1.3 only (1.1 refused), HSTS, 308 redirect (local run) |
| [x] | Rate limiting enabled | gateway per address (429 at burst) and API per user (429 + `Retry-After`) verified through the TLS gateway |
| [ ] | Rate-limit values approved | **Open (Q20):** current values are provisional technical defaults |
| [x] | Agent DB separate from the ERP / system-of-record DB | migration job refuses a foreign non-empty database (`tests/integration/test_database_separation.py`); the application has no setting for any other database |

## ERP (system of record)

| | Item | Evidence / what remains |
|---|---|---|
| [ ] | ERP owner and access contract | **BLOCKED (Q1).** Owner, host, database, read-only role, authentication, tenant and warehouse mechanism, network path: checklist § 2. *2026-10-30: the owner confirmed InvenLogic as the real ERP; contract [docs/erp/invenlogic_contract.json](docs/erp/invenlogic_contract.json) is DRAFT with tenant/warehouse mechanism, rules and mappings filled* |
| [ ] | InvenLogic inputs A, B, C, D, E, F, G, T supplied and the contract VERIFIED | **BLOCKED (owner / DBA).** `python -m scripts.erp_readiness --contract docs/erp/invenlogic_contract.json` must stop listing missing items. Needed: ownership and intended-ERP confirmation (A, G); host, port, database names and PostgreSQL version for `master_data`, `sales`, `inventory` (B, C, D) and their Flyway versions (T); the read-only role in all three databases with credentials in the secret store (E, F). Also confirm the RLS setting name and the status values (`SELECT DISTINCT status`), then `--live` exit 0 and the contract suite against the ERP's TEST records. Steps: [docs/erp/INVENLOGIC_MAPPING.md](docs/erp/INVENLOGIC_MAPPING.md) § 6 |
| [ ] | ERP role verified read only and least privilege | **BLOCKED (Q1).** Gate 5. Tool ready and verified on a throw-away PostgreSQL database (`tests/integration/test_erp_inspection_postgres.py`: SELECT-only role passes, writes refused with `42501`; superuser, owner and single `GRANT INSERT` detected; Agent DB and wrong database refused; data unchanged) |
| [ ] | ERP RLS / tenant isolation verified | **BLOCKED (Q1)** and needs two real test tenants/users |
| [ ] | Live schema mappings verified | **BLOCKED (Q1).** `docs/SCHEMA_INTEGRATION.md`: mappings drafted from the InvenLogic Atlas and checked on the TEST replica (contract suite 18/18, `--live` READY_FOR_ACTIVATION on the replica); none verified on the live catalog |
| [x] | Business read layer foundation (Phase 10) | **MOCK VERIFIED only**: typed capabilities, ERP read port, deterministic results, 6 READ tools, `business` domain, figure grounding; fake ERP + throw-away PostgreSQL ([docs/BUSINESS_INTEGRATION.md](docs/BUSINESS_INTEGRATION.md)) |
| [ ] | Live ERP business answers | **BLOCKED (Q1, Q26).** No verified schema mapping: `ERP_ADAPTER=postgres` answers UNAVAILABLE / RULE_NOT_VERIFIED. Also needs Q4 (ERP data in LLM prompts). *2026-10-30: InvenLogic readers exist and answered through the production factory path on the TEST replica (outstanding, today's sales, stock); still RULE_NOT_VERIFIED on the live ERP until the contract is VERIFIED* |
| [x] | Fake ERP cannot run in production | *2026-10-30: the fake ERP was deleted (owner decision). `ERP_ADAPTER` accepts only `none` / `postgres`; `ErpSettings(adapter="fake")` is refused (unit tests). Production must also never point at the TEST replica (`docker/erp-test/`).* Before: `ERP_FAKE_ADAPTER` production error + factory refusal (unit tests) |
| [x] | Memory cannot override ERP figures | `verify_business_response` replaces unsourced figures (graph test: memory 50,000 vs ERP 45,000) |
| [x] | Agent never writes to the ERP | no write method in `ErpReadPort`/service; business tools READ only (registration refuses WRITE); ERP sessions `default_transaction_read_only=on` + READ ONLY transactions (writes refused even as superuser with the SQL guard off, throw-away PostgreSQL); ERP settings separate from the Agent DB/Alembic; migration job refuses foreign databases (ADR-056) |
| [x] | No fabricated business figures | figures only in SUCCESS results; totals computed in code; replies grounded by code (mock verified). Re-verify against the live ERP |

## Operations

| | Item | Evidence / what remains |
|---|---|---|
| [x] | Health verified | `/health` liveness (container `HEALTHCHECK`, gateway) |
| [x] | Readiness verified | `/ready` gates DB (at head), Redis, auth; answers within 3 s even with a hung database (fixed in Phase 8, ADR-055) |
| [x] | Logs verified | JSON, request ids correlated gateway ↔ API, no tokens or secrets (Phase 8 grep of container logs) |
| [ ] | Log shipping, metrics dashboards, alerts | Needs Q3 (platform tooling). Alert on: 5xx rate, `/ready` failures, `llm.call.failed`, `auth.provider_unavailable`, `ratelimit.exceeded` spikes, certificate expiry |
| [x] | Backup/restore strategy documented | managed PostgreSQL backups + PITR (ARCHITECTURE.md § A.11); Redis needs none |
| [ ] | Backup restore rehearsed | Needs the production database service (Q3). Agent DB backup / restore-into-new / verify scripts exist (`deploy/backup/`, runbook § 20, Phase 15) but have **not been run**, not even locally |
| [x] | Rollback documented | OPERATIONS_RUNBOOK.md § 4 |
| [x] | Bounded shutdown and concurrent-turn cap (Phase 15) | Unit tests + PostgreSQL 16/Redis 7 integration (`tests/test_operational_readiness.py`, `tests/integration/test_lifecycle_postgres.py`; re-run 2026-10-10, PROJECT_STATUS § 3o.5). Not verified in a container or on the production platform; cap value not sized (Q3) |
| [x] | Runbooks documented | OPERATIONS_RUNBOOK.md §§ 1-20 |

## Testing

| | Item | Evidence / what remains |
|---|---|---|
| [x] | Full test suite | PROJECT_STATUS § 3i.5 (Phase 10: PostgreSQL 16, Redis 7, Keycloak 26.4); § 3h.6 (Phase 9). Latest: § 3o.5 (2026-10-10: 1302 passed, 18 skipped, 0 failed; live suites skipped). Phase 22 (2026-10-30, fake ERP deleted, InvenLogic TEST replica): 1373 passed, 35 skipped, 0 failed with PostgreSQL 16, Redis 7 and the InvenLogic TEST replica (PostgreSQL 18.6); without the replica 966 passed, 442 skipped, 0 failed (baseline before Phase 22: 1326 passed, 18 skipped). ruff and mypy clean (PROJECT_STATUS § 3t.3) |
| [ ] | Deployment smoke test (`deploy/smoke/run_smoke.py`, Phase 15) | **NOT VERIFIED:** written, never run (no results recorded). Run locally with Docker before go-live |
| [x] | Security tests | unit + integration security suites; Phase 8 rate-limit and spoofing tests |
| [x] | Live identity tests (reference IdP) | Keycloak live suite |
| [ ] | Live identity tests (production IdP) | Needs Q6 |
| [ ] | Live Claude test | Needs Q22 |
| [x] | Load test (local, platform only) | `deploy/loadtest/` results in PROJECT_STATUS § 3g.7. **Not production capacity**: one API process on a laptop, LLM stubbed |
| [ ] | Load test on the production platform with the real model | Needs Q3, Q22 and a traffic estimate from the business |
| [x] | Failure tests | PostgreSQL, Redis, LLM, IdP, pool exhaustion, API restart, worker (PROJECT_STATUS § 3g.8) |
| [ ] | Live ERP tests | **BLOCKED (Q1).** Inspector verified on a throw-away database only. *2026-10-30: the business tests and the contract suite run on the InvenLogic TEST replica (PostgreSQL 18.6); full suite: 1373 passed, 35 skipped, 0 failed with PostgreSQL 16, Redis 7 and the InvenLogic TEST replica (PostgreSQL 18.6); without the replica 966 passed, 442 skipped, 0 failed (baseline before Phase 22: 1326 passed, 18 skipped). ruff and mypy clean. Local Ollama end-to-end: 5 Hindi/English business questions through the CLI with Ollama qwen3:8b (LLM_OLLAMA_NUM_CTX=4096) and the InvenLogic TEST replica: 5/5 completed, every ERP read SUCCESS; outstanding ₹45,000, today's sales ₹1,25,000, iPhone 15 stock 15 pcs, low stock iPhone 3/5 and Galaxy 0/2 all correct; for "pending bills dikhao" the model picked customer_outstanding instead of customer_open_invoices (right total, no bill list). 2.4-8.1 min per turn on CPU* |
