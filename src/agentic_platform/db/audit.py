"""Append-only audit log (ARCHITECTURE.md § B.6, ADR-011, ADR-028).

Rows are only ever inserted. Migration `0002` installs triggers that reject
UPDATE, DELETE and TRUNCATE for every role, so even the schema owner cannot
rewrite history through normal DML.

The log stores a hash of tool input, never the input itself (it may contain
business data or PII). `details` is for small, non-sensitive structured context.

Phase 5 (migration 0004): row-level security limits the runtime role to rows of
the tenant bound with `db.tenancy.bind_tenant` (rows without a tenant, such as
failed sign-ins, are visible to maintenance only), and the runtime role no longer
holds UPDATE / DELETE / TRUNCATE on the table at all. Reads are always filtered by
tenant here as well.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentic_platform.db.base import Base
from agentic_platform.db.repository import MAX_PAGE_SIZE

ACTOR_TYPES = ("user", "agent", "system")
RESULT_STATUSES = ("ok", "error", "denied")
APPROVAL_DECISIONS = ("approved", "rejected", "edited")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AuditLogEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(_in("actor_type", ACTOR_TYPES), name="actor_type"),
        CheckConstraint(_in("status", RESULT_STATUSES), name="status"),
        CheckConstraint(
            f"approval_decision IS NULL OR {_in('approval_decision', APPROVAL_DECISIONS)}",
            name="approval_decision",
        ),
        Index("ix_audit_log_thread_id_occurred_at", "thread_id", "occurred_at"),
        Index("ix_audit_log_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Nullable until multi-tenancy is decided (ADR-016, PROJECT_STATUS Q7).
    tenant_id: Mapped[str | None] = mapped_column(Text)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(Text)
    entity_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    approval_decision: Mapped[str | None] = mapped_column(Text)
    approver_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class AuditRepository:
    """Insert and read only. There is deliberately no update or delete method."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, entry: AuditLogEntry) -> AuditLogEntry:
        self._session.add(entry)
        await self._session.flush()  # assigns id / occurred_at
        return entry

    async def list_for_thread(
        self, tenant_id: str, thread_id: str, *, limit: int = 100
    ) -> Sequence[AuditLogEntry]:
        """Oldest first. `limit` is capped at MAX_PAGE_SIZE."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        stmt = (
            select(AuditLogEntry)
            .where(AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.thread_id == thread_id)
            .order_by(AuditLogEntry.id)
            .limit(limit)
        )
        return (await self._session.scalars(stmt)).all()

    async def list_for_tenant(
        self, tenant_id: str, *, action: str | None = None, limit: int = 100
    ) -> Sequence[AuditLogEntry]:
        """Newest first. `limit` is capped at MAX_PAGE_SIZE."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        stmt = select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
        if action is not None:
            stmt = stmt.where(AuditLogEntry.action == action)
        stmt = stmt.order_by(AuditLogEntry.id.desc()).limit(limit)
        return (await self._session.scalars(stmt)).all()
