#!/bin/sh
# Agent DB backup (Phase 15). AGENT DB ONLY.
#
#   agent_db_backup.sh <output.dump>
#
# Writes a PostgreSQL custom-format dump (pg_dump -Fc: one consistent snapshot) and a
# manifest next to it (<output.dump>.manifest: database, migration revision, tables with
# their row-level-security flag and row count). agent_db_verify.sh compares a restored
# copy against that manifest.
#
# Refuses any database that is not this application's Agent DB (no `alembic_version`, or
# a platform table missing), so it can never be pointed at an ERP / system of record.
#
# Connection: the standard libpq environment only (PGHOST, PGPORT, PGDATABASE, PGUSER,
# PGPASSWORD or PGPASSFILE, PGSSLMODE...). Never pass a password on the command line.
# Role: the schema OWNER (it owns the tables, so row-level security does not filter what
# it dumps; the runtime role would see one tenant at a time). pg_dump takes only ACCESS
# SHARE locks: the API keeps serving during the backup.
#
# Encryption, transfer and retention of the dump file are the deployment's job
# (OPERATIONS_RUNBOOK.md § 20): the dump contains every tenant's conversations, memory
# and audit trail.
set -eu

out=${1:?usage: agent_db_backup.sh <output.dump>}
: "${PGDATABASE:?set PGDATABASE to the Agent DB name}"
manifest="$out.manifest"
psql_q() { psql -X -q -v ON_ERROR_STOP=1 -At "$@"; }

# Platform tables that identify the Agent DB (migration head 0005).
REQUIRED_TABLES="alembic_version agent_checkpoints approval_request audit_log conversation_thread idempotency_record memory_item"

for table in $REQUIRED_TABLES; do
    present=$(psql_q -c "SELECT to_regclass('public.$table') IS NOT NULL")
    if [ "$present" != "t" ]; then
        echo "REFUSED: table public.$table is missing: '$PGDATABASE' is not the Agent DB." >&2
        echo "Check PGDATABASE/PGHOST: never back up or restore an ERP with these scripts." >&2
        exit 3
    fi
done

revision=$(psql_q -c "SELECT version_num FROM alembic_version")
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "backup: database=$PGDATABASE revision=$revision started=$started"

if [ -e "$out" ]; then
    echo "REFUSED: $out already exists (never overwrite a backup)." >&2
    exit 4
fi
umask 077
pg_dump --format=custom --no-password --lock-wait-timeout=60s --file="$out"

{
    echo "database=$PGDATABASE"
    echo "revision=$revision"
    echo "started=$started"
    echo "finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # table|rls_enabled|rows (exact counts; equal to the dump only when nothing wrote
    # during the backup: see agent_db_verify.sh --strict).
    psql_q -F '|' -c "
        SELECT 'table=' || c.relname, c.relrowsecurity,
               (xpath('/row/n/text()',
                      query_to_xml(format('SELECT count(*) AS n FROM public.%I', c.relname),
                                   false, true, '')))[1]::text
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
        ORDER BY c.relname"
} > "$manifest"

echo "backup: wrote $out ($(wc -c < "$out") bytes) and $manifest"
