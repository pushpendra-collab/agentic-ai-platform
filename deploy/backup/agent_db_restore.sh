#!/bin/sh
# Agent DB restore (Phase 15). AGENT DB ONLY. Restores into a NEW, EMPTY database.
#
#   agent_db_restore.sh <backup.dump> <target database>
#
# Never restores over a live database: a target that already holds tables is refused.
# Recovery is "restore into a new database, verify it (agent_db_verify.sh), then point
# the application at it" (OPERATIONS_RUNBOOK.md § 20), never "overwrite the old one".
#
# Connection: the standard libpq environment (PGHOST, PGPORT, PGUSER, PGPASSWORD or
# PGPASSFILE, PGSSLMODE); PGDATABASE is not used (the target is the second argument).
# Role: a role that may create the database (or the target exists and is empty) and the
# `vector` extension (not a trusted extension: a superuser or the managed service's admin
# role, or create it in the target beforehand). The roles named by AGENT_DB_OWNER_ROLE
# (default agentic_owner) and AGENT_DB_APP_ROLE (default agentic_app) must exist in the
# target cluster: they own the objects and receive the grants recorded in the dump.
set -eu

dump=${1:?usage: agent_db_restore.sh <backup.dump> <target database>}
target=${2:?usage: agent_db_restore.sh <backup.dump> <target database>}
owner_role=${AGENT_DB_OWNER_ROLE:-agentic_owner}
app_role=${AGENT_DB_APP_ROLE:-agentic_app}
psql_q() { psql -X -q -v ON_ERROR_STOP=1 -At "$@"; }

case "$target" in
    *[!A-Za-z0-9_]*|"") echo "REFUSED: target name must match [A-Za-z0-9_]+." >&2; exit 2 ;;
esac
[ -r "$dump" ] || { echo "REFUSED: cannot read $dump." >&2; exit 2; }

# The dump must be an Agent DB dump (pg_restore --list shows its tables).
listing=$(pg_restore --list "$dump")
for table in alembic_version approval_request audit_log conversation_thread memory_item; do
    if ! printf '%s\n' "$listing" | grep -q "TABLE public $table "; then
        echo "REFUSED: $dump has no table $table: not an Agent DB backup." >&2
        exit 3
    fi
done

for role in "$owner_role" "$app_role"; do
    exists=$(psql_q -d postgres -c "SELECT count(*) FROM pg_roles WHERE rolname = '$role'")
    if [ "$exists" != "1" ]; then
        echo "REFUSED: role $role does not exist in the target cluster (create it first)." >&2
        exit 3
    fi
done

exists=$(psql_q -d postgres -c "SELECT count(*) FROM pg_database WHERE datname = '$target'")
if [ "$exists" = "1" ]; then
    tables=$(psql_q -d "$target" -c "
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')")
    if [ "$tables" != "0" ]; then
        echo "REFUSED: database $target already holds $tables table(s). Restore only into a" >&2
        echo "new, empty database; never over a live one." >&2
        exit 4
    fi
else
    psql_q -d postgres -c "CREATE DATABASE \"$target\" OWNER \"$owner_role\""
fi

# Database-level privileges are not part of a dump: the same as docker/postgres/init.
psql_q -d "$target" -c "REVOKE ALL ON DATABASE \"$target\" FROM PUBLIC"
psql_q -d "$target" -c "GRANT CONNECT, TEMPORARY ON DATABASE \"$target\" TO \"$app_role\""

echo "restore: $dump -> $target"
pg_restore --exit-on-error --single-transaction --no-password --dbname="$target" "$dump"
echo "restore: done. Run agent_db_verify.sh before pointing anything at $target."
