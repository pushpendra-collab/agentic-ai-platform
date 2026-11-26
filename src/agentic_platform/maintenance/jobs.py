"""Maintenance jobs (Phase 6, ADR-044).

A job does one bounded step at a time: `run_batch(limit)` changes at most `limit`
rows in **one transaction** and reports how many it handled. The runner repeats
it until a batch comes back short (nothing more is due) or the run's batch budget
is used up. So every job is:

* **bounded:** never loads or changes an unlimited number of rows at once;
* **idempotent and safe to retry:** each batch selects only rows that are still
  due, so running a job twice, or again after a failure, changes nothing extra;
* **failure-tolerant:** a failed batch rolls back on its own; earlier batches
  stay committed and are complete in themselves.

Jobs apply **only retention rules that already exist** (Phase 4/5). They never
decide a retention period: each deletes or expires rows whose own `expires_at`
has passed. Data without a documented retention period (checkpoints, the audit
log, decided approval requests, conversation ownership rows) has no job; see
PROJECT_STATUS open question Q11.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from agentic_platform.memory.manager import MemoryManager
from agentic_platform.services.approvals import ApprovalService
from agentic_platform.services.idempotency import IdempotencyService


@dataclass(frozen=True)
class BatchResult:
    # Rows this batch handled (selected and changed). Fewer than the limit = done.
    processed: int
    # Rows deleted or expired (equal to `processed` for the built-in jobs).
    affected: int
    # Optional breakdown, for example deleted memories by kind. Counts only.
    details: Mapping[str, int] = field(default_factory=dict)


class MaintenanceJob(Protocol):
    name: str
    description: str
    # Which existing, documented rule the job applies.
    policy: str

    async def run_batch(self, limit: int) -> BatchResult: ...


class ExpiredMemoryJob:
    """Deletes durable memories past their `expires_at` (all tenants)."""

    name = "memory.expired"
    description = "Delete expired durable memory (episodes, long-term facts with a TTL)."
    policy = (
        "memory_item.expires_at: episodes expire after MEMORY_EPISODIC_RETENTION_DAYS "
        "(Phase 4, ADR-036); long-term facts only when written with a TTL. Working "
        "memory expires in Redis by its own TTL and needs no job."
    )

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    async def run_batch(self, limit: int) -> BatchResult:
        counts = await self._memory.purge_expired_batch(limit)
        total = sum(counts.values())
        return BatchResult(processed=total, affected=total, details=counts)


class ExpiredApprovalJob:
    """Marks overdue approval requests `expired` (they are kept, not deleted)."""

    name = "approvals.expired"
    description = "Expire pending and approved-but-unused approval requests past expiry."
    policy = (
        "approval_request.expires_at = creation + APPROVAL_TTL_SECONDS (Phase 5, "
        "ADR-040). Rows are kept; their deletion has no defined retention (Q11)."
    )

    def __init__(self, approvals: ApprovalService) -> None:
        self._approvals = approvals

    async def run_batch(self, limit: int) -> BatchResult:
        expired = await self._approvals.expire_due(limit)
        return BatchResult(processed=expired, affected=expired)


class ExpiredIdempotencyJob:
    """Deletes idempotency records past their `expires_at`."""

    name = "idempotency.expired"
    description = "Delete expired idempotency records."
    policy = (
        "idempotency_record.expires_at (record TTL, 24 h by default; Phase 3, ADR-029). "
        "Claims already treat expired records as absent; an executed approval is never "
        "run again without its record (ToolRegistry)."
    )

    def __init__(self, idempotency: IdempotencyService) -> None:
        self._idempotency = idempotency

    async def run_batch(self, limit: int) -> BatchResult:
        purged = await self._idempotency.purge_expired(limit)
        return BatchResult(processed=purged, affected=purged)
