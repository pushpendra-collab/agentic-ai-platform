"""Engine, connection pool and session factory: one `Database` per process.

Pool and server-side limits come from `DatabaseSettings`:

* `pool_size` + `max_overflow` bound concurrent connections; `pool_timeout_seconds`
  bounds how long a request waits for one (then `DatabaseUnavailableError`).
* `pool_pre_ping` and `pool_recycle_seconds` discard dead or stale connections.
* `statement_timeout` and `idle_in_transaction_session_timeout` are set per
  connection, so a runaway query or a forgotten transaction cannot hold locks.
* `command_timeout` (Phase 8) bounds every command on the client side when the server
  is responsive but a command does not finish (see `DatabaseSettings`).

The engine is bound to the event loop that first uses it. Call `dispose()` before
that loop closes (see `Agent.run`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agentic_platform.core.config import DatabaseSettings
from agentic_platform.core.errors import DatabaseNotConfiguredError
from agentic_platform.core.logging import get_logger
from agentic_platform.db.unit_of_work import UnitOfWork

logger = get_logger(__name__)


def engine_options(settings: DatabaseSettings) -> dict[str, Any]:
    server_settings = {
        "application_name": settings.application_name,
        "statement_timeout": str(settings.statement_timeout_ms),
        "idle_in_transaction_session_timeout": str(settings.idle_in_transaction_timeout_ms),
        "timezone": "UTC",
    }
    connect_args: dict[str, Any] = {
        "timeout": settings.connect_timeout_seconds,
        "command_timeout": settings.command_timeout_seconds,
        "server_settings": server_settings,
    }
    if settings.ssl_mode is not None:
        connect_args["ssl"] = settings.ssl_mode
    return {
        "pool_size": settings.pool_size,
        "max_overflow": settings.max_overflow,
        "pool_timeout": settings.pool_timeout_seconds,
        "pool_recycle": settings.pool_recycle_seconds,
        "pool_pre_ping": settings.pool_pre_ping,
        "echo": settings.echo_sql,
        "connect_args": connect_args,
    }


class Database:
    def __init__(self, settings: DatabaseSettings, *, engine: AsyncEngine | None = None) -> None:
        if engine is None:
            if not settings.configured:
                raise DatabaseNotConfiguredError("DATABASE_URL is not set.")
            engine = create_async_engine(settings.runtime_url(), **engine_options(settings))
        self._settings = settings
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @property
    def settings(self) -> DatabaseSettings:
        return self._settings

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def unit_of_work(self, *, read_only: bool = False, tenant_id: str | None = None) -> UnitOfWork:
        """Open a transaction scope: `async with db.unit_of_work() as uow: ...`.

        `tenant_id` binds the row-level-security tenant for the transaction."""
        return UnitOfWork(self._sessions, read_only=read_only, tenant_id=tenant_id)

    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._sessions

    def pool_status(self) -> dict[str, int]:
        pool: Any = self._engine.pool
        status: dict[str, int] = {}
        for name in ("size", "checkedin", "checkedout", "overflow"):
            method = getattr(pool, name, None)
            if callable(method):
                status[name] = int(method())
        return status

    async def dispose(self) -> None:
        """Close pooled connections. The engine stays usable and reconnects lazily."""
        await self._engine.dispose()
        logger.debug("database.pool.disposed")
