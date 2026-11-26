"""Phase 8: failure modes found by the failure drills, reproduced without servers.

A hung PostgreSQL (reachable but not answering) made `/ready` hang: asyncpg turns a
timeout or cancellation into a CancelRequest and waits for the server to acknowledge
it, so `asyncio.timeout` could not return. Readiness must answer within its deadline
no matter how long the driver takes to unwind.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agentic_platform.core.config import DatabaseSettings
from agentic_platform.services.database_health import DatabaseHealthService
from agentic_platform.services.readiness import ReadinessService


class HungDatabase:
    """Stands in for `Database` on a frozen server: the probe never finishes and swallows
    cancellation for a while, like asyncpg waiting for its cancel acknowledgement."""

    def __init__(self, hang_seconds: float) -> None:
        self.settings = DatabaseSettings(url="postgresql://u:p@h/d", health_timeout_seconds=0.2)
        self.hang_seconds = hang_seconds
        self.unwound = asyncio.Event()

    @asynccontextmanager
    async def unit_of_work(self, **_: Any) -> AsyncIterator[Any]:
        loop = asyncio.get_running_loop()
        end = loop.time() + self.hang_seconds
        while loop.time() < end:
            try:
                await asyncio.sleep(end - loop.time())
            except asyncio.CancelledError:
                continue  # the driver keeps waiting for the server
        self.unwound.set()
        raise OSError("connection lost")
        yield  # pragma: no cover


def test_database_health_answers_within_its_deadline_on_a_hung_server() -> None:
    async def scenario() -> None:
        database = HungDatabase(hang_seconds=1.5)
        started = time.perf_counter()
        health = await DatabaseHealthService(database).check()  # type: ignore[arg-type]
        elapsed = time.perf_counter() - started
        assert health.status == "unavailable"
        assert health.error_code == "DATABASE_TIMEOUT"
        assert elapsed < 0.6, elapsed  # the 0.2 s deadline, not the 1.5 s hang
        # The abandoned probe still unwinds in the background (no leaked task errors).
        await asyncio.wait_for(database.unwound.wait(), timeout=3)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_readiness_reports_not_ready_promptly_on_a_hung_database() -> None:
    async def scenario() -> None:
        readiness = ReadinessService(
            database=HungDatabase(hang_seconds=1.5),  # type: ignore[arg-type]
            redis=None,
            production=True,
            authentication_configured=True,
        )
        started = time.perf_counter()
        report = await readiness.check()
        assert time.perf_counter() - started < 0.6
        assert report.status == "not_ready"
        assert report.checks["database"].error_code == "DATABASE_TIMEOUT"
        await asyncio.sleep(1.6)  # let the abandoned probe finish inside this loop

    asyncio.run(scenario())
