"""Authentication: credentials in, `Principal` out (ADR-037).

There is no identity provider yet (PROJECT_STATUS Q6), so two development
mechanisms sit behind one replaceable interface:

* `HmacTokenAuthenticator`: compact tokens signed with HMAC-SHA256
  (`AUTH_TOKEN_SECRET`). Claims: user (`sub`), tenant (`tid`), roles, issue and
  expiry time, session id. Roles are resolved to permissions on the server;
  a token can never carry permissions. A production deployment replaces this
  class with an OIDC/JWT authenticator implementing the same `Authenticator`
  protocol; nothing downstream changes.
* `LocalIdentity`: a trusted local operator (CLI, scripts) names the tenant,
  user and roles. Refused in production unless `AUTH_ALLOW_LOCAL_IDENTITY=true`.
  Never accepted by the HTTP API (Phase 6).

Phase 6 adds the identity-provider boundary (`security/identity_provider.py`,
`security/oidc.py`) behind the same `Authenticator` protocol.

Failed authentications are audited without the credential; the reason is in
`detail` and the audit record, never in the public message.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterable
from typing import Any, Protocol

from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import (
    ConfigurationError,
    InvalidIdentityError,
    UnauthenticatedError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.security.audit import AuditAction, SecurityAudit
from agentic_platform.security.identity import IdentityResolver, Principal

logger = get_logger(__name__)

TOKEN_VERSION = 1
_CLOCK_SKEW_SECONDS = 30
_MAX_TOKEN_CHARS = 4096


class Authenticator(Protocol):
    async def authenticate(self, credential: str | None) -> Principal:
        """Verify `credential` or raise `UnauthenticatedError` / `InvalidIdentityError`."""
        ...


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class HmacTokenAuthenticator:
    """Development/test bearer tokens: `<base64url(claims)>.<base64url(hmac)>`."""

    def __init__(
        self,
        secret: str,
        resolver: IdentityResolver,
        *,
        issuer: str,
        max_ttl_seconds: int,
        audit: SecurityAudit | None = None,
        now: Any = time.time,
        audience: str | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ConfigurationError("The token secret must be at least 32 characters.")
        self._key = secret.encode("utf-8")
        self._resolver = resolver
        self._issuer = issuer
        self._max_ttl = max_ttl_seconds
        self._audit = audit or SecurityAudit(None)
        self._now = now
        self._audience = audience

    def issue(
        self,
        *,
        user_id: str,
        tenant_id: str,
        roles: Iterable[str],
        ttl_seconds: int = 3600,
        session_id: str | None = None,
    ) -> str:
        """Mint a token (development and tests only: production tokens come from the IdP)."""
        if not 0 < ttl_seconds <= self._max_ttl:
            raise ConfigurationError(f"Token TTL must be 1-{self._max_ttl} seconds.")
        issued = int(self._now())
        claims = {
            "v": TOKEN_VERSION,
            "iss": self._issuer,
            "sub": user_id,
            "tid": tenant_id,
            "roles": sorted(set(roles)),
            "iat": issued,
            "exp": issued + ttl_seconds,
            "sid": session_id or uuid.uuid4().hex,
        }
        if self._audience is not None:
            claims["aud"] = self._audience
        payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        return f"{payload}.{self._sign(payload)}"

    async def authenticate(self, credential: str | None) -> Principal:
        try:
            if not credential:
                raise UnauthenticatedError("No credential presented.")
            claims = self._verify(credential)
            principal = self._resolver.resolve(
                user_id=claims.get("sub"),
                tenant_id=claims.get("tid"),
                roles=[r for r in claims.get("roles") or [] if isinstance(r, str)],
                auth_method="token",
                session_id=claims.get("sid") if isinstance(claims.get("sid"), str) else None,
            )
        except UnauthenticatedError as exc:
            await self._audit.record(
                self._audit.event(
                    AuditAction.AUTH_FAILED,
                    "denied",
                    None,
                    error_code=exc.code,
                    details={"reason": exc.detail, "method": "token"},
                )
            )
            raise
        logger.info("auth.authenticated", extra={"fields": {"method": "token"}})
        return principal

    def _sign(self, payload: str) -> str:
        return _b64encode(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())

    def _verify(self, token: str) -> dict[str, Any]:
        if len(token) > _MAX_TOKEN_CHARS or token.count(".") != 1:
            raise InvalidIdentityError("Malformed token.")
        payload, signature = token.split(".")
        if not hmac.compare_digest(self._sign(payload), signature):
            raise InvalidIdentityError("Bad token signature.")
        try:
            claims = json.loads(_b64decode(payload))
        except (binascii.Error, ValueError) as exc:
            raise InvalidIdentityError("Undecodable token claims.") from exc
        if not isinstance(claims, dict) or claims.get("v") != TOKEN_VERSION:
            raise InvalidIdentityError("Unsupported token version.")
        if claims.get("iss") != self._issuer:
            raise InvalidIdentityError("Unexpected token issuer.")
        if self._audience is not None and claims.get("aud") != self._audience:
            raise InvalidIdentityError("Unexpected token audience.")
        issued, expires = claims.get("iat"), claims.get("exp")
        if not isinstance(issued, int) or not isinstance(expires, int):
            raise InvalidIdentityError("Token has no validity period.")
        if not isinstance(claims.get("roles", []), list):
            raise InvalidIdentityError("Token roles must be a list.")
        now = self._now()
        if expires - issued > self._max_ttl:
            raise InvalidIdentityError("Token validity exceeds the allowed maximum.")
        if issued > now + _CLOCK_SKEW_SECONDS:
            raise InvalidIdentityError("Token issued in the future.")
        if now >= expires:
            raise InvalidIdentityError("Token expired.")
        return claims


class LocalIdentity:
    """Identity asserted by a trusted local operator (CLI flags). Development only."""

    def __init__(self, settings: AppSettings, resolver: IdentityResolver) -> None:
        self._settings = settings
        self._resolver = resolver

    @property
    def allowed(self) -> bool:
        return self._settings.local_identity_allowed

    def principal(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        roles: Iterable[str] | None = None,
    ) -> Principal:
        if not self.allowed:
            raise UnauthenticatedError(
                "Local identity is disabled (production, or AUTH_ALLOW_LOCAL_IDENTITY=false)."
            )
        memory = self._settings.memory
        return self._resolver.resolve(
            user_id=user_id or memory.default_user_id,
            tenant_id=tenant_id or memory.default_tenant_id,
            roles=list(roles) if roles is not None else self._settings.auth.local_roles,
            auth_method="local",
        )


def build_token_authenticator(
    settings: AppSettings, resolver: IdentityResolver, audit: SecurityAudit | None = None
) -> HmacTokenAuthenticator | None:
    """The configured token authenticator, or None without AUTH_TOKEN_SECRET.

    Always None in production (Phase 7): development tokens are refused by every entry
    point there (HTTP API, CLI, scripts), not only by the HTTP API."""
    auth = settings.auth
    if auth.token_secret is None:
        return None
    if settings.env == "production":
        logger.warning("auth.dev_tokens.refused_in_production")
        return None
    return HmacTokenAuthenticator(
        auth.token_secret.get_secret_value(),
        resolver,
        issuer=auth.token_issuer,
        max_ttl_seconds=auth.token_max_ttl_seconds,
        audit=audit,
        audience=auth.token_audience,
    )
