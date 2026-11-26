"""Maintenance runner: runs jobs in bounded batches and records what happened (ADR-044).

    run_job(name)
      ├─ lock "maintenance:<job>" (one runner per job across processes; busy → skipped)
      ├─ batch, batch, ...          each batch = one transaction; stop when a batch is
      │                             short, after MAINTENANCE_MAX_BATCHES, or on an error
      └─ JobRunReport               job, run id, start/end, batches, processed,
                                    affected, errors → log + audit_log (system actor)

The run id is bound as the log context's `request_id`, so the job's own log lines
and its audit row share one correlation id. Reports carry counts and error codes
only: never memory content, arguments or identifiers of the rows touched.

A failed batch stops that job for this run (the next run retries); other jobs
still run. Nothing here loops forever: the scheduler (`scheduler.py`) decides
when the next run starts.

Graceful stop (Phase 7): `request_stop()` (the worker calls it on SIGTERM) lets the
batch in progress commit, then ends the job (`complete=false`, so the next run
continues) and skips the jobs not started yet. Locks are released on the way out.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agentic_platform.cache import LockManager
from agentic_platform.core.config import MaintenanceSettings
from agentic_platform.core.errors import (
    AgentPlatformError,
    ConfigurationError,
    LockNotAcquiredError,
)
from agentic_platform.core.logging import get_logger, log_context
from agentic_platform.maintenance.jobs import MaintenanceJob
from agentic_platform.security.audit import AuditAction, SecurityAudit

logger = get_logger(__name__)

JobStatus = Literal["ok", "partial", "failed", "skipped"]
INTERNAL_ERROR = "INTERNAL_ERROR"


class JobRunReport(BaseModel):
    job: str
    run_id: str
    # ok: every batch succeeded · partial: some batches committed, then one failed ·
    # failed: nothing committed · skipped: another runner holds the job's lock.
    status: JobStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    batches: int = 0
    processed: int = 0
    affected: int = 0
    errors: int = 0
    error_codes: list[str] = Field(default_factory=list)
    details: dict[str, int] = Field(default_factory=dict)
    # True when the last batch was short (nothing more was due). False when the run
    # stopped at MAINTENANCE_MAX_BATCHES or on an error: the next run continues.
    complete: bool = False


class MaintenanceRunner:
    def __init__(
        self,
        jobs: Sequence[MaintenanceJob],
        *,
        settings: MaintenanceSettings | None = None,
        locks: LockManager | None = None,
        audit: SecurityAudit | None = None,
    ) -> None:
        names = [job.name for job in jobs]
        if len(set(names)) != len(names):
            raise ConfigurationError("Maintenance job names must be unique.")
        self._jobs = {job.name: job for job in jobs}
        self._settings = settings or MaintenanceSettings()
        self._locks = locks
        self._audit = audit or SecurityAudit(None)
        self._stopping = False

    @property
    def jobs(self) -> list[MaintenanceJob]:
        return list(self._jobs.values())

    @property
    def stopping(self) -> bool:
        return self._stopping

    def request_stop(self) -> None:
        """Finish the current batch, then stop (no new batch, no new job)."""
        if not self._stopping:
            logger.info("maintenance.stop_requested")
        self._stopping = True

    async def run_all(self) -> list[JobRunReport]:
        """Every job once, in registration order. One job's failure does not stop the rest."""
        reports: list[JobRunReport] = []
        for name in self._jobs:
            if self._stopping:
                break
            reports.append(await self.run_job(name))
        return reports

    async def run_job(self, name: str) -> JobRunReport:
        job = self._jobs.get(name)
        if job is None:
            raise ConfigurationError(f"Unknown maintenance job '{name}'.")
        run_id = uuid.uuid4().hex
        with log_context(request_id=run_id, maintenance_job=name):
            started_at, started = datetime.now(UTC), time.perf_counter()
            logger.info("maintenance.job.started")
            report = JobRunReport(
                job=name,
                run_id=run_id,
                status="ok",
                started_at=started_at,
                finished_at=started_at,
                duration_ms=0,
            )
            try:
                if self._locks is None:
                    await self._batches(job, report)
                else:
                    async with self._locks.hold(
                        f"maintenance:{name}",
                        ttl_seconds=self._settings.job_lock_ttl_seconds,
                        wait_seconds=0,
                    ):
                        await self._batches(job, report)
            except LockNotAcquiredError:
                report.status = "skipped"
            except AgentPlatformError as exc:  # lock backend unreachable (fails closed)
                self._failed(report, exc.code)
            report.finished_at = datetime.now(UTC)
            report.duration_ms = int((time.perf_counter() - started) * 1000)
            await self._record(report)
            return report

    async def _batches(self, job: MaintenanceJob, report: JobRunReport) -> None:
        limit = self._settings.batch_size
        for _ in range(self._settings.max_batches):
            if self._stopping:
                return  # complete stays False: the next run continues
            try:
                result = await job.run_batch(limit)
            except AgentPlatformError as exc:
                logger.error(
                    "maintenance.batch.failed",
                    extra={"fields": {"code": exc.code, "detail": exc.detail}},
                )
                self._failed(report, exc.code)
                return
            except Exception:
                logger.exception("maintenance.batch.crashed")
                self._failed(report, INTERNAL_ERROR)
                return
            report.batches += 1
            report.processed += result.processed
            report.affected += result.affected
            for key, count in result.details.items():
                report.details[key] = report.details.get(key, 0) + count
            if result.processed < limit:
                report.complete = True
                return

    @staticmethod
    def _failed(report: JobRunReport, code: str) -> None:
        report.errors += 1
        report.error_codes.append(code)
        report.status = "partial" if report.batches else "failed"

    async def _record(self, report: JobRunReport) -> None:
        fields = report.model_dump(
            mode="json",
            include={
                "job",
                "status",
                "batches",
                "processed",
                "affected",
                "errors",
                "error_codes",
                "complete",
                "duration_ms",
            },
        )
        level = logger.info if report.status in ("ok", "skipped") else logger.error
        level("maintenance.job.finished", extra={"fields": fields})
        await self._audit.record(
            self._audit.event(
                AuditAction.MAINTENANCE_RUN,
                "ok" if report.status in ("ok", "skipped") else "error",
                None,
                actor_type="system",
                tool_name=report.job,
                error_code=report.error_codes[0] if report.error_codes else None,
                details={
                    **fields,
                    "details": report.details,
                    "started_at": report.started_at.isoformat(),
                    "finished_at": report.finished_at.isoformat(),
                },
            )
        )
