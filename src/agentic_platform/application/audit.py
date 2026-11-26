"""Reading the audit trail (needs `audit.read`; always the reader's own tenant).

Used by the CLI (`--audit`) and the HTTP API. Tenant-less system rows (failed
sign-ins, maintenance runs) are never returned: row-level security hides them from
tenant-bound reads (ADR-041).
"""

from __future__ import annotations

from agentic_platform.core.errors import FeatureUnavailableError
from agentic_platform.core.threads import validate_thread_id
from agentic_platform.db.engine import Database
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.identity import Principal
from agentic_platform.security.permissions import Permission
from agentic_platform.services.audit import AuditRecord, AuditService

MAX_AUDIT_READ = 200


class AuditReader:
    def __init__(self, authorizer: Authorizer, database: Database | None) -> None:
        self._authorizer = authorizer
        self._service = AuditService(database) if database is not None else None

    async def list(
        self, principal: Principal, *, thread_id: str | None = None, limit: int = 50
    ) -> list[AuditRecord]:
        await self._authorizer.require(principal, Permission.AUDIT_READ, resource="audit_log")
        if self._service is None:
            raise FeatureUnavailableError("The audit log needs DATABASE_URL.")
        limit = max(1, min(limit, MAX_AUDIT_READ))
        if thread_id is not None:
            return await self._service.for_thread(
                validate_thread_id(thread_id), tenant_id=principal.tenant_id, limit=limit
            )
        return await self._service.for_tenant(principal.tenant_id, limit=limit)
