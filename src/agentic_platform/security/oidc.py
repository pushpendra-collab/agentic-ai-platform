"""OIDC / OAuth2 identity provider: signed JWT access tokens (Phase 6, ADR-043).

`OidcJwtProvider` verifies bearer tokens issued by any OpenID Connect provider that
publishes its signing keys as a JWKS document:

* **signature** with the provider's public key (JWKS, by `kid`); asymmetric
  algorithms from `AUTH_OIDC_ALGORITHMS` only, so neither `none` nor an HMAC
  "key confusion" token can pass;
* **expiry** (`exp`, required), **not-before** (`nbf`) and **issued-at** (`iat`,
  required) with `AUTH_OIDC_LEEWAY_SECONDS` of clock skew;
* **issuer** (`iss`) = `AUTH_OIDC_ISSUER` and **audience** (`aud`) contains
  `AUTH_OIDC_AUDIENCE`, both required;
* a user claim (`AUTH_OIDC_USER_CLAIM`, default `sub`) and a tenant claim
  (`AUTH_OIDC_TENANT_CLAIM`); roles from `AUTH_OIDC_ROLES_CLAIM` are mapped later;
* optionally (Phase 7) a **maximum lifetime** (`exp - iat` at most
  `AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS`), which bounds how long a token outlives a
  disabled user or a removed role (JWTs are not revocable, ADR-048).

Keys are fetched from `AUTH_OIDC_JWKS_URL`, or discovered from
`<issuer>/.well-known/openid-configuration`, cached for
`AUTH_OIDC_JWKS_CACHE_SECONDS`, and refreshed (at most every 30 s) when a token
names an unknown `kid` (key rotation). In production the key URLs must be https.

Not implemented (documented in PROJECT_STATUS): token revocation / introspection,
refresh tokens (the client and the provider handle them), opaque (non-JWT) access
tokens, mTLS-bound tokens. Phase 7 verified it against a real OIDC implementation
(a local Keycloak 26 reference instance, `tests/live/test_oidc_live.py`); no
production provider has been chosen yet (Q6).

Needs PyJWT with `cryptography` (`pip install -e ".[oidc]"`), imported here only,
so deployments that use development tokens do not need it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import (
    ConfigurationError,
    IdentityProviderUnavailableError,
    InvalidIdentityError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.security.identity_provider import ExternalIdentity

try:
    import jwt
    from jwt import PyJWK
except ImportError as exc:  # pragma: no cover - depends on the installation
    raise ConfigurationError(
        'AUTH_PROVIDER=oidc needs PyJWT with cryptography: pip install -e ".[oidc]".'
    ) from exc

logger = get_logger(__name__)

_MAX_TOKEN_CHARS = 8192
_MAX_JWKS_BYTES = 1_048_576
_MIN_REFRESH_SECONDS = 30.0
_DISCOVERY_PATH = "/.well-known/openid-configuration"


class JwksCache:
    """The provider's public signing keys, by `kid`, with a TTL and rotation refresh."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks_url: str | None,
        timeout_seconds: float,
        cache_seconds: float,
        require_https: bool,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._jwks_url = jwks_url
        self._timeout = timeout_seconds
        self._ttl = cache_seconds
        self._require_https = require_https
        self._transport = transport
        self._clock = clock
        self._keys: dict[str | None, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._lock: asyncio.Lock | None = None
        for url in (issuer, jwks_url):
            if url is not None:
                self._check_url(url)

    async def key_for(self, kid: str | None, alg: str) -> Any:
        """The verification key for `kid`. Raises `InvalidIdentityError` if unknown."""
        jwk = await self._lookup(kid, force=False)
        if jwk is None and self._may_refresh():
            jwk = await self._lookup(kid, force=True)
        if jwk is None:
            raise InvalidIdentityError("Token signed with an unknown key.")
        if jwk.get("alg") not in (None, alg):
            raise InvalidIdentityError("Token algorithm does not match its key.")
        try:
            return PyJWK(jwk, algorithm=alg).key
        except jwt.PyJWTError as exc:
            raise InvalidIdentityError("Token key is not usable for its algorithm.") from exc

    # -- internals ----------------------------------------------------------------------

    async def _lookup(self, kid: str | None, *, force: bool) -> dict[str, Any] | None:
        if force or self._stale():
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:
                if force or self._stale():  # another request may have refreshed meanwhile
                    await self._refresh()
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return self._keys.get(kid)

    def _stale(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at >= self._ttl

    def _may_refresh(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at >= _MIN_REFRESH_SECONDS

    async def _refresh(self) -> None:
        url = self._jwks_url or await self._discover()
        document = await self._get_json(url)
        raw = document.get("keys")
        if not isinstance(raw, list):
            raise IdentityProviderUnavailableError("JWKS document has no 'keys' list.")
        keys: dict[str | None, dict[str, Any]] = {}
        for jwk in raw:
            if not isinstance(jwk, dict) or jwk.get("use") not in (None, "sig"):
                continue
            if jwk.get("kty") not in ("RSA", "EC"):  # never symmetric ("oct") keys
                continue
            kid = jwk.get("kid")
            keys[kid if isinstance(kid, str) else None] = jwk
        self._keys = keys
        self._fetched_at = self._clock()
        logger.info("auth.oidc.jwks_refreshed", extra={"fields": {"keys": len(keys)}})

    async def _discover(self) -> str:
        document = await self._get_json(self._issuer + _DISCOVERY_PATH)
        if str(document.get("issuer", "")).rstrip("/") != self._issuer:
            raise IdentityProviderUnavailableError("Discovery document names another issuer.")
        url = document.get("jwks_uri")
        if not isinstance(url, str):
            raise IdentityProviderUnavailableError("Discovery document has no jwks_uri.")
        self._check_url(url)
        self._jwks_url = url
        return url

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
            if response.status_code != 200 or len(response.content) > _MAX_JWKS_BYTES:
                raise IdentityProviderUnavailableError(
                    f"Key endpoint answered HTTP {response.status_code}."
                )
            data = response.json()
        except IdentityProviderUnavailableError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityProviderUnavailableError("Key endpoint unreachable or invalid.") from exc
        if not isinstance(data, dict):
            raise IdentityProviderUnavailableError("Key endpoint returned no JSON object.")
        return data

    def _check_url(self, url: str) -> None:
        if not url.startswith(("https://", "http://")):
            raise ConfigurationError("OIDC issuer and JWKS URLs must be http(s) URLs.")
        if self._require_https and not url.startswith("https://"):
            raise ConfigurationError("OIDC issuer and JWKS URLs must use https in production.")


class OidcJwtProvider:
    """`IdentityProvider` for OIDC/OAuth2 JWT access tokens."""

    name = "oidc"

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keys: JwksCache,
        algorithms: list[str],
        user_claim: str = "sub",
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        leeway_seconds: int = 30,
        max_lifetime_seconds: int | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._keys = keys
        self._algorithms = list(algorithms)
        self._user_claim = user_claim
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._leeway = leeway_seconds
        self._max_lifetime = max_lifetime_seconds

    @classmethod
    def from_settings(
        cls, settings: AppSettings, *, transport: object | None = None
    ) -> OidcJwtProvider:
        auth = settings.auth
        if auth.oidc_issuer is None or auth.oidc_audience is None:
            raise ConfigurationError(
                "AUTH_PROVIDER=oidc needs AUTH_OIDC_ISSUER and AUTH_OIDC_AUDIENCE."
            )
        if transport is not None and not isinstance(transport, httpx.AsyncBaseTransport):
            raise ConfigurationError("transport must be an httpx.AsyncBaseTransport.")
        return cls(
            issuer=auth.oidc_issuer,
            audience=auth.oidc_audience,
            keys=JwksCache(
                issuer=auth.oidc_issuer,
                jwks_url=auth.oidc_jwks_url,
                timeout_seconds=auth.oidc_http_timeout_seconds,
                cache_seconds=auth.oidc_jwks_cache_seconds,
                require_https=settings.env == "production",
                transport=transport,
            ),
            algorithms=auth.oidc_algorithms,
            user_claim=auth.oidc_user_claim,
            tenant_claim=auth.oidc_tenant_claim,
            roles_claim=auth.oidc_roles_claim,
            leeway_seconds=auth.oidc_leeway_seconds,
            max_lifetime_seconds=auth.oidc_max_token_lifetime_seconds,
        )

    async def verify(self, token: str) -> ExternalIdentity:
        if len(token) > _MAX_TOKEN_CHARS or token.count(".") != 2:
            raise InvalidIdentityError("Malformed token.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidIdentityError("Malformed token header.") from exc
        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in self._algorithms:
            raise InvalidIdentityError("Token algorithm not allowed.")
        kid = header.get("kid")
        key = await self._keys.key_for(kid if isinstance(kid, str) else None, alg)
        claims = self._decode(token, key, alg)
        self._check_lifetime(claims)
        return ExternalIdentity(
            provider=self.name,
            issuer=str(claims.get("iss")),
            subject=_string_claim(claims, self._user_claim, "user"),
            tenant_id=_optional_string(claims.get(self._tenant_claim)),
            roles=_roles(claims.get(self._roles_claim)),
            session_id=_session(claims.get("sid")),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )

    def _check_lifetime(self, claims: dict[str, Any]) -> None:
        if self._max_lifetime is None:
            return
        try:
            lifetime = int(claims["exp"]) - int(claims["iat"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidIdentityError("Token lacks a required claim.") from exc
        if lifetime > self._max_lifetime:
            raise InvalidIdentityError("Token lifetime exceeds the accepted maximum.")

    def _decode(self, token: str, key: Any, alg: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidIdentityError("Token expired.") from exc
        except jwt.ImmatureSignatureError as exc:
            raise InvalidIdentityError("Token not valid yet.") from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidIdentityError("Unexpected token audience.") from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidIdentityError("Unexpected token issuer.") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise InvalidIdentityError("Token lacks a required claim.") from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidIdentityError("Bad token signature.") from exc
        except jwt.PyJWTError as exc:
            raise InvalidIdentityError("Invalid token.") from exc
        return claims


def _string_claim(claims: dict[str, Any], name: str, label: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidIdentityError(f"Token has no {label} claim.")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _session(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _roles(value: Any) -> tuple[str, ...]:
    """A list of strings, or one space-separated string (both are common)."""
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, list):
        return tuple(v for v in value if isinstance(v, str))
    return ()
