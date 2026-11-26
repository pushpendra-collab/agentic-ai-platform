"""Scheduled maintenance (Phase 6, ADR-044): bounded, idempotent retention jobs.

    scheduler (local loop, cron, container or cloud job)
        ↓
    MaintenanceRunner          per-job lock · batches · report · log + audit
        ↓
    MaintenanceJob.run_batch   one transaction, at most MAINTENANCE_BATCH_SIZE rows
        ↓
    services / memory manager  (existing retention operations, now bounded)

Built-in jobs (existing retention rules only):

* `memory.expired`: durable memories past `expires_at` (episodes, facts with a TTL);
* `approvals.expired`: overdue approval requests become `expired` (kept);
* `idempotency.expired`: idempotency records past `expires_at` (needs a database).
"""

from __future__ import annotations

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.maintenance.jobs import (
    BatchResult,
    ExpiredApprovalJob,
    ExpiredIdempotencyJob,
    ExpiredMemoryJob,
    MaintenanceJob,
)
from agentic_platform.maintenance.runner import JobRunReport, MaintenanceRunner
from agentic_platform.maintenance.scheduler import MaintenanceScheduler


def default_jobs(deps: AgentDependencies) -> list[MaintenanceJob]:
    """The jobs this deployment can run (memory and idempotency only when configured)."""
    jobs: list[MaintenanceJob] = []
    if deps.memory is not None:
        jobs.append(ExpiredMemoryJob(deps.memory))
    jobs.append(ExpiredApprovalJob(deps.security.approvals))
    if deps.idempotency is not None:
        jobs.append(ExpiredIdempotencyJob(deps.idempotency))
    return jobs


def build_maintenance_runner(deps: AgentDependencies) -> MaintenanceRunner:
    """Runner over the same stores, locks and audit trail the agent uses."""
    return MaintenanceRunner(
        default_jobs(deps),
        settings=deps.settings.maintenance,
        locks=deps.locks,
        audit=deps.security.audit,
    )


__all__ = [
    "BatchResult",
    "ExpiredApprovalJob",
    "ExpiredIdempotencyJob",
    "ExpiredMemoryJob",
    "JobRunReport",
    "MaintenanceJob",
    "MaintenanceRunner",
    "MaintenanceScheduler",
    "build_maintenance_runner",
    "default_jobs",
]
