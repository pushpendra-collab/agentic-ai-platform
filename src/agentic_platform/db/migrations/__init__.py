"""Alembic migration scripts (`script_location = agentic_platform.db:migrations`)."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

SCRIPT_LOCATION = "agentic_platform.db:migrations"


def alembic_config(url: str | None = None) -> Config:
    """Programmatic Alembic config (tests, health checks, deploy scripts)."""
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    if url is not None:
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


# --- Phase 8: never migrate a database this application does not own ---------------------
#
# The Agent DB is separate from any system of record (for example an existing ERP database).
# A migration job pointed at the wrong URL must not create tables, extensions or roles in
# such a database. Before the first migration (no `alembic_version` table yet), the target
# must therefore be empty: no user tables outside the system schemas, except tables owned
# by extensions. A non-empty database without our version table is refused.

FOREIGN_DATABASE_OVERRIDE_ENV = "AGENTIC_MIGRATE_NON_EMPTY_DATABASE"

VERSION_TABLE_SQL = "SELECT to_regclass('alembic_version') IS NOT NULL"

FOREIGN_TABLES_SQL = """
SELECT n.nspname || '.' || c.relname
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND left(n.nspname, 8) <> 'pg_toast'
  AND left(n.nspname, 7) <> 'pg_temp'
  AND NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_depend d
      WHERE d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
  )
ORDER BY 1
LIMIT 5
"""


class ForeignDatabaseError(RuntimeError):
    """The migration target already holds tables but was never migrated by this application."""


def refuse_foreign_database(has_version_table: bool, foreign_tables: list[str],
                            override: str | None) -> None:  # fmt: skip
    """Raise unless the target is ours (version table present) or empty.

    Table names are reported (they are schema metadata, not data) so an operator can see
    which database the job was pointed at."""
    if has_version_table or not foreign_tables or override == "1":
        return
    raise ForeignDatabaseError(
        "Refusing to migrate: the target database has tables but no alembic_version table, "
        f"so it is not this application's database (found: {', '.join(foreign_tables)}). "
        "Check DATABASE_MIGRATION_URL: it must name the Agent DB, never a system of record "
        f"such as the ERP database. Override only for a deliberate adoption: "
        f"{FOREIGN_DATABASE_OVERRIDE_ENV}=1."
    )
