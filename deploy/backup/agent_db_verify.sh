#!/bin/sh
# Agent DB restore verification (Phase 15).
#
#   agent_db_verify.sh <backup.dump.manifest> <restored database> [--strict]
#
# Compares a restored database with the manifest written by agent_db_backup.sh:
#   * the migration revision must be equal (the application checks it too: /ready reports
#     `degraded` when the schema is not at the head of the running version);
#   * the same tables must exist, with the same row-level-security flag;
#   * the append-only audit triggers must exist;
#   * the runtime role must be able to connect;
#   * row counts: reported; with --strict they must be equal (use --strict when nothing
#     wrote during the backup, as in the smoke test).
# Exit 0 = verified, 1 = a difference. Read-only: it changes nothing.
set -eu

manifest=${1:?usage: agent_db_verify.sh <manifest> <database> [--strict]}
target=${2:?usage: agent_db_verify.sh <manifest> <database> [--strict]}
strict=${3:-}
app_role=${AGENT_DB_APP_ROLE:-agentic_app}
psql_q() { psql -X -q -v ON_ERROR_STOP=1 -At -d "$target" "$@"; }
failures=0
fail() { echo "MISMATCH: $*"; failures=$((failures + 1)); }

expected_revision=$(sed -n 's/^revision=//p' "$manifest")
actual_revision=$(psql_q -c "SELECT version_num FROM alembic_version")
[ "$expected_revision" = "$actual_revision" ] \
    || fail "revision $actual_revision, expected $expected_revision"

grep '^table=' "$manifest" | sed 's/^table=//' > /tmp/agent_db_expected.$$
psql_q -F '|' -c "
    SELECT c.relname, c.relrowsecurity,
           (xpath('/row/n/text()',
                  query_to_xml(format('SELECT count(*) AS n FROM public.%I', c.relname),
                               false, true, '')))[1]::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ORDER BY c.relname" > /tmp/agent_db_actual.$$

cut -d'|' -f1,2 /tmp/agent_db_expected.$$ > /tmp/agent_db_expected_shape.$$
cut -d'|' -f1,2 /tmp/agent_db_actual.$$ > /tmp/agent_db_actual_shape.$$
if ! cmp -s /tmp/agent_db_expected_shape.$$ /tmp/agent_db_actual_shape.$$; then
    fail "tables or row-level-security flags differ:"
    diff /tmp/agent_db_expected_shape.$$ /tmp/agent_db_actual_shape.$$ || true
fi
if cmp -s /tmp/agent_db_expected.$$ /tmp/agent_db_actual.$$; then
    echo "row counts: equal"
else
    echo "row counts differ (expected | restored):"
    diff /tmp/agent_db_expected.$$ /tmp/agent_db_actual.$$ || true
    [ "$strict" = "--strict" ] && fail "row counts differ (--strict)"
fi
rm -f /tmp/agent_db_expected.$$ /tmp/agent_db_actual.$$ \
      /tmp/agent_db_expected_shape.$$ /tmp/agent_db_actual_shape.$$

triggers=$(psql_q -c "SELECT count(*) FROM pg_trigger WHERE tgname IN
                      ('audit_log_no_update_delete', 'audit_log_no_truncate')")
[ "$triggers" = "2" ] || fail "audit_log append-only triggers missing ($triggers of 2)"

can_connect=$(psql_q -c "SELECT has_database_privilege('$app_role', current_database(), 'CONNECT')")
[ "$can_connect" = "t" ] || fail "role $app_role cannot connect"

if [ "$failures" -eq 0 ]; then
    echo "VERIFIED: $target matches $manifest (revision $actual_revision)."
    exit 0
fi
echo "NOT VERIFIED: $failures difference(s)."
exit 1
