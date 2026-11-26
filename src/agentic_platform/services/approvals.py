"""Human-in-the-loop approvals (ADR-040).

    request ─► pending ─► decide(approve) ─► approved ─► validate_for_execution ─► complete
                 │        decide(reject)  ─► rejected        (re-checked on resume)  ─► executed
                 ├─ expiry ─► expired
                 └─ cancel ─► cancelled

Rules enforced here, in code:

* An approval is specific: one operation (name + SHA-256 of its validated
  arguments), one requester, one tenant, one workflow (thread + turn
  `request_id`, the correlation id shared with the audit log).
* Approving needs the policy's `approver_permission` (default `approval.decide`);
  approving your own request needs a policy that allows it. A `requester_only`
  policy (private operations, migration 0005) hides the request from everyone
  but its requester.
* Approval never grants permissions: the operation is authorized again, with the
  requester's current permissions, when it runs (`ToolRegistry.execute_approved`).
* Before execution the approval is re-validated: same tenant, same requester, same
  workflow, same operation and arguments, still approved, not expired, not used.
* Every state change is audited in the same transaction as the change.
* Other tenants' approvals are invisible ("not found").

Storage: `approval_request` in PostgreSQL (RLS by tenant), or process-local
without a database. Expiry uses the database clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func

from agentic_platform.core.config import ApprovalSettings
from agentic_platform.core.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalCancelledError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalInvalidError,
    ApprovalNotFoundError,
    ApprovalPendingError,
    ApprovalRejectedError,
    ApprovalStaleError,
    PermissionDeniedError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.db.approvals import ApprovalRepository, ApprovalRequestRow
from agentic_platform.db.engine import Database
from agentic_platform.db.tenancy import enable_maintenance
from agentic_platform.security.audit import AuditAction, Auditor, SecurityAudit
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.identity import Principal
from agentic_platform.security.permissions import Permission
from agentic_platform.security.policy import ApprovalPolicy, RiskLevel
from agentic_platform.services.audit import AuditEvent, AuditService

logger = get_logger(__name__)

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "cancelled", "executed"]
MAX_SUMMARY_CHARS = 1000
MAX_NOTE_CHARS = 1000


class NewApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    tenant_id: str
    requester_id: str
    thread_id: str
    request_id: str
    operation: str
    operation_hash: str
    required_permission: str
    approver_permission: str
    allow_self_approval: bool
    risk: RiskLevel
    # Only the requester may see and decide it (migration 0005).
    requester_only: bool = False
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    reason: str
    resource_refs: list[dict[str, str]] = Field(default_factory=list)


class ApprovalRecord(NewApproval):
    status: ApprovalStatus
    decided_by: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime
    executed_at: datetime | None = None
    execution_status: Literal["ok", "error"] | None = None
    created_at: datetime
    # Computed by the store's clock (the database clock for PostgreSQL) when read.
    expired: bool = False

    def public_view(self) -> dict[str, Any]:
        """What a requester or approver is shown (no hashes, no internal policy)."""
        return {
            "approval_id": str(self.id),
            "operation": self.operation,
            "summary": self.summary,
            "risk": self.risk,
            "status": "expired" if self._lapsed else self.status,
            "requested_by": self.requester_id,
            "expires_at": self.expires_at.isoformat(),
        }

    @property
    def _lapsed(self) -> bool:
        return self.expired and self.status in ("pending", "approved")


class OperationBinding(BaseModel):
    """What the resuming workflow expects the approval to be for."""

    model_config = ConfigDict(frozen=True)

    operation: str
    operation_hash: str
    thread_id: str
    request_id: str


class ApprovalStore(Protocol):
    async def create(
        self, new: NewApproval, ttl_seconds: int, audit: AuditEvent | None
    ) -> ApprovalRecord: ...

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord | None: ...

    async def find(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] = (),
        requester_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]: ...

    async def transition(
        self,
        tenant_id: str,
        approval_id: uuid.UUID,
        *,
        from_statuses: Sequence[str],
        to_status: ApprovalStatus,
        require_unexpired: bool,
        decided_by: str | None = None,
        note: str | None = None,
        execution_status: str | None = None,
        audit: AuditEvent | None = None,
    ) -> ApprovalRecord | None:
        """Compare-and-set, audited atomically. None if the row was not in `from_statuses`."""
        ...

    async def expire_due(self, limit: int | None = None) -> list[tuple[str, uuid.UUID]]: ...


def _record(row: ApprovalRequestRow, expired: bool) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        requester_id=row.requester_id,
        thread_id=row.thread_id,
        request_id=row.request_id,
        operation=row.operation,
        operation_hash=row.operation_hash,
        required_permission=row.required_permission,
        approver_permission=row.approver_permission,
        allow_self_approval=row.allow_self_approval,
        requester_only=row.requester_only,
        risk=row.risk,
        summary=row.summary,
        reason=row.reason,
        resource_refs=list(row.resource_refs),
        status=row.status,
        decided_by=row.decided_by,
        decision_note=row.decision_note,
        decided_at=row.decided_at,
        expires_at=row.expires_at,
        executed_at=row.executed_at,
        execution_status=row.execution_status,
        created_at=row.created_at,
        expired=expired,
    )


def _transition_values(
    to_status: str, decided_by: str | None, note: str | None, execution_status: str | None
) -> dict[str, Any]:
    values: dict[str, Any] = {"status": to_status}
    if to_status in ("approved", "rejected", "cancelled"):
        values.update(decided_by=decided_by, decided_at=func.now(), decision_note=note)
    elif to_status == "expired":
        values["decided_at"] = func.coalesce(ApprovalRequestRow.decided_at, func.now())
    elif to_status == "executed":
        values.update(executed_at=func.now(), execution_status=execution_status)
    return values


class PostgresApprovalStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._audit = AuditService(database)

    async def create(
        self, new: NewApproval, ttl_seconds: int, audit: AuditEvent | None
    ) -> ApprovalRecord:
        async with self._database.unit_of_work(tenant_id=new.tenant_id) as uow:
            row, expired = await ApprovalRepository(uow.session).insert(
                new.model_dump(), ttl_seconds
            )
            record = _record(row, expired)
            if audit is not None:
                await self._audit.record(audit, uow=uow)
            return record

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord | None:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            found = await ApprovalRepository(uow.session).get(tenant_id, approval_id)
            return _record(*found) if found else None

    async def find(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] = (),
        requester_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            rows = await ApprovalRepository(uow.session).find(
                tenant_id,
                statuses=statuses,
                requester_id=requester_id,
                thread_id=thread_id,
                limit=limit,
            )
            return [_record(*r) for r in rows]

    async def transition(
        self,
        tenant_id: str,
        approval_id: uuid.UUID,
        *,
        from_statuses: Sequence[str],
        to_status: ApprovalStatus,
        require_unexpired: bool,
        decided_by: str | None = None,
        note: str | None = None,
        execution_status: str | None = None,
        audit: AuditEvent | None = None,
    ) -> ApprovalRecord | None:
        async with self._database.unit_of_work(tenant_id=tenant_id) as uow:
            found = await ApprovalRepository(uow.session).transition(
                tenant_id,
                approval_id,
                from_statuses=from_statuses,
                values=_transition_values(to_status, decided_by, note, execution_status),
                require_unexpired=require_unexpired,
            )
            if found is None:
                return None
            if audit is not None:
                await self._audit.record(audit, uow=uow)
            return _record(*found)

    async def expire_due(self, limit: int | None = None) -> list[tuple[str, uuid.UUID]]:
        async with self._database.unit_of_work() as uow:
            await enable_maintenance(uow.session)
            return await ApprovalRepository(uow.session).expire_due(limit)


class InMemoryApprovalStore:
    """Process-local (development, tests). Same rules as the PostgreSQL store."""

    def __init__(
        self,
        *,
        auditor: Auditor | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._rows: dict[uuid.UUID, ApprovalRecord] = {}
        self._auditor = auditor
        self.now = now

    def _view(self, record: ApprovalRecord) -> ApprovalRecord:
        return record.model_copy(update={"expired": record.expires_at <= self.now()})

    async def create(
        self, new: NewApproval, ttl_seconds: int, audit: AuditEvent | None
    ) -> ApprovalRecord:
        now = self.now()
        record = ApprovalRecord(
            **new.model_dump(),
            status="pending",
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
        )
        self._rows[new.id] = record
        await self._record(audit)
        return self._view(record)

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord | None:
        record = self._rows.get(approval_id)
        return self._view(record) if record and record.tenant_id == tenant_id else None

    async def find(
        self,
        tenant_id: str,
        *,
        statuses: Sequence[str] = (),
        requester_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        found = [
            self._view(r)
            for r in self._rows.values()
            if r.tenant_id == tenant_id
            and (not statuses or r.status in statuses)
            and (requester_id is None or r.requester_id == requester_id)
            and (thread_id is None or r.thread_id == thread_id)
        ]
        found.sort(key=lambda r: r.created_at, reverse=True)
        return found[:limit]

    async def transition(
        self,
        tenant_id: str,
        approval_id: uuid.UUID,
        *,
        from_statuses: Sequence[str],
        to_status: ApprovalStatus,
        require_unexpired: bool,
        decided_by: str | None = None,
        note: str | None = None,
        execution_status: str | None = None,
        audit: AuditEvent | None = None,
    ) -> ApprovalRecord | None:
        record = self._rows.get(approval_id)
        if record is None or record.tenant_id != tenant_id or record.status not in from_statuses:
            return None
        now = self.now()
        if require_unexpired and record.expires_at <= now:
            return None
        update: dict[str, Any] = {"status": to_status}
        if to_status in ("approved", "rejected", "cancelled"):
            update.update(decided_by=decided_by, decided_at=now, decision_note=note)
        elif to_status == "expired":
            update["decided_at"] = record.decided_at or now
        elif to_status == "executed":
            update.update(executed_at=now, execution_status=execution_status)
        changed = record.model_copy(update=update)
        self._rows[approval_id] = changed
        await self._record(audit)
        return self._view(changed)

    async def expire_due(self, limit: int | None = None) -> list[tuple[str, uuid.UUID]]:
        now = self.now()
        due = sorted(
            (
                r
                for r in self._rows.values()
                if r.status in ("pending", "approved") and r.expires_at <= now
            ),
            key=lambda r: r.expires_at,
        )
        if limit is not None:
            due = due[: max(1, limit)]
        for r in due:
            self._rows[r.id] = r.model_copy(
                update={"status": "expired", "decided_at": r.decided_at or now}
            )
        return [(r.tenant_id, r.id) for r in due]

    async def _record(self, audit: AuditEvent | None) -> None:
        if audit is not None and self._auditor is not None:
            await self._auditor.record(audit)


def _visible(record: ApprovalRecord, principal: Principal) -> bool:
    """The requester sees their own requests; others need the approver permission,
    and never see a requester-only (private) request."""
    if record.requester_id == principal.user_id:
        return True
    return not record.requester_only and principal.has(record.approver_permission)


def _parse_id(approval_id: str | uuid.UUID) -> uuid.UUID | None:
    if isinstance(approval_id, uuid.UUID):
        return approval_id
    try:
        return uuid.UUID(str(approval_id))
    except ValueError:
        return None


class ApprovalService:
    def __init__(
        self,
        store: ApprovalStore,
        authorizer: Authorizer,
        settings: ApprovalSettings | None = None,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._audit: SecurityAudit = authorizer.audit
        self._settings = settings or ApprovalSettings()

    @property
    def store(self) -> ApprovalStore:
        return self._store

    # -- request ------------------------------------------------------------------------

    async def request(
        self,
        principal: Principal,
        *,
        operation: str,
        operation_hash: str,
        summary: str,
        required_permission: str,
        risk: RiskLevel,
        policy: ApprovalPolicy,
        thread_id: str,
        request_id: str,
        resource_refs: Sequence[dict[str, str]] = (),
    ) -> ApprovalRecord:
        """Create a pending approval for the principal's operation (audited atomically)."""
        await self._authorizer.require(
            principal, Permission.APPROVAL_REQUEST, resource=f"operation:{operation}"
        )
        approval_id = uuid.uuid4()
        text = " ".join(summary.split()) or operation
        new = NewApproval(
            id=approval_id,
            tenant_id=principal.tenant_id,
            requester_id=principal.user_id,
            thread_id=thread_id,
            request_id=request_id,
            operation=operation,
            operation_hash=operation_hash,
            required_permission=required_permission,
            approver_permission=policy.approver_permission,
            allow_self_approval=policy.allow_self_approval,
            requester_only=policy.requester_only,
            risk=risk,
            summary=text[: MAX_SUMMARY_CHARS - 1] + "…" if len(text) > MAX_SUMMARY_CHARS else text,
            reason=policy.reason,
            resource_refs=list(resource_refs),
        )
        event = self._audit.event(
            AuditAction.APPROVAL_REQUESTED,
            "ok",
            principal,
            actor_type="agent",
            tool_name=operation,
            input_hash=operation_hash,
            entity_refs=[{"type": "approval", "id": str(approval_id)}],
            details={"risk": risk, "reason": policy.reason},
        )
        record = await self._store.create(
            new, policy.ttl_seconds or self._settings.ttl_seconds, event
        )
        logger.info(
            "approval.requested",
            extra={"fields": {"approval_id": str(approval_id), "operation": operation}},
        )
        return record

    # -- read ---------------------------------------------------------------------------

    async def get(self, principal: Principal, approval_id: str | uuid.UUID) -> ApprovalRecord:
        """Visible to the requester and to users who may decide it; otherwise 'not found'."""
        record = await self._load(principal, approval_id)
        if not _visible(record, principal):
            await self._authorizer.deny(
                principal, None, reason="not_visible", resource=f"approval:{record.id}"
            )
            raise ApprovalNotFoundError("Approval not visible to the principal.")
        return record

    async def list_open(self, principal: Principal, *, limit: int = 50) -> list[ApprovalRecord]:
        """Pending requests the principal may decide, plus the principal's own."""
        found = await self._store.find(principal.tenant_id, statuses=("pending",), limit=limit)
        return [r for r in found if _visible(r, principal)]

    # -- decide / cancel ----------------------------------------------------------------

    async def decide(
        self,
        principal: Principal,
        approval_id: str | uuid.UUID,
        *,
        approve: bool,
        note: str | None = None,
    ) -> ApprovalRecord:
        record = await self.get(principal, approval_id)
        resource = f"approval:{record.id}"
        if not principal.has(record.approver_permission):
            await self._authorizer.deny(
                principal,
                record.approver_permission,
                reason="missing_permission",
                resource=resource,
            )
            raise PermissionDeniedError(f"{principal.user_id} may not decide {resource}.")
        if record.requester_id == principal.user_id and not record.allow_self_approval:
            await self._authorizer.deny(
                principal, record.approver_permission, reason="self_approval", resource=resource
            )
            raise PermissionDeniedError(
                f"Self-approval of {resource} is not allowed.",
                public_message="You cannot approve your own request.",
            )
        await self._ensure_open(principal, record)
        decision: Literal["approved", "rejected"] = "approved" if approve else "rejected"
        event = self._audit.event(
            AuditAction.APPROVAL_DECIDED,
            "ok",
            principal,
            tool_name=record.operation,
            input_hash=record.operation_hash,
            entity_refs=[{"type": "approval", "id": str(record.id)}],
            approval_decision=decision,
            approver_id=principal.user_id,
            details={"requester_id": record.requester_id},
        )
        updated = await self._store.transition(
            principal.tenant_id,
            record.id,
            from_statuses=("pending",),
            to_status=decision,
            require_unexpired=True,
            decided_by=principal.user_id,
            note=(note or "").strip()[:MAX_NOTE_CHARS] or None,
            audit=event,
        )
        if updated is None:  # lost a race with another decision or with expiry
            await self._ensure_open(principal, await self._load(principal, record.id))
            raise ApprovalAlreadyDecidedError(f"{resource} changed while being decided.")
        logger.info(
            "approval.decided",
            extra={"fields": {"approval_id": str(record.id), "decision": decision}},
        )
        return updated

    async def cancel(self, principal: Principal, approval_id: str | uuid.UUID) -> ApprovalRecord:
        """The requester withdraws a pending request."""
        record = await self.get(principal, approval_id)
        if record.requester_id != principal.user_id:
            await self._authorizer.deny(
                principal, None, reason="not_requester", resource=f"approval:{record.id}"
            )
            raise PermissionDeniedError("Only the requester can cancel an approval request.")
        await self._ensure_open(principal, record)
        updated = await self._cancel(principal, record)
        if updated is None:
            raise ApprovalAlreadyDecidedError("Approval changed while being cancelled.")
        return updated

    async def cancel_for_thread(self, principal: Principal, thread_id: str) -> int:
        """Withdraw the principal's open requests of one conversation (when it is forgotten)."""
        open_ = await self._store.find(
            principal.tenant_id,
            statuses=("pending", "approved"),
            requester_id=principal.user_id,
            thread_id=thread_id,
            limit=200,
        )
        cancelled = 0
        for record in open_:
            if await self._cancel(principal, record, from_statuses=("pending", "approved")):
                cancelled += 1
        return cancelled

    # -- execution ----------------------------------------------------------------------

    async def validate_for_execution(
        self,
        principal: Principal,
        approval_id: str | uuid.UUID,
        binding: OperationBinding,
    ) -> ApprovalRecord:
        """The approval the paused workflow may now act on. Raises an `ApprovalError` otherwise.

        Returns an `approved` record, or an `executed` one (already used: the caller
        must replay, never run the operation again).
        """
        try:
            return await self._validate(principal, approval_id, binding)
        except ApprovalError as exc:
            await self._audit.record(
                self._audit.event(
                    AuditAction.OPERATION_REJECTED,
                    "denied",
                    principal,
                    actor_type="agent",
                    tool_name=binding.operation,
                    error_code=exc.code,
                    input_hash=binding.operation_hash,
                    entity_refs=[{"type": "approval", "id": str(approval_id)}],
                    details={"reason": exc.detail},
                )
            )
            raise

    async def _validate(
        self,
        principal: Principal,
        approval_id: str | uuid.UUID,
        binding: OperationBinding,
    ) -> ApprovalRecord:
        parsed = _parse_id(approval_id)
        record = await self._store.get(principal.tenant_id, parsed) if parsed else None
        if record is None:  # missing, malformed, or another tenant's
            raise ApprovalInvalidError("No such approval in the principal's tenant.")
        if record.requester_id != principal.user_id:
            raise ApprovalInvalidError("Approval belongs to another user.")
        if (record.thread_id, record.request_id) != (binding.thread_id, binding.request_id):
            raise ApprovalInvalidError("Approval belongs to another workflow.")
        if record.operation != binding.operation:
            raise ApprovalInvalidError("Approval is for another operation.")
        if record.operation_hash != binding.operation_hash:
            raise ApprovalStaleError("Operation arguments changed after the approval request.")
        if record.status == "executed":
            return record
        if record.status in ("pending", "approved") and record.expired:
            await self._expire(principal, record)
            raise ApprovalExpiredError("Approval expired before use.")
        if record.status == "pending":
            raise ApprovalPendingError("Approval not decided yet.")
        if record.status == "rejected":
            raise ApprovalRejectedError("Approval was rejected.")
        if record.status == "expired":
            raise ApprovalExpiredError("Approval expired.")
        if record.status == "cancelled":
            raise ApprovalCancelledError("Approval was cancelled.")
        return record

    async def complete(
        self, principal: Principal, record: ApprovalRecord, *, ok: bool, error_code: str | None
    ) -> ApprovalRecord | None:
        """Mark an approved request used (`executed`), audited atomically."""
        event = self._audit.event(
            AuditAction.OPERATION_EXECUTED,
            "ok" if ok else "error",
            principal,
            actor_type="agent",
            tool_name=record.operation,
            error_code=error_code,
            input_hash=record.operation_hash,
            entity_refs=[{"type": "approval", "id": str(record.id)}],
            approval_decision="approved",
            approver_id=record.decided_by,
        )
        return await self._store.transition(
            principal.tenant_id,
            record.id,
            from_statuses=("approved",),
            to_status="executed",
            require_unexpired=False,
            execution_status="ok" if ok else "error",
            audit=event,
        )

    # -- maintenance --------------------------------------------------------------------

    async def expire_due(self, limit: int | None = None) -> int:
        """Retention job: expire overdue requests of every tenant (audited per tenant).

        `limit` bounds one call (Phase 6 maintenance runs it in batches)."""
        expired = await self._store.expire_due(limit)
        for tenant_id, approval_id in expired:
            await self._audit.record(
                self._audit.event(
                    AuditAction.APPROVAL_EXPIRED,
                    "ok",
                    None,
                    actor_type="system",
                    tenant_id=tenant_id,
                    entity_refs=[{"type": "approval", "id": str(approval_id)}],
                )
            )
        return len(expired)

    # -- helpers ------------------------------------------------------------------------

    async def _load(self, principal: Principal, approval_id: str | uuid.UUID) -> ApprovalRecord:
        parsed = _parse_id(approval_id)
        record = await self._store.get(principal.tenant_id, parsed) if parsed else None
        if record is None:
            raise ApprovalNotFoundError("No such approval in the principal's tenant.")
        return record

    async def _ensure_open(self, principal: Principal, record: ApprovalRecord) -> None:
        if record.status == "pending" and record.expired:
            await self._expire(principal, record)
            raise ApprovalExpiredError("Approval expired before it was decided.")
        if record.status == "expired":
            raise ApprovalExpiredError("Approval expired.")
        if record.status != "pending":
            raise ApprovalAlreadyDecidedError(f"Approval is already {record.status}.")

    async def _expire(self, principal: Principal, record: ApprovalRecord) -> None:
        await self._store.transition(
            principal.tenant_id,
            record.id,
            from_statuses=("pending", "approved"),
            to_status="expired",
            require_unexpired=False,
            audit=self._audit.event(
                AuditAction.APPROVAL_EXPIRED,
                "ok",
                None,
                actor_type="system",
                tenant_id=principal.tenant_id,
                entity_refs=[{"type": "approval", "id": str(record.id)}],
            ),
        )

    async def _cancel(
        self,
        principal: Principal,
        record: ApprovalRecord,
        *,
        from_statuses: Sequence[str] = ("pending",),
    ) -> ApprovalRecord | None:
        return await self._store.transition(
            principal.tenant_id,
            record.id,
            from_statuses=from_statuses,
            to_status="cancelled",
            require_unexpired=False,
            decided_by=principal.user_id,
            audit=self._audit.event(
                AuditAction.APPROVAL_CANCELLED,
                "ok",
                principal,
                entity_refs=[{"type": "approval", "id": str(record.id)}],
            ),
        )
