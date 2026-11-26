"""Identity-provider boundary (Phase 6, ADR-043).

    Authorization: Bearer <token>
          │
          ▼
    IdentityProvider.verify(token)    provider-specific: signature, expiry, issuer,
          │                           audience (`security/oidc.py` for OIDC/JWT)
          ▼
    ExternalIdentity                  subject, tenant, external roles, session.
          │                           Verified, but NOT an authority for permissions
          ▼
    IdentityMapper.to_principal()     external roles → platform roles (RoleMapping);
          │                           permissions resolved by the server's RoleCatalog
          ▼
    Principal ──► Authorizer ──► application

A concrete provider (Keycloak, Entra ID, Okta, Auth0, a company SSO, ...) is chosen
by configuration, not by code changes elsewhere: only an `IdentityProvider`
implementation knows token formats. Everything downstream sees a `Principal`.

Rules kept from Phase 5 (ADR-037):

* claims can name roles at most; a `permissions` claim is ignored, and roles the
  catalogue does not know grant nothing;
* failed sign-ins are audited with a reason code, never with the credential;
* the server stays responsible for every authorization decision.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentic_platform.core.config import AppSettings, AuthProvider
from agentic_platform.core.errors import (
    ConfigurationError,
    IdentityProviderUnavailableError,
    UnauthenticatedError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.security.audit import AuditAction, SecurityAudit
from agentic_platform.security.authentication import Authenticator, build_token_authenticator
from agentic_platform.security.identity import IdentityResolver, Principal

logger = get_logger(__name__)


class ExternalIdentity(BaseModel):
    """What an identity provider vouches for, after its token was verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    subject: str
    tenant_id: str | None = None
    # External role or group names as the provider spells them. Mapped, never trusted.
    roles: tuple[str, ...] = ()
    issuer: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None


class IdentityProvider(Protocol):
    """Verifies a provider token. Raises `InvalidIdentityError` for bad tokens and
    `IdentityProviderUnavailableError` when the provider itself cannot be reached."""

    name: str

    async def verify(self, token: str) -> ExternalIdentity: ...


class RoleMapping:
    """External role/group names → platform roles.

    Without a mapping, external names are used as platform role names (unknown ones
    are dropped by the role catalogue). With one, only mapped names grant roles.
    """

    def __init__(self, mapping: Mapping[str, Iterable[str]] | None = None) -> None:
        self._mapping = (
            None
            if mapping is None
            else {str(k): tuple(str(r) for r in v) for k, v in mapping.items()}
        )

    @classmethod
    def from_json(cls, text: str | None) -> RoleMapping:
        if text is None:
            return cls()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("AUTH_OIDC_ROLE_MAP_JSON is not valid JSON.") from exc
        if not isinstance(data, dict) or not all(
            isinstance(v, list) and all(isinstance(r, str) for r in v) for v in data.values()
        ):
            raise ConfigurationError(
                'AUTH_OIDC_ROLE_MAP_JSON must look like {"external": ["platform_role", ...]}.'
            )
        return cls(data)

    def map(self, external: Iterable[str]) -> list[str]:
        if self._mapping is None:
            return list(external)
        roles: list[str] = []
        for name in external:
            roles.extend(self._mapping.get(name, ()))
        return roles


class IdentityMapper:
    """`ExternalIdentity` → `Principal`, through the server's role catalogue."""

    def __init__(self, resolver: IdentityResolver, roles: RoleMapping | None = None) -> None:
        self._resolver = resolver
        self._roles = roles or RoleMapping()

    def to_principal(self, identity: ExternalIdentity) -> Principal:
        return self._resolver.resolve(
            user_id=identity.subject,
            tenant_id=identity.tenant_id,
            roles=self._roles.map(identity.roles),
            auth_method="oidc",
            session_id=identity.session_id,
        )


class ProviderAuthenticator:
    """`Authenticator` for any `IdentityProvider` (credential in, `Principal` out)."""

    def __init__(
        self,
        provider: IdentityProvider,
        mapper: IdentityMapper,
        audit: SecurityAudit | None = None,
    ) -> None:
        self._provider = provider
        self._mapper = mapper
        self._audit = audit or SecurityAudit(None)

    @property
    def provider(self) -> IdentityProvider:
        return self._provider

    async def authenticate(self, credential: str | None) -> Principal:
        try:
            if not credential:
                raise UnauthenticatedError("No credential presented.")
            identity = await self._provider.verify(credential)
            principal = self._mapper.to_principal(identity)
        except UnauthenticatedError as exc:
            await self._audit.record(
                self._audit.event(
                    AuditAction.AUTH_FAILED,
                    "denied",
                    None,
                    error_code=exc.code,
                    details={"reason": exc.detail, "method": self._provider.name},
                )
            )
            raise
        except IdentityProviderUnavailableError as exc:
            logger.error(
                "auth.provider_unavailable",
                extra={"fields": {"method": self._provider.name, "detail": exc.detail}},
            )
            raise
        logger.info("auth.authenticated", extra={"fields": {"method": self._provider.name}})
        return principal


def resolve_auth_provider(settings: AppSettings) -> AuthProvider:
    """Which bearer tokens the HTTP API accepts. Raises `ConfigurationError` when none
    is usable, and always for development tokens in production."""
    auth = settings.auth
    provider = auth.provider
    production = settings.env == "production"
    if provider is None:
        if auth.token_secret is None or production:
            raise ConfigurationError(
                "No HTTP authentication is configured. Set AUTH_PROVIDER=oidc with "
                "AUTH_OIDC_ISSUER and AUTH_OIDC_AUDIENCE, or (outside production only) "
                "AUTH_TOKEN_SECRET for development tokens."
            )
        provider = "dev_token"
    if provider == "dev_token":
        if production:
            raise ConfigurationError(
                "Development tokens are not accepted in production; set AUTH_PROVIDER=oidc."
            )
        if auth.token_secret is None:
            raise ConfigurationError("AUTH_PROVIDER=dev_token needs AUTH_TOKEN_SECRET.")
    return provider


def build_authenticator(
    settings: AppSettings,
    resolver: IdentityResolver,
    audit: SecurityAudit | None = None,
    *,
    transport: object | None = None,
) -> Authenticator:
    """The HTTP API's authenticator for the configured provider.

    `transport` is an optional `httpx.AsyncBaseTransport` for the OIDC key fetch
    (tests). The local development identity is never returned: it is not an HTTP
    credential."""
    provider = resolve_auth_provider(settings)
    if provider == "dev_token":
        tokens = build_token_authenticator(settings, resolver, audit)
        assert tokens is not None  # resolve_auth_provider checked AUTH_TOKEN_SECRET
        return tokens
    from agentic_platform.security.oidc import OidcJwtProvider  # optional dependency

    return ProviderAuthenticator(
        OidcJwtProvider.from_settings(settings, transport=transport),
        IdentityMapper(resolver, RoleMapping.from_json(settings.auth.oidc_role_map_json)),
        audit,
    )
