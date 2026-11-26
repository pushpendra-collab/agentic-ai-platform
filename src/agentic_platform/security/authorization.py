"""Authorization: the one place that says yes or no (ADR-038).

Every protected path calls the `Authorizer`: the agent entry point
(conversations), the tool registry (tools), `AuthorizedMemory` (memory), the
approval service (approvals) and the audit reader. Decisions use only the
authenticated `Principal` and server-side resource data; prompts, LLM output and
client-supplied identifiers never decide.

* `can()` is a pure check (tool listing, filters).
* `require()` raises `PermissionDeniedError` and audits the denial.
* `require_tenant()` / `require_owner()` hide other tenants' and users' resources
  behind the same "not found" error a missing resource gets, and audit the attempt.

Permissions were resolved at authentication time, so checks are set lookups:
no database round trip per check (no N+1 authorization queries).
"""

from __future__ import annotations

from agentic_platform.core.errors import (
    CrossTenantAccessError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit
from agentic_platform.security.audit import AuditAction, SecurityAudit
from agentic_platform.security.identity import Principal

logger = get_logger(__name__)


class Authorizer:
    def __init__(self, audit: SecurityAudit | None = None) -> None:
        self._audit = audit or SecurityAudit(None)

    @property
    def audit(self) -> SecurityAudit:
        return self._audit

    @staticmethod
    def can(principal: Principal, permission: str) -> bool:
        return principal.has(permission)

    async def require(
        self,
        principal: Principal,
        permission: str,
        *,
        resource: str | None = None,
        public_message: str | None = None,
    ) -> None:
        if principal.has(permission):
            return
        await self.deny(principal, permission, reason="missing_permission", resource=resource)
        raise PermissionDeniedError(
            f"{principal.user_id} lacks {permission}"
            + (f" for {resource}" if resource else "")
            + ".",
            public_message=public_message,
        )

    async def require_tenant(
        self,
        principal: Principal,
        tenant_id: str,
        *,
        resource: str,
        error: type[ResourceNotFoundError] = CrossTenantAccessError,
    ) -> None:
        """The resource must belong to the principal's tenant; otherwise it does not exist."""
        if tenant_id == principal.tenant_id:
            return
        await self.deny(principal, None, reason="cross_tenant", resource=resource)
        raise error(f"Cross-tenant access to {resource} refused.")

    async def require_owner(
        self,
        principal: Principal,
        owner_user_id: str,
        *,
        resource: str,
        error: type[ResourceNotFoundError] = ResourceNotFoundError,
    ) -> None:
        """Private resources are visible to their owner only (no sharing yet, ADR-039)."""
        if owner_user_id == principal.user_id:
            return
        await self.deny(principal, None, reason="not_owner", resource=resource)
        raise error(f"{principal.user_id} is not the owner of {resource}.")

    async def deny(
        self,
        principal: Principal | None,
        permission: str | None,
        *,
        reason: str,
        resource: str | None = None,
        actor_type: str = "user",
    ) -> None:
        """Record a refused attempt (audit + log). Never raises.

        The audit row is the authoritative record. The observability event (Phase 13) is
        for metrics and diagnostics and carries less: no tenant, user or resource id."""
        logger.warning(
            "authz.denied",
            extra={"fields": {"permission": permission, "reason": reason, "resource": resource}},
        )
        code = (
            PermissionDeniedError.code
            if reason == "missing_permission"
            else ResourceNotFoundError.code
        )
        emit(
            EventName.AUTHZ_DENIED,
            stage=Stage.SECURITY,
            status="rejected",
            error_category="authorization",
            error_code=code,
            attributes={"permission": permission, "reason": reason},
        )
        await self._audit.record(
            self._audit.event(
                AuditAction.AUTHZ_DENIED,
                "denied",
                principal,
                actor_type="agent" if actor_type == "agent" else "user",
                error_code=PermissionDeniedError.code
                if reason == "missing_permission"
                else ResourceNotFoundError.code,
                details={"permission": permission, "reason": reason, "resource": resource},
            )
        )
