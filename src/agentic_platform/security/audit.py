"""Security audit events (ADR-028, ADR-040).

One place builds audit events for security-sensitive actions so every entry
carries the same correlation fields: tenant, actor, `request_id` and
`thread_id` (from the log context), and entity references such as the approval id.
That lets an investigation follow request → graph → node → approval → tool → result.

Never recorded: tokens, passwords, secrets, tool arguments or memory content.
Content is represented by a SHA-256 `input_hash` only.

`record()` is best-effort (a failure is logged): used for denials and reads.
`record_required()` raises: used where an action must not proceed unaudited.
Without a database there is no audit log (as in Phase 3); events are logged only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from agentic_platform.core.logging import current_log_context, get_logger
from agentic_platform.security.identity import Principal
from agentic_platform.services.audit import AuditEvent

logger = get_logger(__name__)

ActorType = Literal["user", "agent", "system"]


class AuditAction:
    AUTH_FAILED = "auth.failed"
    AUTHZ_DENIED = "authz.denied"
    MEMORY_FACT_CREATE = "memory.fact.create"
    MEMORY_FACT_UPDATE = "memory.fact.update"
    MEMORY_FACT_DELETE = "memory.fact.delete"
    MEMORY_DOCUMENT_WRITE = "memory.document.write"
    MEMORY_DOCUMENT_DELETE = "memory.document.delete"
    MEMORY_PROCEDURE_WRITE = "memory.procedure.write"
    MEMORY_PROCEDURE_DELETE = "memory.procedure.delete"
    MEMORY_SUMMARY_WRITE = "memory.summary.write"
    MEMORY_USER_FORGET = "memory.user.forget"
    CONVERSATION_FORGET = "conversation.forget"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_CANCELLED = "approval.cancelled"
    APPROVAL_EXPIRED = "approval.expired"
    OPERATION_EXECUTED = "operation.executed"
    OPERATION_REJECTED = "operation.rejected"
    MAINTENANCE_RUN = "maintenance.run"


class Auditor(Protocol):
    async def record(self, event: AuditEvent) -> int: ...


class SecurityAudit:
    def __init__(self, auditor: Auditor | None) -> None:
        self._auditor = auditor

    @property
    def enabled(self) -> bool:
        return self._auditor is not None

    def event(
        self,
        action: str,
        status: Literal["ok", "error", "denied"],
        principal: Principal | None,
        *,
        actor_type: ActorType = "user",
        tenant_id: str | None = None,
        tool_name: str | None = None,
        error_code: str | None = None,
        input_hash: str | None = None,
        entity_refs: Sequence[dict[str, str]] = (),
        approval_decision: Literal["approved", "rejected", "edited"] | None = None,
        approver_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        context = current_log_context()
        return AuditEvent(
            action=action,
            status=status,
            actor_type=actor_type,
            actor_id=principal.user_id if principal else None,
            tenant_id=principal.tenant_id if principal else tenant_id,
            tool_name=tool_name,
            error_code=error_code,
            input_hash=input_hash,
            entity_refs=list(entity_refs),
            approval_decision=approval_decision,
            approver_id=approver_id,
            request_id=context.get("request_id"),
            thread_id=context.get("thread_id"),
            details=details or {},
        )

    async def record(self, event: AuditEvent) -> None:
        """Best effort: a failure is logged, never raised."""
        self._log(event)
        if self._auditor is None:
            return
        try:
            await self._auditor.record(event)
        except Exception:
            logger.error(
                "security.audit.failed", exc_info=True, extra={"fields": {"action": event.action}}
            )

    async def record_required(self, event: AuditEvent) -> None:
        """Fail closed: the caller must not proceed if this raises."""
        self._log(event)
        if self._auditor is not None:
            await self._auditor.record(event)

    @staticmethod
    def _log(event: AuditEvent) -> None:
        logger.info(
            "security.audit",
            extra={
                "fields": {
                    "action": event.action,
                    "status": event.status,
                    "error_code": event.error_code,
                    "actor_id": event.actor_id,
                    "tenant_id": event.tenant_id,
                }
            },
        )
