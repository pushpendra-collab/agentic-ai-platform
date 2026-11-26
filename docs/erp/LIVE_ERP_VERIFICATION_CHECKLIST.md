# Live ERP Verification Checklist

> **For:** the ERP database owner, the ERP application owner and the people who sign off go-live.
> **Rule (ADR-057, ADR-058):** a line is VERIFIED only with evidence from a run against the real ERP. Nothing below is guessed: every value the ERP owner has not supplied is marked **TO BE SUPPLIED**.
> **Last updated:** 2026-10-05 (Phase 14). Status: **ERP LIVE ACCESS: BLOCKED** (open question Q1). Phase 14 added the contract ([ERP_CONTRACT.md](ERP_CONTRACT.md)) and `python -m scripts.erp_readiness`, which runs § 3 and the adapter's security gate in one step once the contract exists. Phase 18 (2026-10-10, documentation only) re-checked the gate (still BLOCKED; no ERP contacted) and added the capability matrix, owner decisions and ordered plan to [ERP_CONTRACT.md](ERP_CONTRACT.md) § 5–§ 11.

## 1. Status at the end of Phase 9

| Area | Status | Why |
|---|---|---|
| ERP owner / access contract (§ 2) | **BLOCKED** | no owner, host, database, role or tenant mechanism has been supplied (Q1) |
| ERP security inspection (§ 3) | **BLOCKED** (tool ready) | `scripts/inspect_erp_security.py` exists and is verified against a throw-away PostgreSQL database; it has never run against the ERP |
| Live schema mappings (§ 4) | **BLOCKED** | needs § 3 first; see [../SCHEMA_INTEGRATION.md](../SCHEMA_INTEGRATION.md) |
| Business read layer, business tools, business query agent | **FOUNDATION IMPLEMENTED (Phase 10), MOCK VERIFIED; live NOT VERIFIED** | contracts, fake adapter and read-only boundary exist ([../BUSINESS_INTEGRATION.md](../BUSINESS_INTEGRATION.md)); no schema mapping, so `ERP_ADAPTER=postgres` answers RULE_NOT_VERIFIED |
| Tenant isolation on ERP data | **BLOCKED** | needs § 2 (tenant mechanism) and two real test tenants/users |
| ERP writes | **NOT APPLICABLE** | no ERP write path exists; none is planned before a separate, approved phase |

A local PostgreSQL container `inventoryai-postgres` (compose project `inven-logic`, `C:\Users\Dell\Documents\Abc\inven-logic`, host port 5433, seed data mounted) exists on the development machine. It may be a development copy of the ERP, but **nobody has confirmed that**. It wasn't started or connected to in Phase 9. The ERP owner must say whether it represents the production schema before anyone uses it, even for schema discovery.

## 2. Access contract (supplied by the ERP owner; no secrets in this file)

| Item | Value | Status |
|---|---|---|
| ERP database owner (person/team) | TO BE SUPPLIED | BLOCKED |
| ERP application owner (person/team) | TO BE SUPPLIED | BLOCKED |
| Database host / port | TO BE SUPPLIED | BLOCKED |
| Database name (`--expect-database`) | TO BE SUPPLIED | BLOCKED |
| PostgreSQL version | read from the catalog by the inspector | BLOCKED |
| Connection method (private network, VPN, bastion, PgBouncer) | TO BE SUPPLIED | BLOCKED |
| Read-only role name | TO BE SUPPLIED (created and owned by the ERP DBA, not by this project) | BLOCKED |
| Authentication method (password, client certificate, IAM token) | TO BE SUPPLIED | BLOCKED |
| TLS mode (`sslmode=verify-full` expected) and CA | TO BE SUPPLIED | BLOCKED |
| Tenant identification mechanism (column, `current_setting` name, schema per tenant, database per tenant) | TO BE SUPPLIED; the inspector lists the `current_setting(...)` names that RLS policies use | BLOCKED |
| Warehouse scope mechanism | TO BE SUPPLIED | BLOCKED |
| RLS policy ownership (who writes and changes policies) | TO BE SUPPLIED | BLOCKED |
| Allowed source IP/network of the agent platform | TO BE SUPPLIED (follows the deployment target, Q3) | BLOCKED |
| Firewall / VPN / private-link requirements | TO BE SUPPLIED | BLOCKED |
| Connection timeout | proposal: 5 s (same as the Agent DB); owner to confirm | BLOCKED |
| Statement timeout | proposal: ≤ 10 s, set on the role (`ALTER ROLE ... SET statement_timeout`); owner to confirm | BLOCKED |
| Where the credential lives | a secret store entry injected as `ERP_INSPECT_DATABASE_URL` (inspection) | BLOCKED |

**Required role shape** (what § 3 checks): `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION`; `SELECT` only on the agreed tables or views; no `INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER`; no `CREATE` on the database or any schema; owns no object; `ALTER ROLE <role> SET default_transaction_read_only = on`; not a member of `pg_write_all_data` or any elevated role. It must never be the ERP application's own role.

## 3. Security inspection (run first; read only)

```powershell
# from the repository root, with the credential loaded from the secret store (never typed into files)
$env:ERP_INSPECT_DATABASE_URL = "<from the secret store>"
.venv\Scripts\python -m scripts.inspect_erp_security --expect-database <erp database name>
.venv\Scripts\python -m scripts.inspect_erp_security --expect-database <erp database name> --json > erp-inspection.json
Remove-Item env:ERP_INSPECT_DATABASE_URL
```

What the inspector does and doesn't do ([OPERATIONS_RUNBOOK.md](../../OPERATIONS_RUNBOOK.md) § 15):

* Before connecting, it **refuses** if the URL names the same host, port and database as `DATABASE_URL` or `DATABASE_MIGRATION_URL` (the Agent DB). After connecting, it refuses if the database name isn't `--expect-database`, or if the database has an `alembic_version` table (the Agent DB). Exit 2, nothing inspected.
* Session `default_transaction_read_only=on`, one READ ONLY transaction, always rolled back; statement timeout 10 s, lock timeout 1 s. It reads only the catalog and `flyway_schema_history`, never a business row.
* It proves INSERT, UPDATE and DELETE are refused **by the server** with `EXPLAIN` (no `ANALYZE`). PostgreSQL checks table privileges when it starts the executor, even for EXPLAIN, but it never executes the statement and never fires a trigger. DDL is proven impossible from the catalog: there's no `CREATE` on the database or any schema, and the role owns nothing, so it can't run `CREATE`, `ALTER` or `DROP`.
* Exit 0 = no FAIL, 1 = at least one FAIL, 2 = refused or not reachable. The output never contains the password.

| # | Check (inspector id) | Expected | Status |
|---|---|---|---|
| 3.1 | Database identity (`identity`, `identity.not_agent_db`) | expected name, no `alembic_version` | BLOCKED |
| 3.2 | Server version (`identity`) | recorded | BLOCKED |
| 3.3 | Current role (`identity`) | the agreed read-only role | BLOCKED |
| 3.4 | `role.nosuperuser`, `role.nobypassrls`, `role.attributes` | PASS | BLOCKED |
| 3.5 | `role.memberships`, `role.owns_nothing` | PASS | BLOCKED |
| 3.6 | SELECT works | a SELECT through the read path (Phase 10 or later, after the read layer exists) | BLOCKED |
| 3.7 | INSERT / UPDATE / DELETE refused (`probe.writes_refused`, `privilege.no_table_writes`) | PASS (`42501`) | BLOCKED |
| 3.8 | DDL refused (`privilege.no_ddl`) | PASS | BLOCKED |
| 3.9 | Read-only default (`role.default_read_only`, `session.read_only`) | PASS | BLOCKED |
| 3.10 | RLS / FORCE RLS on tenant tables (`rls.*`) | every tenant table has RLS; FORCE where the owner role is used by the ERP app | BLOCKED |
| 3.11 | Tenant settings (`rls.tenant_settings`) | matches § 2 | BLOCKED |
| 3.12 | Flyway state (`flyway`) | latest version recorded, no failed migrations | BLOCKED |
| 3.13 | SECURITY DEFINER functions (`privilege.security_definer`) | none executable, or each reviewed | BLOCKED |

Verified in Phase 9 **against a throw-away PostgreSQL 16 database only** (`tests/integration/test_erp_inspection_postgres.py`, created and dropped by the test): a SELECT-only role passes with all INSERT/UPDATE/DELETE plans refused with `42501`. A superuser owner fails every relevant check, and its plans are never executed: rows, sequence position and catalog are identical before and after. A single `GRANT INSERT` is detected. The wrong database name and the Agent DB are refused.

## 4. After § 3 passes (later phases; not started)

1. Verify every table and column the read layer will use against the live catalog ([../SCHEMA_INTEGRATION.md](../SCHEMA_INTEGRATION.md)).
2. Agree each business rule with the ERP owner, for example what counts as "outstanding", "overdue", "low stock" or "daily sales" (time zone, cancelled and draft documents, returns). A rule that isn't agreed answers `UNAVAILABLE / RULE NOT VERIFIED`.
3. Only then build the read layer and typed tools, and run the six reference queries in PROJECT_STATUS § 3h with two real test tenants.
