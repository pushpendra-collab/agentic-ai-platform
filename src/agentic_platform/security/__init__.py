"""Identity, authorization and the security audit trail (Phase 5, ADR-037 to ADR-041).

    Credentials ──► Authenticator ──► Principal (user, tenant, roles → permissions)
                                         │  acting_as(principal)
                                         ▼
    Agent ─► ConversationService (ownership) ─► graph ─► ToolRegistry / AuthorizedMemory
                                                            │
                                     Authorizer (require / require_tenant / require_owner)
                                                            │
                                     ApprovalService (HITL) · SecurityAudit (audit_log)

The LLM is never an authority here: identity comes from authentication, and every
permission, ownership and approval decision is made by this package's code.
"""

from agentic_platform.security.audit import AuditAction, SecurityAudit
from agentic_platform.security.authentication import (
    Authenticator,
    HmacTokenAuthenticator,
    LocalIdentity,
    build_token_authenticator,
)
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.context import acting_as, current_principal, require_principal
from agentic_platform.security.identity import IdentityResolver, Principal
from agentic_platform.security.identity_provider import (
    ExternalIdentity,
    IdentityMapper,
    IdentityProvider,
    ProviderAuthenticator,
    RoleMapping,
    build_authenticator,
    resolve_auth_provider,
)
from agentic_platform.security.permissions import (
    DEFAULT_ROLES,
    PLATFORM_PERMISSIONS,
    Permission,
    RoleCatalog,
)

__all__ = [
    "DEFAULT_ROLES",
    "PLATFORM_PERMISSIONS",
    "AuditAction",
    "Authenticator",
    "Authorizer",
    "ExternalIdentity",
    "HmacTokenAuthenticator",
    "IdentityMapper",
    "IdentityProvider",
    "IdentityResolver",
    "LocalIdentity",
    "Permission",
    "Principal",
    "ProviderAuthenticator",
    "RoleCatalog",
    "RoleMapping",
    "SecurityAudit",
    "acting_as",
    "build_authenticator",
    "build_token_authenticator",
    "current_principal",
    "require_principal",
    "resolve_auth_provider",
]
