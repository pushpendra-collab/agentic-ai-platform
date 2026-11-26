"""Local maintenance scheduler (ADR-044).

`MaintenanceScheduler.run_forever()` calls `MaintenanceRunner.run_all()` every
`MAINTENANCE_INTERVAL_SECONDS` until stopped. It is the **development** schedule
(and a simple worker): production can equally run one pass per invocation from
cron, a container scheduler or a cloud job (`python -m agentic_platform.maintenance`),
because the runner, not the scheduler, carries the guarantees (bounded batches,
per-job locks, idempotent jobs).

A failing run is logged and the loop carries on; nothing it does can block a
request handler, and the API process only starts it when API_RUN_MAINTENANCE=true.
"""

from __future__ import annotations

import asyncio
import contextlib

from agentic_platform.core.logging import get_logger
from agentic_platform.maintenance.runner import JobRunReport, MaintenanceRunner

logger = get_logger(__name__)


class MaintenanceScheduler:
    def __init__(self, runner: MaintenanceRunner, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self._runner = runner
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.runs = 0

    async def run_once(self) -> list[JobRunReport]:
        try:
            return await self._runner.run_all()
        except Exception:  # the runner already isolates jobs; this is a last resort
            logger.exception("maintenance.run.crashed")
            return []
        finally:
            self.runs += 1

    async def run_forever(self) -> None:
        logger.info("maintenance.scheduler.started", extra={"fields": {"interval": self._interval}})
        while not self._stop.is_set():
            await self.run_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
        logger.info("maintenance.scheduler.stopped")

    def request_stop(self) -> None:
        """Signal-safe stop (SIGTERM): the current batch commits, then the loop ends."""
        self._runner.request_stop()
        self._stop.set()

    def start(self) -> asyncio.Task[None]:
        """Run in the background of the current event loop (API_RUN_MAINTENANCE)."""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run_forever(), name="maintenance-scheduler")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
