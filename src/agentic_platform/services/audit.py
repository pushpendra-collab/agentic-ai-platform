"""Audit service: records who did what, through which tool, with what result.

`record()` writes in its own unit of work. A business service that must audit
atomically with its own change (Phase 4+ write tools) passes its unit of work,
so the audit row commits or rolls back together with the change.

Phase 5: events carry the authenticated principal (`tenant_id`, `actor_id`);
see `security/audit.py`. Every write binds the event's tenant for row-level
security, and reads are tenant-scoped (the caller's authorization is checked by
`security`, not here).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_platform.core.logging import get_logger
from agentic_platform.db.audit import AuditLogEntry, AuditRepository
from agentic_platform.db.engine import Database
from agentic_platform.db.tenancy import bind_tenant, enable_maintenance
from agentic_platform.db.unit_of_work import UnitOfWork

logger = get_logger(__name__)

_MAX_TEXT = 200


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=_MAX_TEXT)
    status: Literal["ok", "error", "denied"]
    actor_type: Literal["user", "agent", "system"] = "agent"
    actor_id: str | None = None
    tenant_id: str | None = None
    tool_name: str | None = Field(default=None, max_length=_MAX_TEXT)
    error_code: str | None = None
    input_hash: str | None = None
    entity_refs: list[dict[str, str]] = Field(default_factory=list)
    approval_decision: Literal["approved", "rejected", "edited"] | None = None
    approver_id: str | None = None
    request_id: str | None = None
    thread_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(AuditEvent):
    id: int
    occurred_at: str


class AuditService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(self, event: AuditEvent, *, uow: UnitOfWork | None = None) -> int:
        """Append one entry and return its id. Raises `DatabaseError` on failure."""
        if uow is not None:
            return await self._append(uow, event)
        async with self._database.unit_of_work() as own:
            return await self._append(own, event)

    async def for_thread(
        self, thread_id: str, *, tenant_id: str, limit: int = 100
    ) -> list[AuditRecord]:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            rows = await AuditRepository(uow.session).list_for_thread(
                tenant_id, thread_id, limit=limit
            )
            return [_to_record(row) for row in rows]

    async def for_tenant(
        self, tenant_id: str, *, action: str | None = None, limit: int = 100
    ) -> list[AuditRecord]:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            rows = await AuditRepository(uow.session).list_for_tenant(
                tenant_id, action=action, limit=limit
            )
            return [_to_record(row) for row in rows]

    @staticmethod
    async def _append(uow: UnitOfWork, event: AuditEvent) -> int:
        if event.tenant_id is not None:
            await bind_tenant(uow.session, event.tenant_id)
        else:
            # System-level event (e.g. a failed sign-in: no trusted tenant). The insert
            # returns its id, and RLS applies SELECT policies to returned rows, so
            # tenant-less rows need the maintenance scope. Tenants never see them.
            await enable_maintenance(uow.session)
        entry = await AuditRepository(uow.session).append(AuditLogEntry(**event.model_dump()))
        logger.debug(
            "audit.recorded",
            extra={
                "fields": {"audit_id": entry.id, "action": event.action, "status": event.status}
            },
        )
        return entry.id


def _to_record(row: AuditLogEntry) -> AuditRecord:
    fields = {name: getattr(row, name) for name in AuditEvent.model_fields}
    return AuditRecord(id=row.id, occurred_at=row.occurred_at.isoformat(), **fields)
