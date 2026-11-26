"""Authenticated identity (ADR-037).

A `Principal` is who is making a request: user, tenant (company), roles and the
permissions those roles grant. It is created **only** by an authenticator (or the
local-operator path) through `IdentityResolver`, never from LLM output and never
from client-supplied permission lists: clients name roles at most, and the
server resolves roles to permissions.

The principal is the trusted source of `tenant_id` / `user_id` for everything
downstream (conversations, memory scope, tools, approvals, audit).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_platform.core.errors import InvalidIdentityError, MissingTenantError
from agentic_platform.memory.models import MemoryScope
from agentic_platform.security.permissions import RoleCatalog

_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")

AuthMethod = Literal["token", "oidc", "local", "service"]


class Principal(BaseModel):
    """An authenticated user acting within one tenant. Immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(pattern=_ID.pattern)
    tenant_id: str = Field(pattern=_ID.pattern)
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    auth_method: AuthMethod
    session_id: str | None = Field(default=None, max_length=128)
    authenticated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def memory_scope(self) -> MemoryScope:
        """The only memory scope this principal may use for private memory."""
        return MemoryScope(self.tenant_id, self.user_id)

    @property
    def actor(self) -> dict[str, str]:
        """Identifiers only (no permissions): what graph state remembers about the actor."""
        return {"tenant_id": self.tenant_id, "user_id": self.user_id}

    def log_fields(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "user_id": self.user_id}


class IdentityResolver:
    """Turns verified identity claims into a `Principal` (roles → permissions)."""

    def __init__(self, catalog: RoleCatalog | None = None) -> None:
        self._catalog = catalog or RoleCatalog()

    @property
    def catalog(self) -> RoleCatalog:
        return self._catalog

    def resolve(
        self,
        *,
        user_id: object,
        tenant_id: object,
        roles: Iterable[str],
        auth_method: AuthMethod,
        session_id: str | None = None,
    ) -> Principal:
        if tenant_id is None or (isinstance(tenant_id, str) and not tenant_id.strip()):
            raise MissingTenantError("Identity has no tenant.")
        if not isinstance(user_id, str) or not _ID.fullmatch(user_id):
            raise InvalidIdentityError("Identity has no valid user id.")
        if not isinstance(tenant_id, str) or not _ID.fullmatch(tenant_id):
            raise InvalidIdentityError("Identity has an invalid tenant id.")
        known = self._catalog.known(roles)
        return Principal(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=known,
            permissions=self._catalog.permissions_for(known),
            auth_method=auth_method,
            session_id=session_id,
        )
