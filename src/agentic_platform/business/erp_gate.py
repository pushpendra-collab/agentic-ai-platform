"""The ERP security gate the live adapter passes before its first business read
(Phase 14, ADR-063).

`ReadOnlyErpDatabase.verify()` already proves (Phase 10): the expected database name,
not the Agent DB, a read-only session, no superuser, no BYPASSRLS. This module adds the
checks the brief requires, read from the catalog with the *effective* privileges of the
connected role (never trusted from configuration):

* role attributes: no CREATEDB / CREATEROLE / REPLICATION, no elevated membership
  (`pg_write_all_data`, superuser or BYPASSRLS roles, ...);
* no write privilege on any relation (table INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER or
  column INSERT/UPDATE), no CREATE on the database or any schema, owns no relation;
* with a contract (`erp_contract.py`): the PostgreSQL major version, every mapped
  relation and column with its exact catalog type, SELECT on each, and the tenant and
  warehouse isolation mechanism the owner confirmed (the tenant column on every mapped
  relation, or RLS enabled with policies that read the confirmed setting).

The checks are catalog reads only (`ErpQuery`, parameterised; privilege names are bound
parameters, so the statement guard's keyword rule holds). The evaluation is a pure
function, so it is unit-tested without a database and integration-tested on a throw-away
PostgreSQL. Any problem refuses the connection: the adapter fails closed.

The operator inspector (`scripts/inspect_erp_security.py`, ADR-058) remains the full,
human-readable report; this gate is the compact runtime subset, reusing its checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentic_platform.business.adapters.sql_guard import ErpQuery
from agentic_platform.business.erp_contract import ErpContract

_USER_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') AND left(n.nspname, 3) <> 'pg_'"
)

PRIVILEGES_QUERY = ErpQuery(
    "erp.gate.privileges",
    "WITH RECURSIVE m(roleid) AS ("
    " SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user"
    " UNION SELECT a.roleid FROM pg_catalog.pg_auth_members a JOIN m ON a.member = m.roleid)"
    " SELECT r.rolcreatedb AS create_db, r.rolcreaterole AS create_role,"
    " r.rolreplication AS replication,"
    " current_setting('server_version_num')::int AS server_version_num,"
    " has_database_privilege(current_database(), :create_privilege) AS database_create,"
    " (SELECT count(*) FROM pg_catalog.pg_namespace n WHERE "
    + _USER_SCHEMAS
    + " AND has_schema_privilege(n.oid, :create_privilege))::int AS create_schemas,"
    " (SELECT count(*) FROM pg_catalog.pg_class c"
    " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE "
    + _USER_SCHEMAS
    + " AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
    " AND (has_table_privilege(c.oid, :table_writes)"
    " OR has_any_column_privilege(c.oid, :column_writes)))::int AS writable_relations,"
    " (SELECT count(*) FROM pg_catalog.pg_class c"
    " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE "
    + _USER_SCHEMAS
    + " AND pg_has_role(current_user, c.relowner, 'USAGE'))::int AS owned_relations,"
    " (SELECT count(*) FROM m JOIN pg_catalog.pg_roles e ON e.oid = m.roleid"
    " WHERE e.rolname <> current_user AND (e.rolsuper OR e.rolbypassrls OR e.rolcreaterole"
    " OR e.rolcreatedb OR e.rolname IN ('pg_write_all_data', 'pg_write_server_files',"
    " 'pg_execute_server_program', 'pg_database_owner')))::int AS elevated_memberships"
    " FROM pg_catalog.pg_roles r WHERE r.rolname = current_user",
)
PRIVILEGE_PARAMS: dict[str, str] = {
    "create_privilege": "CREATE",
    "table_writes": "INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER",
    "column_writes": "INSERT, UPDATE",
}

SCHEMA_QUERY = ErpQuery(
    "erp.gate.schema",
    "SELECT n.nspname || '.' || c.relname AS relation, c.relkind::text AS relkind,"
    " c.relrowsecurity AS rls, has_table_privilege(c.oid, :select_privilege) AS readable,"
    " a.attname::text AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type"
    " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    " LEFT JOIN pg_catalog.pg_attribute a"
    " ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
    " WHERE n.nspname || '.' || c.relname = ANY (CAST(:relations AS text[]))",
)

POLICIES_QUERY = ErpQuery(
    "erp.gate.policies",
    "SELECT p.schemaname || '.' || p.tablename AS relation, count(*)::int AS policies,"
    " count(*) FILTER (WHERE coalesce(p.qual, '') LIKE :setting_pattern)::int AS tenant_policies"
    " FROM pg_catalog.pg_policies p"
    " WHERE p.schemaname || '.' || p.tablename = ANY (CAST(:relations AS text[]))"
    " GROUP BY 1",
)

# Multi-database contracts: the tenant policies call a function (InvenLogic:
# `invenlogic_company_id()`) and only the function reads the setting. Proven here.
FUNCTION_QUERY = ErpQuery(
    "erp.gate.policy_function",
    "SELECT count(*)::int AS functions FROM pg_catalog.pg_proc p"
    " JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
    " WHERE p.proname = :function AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
    " AND p.prosrc LIKE :setting_pattern",
)

_KINDS = {"table": {"r", "p"}, "view": {"v", "m"}}


def gate_params(
    contract: ErpContract | None, database: str | None = None
) -> dict[str, dict[str, Any]]:
    """Bound parameters of the schema, policy and function queries for a contract (with
    `database`, for the relations of that database key only)."""
    relations = [r.qualified for r in contract.relations(database)] if contract else []
    isolation = contract.tenant_isolation if contract else None
    setting = isolation.setting if isolation is not None else None
    function = isolation.policy_function if isolation is not None else None
    setting_pattern = f"%current_setting('{setting}'%" if setting else "%"
    return {
        "schema": {"relations": relations, "select_privilege": "SELECT"},
        "policies": {
            "relations": relations,
            "setting_pattern": f"%{function}()%" if function else setting_pattern,
        },
        "function": {"function": function or "", "setting_pattern": setting_pattern},
    }


def evaluate_privileges(row: Mapping[str, Any] | None) -> list[str]:
    """Problems with the role's effective privileges (no contract needed)."""
    if row is None:
        return ["no role row for the current user"]
    problems = [
        message
        for failed, message in (
            (bool(row["create_db"]), "role has CREATEDB"),
            (bool(row["create_role"]), "role has CREATEROLE"),
            (bool(row["replication"]), "role has REPLICATION"),
            (bool(row["database_create"]), "role may CREATE in the database"),
            (int(row["create_schemas"]) > 0, "role may CREATE in a schema"),
            (int(row["writable_relations"]) > 0, "role has write privileges on relations"),
            (int(row["owned_relations"]) > 0, "role owns relations"),
            (int(row["elevated_memberships"]) > 0, "role is a member of an elevated role"),
        )
        if failed
    ]
    return problems


def evaluate_contract_schema(
    contract: ErpContract,
    privileges: Mapping[str, Any],
    schema_rows: Sequence[Mapping[str, Any]],
    policy_rows: Sequence[Mapping[str, Any]],
    *,
    database: str | None = None,
    function_rows: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Problems between the live catalog and the verified contract. With `database`, the
    catalog is that database key's and only its relations are checked."""
    problems: list[str] = []
    identity = contract.database_identity(database)
    if identity is not None:
        major = int(privileges["server_version_num"]) // 10000
        if major != identity.postgres_major_version:
            problems.append(f"PostgreSQL {major}, contract says {identity.postgres_major_version}")
    found: dict[str, dict[str, Any]] = {}
    for row in schema_rows:
        entry = found.setdefault(
            row["relation"],
            {"relkind": row["relkind"], "rls": row["rls"], "readable": row["readable"],
             "columns": {}},
        )  # fmt: skip
        if row["column_name"] is not None:
            entry["columns"][row["column_name"]] = str(row["data_type"]).lower()
    mine = {r.qualified for r in contract.relations(database)}
    for relation in contract.relations(database):
        if relation.qualified not in found:
            problems.append(f"{relation.qualified}: not found")
            continue
        entry = found[relation.qualified]
        if entry["relkind"] not in _KINDS[relation.kind]:
            problems.append(f"{relation.qualified}: not a {relation.kind}")
        if not entry["readable"]:
            problems.append(f"{relation.qualified}: no SELECT privilege")
    for mapping in contract.capabilities.values():
        here = {r.qualified for r in mapping.sources if r.database == database} & mine
        for column in mapping.columns:
            if column.relation not in found or column.relation not in here:
                continue
            actual = found[column.relation]["columns"].get(column.column)
            if actual is None:
                problems.append(f"{column.relation}.{column.column}: column not found")
            elif actual != column.data_type.lower():
                problems.append(
                    f"{column.relation}.{column.column}: type {actual}, contract {column.data_type}"
                )
    problems += _tenant_problems(contract, found, policy_rows, database, function_rows)
    problems += _warehouse_problems(contract)
    return problems


def _tenant_problems(
    contract: ErpContract,
    found: Mapping[str, Mapping[str, Any]],
    policy_rows: Sequence[Mapping[str, Any]],
    database: str | None = None,
    function_rows: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    isolation = contract.tenant_isolation
    if isolation is None:
        return ["tenant isolation is not defined: cannot be proven"]
    problems: list[str] = []
    if isolation.mechanism == "column":
        for relation in contract.relations(database):
            columns = found.get(relation.qualified, {}).get("columns", {})
            if relation.qualified in found and isolation.column not in columns:
                problems.append(f"{relation.qualified}: no tenant column {isolation.column}")
        for key, mapping in contract.capabilities.items():
            tenant = [c for c in mapping.columns if c.role == "tenant"]
            if not tenant or any(c.column != isolation.column for c in tenant):
                problems.append(f"{key}: tenant column is not the confirmed {isolation.column}")
    else:
        policies = {row["relation"]: row for row in policy_rows}
        reads = (
            f"{isolation.policy_function}()" if isolation.policy_function else isolation.setting
        )
        for relation in contract.relations(database):
            entry = found.get(relation.qualified)
            if entry is None or relation.kind != "table":
                continue
            if not entry["rls"]:
                problems.append(f"{relation.qualified}: row-level security is not enabled")
            if int(policies.get(relation.qualified, {}).get("tenant_policies", 0)) == 0:
                problems.append(f"{relation.qualified}: no RLS policy reads {reads}")
        if isolation.policy_function is not None and not any(
            int(row.get("functions", 0)) > 0 for row in function_rows
        ):
            problems.append(
                f"function {isolation.policy_function}() does not read {isolation.setting}"
            )
    return problems


def _warehouse_problems(contract: ErpContract) -> list[str]:
    isolation = contract.warehouse_isolation
    stock = [
        m for k, m in contract.capabilities.items()
        if k in ("product_stock", "warehouse_stock", "low_stock_products")
    ]  # fmt: skip
    if not stock:
        return []
    if isolation is None:
        return ["warehouse isolation is not defined: cannot be proven"]
    return [
        f"{m.capability}: warehouse column is not the confirmed {isolation.column}"
        for m in stock
        if not any(c.role == "warehouse" and c.column == isolation.column for c in m.columns)
    ]


__all__ = [
    "FUNCTION_QUERY",
    "POLICIES_QUERY",
    "PRIVILEGES_QUERY",
    "PRIVILEGE_PARAMS",
    "SCHEMA_QUERY",
    "evaluate_contract_schema",
    "evaluate_privileges",
    "gate_params",
]
