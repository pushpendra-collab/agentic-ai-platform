"""Database layer tests that need no PostgreSQL server."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import exc as sa_exc

from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, DatabaseSettings, normalise_postgres_url
from agentic_platform.core.errors import (
    DatabaseConcurrencyError,
    DatabaseConflictError,
    DatabaseError,
    DatabaseIntegrityError,
    DatabaseNotConfiguredError,
    DatabasePermissionError,
    DatabaseReadOnlyError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)
from agentic_platform.db import Base, Database
from agentic_platform.db.engine import engine_options
from agentic_platform.db.errors import translate_db_error
from agentic_platform.db.migrations import SCRIPT_LOCATION, alembic_config, head_revision
from agentic_platform.db.models import PLATFORM_TABLES
from agentic_platform.services.database_health import DatabaseHealthService
from tests.fakes import ScriptedLLM

URL = "postgresql://agentic_app:s3cret-pw@localhost:5432/agentic"


# --- configuration ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@h:5432/d",
        "postgresql://u:p@h:5432/d",
        "postgresql+asyncpg://u:p@h:5432/d",
        "POSTGRESQL://u:p@h:5432/d",
    ],
)
def test_url_normalised_to_asyncpg(url: str) -> None:
    assert normalise_postgres_url(url) == "postgresql+asyncpg://u:p@h:5432/d"


@pytest.mark.parametrize(
    "url",
    ["sqlite:///x.db", "mysql://u:p@h/d", "postgresql+psycopg://u:p@h/d", "localhost:5432"],
)
def test_non_postgres_urls_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        DatabaseSettings(url=url)


def test_database_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DATABASE_POOL_SIZE", "12")
    monkeypatch.setenv("DATABASE_SSL_MODE", "require")
    s = AppSettings().database
    assert s.configured
    assert s.pool_size == 12
    assert s.runtime_url().startswith("postgresql+asyncpg://")
    assert s.migrations_url() == s.runtime_url()  # falls back to DATABASE_URL
    assert "s3cret-pw" not in repr(s)
    assert "s3cret-pw" not in str(AppSettings().model_dump())


def test_blank_values_mean_unset() -> None:
    s = DatabaseSettings(url="  ", ssl_mode="")
    assert s.ssl_mode is None
    assert not s.configured
    with pytest.raises(ValueError):
        s.runtime_url()


def test_migration_url_preferred() -> None:
    s = DatabaseSettings(url=URL, migration_url="postgresql://owner:o@localhost/agentic")
    assert s.migrations_url() == "postgresql+asyncpg://owner:o@localhost/agentic"


def test_engine_options() -> None:
    opts = engine_options(
        DatabaseSettings(
            url=URL, pool_size=3, max_overflow=2, statement_timeout_ms=900, ssl_mode="verify-full"
        )
    )
    assert (opts["pool_size"], opts["max_overflow"], opts["pool_pre_ping"]) == (3, 2, True)
    connect: dict[str, Any] = opts["connect_args"]
    assert connect["ssl"] == "verify-full"
    assert connect["server_settings"]["statement_timeout"] == "900"
    assert connect["server_settings"]["timezone"] == "UTC"
    assert "ssl" not in engine_options(DatabaseSettings(url=URL))["connect_args"]
    # Phase 8: client-side bound for hung servers/networks, above statement_timeout.
    assert engine_options(DatabaseSettings(url=URL))["connect_args"]["command_timeout"] == 20.0
    tight = engine_options(DatabaseSettings(url=URL, command_timeout_seconds=2.5))
    assert tight["connect_args"]["command_timeout"] == 2.5


def test_database_requires_url() -> None:
    with pytest.raises(DatabaseNotConfiguredError):
        Database(DatabaseSettings())


def test_database_is_lazy() -> None:
    db = Database(DatabaseSettings(url=URL, pool_size=4))  # no connection attempted
    assert db.pool_status()["size"] == 4


# --- error translation -------------------------------------------------------------


class _DriverError(Exception):
    def __init__(self, sqlstate: str | None, constraint: str | None = None) -> None:
        super().__init__("driver message")
        self.sqlstate = sqlstate
        self.constraint_name = constraint


def _wrapped(sqlstate: str | None, cls: type[sa_exc.DBAPIError] = sa_exc.DBAPIError, **kw: Any):  # type: ignore[no-untyped-def]
    return cls(
        "INSERT INTO t VALUES (:secret)",
        {"secret": "card-4111-1111"},
        _DriverError(sqlstate, **kw),
    )


@pytest.mark.parametrize(
    ("sqlstate", "expected", "retryable"),
    [
        ("08006", DatabaseUnavailableError, True),
        ("57P01", DatabaseUnavailableError, True),
        ("53300", DatabaseUnavailableError, True),
        ("28P01", DatabaseUnavailableError, False),
        ("3D000", DatabaseUnavailableError, False),
        ("57014", DatabaseTimeoutError, True),
        ("40001", DatabaseConcurrencyError, True),
        ("40P01", DatabaseConcurrencyError, True),
        ("23505", DatabaseConflictError, False),
        ("23503", DatabaseIntegrityError, False),
        ("25006", DatabaseReadOnlyError, False),
        ("42501", DatabasePermissionError, False),
        ("42P01", DatabaseError, False),
    ],
)
def test_sqlstate_mapping(sqlstate: str, expected: type[DatabaseError], retryable: bool) -> None:
    error = translate_db_error(_wrapped(sqlstate))
    assert type(error) is expected
    assert error.retryable is retryable


def test_detail_never_contains_sql_or_parameters() -> None:
    error = translate_db_error(_wrapped("23505", constraint="uq_widgets_code"))
    assert "uq_widgets_code" in error.detail
    assert "23505" in error.detail
    for leaked in ("card-4111", "INSERT", "secret"):
        assert leaked not in error.detail
        assert leaked not in error.public_message


def test_non_sqlstate_errors() -> None:
    pool_timeout = sa_exc.TimeoutError("QueuePool limit reached")
    assert type(translate_db_error(pool_timeout)) is DatabaseUnavailableError
    assert type(translate_db_error(ConnectionRefusedError())) is DatabaseUnavailableError
    assert type(translate_db_error(_wrapped(None, sa_exc.OperationalError))) is (
        DatabaseUnavailableError
    )
    assert type(translate_db_error(ValueError("x"))) is DatabaseError
    already = DatabaseConflictError("x")
    assert translate_db_error(already) is already


# --- health service ------------------------------------------------------------------


def test_health_not_configured() -> None:
    health = asyncio.run(DatabaseHealthService(None).check())
    assert (health.status, health.error_code) == ("not_configured", "DATABASE_NOT_CONFIGURED")


def test_health_unreachable_server() -> None:
    db = Database(
        DatabaseSettings(
            url="postgresql://u:p@127.0.0.1:1/none",
            connect_timeout_seconds=1,
            health_timeout_seconds=3,
        )
    )

    async def check() -> Any:
        try:
            return await DatabaseHealthService(db).check()
        finally:
            await db.dispose()

    health = asyncio.run(check())
    assert health.status == "unavailable"
    assert health.error_code == "DATABASE_UNAVAILABLE"
    assert health.server_version is None


# --- wiring and guards -------------------------------------------------------------


def _settings_with_db() -> AppSettings:
    return AppSettings(database=DatabaseSettings(url=URL))


def test_no_database_tool_without_database() -> None:
    deps = build_dependencies(AppSettings(), llm=ScriptedLLM())
    assert deps.database is None
    assert "system.database_health" not in [s.name for s in deps.tools.specs()]


def test_database_tool_registered_with_database() -> None:
    deps = build_dependencies(_settings_with_db(), llm=ScriptedLLM())
    assert deps.database is not None
    assert "system.database_health" in [s.name for s in deps.tools.specs()]


SQL_LIKE_FIELDS = {"sql", "query", "raw_sql", "statement", "where", "filter", "order_by"}


def test_no_tool_accepts_sql() -> None:
    """ADR-004: the LLM can never pass SQL (or SQL fragments) to any tool."""
    deps = build_dependencies(_settings_with_db(), llm=ScriptedLLM())
    for spec in deps.tools.specs():
        fields = set(spec.input_model.model_fields)
        assert not fields & SQL_LIKE_FIELDS, f"{spec.name} accepts SQL-like input {fields}"
        assert "sql" not in spec.name.lower()


# --- migrations ------------------------------------------------------------------


def test_single_linear_head() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0005"]
    assert head_revision() == "0005"
    assert [r.revision for r in script.walk_revisions()] == [
        "0005",
        "0004",
        "0003",
        "0002",
        "0001",
    ]
    for revision in script.walk_revisions():
        assert revision.doc, f"migration {revision.revision} needs a docstring"


def test_offline_sql_generation() -> None:
    buffer = io.StringIO()
    config = Config(output_buffer=buffer)
    config.set_main_option("script_location", SCRIPT_LOCATION)
    config.set_main_option("sqlalchemy.url", URL)
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE alembic_version" in sql
    assert "INSERT INTO alembic_version (version_num) VALUES ('0001')" in sql
    assert "UPDATE alembic_version SET version_num='0002'" in sql
    assert "UPDATE alembic_version SET version_num='0003'" in sql
    assert "UPDATE alembic_version SET version_num='0004'" in sql
    assert "UPDATE alembic_version SET version_num='0005'" in sql
    # Phase 6: requester-only (private) approval requests.
    assert "ADD COLUMN requester_only BOOLEAN" in sql
    # Phase 5: tenant RLS on ownership, approvals, the audit log and checkpoints.
    for table in (
        "conversation_thread",
        "approval_request",
        "agent_checkpoints",
        "agent_checkpoint_blobs",
        "agent_checkpoint_writes",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY {table}_tenant_isolation" in sql
    assert "CREATE POLICY audit_log_tenant_read ON audit_log FOR SELECT" in sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "embedding vector" in sql
    for table in PLATFORM_TABLES:
        assert f"CREATE TABLE {table}" in sql
    assert "CREATE TRIGGER audit_log_no_update_delete" in sql
    assert "CREATE TRIGGER audit_log_no_truncate" in sql


def test_no_business_schema_defined() -> None:
    """Only platform tables (Phase 3) exist: no business tables were invented."""
    assert set(Base.metadata.tables) == PLATFORM_TABLES
