"""Read-only security inspection of the ERP (system-of-record) database (Phase 9).

    $env:ERP_INSPECT_DATABASE_URL = "<from the secret store>"   # never typed into files
    python -m scripts.inspect_erp_security --expect-database <erp database name> [--json]

What it proves, from the live catalog, for the role in the URL:

* the target is the expected database and NOT the Agent DB (no `alembic_version`, not the
  host/port/database of DATABASE_URL or DATABASE_MIGRATION_URL); otherwise exit 2;
* role attributes: NOSUPERUSER, NOBYPASSRLS, no CREATEDB/CREATEROLE/REPLICATION, no
  elevated or write-all memberships, owns nothing (owners can run DDL and skip RLS);
* no INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER privilege on any relation, no CREATE on the
  database or any schema (so no DDL), no sequence UPDATE;
* INSERT/UPDATE/DELETE are refused by the server: each is planned with `EXPLAIN` (no
  ANALYZE) on readable tables. PostgreSQL checks table privileges when it starts the
  executor, also for EXPLAIN, but an EXPLAIN-only statement never runs, fires no trigger
  and is allowed inside a READ ONLY transaction. Expected answer: SQLSTATE 42501;
* RLS / FORCE RLS / policy count per readable table, the `current_setting(...)` names the
  policies use (the tenant mechanism), views that do not run as the invoker;
* Flyway state (`flyway_schema_history`), when present and readable.

Safety: the session runs with `default_transaction_read_only=on`, every statement runs in
a READ ONLY transaction that is rolled back, with statement and lock timeouts. Nothing is
written, created or altered, and no business row is read. Output never contains the
password. Exit codes: 0 = no FAIL, 1 = at least one FAIL, 2 = refused / not inspected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import asyncpg  # type: ignore[import-untyped]  # no stubs; the app reaches it via SQLAlchemy

URL_ENV = "ERP_INSPECT_DATABASE_URL"
AGENT_DB_ENVS = ("DATABASE_URL", "DATABASE_MIGRATION_URL")
APPLICATION_NAME = "agentic_erp_inspect"
INSUFFICIENT_PRIVILEGE = "42501"

Status = Literal["PASS", "FAIL", "WARN", "INFO"]

_USER_RELATIONS = """
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND left(n.nspname, 8) <> 'pg_toast' AND left(n.nspname, 7) <> 'pg_temp'
"""

IDENTITY_SQL = """
SELECT current_database() AS database,
       current_setting('server_version') AS server_version,
       current_user::text AS current_user,
       session_user::text AS session_user,
       current_setting('transaction_read_only') AS transaction_read_only,
       EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relname = 'alembic_version'
               AND relkind = 'r') AS has_alembic_version
"""

ROLE_SQL = """
SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication,
       EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s
               WHERE s.setrole = r.oid
                 AND 'default_transaction_read_only=on' = ANY (s.setconfig)) AS default_read_only
FROM pg_catalog.pg_roles r WHERE r.rolname = current_user
"""

MEMBERSHIP_SQL = """
WITH RECURSIVE m(roleid) AS (
    SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user
    UNION
    SELECT a.roleid FROM pg_catalog.pg_auth_members a JOIN m ON a.member = m.roleid
)
SELECT r.rolname::text AS name,
       (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb
        OR r.rolname IN ('pg_write_all_data', 'pg_write_server_files',
                         'pg_execute_server_program', 'pg_database_owner')) AS elevated
FROM m JOIN pg_catalog.pg_roles r ON r.oid = m.roleid
WHERE r.rolname <> current_user
ORDER BY 1
"""

OWNED_SQL = f"""
SELECT 'database ' || d.datname FROM pg_catalog.pg_database d
WHERE d.datname = current_database() AND pg_has_role(current_user, d.datdba, 'USAGE')
UNION ALL
SELECT 'schema ' || n.nspname FROM pg_catalog.pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND left(n.nspname, 3) <> 'pg_' AND pg_has_role(current_user, n.nspowner, 'USAGE')
UNION ALL
(SELECT 'relation ' || n.nspname || '.' || c.relname {_USER_RELATIONS}
   AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
   AND pg_has_role(current_user, c.relowner, 'USAGE')
 ORDER BY 1 LIMIT 20)
"""

WRITABLE_RELATIONS_SQL = f"""
SELECT n.nspname || '.' || c.relname {_USER_RELATIONS}
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND (has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER')
       OR has_any_column_privilege(c.oid, 'INSERT, UPDATE'))
ORDER BY 1
"""

SEQUENCES_SQL = f"""
SELECT n.nspname || '.' || c.relname AS name,
       has_sequence_privilege(c.oid, 'UPDATE') AS can_update,
       has_sequence_privilege(c.oid, 'USAGE') AS can_use
{_USER_RELATIONS} AND c.relkind = 'S'
ORDER BY 1
"""

CREATE_PRIVILEGES_SQL = """
SELECT has_database_privilege(current_database(), 'CREATE') AS database_create,
       has_database_privilege(current_database(), 'TEMPORARY') AS database_temp,
       ARRAY(SELECT n.nspname::text FROM pg_catalog.pg_namespace n
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND left(n.nspname, 3) <> 'pg_'
               AND has_schema_privilege(n.oid, 'CREATE') ORDER BY 1) AS create_schemas
"""

SECURITY_DEFINER_SQL = """
SELECT count(*) FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND has_function_privilege(p.oid, 'EXECUTE')
"""

READABLE_TABLES_SQL = f"""
SELECT n.nspname::text AS schema, c.relname::text AS name,
       c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls,
       (SELECT count(*) FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid)::int AS policies,
       (SELECT a.attname::text FROM pg_catalog.pg_attribute a
        WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attgenerated = '' AND a.attidentity = ''
        ORDER BY a.attnum LIMIT 1) AS probe_column
{_USER_RELATIONS}
  AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY 1, 2
"""

INVOKER_VIEWS_SQL = f"""
SELECT n.nspname || '.' || c.relname {_USER_RELATIONS}
  AND c.relkind = 'v' AND has_table_privilege(c.oid, 'SELECT')
  AND NOT coalesce('security_invoker=true' = ANY (c.reloptions), false)
  AND NOT coalesce('security_invoker=on' = ANY (c.reloptions), false)
ORDER BY 1
"""

TENANT_SETTINGS_SQL = """
SELECT DISTINCT m[1] AS setting
FROM pg_catalog.pg_policies p,
     regexp_matches(coalesce(p.qual, '') || ' ' || coalesce(p.with_check, ''),
                    'current_setting\\(\\s*''([^'']+)''', 'g') AS m
WHERE p.schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1
"""

FLYWAY_TABLES_SQL = f"""
SELECT n.nspname::text AS schema, has_table_privilege(c.oid, 'SELECT') AS readable
{_USER_RELATIONS} AND c.relname = 'flyway_schema_history' AND c.relkind = 'r'
ORDER BY 1
"""


def ident(name: str) -> str:
    """Quote an identifier (names come from the catalog, never from user input)."""
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class TableRls:
    schema: str
    name: str
    rls: bool
    force_rls: bool
    policies: int
    probe_column: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class Probe:
    relation: str
    operation: Literal["INSERT", "UPDATE", "DELETE"]
    sqlstate: str | None  # None = the server accepted the statement (privilege present)


@dataclass(frozen=True)
class FlywayState:
    schema: str
    readable: bool
    latest_version: str | None = None
    applied: int | None = None
    failed: int | None = None


@dataclass
class Facts:
    database: str
    server_version: str
    current_user: str
    session_user: str
    transaction_read_only: str
    has_alembic_version: bool
    superuser: bool = False
    bypass_rls: bool = False
    create_db: bool = False
    create_role: bool = False
    replication: bool = False
    role_default_read_only: bool = False
    memberships: list[tuple[str, bool]] = field(default_factory=list)
    owned: list[str] = field(default_factory=list)
    writable_relations: list[str] = field(default_factory=list)
    updatable_sequences: list[str] = field(default_factory=list)
    usable_sequences: int = 0
    database_create: bool = False
    database_temp: bool = False
    create_schemas: list[str] = field(default_factory=list)
    security_definer_functions: int = 0
    readable_tables: list[TableRls] = field(default_factory=list)
    definer_views: list[str] = field(default_factory=list)
    tenant_settings: list[str] = field(default_factory=list)
    flyway: list[FlywayState] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    check: str
    status: Status
    detail: str


class InspectionRefused(RuntimeError):
    """The target is not (provably) the expected ERP database; nothing was inspected."""


# --- target identity (before connecting) ------------------------------------------------


def plain_url(url: str) -> str:
    scheme, sep, rest = url.strip().partition("://")
    if not sep or scheme.lower() not in ("postgres", "postgresql", "postgresql+asyncpg"):
        raise InspectionRefused("the ERP URL must be a postgresql:// URL")
    return f"postgresql://{rest}"


def endpoint(url: str) -> tuple[str, int, str]:
    parts = urlsplit(plain_url(url))
    return (parts.hostname or "").lower(), parts.port or 5432, parts.path.lstrip("/")


def describe(url: str) -> str:
    """host:port/database as user, without the password."""
    parts = urlsplit(plain_url(url))
    host, port, database = endpoint(url)
    return f"{host}:{port}/{database} as {parts.username or '(default user)'}"


def refuse_agent_database_url(url: str, environ: dict[str, str]) -> None:
    target = endpoint(url)
    for name in AGENT_DB_ENVS:
        other = environ.get(name, "").strip()
        if other and endpoint(other) == target:
            raise InspectionRefused(
                f"the ERP URL names the same host, port and database as {name} (the Agent DB)"
            )


# --- collection (read only) -------------------------------------------------------------


Operation = Literal["INSERT", "UPDATE", "DELETE"]


async def _probe(conn: asyncpg.Connection, relation: str, operation: Operation, sql: str) -> Probe:
    try:
        async with conn.transaction():  # savepoint: a refused probe does not end the outer one
            await conn.execute(sql)
    except asyncpg.PostgresError as exc:
        return Probe(relation, operation, str(exc.sqlstate))
    return Probe(relation, operation, None)


async def collect(conn: asyncpg.Connection, expected_database: str, probe_limit: int) -> Facts:
    """Everything runs in one READ ONLY transaction that is always rolled back."""
    transaction = conn.transaction(readonly=True, isolation="repeatable_read")
    await transaction.start()
    try:
        row = await conn.fetchrow(IDENTITY_SQL)
        facts = Facts(**dict(row))
        if facts.database != expected_database:
            raise InspectionRefused(
                f"connected database is '{facts.database}', expected '{expected_database}'"
            )
        if facts.has_alembic_version:
            raise InspectionRefused(
                "the database has an alembic_version table: this is the Agent DB, not the ERP"
            )

        role = await conn.fetchrow(ROLE_SQL)
        if role is not None:
            facts.superuser, facts.bypass_rls = role["rolsuper"], role["rolbypassrls"]
            facts.create_db, facts.create_role = role["rolcreatedb"], role["rolcreaterole"]
            facts.replication = role["rolreplication"]
            facts.role_default_read_only = role["default_read_only"]
        facts.memberships = [(r["name"], r["elevated"]) for r in await conn.fetch(MEMBERSHIP_SQL)]
        facts.owned = [r[0] for r in await conn.fetch(OWNED_SQL)]
        facts.writable_relations = [r[0] for r in await conn.fetch(WRITABLE_RELATIONS_SQL)]
        sequences = await conn.fetch(SEQUENCES_SQL)
        facts.updatable_sequences = [r["name"] for r in sequences if r["can_update"]]
        facts.usable_sequences = sum(1 for r in sequences if r["can_use"])
        create = await conn.fetchrow(CREATE_PRIVILEGES_SQL)
        if create is not None:
            facts.database_create = create["database_create"]
            facts.database_temp = create["database_temp"]
            facts.create_schemas = list(create["create_schemas"])
        facts.security_definer_functions = int(await conn.fetchval(SECURITY_DEFINER_SQL) or 0)
        facts.readable_tables = [TableRls(**dict(r)) for r in await conn.fetch(READABLE_TABLES_SQL)]
        facts.definer_views = [r[0] for r in await conn.fetch(INVOKER_VIEWS_SQL)]
        facts.tenant_settings = [r["setting"] for r in await conn.fetch(TENANT_SETTINGS_SQL)]

        for found in await conn.fetch(FLYWAY_TABLES_SQL):
            state = FlywayState(found["schema"], found["readable"])
            if state.readable:
                history = f"{ident(state.schema)}.flyway_schema_history"
                summary = await conn.fetchrow(
                    f"SELECT (SELECT version FROM {history} WHERE version IS NOT NULL AND success"
                    f"        ORDER BY installed_rank DESC LIMIT 1) AS latest,"
                    f"       count(*) FILTER (WHERE success)::int AS applied,"
                    f"       count(*) FILTER (WHERE NOT success)::int AS failed FROM {history}"
                )
                if summary is not None:
                    state = FlywayState(
                        state.schema, True, summary["latest"], summary["applied"], summary["failed"]
                    )
            facts.flyway.append(state)

        for table in facts.readable_tables[:probe_limit]:
            target = f"{ident(table.schema)}.{ident(table.name)}"
            facts.probes.append(
                await _probe(
                    conn, table.qualified, "INSERT", f"EXPLAIN INSERT INTO {target} DEFAULT VALUES"
                )
            )
            if table.probe_column is not None:
                column = ident(table.probe_column)
                facts.probes.append(
                    await _probe(
                        conn,
                        table.qualified,
                        "UPDATE",
                        f"EXPLAIN UPDATE {target} SET {column} = {column} WHERE false",
                    )
                )
            facts.probes.append(
                await _probe(
                    conn, table.qualified, "DELETE", f"EXPLAIN DELETE FROM {target} WHERE false"
                )
            )
        return facts
    finally:
        await transaction.rollback()


async def inspect(
    url: str, expected_database: str, *, probe_limit: int = 25, connect_timeout: float = 10.0
) -> Facts:
    conn = await asyncpg.connect(
        plain_url(url),
        timeout=connect_timeout,
        server_settings={
            "application_name": APPLICATION_NAME,
            "default_transaction_read_only": "on",
            "statement_timeout": "10000",
            "lock_timeout": "1000",
            "idle_in_transaction_session_timeout": "30000",
        },
    )
    try:
        return await collect(conn, expected_database, probe_limit)
    finally:
        await conn.close()


# --- evaluation (pure) ------------------------------------------------------------------


def _names(items: Sequence[str], limit: int = 8) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def evaluate(facts: Facts) -> list[Finding]:
    out: list[Finding] = []

    def add(check: str, status: Status, detail: str) -> None:
        out.append(Finding(check, status, detail))

    add(
        "identity",
        "INFO",
        f"database={facts.database} server={facts.server_version} "
        f"current_user={facts.current_user} session_user={facts.session_user}",
    )
    add("identity.not_agent_db", "PASS", "no alembic_version table")
    add(
        "session.read_only",
        "PASS" if facts.transaction_read_only == "on" else "FAIL",
        f"transaction_read_only={facts.transaction_read_only}",
    )
    add(
        "role.default_read_only",
        "PASS" if facts.role_default_read_only else "WARN",
        "role has default_transaction_read_only=on"
        if facts.role_default_read_only
        else "role does not set default_transaction_read_only=on (defence in depth)",
    )

    add("role.nosuperuser", "FAIL" if facts.superuser else "PASS", f"rolsuper={facts.superuser}")
    add(
        "role.nobypassrls",
        "FAIL" if facts.bypass_rls else "PASS",
        f"rolbypassrls={facts.bypass_rls}",
    )
    extra = [
        n
        for n, on in (
            ("CREATEDB", facts.create_db),
            ("CREATEROLE", facts.create_role),
            ("REPLICATION", facts.replication),
        )
        if on
    ]
    add(
        "role.attributes",
        "FAIL" if extra else "PASS",
        f"privileged attributes: {', '.join(extra)}"
        if extra
        else "no CREATEDB/CREATEROLE/REPLICATION",
    )
    elevated = [n for n, flag in facts.memberships if flag]
    add(
        "role.memberships",
        "FAIL" if elevated else "PASS",
        f"elevated memberships: {_names(elevated)}"
        if elevated
        else f"memberships: {_names([n for n, _ in facts.memberships]) or 'none'}",
    )
    add(
        "role.owns_nothing",
        "FAIL" if facts.owned else "PASS",
        f"owns: {_names(facts.owned)}" if facts.owned else "owns no database, schema or relation",
    )

    add(
        "privilege.no_table_writes",
        "FAIL" if facts.writable_relations else "PASS",
        f"write privilege on: {_names(facts.writable_relations)}"
        if facts.writable_relations
        else "no INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER on any relation",
    )
    if facts.updatable_sequences:
        add("privilege.sequences", "FAIL", f"UPDATE on: {_names(facts.updatable_sequences)}")
    elif facts.usable_sequences:
        add(
            "privilege.sequences",
            "WARN",
            f"USAGE on {facts.usable_sequences} sequence(s): nextval() would advance them",
        )
    else:
        add("privilege.sequences", "PASS", "no sequence UPDATE or USAGE")
    ddl = facts.database_create or bool(facts.create_schemas) or bool(facts.owned)
    add(
        "privilege.no_ddl",
        "FAIL" if ddl else "PASS",
        f"database CREATE={facts.database_create}, schema CREATE: "
        f"{_names(facts.create_schemas) or 'none'}, owned objects: {len(facts.owned)}",
    )
    add(
        "privilege.temporary",
        "INFO",
        f"TEMPORARY={facts.database_temp} (session-local temp tables only)",
    )
    add(
        "privilege.security_definer",
        "WARN" if facts.security_definer_functions else "PASS",
        f"{facts.security_definer_functions} executable SECURITY DEFINER function(s): review "
        "that none writes"
        if facts.security_definer_functions
        else "no executable SECURITY DEFINER functions",
    )

    if not facts.probes:
        add("probe.writes_refused", "WARN", "no readable table to probe")
    else:
        accepted = [f"{p.operation} {p.relation}" for p in facts.probes if p.sqlstate is None]
        other = [
            f"{p.operation} {p.relation} ({p.sqlstate})"
            for p in facts.probes
            if p.sqlstate not in (None, INSUFFICIENT_PRIVILEGE)
        ]
        if accepted:
            add("probe.writes_refused", "FAIL", f"server accepted: {_names(accepted)}")
        elif other:
            add("probe.writes_refused", "WARN", f"inconclusive: {_names(other)}")
        else:
            tables = len({p.relation for p in facts.probes})
            add(
                "probe.writes_refused",
                "PASS",
                f"{len(facts.probes)} INSERT/UPDATE/DELETE plans on {tables} table(s) "
                f"refused with {INSUFFICIENT_PRIVILEGE}",
            )

    readable = facts.readable_tables
    no_rls = [t.qualified for t in readable if not t.rls]
    add(
        "rls.readable_tables",
        "WARN" if not readable else "INFO",
        f"{len(readable)} readable table(s); RLS enabled on {sum(t.rls for t in readable)}, "
        f"FORCE on {sum(t.force_rls for t in readable)}",
    )
    add(
        "rls.enabled",
        "WARN" if no_rls else "PASS",
        f"readable WITHOUT RLS (confirm with the ERP owner): {_names(no_rls)}"
        if no_rls
        else "every readable table has RLS enabled",
    )
    no_policy = [t.qualified for t in readable if t.rls and t.policies == 0]
    if no_policy:
        add("rls.policies", "INFO", f"RLS without policies (deny all): {_names(no_policy)}")
    add(
        "rls.views",
        "WARN" if facts.definer_views else "PASS",
        f"readable views run as their owner (RLS of the owner applies): "
        f"{_names(facts.definer_views)}"
        if facts.definer_views
        else "no readable owner-rights views",
    )
    add(
        "rls.tenant_settings",
        "INFO" if facts.tenant_settings else "WARN",
        f"policies use current_setting: {_names(facts.tenant_settings)}"
        if facts.tenant_settings
        else "no policy uses current_setting(...): tenant mechanism not visible in the catalog",
    )

    if not facts.flyway:
        add("flyway", "WARN", "no flyway_schema_history table found")
    for state in facts.flyway:
        if not state.readable:
            add(
                "flyway", "WARN", f"{state.schema}.flyway_schema_history exists but is not readable"
            )
        else:
            add(
                "flyway",
                "WARN" if state.failed else "INFO",
                f"{state.schema}.flyway_schema_history: latest={state.latest_version} "
                f"applied={state.applied} failed={state.failed}",
            )
    return out


# --- command line -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.inspect_erp_security",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    parser.add_argument(
        "--expect-database", required=True, help="the ERP database name agreed with the ERP owner"
    )
    parser.add_argument(
        "--probe-limit",
        type=int,
        default=25,
        help="readable tables to probe with EXPLAIN INSERT/UPDATE/DELETE",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    env = dict(os.environ) if environ is None else environ

    url = env.get(URL_ENV, "").strip()
    if not url:
        print(f"REFUSED: {URL_ENV} is not set (load it from the secret store)", file=sys.stderr)
        return 2
    try:
        refuse_agent_database_url(url, env)
        target = describe(url)
        facts = asyncio.run(inspect(url, args.expect_database, probe_limit=args.probe_limit))
    except InspectionRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        # Type only: driver messages can contain the host or user, never print the URL.
        print(f"NOT INSPECTED: {type(exc).__name__}", file=sys.stderr)
        return 2

    findings = evaluate(facts)
    failed = any(f.status == "FAIL" for f in findings)
    if args.json:
        print(
            json.dumps(
                {
                    "target": target,
                    "verdict": "FAIL" if failed else "PASS",
                    "findings": [asdict(f) for f in findings],
                    "probes": [asdict(p) for p in facts.probes],
                },
                indent=2,
            )
        )
    else:
        print(f"ERP security inspection: {target}")
        for f in findings:
            print(f"{f.status:<5} {f.check:<28} {f.detail}")
        print(f"VERDICT: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
