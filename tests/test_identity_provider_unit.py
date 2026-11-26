"""Phase 6 identity-provider boundary: OIDC/JWT verification against a stubbed JWKS
endpoint with locally generated keys, role mapping, provider selection, development
token audience, and an OIDC token through the HTTP API.

No real identity provider is contacted (none is chosen, PROJECT_STATUS Q6)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from jwt.algorithms import ECAlgorithm, RSAAlgorithm  # noqa: E402

from agentic_platform.core.config import AppSettings, AuthSettings  # noqa: E402
from agentic_platform.core.errors import (  # noqa: E402
    ConfigurationError,
    IdentityProviderUnavailableError,
    InvalidIdentityError,
    MissingTenantError,
    UnauthenticatedError,
)
from agentic_platform.security import (  # noqa: E402
    HmacTokenAuthenticator,
    IdentityResolver,
    RoleCatalog,
    SecurityAudit,
    build_authenticator,
    resolve_auth_provider,
)
from tests.api_helpers import RecordingAuditor  # noqa: E402

ISSUER = "https://idp.example.com/realms/acme"
AUDIENCE = "agentic-api"
JWKS_URL = "https://idp.example.com/realms/acme/protocol/openid-connect/certs"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeIdp:
    """Discovery + JWKS endpoints served through httpx.MockTransport."""

    def __init__(self) -> None:
        self.keys: dict[str, Any] = {}
        self.jwks: list[dict[str, Any]] = []
        self.requests: list[str] = []
        self.down = False
        self.add_rsa("key-1")

    def add_rsa(self, kid: str) -> Any:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        self.keys[kid] = key
        self.jwks.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
        return key

    def add_ec(self, kid: str) -> Any:
        key = ec.generate_private_key(ec.SECP256R1())
        jwk = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
        self.keys[kid] = key
        self.jwks.append({**jwk, "kid": kid, "use": "sig", "alg": "ES256"})
        return key

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.down:
            return httpx.Response(503)
        if str(request.url) == ISSUER + "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URL})
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json={"keys": self.jwks})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def token(
        self,
        kid: str = "key-1",
        alg: str = "RS256",
        drop: tuple[str, ...] = (),
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "asha",
            "tenant_id": "acme",
            "roles": ["platform-users"],
            "iat": now,
            "exp": now + 600,
            "sid": "session-1",
        }
        claims.update(overrides)
        for name in drop:
            claims.pop(name, None)
        return jwt.encode(claims, self.keys[kid], algorithm=alg, headers={"kid": kid})


ROLE_MAP = json.dumps(
    {"platform-users": ["member"], "platform-approvers": ["approver"], "admins": ["tenant_admin"]}
)


def oidc_settings(**auth: Any) -> AppSettings:
    values: dict[str, Any] = {
        "provider": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_audience": AUDIENCE,
        "oidc_role_map_json": ROLE_MAP,
        "oidc_algorithms": ["RS256", "ES256"],
    }
    values.update(auth)
    return AppSettings(env="test", auth=AuthSettings(**values))


def authenticator(idp: FakeIdp, auditor: RecordingAuditor | None = None, **auth: Any) -> Any:
    return build_authenticator(
        oidc_settings(**auth),
        IdentityResolver(),
        SecurityAudit(auditor),
        transport=idp.transport,
    )


# --- valid tokens ---------------------------------------------------------------------------


def test_valid_token_becomes_a_server_resolved_principal() -> None:
    idp = FakeIdp()
    auth = authenticator(idp)
    principal = run(auth.authenticate(idp.token(roles=["platform-users", "platform-approvers"])))
    assert (principal.user_id, principal.tenant_id) == ("asha", "acme")
    assert principal.auth_method == "oidc" and principal.session_id == "session-1"
    assert principal.roles == {"member", "approver"}
    assert principal.has("approval.decide") and not principal.has("audit.read")
    # discovery, then JWKS, then cached
    assert idp.requests == [ISSUER + "/.well-known/openid-configuration", JWKS_URL]
    run(auth.authenticate(idp.token()))
    assert len(idp.requests) == 2


def test_explicit_jwks_url_and_ec_keys() -> None:
    idp = FakeIdp()
    idp.add_ec("ec-1")
    auth = authenticator(idp, oidc_jwks_url=JWKS_URL)
    principal = run(auth.authenticate(idp.token(kid="ec-1", alg="ES256")))
    assert principal.user_id == "asha"
    assert idp.requests == [JWKS_URL]  # no discovery needed


def test_claims_never_grant_permissions_and_unmapped_roles_grant_nothing() -> None:
    idp = FakeIdp()
    principal = run(
        authenticator(idp).authenticate(
            idp.token(roles=["superuser", "tenant_admin"], permissions=["audit.read"])
        )
    )
    assert principal.roles == frozenset() and principal.permissions == frozenset()
    # Without a role map, external names that are catalogue roles are used as-is.
    unmapped = authenticator(idp, oidc_role_map_json=None)
    p2 = run(unmapped.authenticate(idp.token(roles="member auditor unknown")))
    assert p2.roles == {"member", "auditor"}


def test_custom_claim_names_must_still_be_valid_ids() -> None:
    idp = FakeIdp()
    auth = authenticator(idp, oidc_user_claim="uid", oidc_tenant_claim="org",
                         oidc_roles_claim="groups")  # fmt: skip
    principal = run(auth.authenticate(idp.token(uid="u-42", org="acme", groups=["admins"])))
    assert (principal.user_id, principal.tenant_id, principal.roles) == (
        "u-42",
        "acme",
        {"tenant_admin"},
    )
    with pytest.raises(InvalidIdentityError):
        run(auth.authenticate(idp.token(uid="asha@acme.example", org="acme")))


# --- invalid tokens -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda idp: idp.token(exp=int(time.time()) - 120), "Token expired."),
        (lambda idp: idp.token(aud="another-api"), "Unexpected token audience."),
        (lambda idp: idp.token(iss="https://evil.example.com"), "Unexpected token issuer."),
        (lambda idp: idp.token(nbf=int(time.time()) + 3600), "Token not valid yet."),
        (lambda idp: idp.token(drop=("exp",)), "Token lacks a required claim."),
        (lambda idp: idp.token(drop=("aud",)), "Token lacks a required claim."),
        (lambda idp: idp.token(drop=("sub",)), "Token has no user claim."),
        (lambda idp: "not-a-jwt", "Malformed token."),
        (lambda idp: "a.b.c", "Malformed token header."),
    ],
)
def test_invalid_tokens_are_rejected_and_audited(make: Any, reason: str) -> None:
    idp = FakeIdp()
    auditor = RecordingAuditor()
    auth = authenticator(idp, auditor)
    token = make(idp)
    with pytest.raises(InvalidIdentityError):
        run(auth.authenticate(token))
    (event,) = auditor.of("auth.failed")
    assert event.details == {"reason": reason, "method": "oidc"}
    assert event.tenant_id is None and token not in event.model_dump_json()


def test_missing_tenant_and_missing_credential() -> None:
    idp = FakeIdp()
    auth = authenticator(idp)
    with pytest.raises(MissingTenantError):
        run(auth.authenticate(idp.token(drop=("tenant_id",))))
    with pytest.raises(UnauthenticatedError):
        run(auth.authenticate(None))


def test_forged_and_algorithm_confusion_tokens_are_rejected() -> None:
    idp = FakeIdp()
    auth = authenticator(idp)
    # Signed by another key that claims the provider's key id.
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(idp.token(), options={"verify_signature": False})
    forged = jwt.encode(claims, attacker, algorithm="RS256", headers={"kid": "key-1"})
    with pytest.raises(InvalidIdentityError, match="signature"):
        run(auth.authenticate(forged))
    # "none" and HMAC-with-the-public-key tokens never reach signature checking.
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "key-1"})
    with pytest.raises(InvalidIdentityError, match="algorithm not allowed"):
        run(auth.authenticate(unsigned))
    pem = idp.keys["key-1"].public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    confused = _hs256(claims, pem)
    with pytest.raises(InvalidIdentityError, match="algorithm not allowed"):
        run(auth.authenticate(confused))
    # An allowed algorithm, but the key is published for another one.
    both = authenticator(idp, oidc_algorithms=["RS256", "RS384"])
    with pytest.raises(InvalidIdentityError, match="does not match"):
        run(both.authenticate(_retag(idp.token(), alg="RS384")))


def _hs256(claims: dict[str, Any], secret: bytes) -> str:
    import base64
    import hashlib
    import hmac

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "key-1"}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


def _retag(token: str, *, alg: str) -> str:
    import base64

    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    decoded["alg"] = alg
    new = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
    return f"{new}.{payload}.{signature}"


def test_key_rotation_refreshes_and_unknown_keys_are_refused() -> None:
    idp = FakeIdp()
    auth = authenticator(idp, oidc_algorithms=["RS256", "RS384"])
    run(auth.authenticate(idp.token()))
    fetches = len(idp.requests)
    idp.add_rsa("key-2")  # the provider rotates its keys
    # Refreshes are rate limited (30 s): pretend the cache is old enough.
    auth.provider._keys._fetched_at -= 60
    assert run(auth.authenticate(idp.token(kid="key-2"))).user_id == "asha"
    assert len(idp.requests) == fetches + 1
    with pytest.raises(InvalidIdentityError, match="unknown key"):
        run(auth.authenticate(_with_kid(idp.token(), "key-999")))
    assert len(idp.requests) == fetches + 1  # no refresh storm within 30 s


def _with_kid(token: str, kid: str) -> str:
    import base64

    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    decoded["kid"] = kid
    new = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
    return f"{new}.{payload}.{signature}"


def test_provider_outage_is_unavailable_not_invalid() -> None:
    idp = FakeIdp()
    idp.down = True
    auditor = RecordingAuditor()
    auth = authenticator(idp, auditor)
    with pytest.raises(IdentityProviderUnavailableError) as raised:
        run(auth.authenticate(idp.token()))
    assert raised.value.retryable
    assert auditor.of("auth.failed") == []  # nothing is known about the credential


def test_discovery_must_name_the_configured_issuer() -> None:
    idp = FakeIdp()
    original = idp.handler

    def lying(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200, json={"issuer": "https://evil.example.com", "jwks_uri": JWKS_URL}
            )
        return original(request)

    auth = build_authenticator(
        oidc_settings(), IdentityResolver(), transport=httpx.MockTransport(lying)
    )
    with pytest.raises(IdentityProviderUnavailableError):
        run(auth.authenticate(idp.token()))


# --- configuration --------------------------------------------------------------------------


def test_provider_selection_rules() -> None:
    secret = "s" * 40
    dev = AppSettings(env="development", auth=AuthSettings(token_secret=secret))
    assert resolve_auth_provider(dev) == "dev_token"
    assert isinstance(build_authenticator(dev, IdentityResolver()), HmacTokenAuthenticator)
    with pytest.raises(ConfigurationError, match="No HTTP authentication"):
        resolve_auth_provider(AppSettings(env="development"))
    with pytest.raises(ConfigurationError, match="production"):
        resolve_auth_provider(AppSettings(env="production", auth=AuthSettings(token_secret=secret)))
    with pytest.raises(ConfigurationError, match="AUTH_TOKEN_SECRET"):
        resolve_auth_provider(AppSettings(env="test", auth=AuthSettings(provider="dev_token")))
    assert resolve_auth_provider(oidc_settings()) == "oidc"
    with pytest.raises(ConfigurationError, match="AUTH_OIDC_ISSUER"):
        build_authenticator(
            AppSettings(env="test", auth=AuthSettings(provider="oidc")), IdentityResolver()
        )
    with pytest.raises(ConfigurationError, match="https"):
        build_authenticator(
            AppSettings(
                env="production",
                auth=AuthSettings(
                    provider="oidc", oidc_issuer="http://idp.local", oidc_audience=AUDIENCE
                ),
            ),
            IdentityResolver(),
        )
    with pytest.raises(ConfigurationError, match="ROLE_MAP"):
        build_authenticator(oidc_settings(oidc_role_map_json="[1, 2]"), IdentityResolver())


@pytest.mark.parametrize("algorithms", [["HS256"], ["none"], []])
def test_only_asymmetric_algorithms_are_configurable(algorithms: list[str]) -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        AuthSettings(oidc_algorithms=algorithms)


def test_development_tokens_check_the_audience_when_configured() -> None:
    resolver = IdentityResolver(RoleCatalog())
    secret = "s" * 40
    with_aud = HmacTokenAuthenticator(
        secret, resolver, issuer="dev", max_ttl_seconds=3600, audience="agentic-api"
    )
    without = HmacTokenAuthenticator(secret, resolver, issuer="dev", max_ttl_seconds=3600)
    good = with_aud.issue(user_id="asha", tenant_id="acme", roles=["member"])
    assert run(with_aud.authenticate(good)).user_id == "asha"
    plain = without.issue(user_id="asha", tenant_id="acme", roles=["member"])
    with pytest.raises(InvalidIdentityError, match="audience"):
        run(with_aud.authenticate(plain))
    other = HmacTokenAuthenticator(
        secret, resolver, issuer="dev", max_ttl_seconds=3600, audience="other-api"
    ).issue(user_id="asha", tenant_id="acme", roles=["member"])
    with pytest.raises(InvalidIdentityError, match="audience"):
        run(with_aud.authenticate(other))


# --- through the HTTP API -------------------------------------------------------------------


def test_oidc_token_through_the_http_api(settings: AppSettings) -> None:
    from fastapi.testclient import TestClient

    from agentic_platform.agents import Agent
    from agentic_platform.agents.dependencies import build_dependencies
    from agentic_platform.api import create_app
    from tests.fakes import ScriptedLLM

    idp = FakeIdp()
    app_settings = settings.model_copy(update={"auth": oidc_settings().auth})
    agent = Agent(build_dependencies(app_settings, llm=ScriptedLLM()))
    security = agent.dependencies.security
    auth = build_authenticator(
        app_settings, security.resolver, security.audit, transport=idp.transport
    )
    app = create_app(app_settings, agent=agent, authenticator=auth)
    with TestClient(app, raise_server_exceptions=False) as client:
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {idp.token()}"})
        assert me.status_code == 200
        assert (me.json()["auth_method"], me.json()["roles"]) == ("oidc", ["member"])
        expired = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {idp.token(exp=int(time.time()) - 120)}"},
        )
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "INVALID_CREDENTIALS"
        idp.down = True
        auth.provider._keys._fetched_at = None  # force a key fetch
        outage = client.get("/api/v1/me", headers={"Authorization": f"Bearer {idp.token()}"})
        assert outage.status_code == 503
        assert outage.json()["error"]["code"] == "IDENTITY_PROVIDER_UNAVAILABLE"
