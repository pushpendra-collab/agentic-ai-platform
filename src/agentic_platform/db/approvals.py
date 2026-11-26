"""Human-in-the-loop approval requests (ADR-040).

One row per approval request: which tenant, which requester, which operation
(tool name + SHA-256 of its validated arguments, never the arguments), which
workflow (thread + turn `request_id`, the correlation id shared with the audit
log), the policy that applies, and the lifecycle:

    pending ──► approved ──► executed
       │  └───► rejected
       ├──────► expired      (also an approved request that was not used in time)
       └──────► cancelled

`requester_only` (migration 0005): a private operation's request is visible to,
and decidable by, its requester only (for example the user's own memory facts).

Transitions are compare-and-set (`UPDATE ... WHERE status IN (...)`), so two
approvers or two resumes can never both win. Expiry uses the database clock.
RLS (migration 0004) restricts the runtime role to the bound tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    and_,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentic_platform.db.base import Base, TimestampMixin

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled", "executed")
RISK_LEVELS = ("low", "medium", "high")
EXECUTION_STATUSES = ("ok", "error")
MAX_LIST = 200


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ApprovalRequestRow(TimestampMixin, Base):
    __tablename__ = "approval_request"
    __table_args__ = (
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        CheckConstraint(_in("risk", RISK_LEVELS), name="risk"),
        CheckConstraint(
            f"execution_status IS NULL OR {_in('execution_status', EXECUTION_STATUSES)}",
            name="execution_status",
        ),
        CheckConstraint(
            "status <> 'pending' OR (decided_at IS NULL AND decided_by IS NULL)",
            name="pending_undecided",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'rejected', 'executed') OR decided_by IS NOT NULL",
            name="decision_has_approver",
        ),
        CheckConstraint("(executed_at IS NULL) = (status <> 'executed')", name="executed_at"),
        CheckConstraint("char_length(tenant_id) BETWEEN 1 AND 128", name="tenant_length"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 1000", name="summary_length"),
        CheckConstraint(
            "decision_note IS NULL OR char_length(decision_note) <= 1000", name="note_length"
        ),
        CheckConstraint("NOT requester_only OR allow_self_approval", name="requester_only_self"),
        Index("ix_approval_request_tenant_status", "tenant_id", "status", "created_at"),
        Index("ix_approval_request_thread", "tenant_id", "thread_id"),
        Index("ix_approval_request_requester", "tenant_id", "requester_id", "created_at"),
        Index(
            "ix_approval_request_pending_expiry",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(Text)
    requester_id: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(Text)  # correlation id (audit_log.request_id)
    operation: Mapped[str] = mapped_column(Text)
    operation_hash: Mapped[str] = mapped_column(Text)
    required_permission: Mapped[str] = mapped_column(Text)
    approver_permission: Mapped[str] = mapped_column(Text)
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Migration 0005: only the requester may see and decide (private operations).
    requester_only: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    risk: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    resource_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    decided_by: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_status: Mapped[str | None] = mapped_column(Text)


# Rows are returned with `expired` computed by the database clock.
Fetched = tuple[ApprovalRequestRow, bool]


def _expired() -> Any:
    return (ApprovalRequestRow.expires_at <= func.now()).label("expired")


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, values: dict[str, Any], ttl_seconds: int) -> Fetched:
        stmt = (
            insert(ApprovalRequestRow)
            .values(**values, expires_at=func.now() + timedelta(seconds=ttl_seconds))
            .returning(ApprovalRequestRow, _expired())
        )
        row = (await self._session.execute(stmt)).one()
        return row[0], bool(row[1])

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> Fetched | None:
        stmt = select(ApprovalRequestRow, _expired()).where(
            ApprovalRequestRow.tenant_id == tenant_id, ApprovalRequestRow.id == approval_id
        )
        row = (await self._session.execute(stmt)).first()
        return (row[0], bool(row[1])) if row else None

    async def find(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] = (),
        requester_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[Fetched]:
        conditions = [ApprovalRequestRow.tenant_id == tenant_id]
        if statuses:
            conditions.append(ApprovalRequestRow.status.in_(list(statuses)))
        if requester_id is not None:
            conditions.append(ApprovalRequestRow.requester_id == requester_id)
        if thread_id is not None:
            conditions.append(ApprovalRequestRow.thread_id == thread_id)
        stmt = (
            select(ApprovalRequestRow, _expired())
            .where(and_(*conditions))
            .order_by(ApprovalRequestRow.created_at.desc(), ApprovalRequestRow.id)
            .limit(max(1, min(limit, MAX_LIST)))
        )
        return [(r[0], bool(r[1])) for r in (await self._session.execute(stmt)).all()]

    async def transition(
        self,
        tenant_id: str,
        approval_id: uuid.UUID,
        *,
        from_statuses: Sequence[str],
        values: dict[str, Any],
        require_unexpired: bool,
    ) -> Fetched | None:
        """Compare-and-set. None when the row is not in `from_statuses` (or expired)."""
        conditions = [
            ApprovalRequestRow.tenant_id == tenant_id,
            ApprovalRequestRow.id == approval_id,
            ApprovalRequestRow.status.in_(list(from_statuses)),
        ]
        if require_unexpired:
            conditions.append(ApprovalRequestRow.expires_at > func.now())
        stmt = (
            update(ApprovalRequestRow)
            .where(and_(*conditions))
            .values(**values, updated_at=func.now())
            .returning(ApprovalRequestRow, _expired())
            .execution_options(synchronize_session=False)
        )
        row = (await self._session.execute(stmt)).first()
        return (row[0], bool(row[1])) if row else None

    async def expire_due(self, limit: int | None = None) -> list[tuple[str, uuid.UUID]]:
        """Maintenance: mark pending and unused approved requests past expiry as expired.

        With `limit`, at most that many (oldest expiry first; rows locked by a
        concurrent transaction are skipped)."""
        due: Any = and_(
            ApprovalRequestRow.status.in_(["pending", "approved"]),
            ApprovalRequestRow.expires_at <= func.now(),
        )
        if limit is not None:
            due = ApprovalRequestRow.id.in_(
                select(ApprovalRequestRow.id)
                .where(due)
                .order_by(ApprovalRequestRow.expires_at)
                .limit(max(1, limit))
                .with_for_update(skip_locked=True)
            )
        stmt = (
            update(ApprovalRequestRow)
            .where(due)
            .values(
                status="expired",
                decided_at=func.coalesce(ApprovalRequestRow.decided_at, func.now()),
                updated_at=func.now(),
            )
            .returning(ApprovalRequestRow.tenant_id, ApprovalRequestRow.id)
            .execution_options(synchronize_session=False)
        )
        return [(r[0], r[1]) for r in (await self._session.execute(stmt)).all()]
