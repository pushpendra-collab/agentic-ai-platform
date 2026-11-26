"""Database health service.

Runs read-only probes inside a read-only unit of work, under a hard timeout, and
reports connectivity, latency, pool usage and whether the schema is at the
latest migration. It never raises.

* `ok`: reachable and the schema is at the latest migration.
* `degraded`: reachable, but migrations are pending (or the schema is ahead of
  this code). Not ready to serve traffic.
* `unavailable`: cannot connect or the probe failed / timed out.
* `not_configured`: DATABASE_URL is not set.

Phase 8: the timeout is a hard deadline for the *caller*. When the server hangs
(reachable but not answering, for example a frozen process or a stalled failover),
asyncpg turns a timeout or cancellation into a PostgreSQL CancelRequest and waits
for the server to acknowledge it, which a hung server never does. So the probe runs
as its own task and readiness stops waiting at the deadline instead of awaiting the
driver's cleanup; the abandoned probe finishes (or fails) in the background. Without
this, `/ready` itself hung during the outage it is meant to report.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from agentic_platform.core.errors import DatabaseError, DatabaseTimeoutError
from agentic_platform.core.logging import get_logger
from agentic_platform.db.engine import Database
from agentic_platform.db.errors import translate_db_error
from agentic_platform.db.health import DatabaseHealthRepository, ServerInfo
from agentic_platform.db.migrations import head_revision
from agentic_platform.services.lifecycle import abandon_after as _within

logger = get_logger(__name__)


class MigrationStatus(BaseModel):
    current_revision: str | None
    head_revision: str | None
    up_to_date: bool


class DatabaseHealth(BaseModel):
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    latency_ms: int | None = None
    server_version: str | None = None
    database: str | None = None
    read_only_replica: bool | None = None
    migrations: MigrationStatus | None = None
    pool: dict[str, int] | None = None
    error_code: str | None = None


class DatabaseHealthService:
    def __init__(self, database: Database | None) -> None:
        self._database = database

    async def check(self) -> DatabaseHealth:
        if self._database is None:
            return DatabaseHealth(status="not_configured", error_code="DATABASE_NOT_CONFIGURED")

        started = time.perf_counter()
        timeout = self._database.settings.health_timeout_seconds
        try:
            info, current = await _within(self._probe(), timeout)
        except TimeoutError:
            error: DatabaseError = DatabaseTimeoutError(f"Health check exceeded {timeout}s.")
            return self._unavailable(error, started)
        except Exception as exc:  # health checks report, they do not raise
            return self._unavailable(translate_db_error(exc), started)

        head = head_revision()
        return DatabaseHealth(
            status="ok" if current == head else "degraded",
            latency_ms=_elapsed_ms(started),
            server_version=info.server_version,
            database=info.database,
            read_only_replica=info.in_recovery,
            migrations=MigrationStatus(
                current_revision=current, head_revision=head, up_to_date=current == head
            ),
            pool=self._database.pool_status(),
        )

    async def _probe(self) -> tuple[ServerInfo, str | None]:
        assert self._database is not None
        async with self._database.unit_of_work(read_only=True) as uow:
            repo = DatabaseHealthRepository(uow.session)
            return await repo.server_info(), await repo.migration_revision()

    def _unavailable(self, error: DatabaseError, started: float) -> DatabaseHealth:
        logger.warning(
            "database.health.unavailable",
            extra={"fields": {"code": error.code, "detail": error.detail}},
        )
        return DatabaseHealth(
            status="unavailable", latency_ms=_elapsed_ms(started), error_code=error.code
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
