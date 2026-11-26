"""Permission model: platform permissions and the role catalogue (ADR-037).

Permissions are dotted strings, `<area>.<resource>.<action>` or `<area>.<action>`
(ARCHITECTURE.md § B.6). This module defines only the **platform** permissions
Phase 5 enforces. Future domain agents add their own strings (for example
`purchase.order.create`) as `ToolSpec.required_permission` values and grant
them through the role catalogue; nothing here has to change.

Roles map to permission sets and are additive. The built-in roles are
**provisional platform roles**, not business roles: the real role catalogue is an
open question (PROJECT_STATUS Q8). Replace them with `AUTH_ROLE_CATALOG_JSON`.

Permissions are resolved once, when a principal is authenticated, so every later
check is an in-memory set lookup (no per-check database query).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from agentic_platform.core.errors import ConfigurationError

_PERMISSION = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")


class Permission:
    """Platform permissions (string constants, so domain permissions can coexist)."""

    CONVERSATION_READ = "conversation.read"  # status/history of the caller's own threads
    CONVERSATION_WRITE = "conversation.write"  # start, continue and resume own threads
    CONVERSATION_DELETE = "conversation.delete"  # forget own threads
    MEMORY_READ = "memory.read"  # recall own + tenant-shared memory
    MEMORY_WRITE = "memory.write"  # write/update own private memory
    MEMORY_DELETE = "memory.delete"  # delete own private memory
    MEMORY_SHARED_WRITE = "memory.shared.write"  # tenant knowledge, shared facts, procedures
    MEMORY_SHARED_DELETE = "memory.shared.delete"
    TOOL_EXECUTE = "tool.execute"  # run (read) tools through the agent
    APPROVAL_REQUEST = "approval.request"  # ask for approval of a protected operation
    APPROVAL_DECIDE = "approval.decide"  # approve or reject other users' requests
    AUDIT_READ = "audit.read"  # read the tenant's audit trail


PLATFORM_PERMISSIONS = frozenset(
    value for name, value in vars(Permission).items() if name.isupper()
)

# Provisional platform roles (Q8). A typical user has `member` plus extra roles.
DEFAULT_ROLES: dict[str, frozenset[str]] = {
    "member": frozenset(
        {
            Permission.CONVERSATION_READ,
            Permission.CONVERSATION_WRITE,
            Permission.CONVERSATION_DELETE,
            Permission.MEMORY_READ,
            Permission.MEMORY_WRITE,
            Permission.MEMORY_DELETE,
            Permission.TOOL_EXECUTE,
            Permission.APPROVAL_REQUEST,
        }
    ),
    "knowledge_manager": frozenset(
        {Permission.MEMORY_SHARED_WRITE, Permission.MEMORY_SHARED_DELETE}
    ),
    "approver": frozenset({Permission.APPROVAL_DECIDE}),
    "auditor": frozenset({Permission.AUDIT_READ}),
    "tenant_admin": PLATFORM_PERMISSIONS,
}


def check_permission(value: str) -> str:
    if not isinstance(value, str) or not _PERMISSION.fullmatch(value):
        raise ConfigurationError(f"Invalid permission name {value!r}.")
    return value


class RoleCatalog:
    """Role → permissions. Unknown roles grant nothing."""

    def __init__(self, roles: Mapping[str, Iterable[str]] | None = None) -> None:
        source = DEFAULT_ROLES if roles is None else roles
        catalog: dict[str, frozenset[str]] = {}
        for role, permissions in source.items():
            if not isinstance(role, str) or not _ROLE.fullmatch(role):
                raise ConfigurationError(f"Invalid role name {role!r}.")
            catalog[role] = frozenset(check_permission(p) for p in permissions)
        self._roles = catalog

    @classmethod
    def from_json(cls, text: str | None) -> RoleCatalog:
        if text is None:
            return cls()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("AUTH_ROLE_CATALOG_JSON is not valid JSON.") from exc
        if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
            raise ConfigurationError(
                'AUTH_ROLE_CATALOG_JSON must look like {"role": ["permission", ...]}.'
            )
        return cls(data)

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(self._roles)

    def known(self, roles: Iterable[str]) -> frozenset[str]:
        return frozenset(r for r in roles if r in self._roles)

    def permissions_for(self, roles: Iterable[str]) -> frozenset[str]:
        granted: set[str] = set()
        for role in roles:
            granted |= self._roles.get(role, frozenset())
        return frozenset(granted)
