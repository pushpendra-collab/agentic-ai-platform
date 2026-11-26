"""Alembic environment (async, asyncpg).

URL resolution: `sqlalchemy.url` set programmatically (tests) wins; otherwise
DATABASE_MIGRATION_URL, then DATABASE_URL from settings / `.env`.

Online runs take a PostgreSQL advisory lock so that two deployments starting at
the same time cannot apply migrations concurrently.

Phase 8: before migrating, the target must be this application's database (it has our
`alembic_version` table) or empty. A database that already holds other tables, such as
an ERP system of record, is refused (`refuse_foreign_database`).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from agentic_platform.core.config import DatabaseSettings, normalise_postgres_url
from agentic_platform.db.migrations import (
    FOREIGN_DATABASE_OVERRIDE_ENV,
    FOREIGN_TABLES_SQL,
    VERSION_TABLE_SQL,
    refuse_foreign_database,
)
from agentic_platform.db.models import target_metadata

# Arbitrary constant shared by every migration runner of this application.
MIGRATION_LOCK_ID = 7_420_190_001

config = context.config
if config.config_file_name is not None:  # CLI runs; programmatic configs have no file
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return normalise_postgres_url(explicit)
    return DatabaseSettings().migrations_url()


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    connection.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))
    connection.commit()
    try:
        refuse_foreign_database(
            bool(connection.execute(text(VERSION_TABLE_SQL)).scalar()),
            [str(row[0]) for row in connection.execute(text(FOREIGN_TABLES_SQL))],
            os.environ.get(FOREIGN_DATABASE_OVERRIDE_ENV),
        )
        connection.commit()  # end the inspection transaction; Alembic opens its own
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))
        connection.commit()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
