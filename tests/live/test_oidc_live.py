"""Phase 7: `OidcJwtProvider` against a REAL OpenID Connect provider.

Two modes, both skipped unless configured (they need a running provider):

1. **Keycloak reference run** (`LIVE_KEYCLOAK_URL`, `LIVE_KEYCLOAK_ADMIN_PASSWORD`):
   bootstraps a throw-away realm (`tests/live/keycloak_admin.py`) and verifies the
   whole matrix: discovery, JWKS caching, RS256 signatures, issuer, audience, expiry,
   tampering, algorithm confusion, key rotation, role mapping, tenant claim, disabled
   users and role changes (revocation behaviour), and the HTTP API end to end.

       docker compose --profile idp-test up -d keycloak
       $env:LIVE_KEYCLOAK_URL="http://localhost:8080"
       $env:LIVE_KEYCLOAK_ADMIN_PASSWORD="keycloak_admin_dev"
       .venv\\Scripts\\python -m pytest tests/live/test_oidc_live.py -m live_oidc

2. **Any provider** (`LIVE_OIDC_ISSUER`, `LIVE_OIDC_AUDIENCE`, `LIVE_OIDC_ACCESS_TOKEN`,
   optional `LIVE_OIDC_TENANT_CLAIM`, `LIVE_OIDC_ROLES_CLAIM`, `LIVE_OIDC_ROLE_MAP_JSON`,
   `LIVE_OIDC_EXPECTED_TENANT`): verifies a token obtained from the production
   candidate (valid, tampered, wrong audience, wrong issuer). Use it once Q6 is decided.

Keycloak is a reference implementation for verification, not the provider selection.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import httpx
import pytest

jwt = pytest.importorskip("jwt")

from agentic_platform.core.config import AppSettings, AuthSettings  # noqa: E402
from agentic_platform.core.errors import (  # noqa: E402
    InvalidIdentityError,
    MissingTenantError,
    UnauthenticatedError,
)
from agentic_platform.security import IdentityResolver, SecurityAudit  # noqa: E402
from agentic_platform.security.identity_provider import (  # noqa: E402
    IdentityMapper,
    ProviderAuthenticator,
    RoleMapping,
    build_authenticator,
)
from agentic_platform.security.oidc import JwksCache, OidcJwtProvider  # noqa: E402
from tests.api_helpers import RecordingAuditor, bearer, error_of  # noqa: E402
from tests.live import keycloak_admin as kc  # noqa: E402

# Read at import time: the autouse fixture in conftest.py clears AUTH_* variables.
KEYCLOAK_URL = os.environ.get("LIVE_KEYCLOAK_URL", "").rstrip("/")
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("LIVE_KEYCLOAK_ADMIN_PASSWORD", "")
GENERIC = {k: os.environ.get(f"LIVE_OIDC_{k}", "") for k in (
    "ISSUER", "AUDIENCE", "ACCESS_TOKEN", "TENANT_CLAIM", "ROLES_CLAIM", "ROLE_MAP_JSON",
    "EXPECTED_TENANT", "ALGORITHMS",
)}  # fmt: skip

pytestmark = pytest.mark.live_oidc
needs_keycloak = pytest.mark.skipif(
    not (KEYCLOAK_URL and KEYCLOAK_ADMIN_PASSWORD),
    reason="set LIVE_KEYCLOAK_URL and LIVE_KEYCLOAK_ADMIN_PASSWORD (reference Keycloak)",
)
needs_generic = pytest.mark.skipif(
    not (GENERIC["ISSUER"] and GENERIC["AUDIENCE"] and GENERIC["ACCESS_TOKEN"]),
    reason="set LIVE_OIDC_ISSUER, LIVE_OIDC_AUDIENCE and LIVE_OIDC_ACCESS_TOKEN",
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class CountingTransport(httpx.AsyncBaseTransport):
    """The real network transport, counting requests (JWKS caching evidence)."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.urls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return await self._inner.handle_async_request(request)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return data


def _header(token: str) -> dict[str, Any]:
    return dict(jwt.get_unverified_header(token))


def _b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# =============================================================================================
# Keycloak reference run
# =============================================================================================


@pytest.fixture(scope="module")
def keycloak() -> kc.KeycloakVerifier:
    verifier = kc.KeycloakVerifier(KEYCLOAK_URL, KEYCLOAK_ADMIN_PASSWORD)
    verifier.bootstrap()
    return verifier


def kc_auth_settings(verifier: kc.KeycloakVerifier, **overrides: Any) -> AuthSettings:
    values: dict[str, Any] = {
        "provider": "oidc",
        "oidc_issuer": verifier.issuer,
        "oidc_audience": kc.API_AUDIENCE,
        "oidc_role_map_json": json.dumps(kc.ROLE_MAP),
        "oidc_algorithms": ["RS256"],
    }
    values.update(overrides)
    return AuthSettings(**values)


def kc_authenticator(
    verifier: kc.KeycloakVerifier,
    auditor: RecordingAuditor | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **overrides: Any,
) -> Any:
    settings = AppSettings(env="test", auth=kc_auth_settings(verifier, **overrides))
    return build_authenticator(
        settings, IdentityResolver(), SecurityAudit(auditor), transport=transport
    )


def kc_provider(
    verifier: kc.KeycloakVerifier, clock: FakeClock, transport: httpx.AsyncBaseTransport
) -> ProviderAuthenticator:
    """Provider with a controllable clock for the JWKS cache (rotation timing)."""
    keys = JwksCache(
        issuer=verifier.issuer,
        jwks_url=None,
        timeout_seconds=5,
        cache_seconds=600,
        require_https=False,
        transport=transport,
        clock=clock,
    )
    provider = OidcJwtProvider(
        issuer=verifier.issuer, audience=kc.API_AUDIENCE, keys=keys, algorithms=["RS256"]
    )
    mapper = IdentityMapper(IdentityResolver(), RoleMapping(kc.ROLE_MAP))
    return ProviderAuthenticator(provider, mapper)


@needs_keycloak
def test_real_token_discovery_signature_and_principal(keycloak: kc.KeycloakVerifier) -> None:
    transport = CountingTransport()
    auth = kc_authenticator(keycloak, transport=transport)
    token = keycloak.access_token("asha")
    claims = _claims(token)
    # The shape the platform needs: RS256, kid, top-level tenant and roles, API audience.
    assert _header(token)["alg"] == "RS256" and _header(token)["kid"]
    assert claims["iss"] == keycloak.issuer
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    assert kc.API_AUDIENCE in audiences
    assert claims["tenant_id"] == "acme" and "member" in claims["roles"]

    async def verify_eleven_times() -> Any:
        first = await auth.authenticate(token)
        for _ in range(10):
            await auth.authenticate(token)
        return first

    principal = run(verify_eleven_times())
    assert principal.user_id == claims["sub"]  # stable subject, not the editable username
    assert principal.tenant_id == "acme"
    assert principal.roles == {"member"}  # default-roles-*, offline_access, ... dropped
    assert principal.auth_method == "oidc"
    assert "conversation.write" in principal.permissions
    assert "audit.read" not in principal.permissions
    # Discovery once, JWKS once; then the cache serves repeated verifications.
    assert transport.urls == [
        keycloak.issuer + "/.well-known/openid-configuration",
        keycloak.issuer + "/protocol/openid-connect/certs",
    ]


@needs_keycloak
def test_role_mapping_ignores_unmapped_and_forged_roles(keycloak: kc.KeycloakVerifier) -> None:
    token = keycloak.access_token("ravi")
    assert "kc-forged-admin" in _claims(token)["roles"]
    principal = run(kc_authenticator(keycloak).authenticate(token))
    assert set(principal.roles) == {"member", "auditor"}
    assert "audit.read" in principal.permissions
    assert "approval.decide" not in principal.permissions  # unmapped role grants nothing
    # Without a role map only catalogue role names count; Keycloak's names do not match.
    unmapped = run(kc_authenticator(keycloak, oidc_role_map_json=None).authenticate(token))
    assert unmapped.roles == {"member"}


@needs_keycloak
def test_missing_tenant_claim_is_refused(keycloak: kc.KeycloakVerifier) -> None:
    token = keycloak.access_token("nora")
    assert "tenant_id" not in _claims(token)
    with pytest.raises(MissingTenantError):
        run(kc_authenticator(keycloak).authenticate(token))


@needs_keycloak
def test_users_cannot_edit_their_tenant_attribute(keycloak: kc.KeycloakVerifier) -> None:
    # The tenant comes from an attribute only administrators may edit.
    assert keycloak.profile_policy() == "ADMIN_EDIT"
    assert keycloak.user_attributes("asha").get("tenant_id") == ["acme"]


@needs_keycloak
def test_wrong_audience_and_id_tokens_are_refused(keycloak: kc.KeycloakVerifier) -> None:
    auditor = RecordingAuditor()
    auth = kc_authenticator(keycloak, auditor)
    other = keycloak.access_token("asha", client_id=kc.OTHER_CLIENT)
    assert kc.API_AUDIENCE not in str(_claims(other).get("aud"))
    with pytest.raises(InvalidIdentityError, match="audience"):
        run(auth.authenticate(other))
    # An ID token is for the client, not an API credential: its audience is the client.
    id_token = keycloak.id_token("asha")
    assert _claims(id_token)["aud"] == kc.CLI_CLIENT
    with pytest.raises(InvalidIdentityError, match="audience"):
        run(auth.authenticate(id_token))
    reasons = [e.details["reason"] for e in auditor.of("auth.failed")]
    assert reasons == ["Unexpected token audience."] * 2
    assert all(other not in json.dumps(e.details) for e in auditor.events)


@needs_keycloak
def test_wrong_issuer_is_refused(keycloak: kc.KeycloakVerifier) -> None:
    jwks = keycloak.issuer + "/protocol/openid-connect/certs"
    auth = kc_authenticator(
        keycloak, oidc_issuer=keycloak.base_url + "/realms/elsewhere", oidc_jwks_url=jwks
    )
    with pytest.raises(InvalidIdentityError, match="issuer"):
        run(auth.authenticate(keycloak.access_token("asha")))
    # Discovery for a realm that does not exist: the provider is unavailable, not "valid".
    from agentic_platform.core.errors import IdentityProviderUnavailableError

    missing = kc_authenticator(keycloak, oidc_issuer=keycloak.base_url + "/realms/elsewhere")
    with pytest.raises(IdentityProviderUnavailableError):
        run(missing.authenticate(keycloak.access_token("asha")))


@needs_keycloak
def test_tampered_forged_and_malformed_tokens_are_refused(keycloak: kc.KeycloakVerifier) -> None:
    auth = kc_authenticator(keycloak)
    token = keycloak.access_token("asha")
    header, payload, signature = token.split(".")
    # 1. A changed signature.
    flipped = signature[:-6] + ("AAAAAA" if not signature.endswith("AAAAAA") else "BBBBBB")
    # 2. A changed payload (another tenant) under the original signature.
    claims = _claims(token)
    other_tenant = f"{header}.{_b64({**claims, 'tenant_id': 'globex'})}.{signature}"
    # 3. A client-added permissions/roles claim under the original signature.
    escalated = f"{header}.{_b64({**claims, 'roles': ['kc-auditors']})}.{signature}"
    for forged in (f"{header}.{payload}.{flipped}", other_tenant, escalated):
        with pytest.raises(InvalidIdentityError):
            run(auth.authenticate(forged))
    # 4. alg=none and HS256 signed with the provider's public key (algorithm confusion).
    kid = _header(token)["kid"]
    none_token = f"{_b64({'alg': 'none', 'kid': kid})}.{payload}."
    hs = jwt.encode(claims, "public-key-material-" + "x" * 32, algorithm="HS256",
                    headers={"kid": kid})  # fmt: skip
    for forged in (none_token, hs):
        with pytest.raises(InvalidIdentityError, match="algorithm"):
            run(auth.authenticate(forged))
    # 5. Malformed and missing.
    for bad in ("not-a-jwt", "a.b.c", token + ".x", "x" * 9000):
        with pytest.raises(InvalidIdentityError):
            run(auth.authenticate(bad))
    with pytest.raises(UnauthenticatedError):
        run(auth.authenticate(None))


@needs_keycloak
def test_expired_token_is_refused(keycloak: kc.KeycloakVerifier) -> None:
    auth = kc_authenticator(keycloak, oidc_leeway_seconds=0)
    token = keycloak.access_token("asha", client_id=kc.SHORT_CLIENT)
    claims = _claims(token)
    assert claims["exp"] - claims["iat"] == 10
    run(auth.authenticate(token))  # valid now
    time.sleep(max(0.0, claims["exp"] - time.time()) + 1.5)
    with pytest.raises(InvalidIdentityError, match="expired"):
        run(auth.authenticate(token))
    # Leeway is clock skew only: with 30 s leeway the same token is still accepted.
    run(kc_authenticator(keycloak, oidc_leeway_seconds=30).authenticate(token))


@needs_keycloak
def test_max_token_lifetime_bounds_provider_tokens(keycloak: kc.KeycloakVerifier) -> None:
    token = keycloak.access_token("asha")  # realm lifespan 300 s
    with pytest.raises(InvalidIdentityError, match="lifetime"):
        run(kc_authenticator(keycloak, oidc_max_token_lifetime_seconds=120).authenticate(token))
    run(kc_authenticator(keycloak, oidc_max_token_lifetime_seconds=300).authenticate(token))


@needs_keycloak
def test_key_rotation(keycloak: kc.KeycloakVerifier) -> None:
    clock = FakeClock()

    async def scenario() -> None:
        transport = CountingTransport()
        auth = kc_provider(keycloak, clock, transport)
        old_token = keycloak.access_token("asha")
        old_kid = _header(old_token)["kid"]
        await auth.authenticate(old_token)
        fetches = len(transport.urls)

        keycloak.add_signing_key("rotated-rsa", priority=500)
        new_token = keycloak.access_token("asha")
        assert _header(new_token)["kid"] != old_kid
        # Within the 30 s refresh floor an unknown kid does not trigger a fetch.
        with pytest.raises(InvalidIdentityError, match="unknown key"):
            await auth.authenticate(new_token)
        assert len(transport.urls) == fetches
        # After the floor: one JWKS refresh picks up the new key.
        clock.now += 31
        assert (await auth.authenticate(new_token)).tenant_id == "acme"
        assert len(transport.urls) == fetches + 1
        # The old key is still published, so tokens signed before rotation keep working.
        await auth.authenticate(old_token)

        # Retire the old key: once the cache expires, old-key tokens are refused.
        for name in keycloak.key_provider_names():
            if name != "rotated-rsa" and name.startswith("rsa"):
                keycloak.remove_key_provider(name)
        clock.now += 601
        with pytest.raises(InvalidIdentityError, match="unknown key"):
            await auth.authenticate(old_token)
        assert (await auth.authenticate(keycloak.access_token("asha"))).user_id

    try:
        run(scenario())
    finally:
        keycloak.bootstrap()  # fresh realm (and keys) for the remaining tests


@needs_keycloak
def test_disabled_user_and_role_change_take_effect_on_the_next_token(
    keycloak: kc.KeycloakVerifier,
) -> None:
    """Documents the revocation behaviour (ADR-048): access tokens are self-contained."""
    auth = kc_authenticator(keycloak)
    token = keycloak.access_token("gita")
    keycloak.set_roles("gita", remove=["member"])
    try:
        # The already-issued token still carries the role until it expires...
        assert "conversation.write" in run(auth.authenticate(token)).permissions
        # ...the next token does not.
        fresh = run(auth.authenticate(keycloak.access_token("gita")))
        assert not fresh.roles and "conversation.write" not in fresh.permissions
    finally:
        keycloak.set_roles("gita", add=["member"])

    keycloak.set_enabled("gita", False)
    try:
        # A disabled user gets no new token...
        refused = keycloak.token_response("gita")
        assert refused.status_code in (400, 401)
        assert refused.json().get("error") in ("invalid_grant", "user_disabled")
        # ...but a token issued before is accepted until `exp` (no introspection call).
        assert run(auth.authenticate(token)).tenant_id == "globex"
    finally:
        keycloak.set_enabled("gita", True)


@needs_keycloak
def test_real_tokens_through_the_http_api(
    keycloak: kc.KeycloakVerifier, make_agent: Any, settings: AppSettings
) -> None:
    from fastapi.testclient import TestClient

    from agentic_platform.api import create_app
    from tests.conftest import understanding

    app_settings = settings.model_copy(update={"auth": kc_auth_settings(keycloak)})
    agent, _ = make_agent(
        understanding(intent="greet", tool_calls=[]), "Hello!", checkpointer=None
    )
    security = agent.dependencies.security
    auth = build_authenticator(app_settings, security.resolver, security.audit)
    app = create_app(app_settings, agent=agent, authenticator=auth)
    asha, gita = keycloak.access_token("asha"), keycloak.access_token("gita")
    with TestClient(app, raise_server_exceptions=False) as client:
        me = client.get("/api/v1/me", headers=bearer(asha))
        assert me.status_code == 200, me.text
        body = me.json()
        identity = (body["tenant_id"], body["roles"], body["auth_method"])
        assert identity == ("acme", ["member"], "oidc")

        # Client-supplied identity is refused, never an override.
        for field in ("tenant_id", "user_id", "roles", "permissions"):
            response = client.post(
                "/api/v1/agent/turn",
                headers=bearer(asha),
                json={"message": "hi", field: "globex" if field.endswith("id") else ["x"]},
            )
            assert response.status_code == 422, (field, response.text)

        turn = client.post("/api/v1/conversations", headers=bearer(asha), json={"message": "hi"})
        assert turn.status_code == 201, turn.text
        thread = turn.json()["thread_id"]
        # Another tenant's user cannot see it: identical to a missing conversation.
        other = client.get(f"/api/v1/conversations/{thread}", headers=bearer(gita))
        missing = client.get("/api/v1/conversations/does-not-exist", headers=bearer(gita))
        assert other.status_code == missing.status_code == 404
        assert error_of(other)["code"] == error_of(missing)["code"]

        assert client.get("/api/v1/me").status_code == 401
        h, p, s = asha.split(".")
        tampered = client.get("/api/v1/me", headers=bearer(f"{h}.{p}.{s[:-4]}AAAA"))
        assert tampered.status_code == 401
        assert error_of(tampered)["code"] == "INVALID_CREDENTIALS"
        assert tampered.headers["www-authenticate"].startswith("Bearer")


# =============================================================================================
# Any provider (the production candidate once Q6 is decided)
# =============================================================================================


def generic_settings(**overrides: Any) -> AppSettings:
    values: dict[str, Any] = {
        "provider": "oidc",
        "oidc_issuer": GENERIC["ISSUER"],
        "oidc_audience": GENERIC["AUDIENCE"],
    }
    for key, field in (("TENANT_CLAIM", "oidc_tenant_claim"), ("ROLES_CLAIM", "oidc_roles_claim"),
                       ("ROLE_MAP_JSON", "oidc_role_map_json")):  # fmt: skip
        if GENERIC[key]:
            values[field] = GENERIC[key]
    if GENERIC["ALGORITHMS"]:
        values["oidc_algorithms"] = json.loads(GENERIC["ALGORITHMS"])
    values.update(overrides)
    return AppSettings(env="test", auth=AuthSettings(**values))


def generic_auth(**overrides: Any) -> Any:
    return build_authenticator(generic_settings(**overrides), IdentityResolver())


@needs_generic
def test_provider_token_is_accepted_and_mapped() -> None:
    principal = run(generic_auth().authenticate(GENERIC["ACCESS_TOKEN"]))
    if GENERIC["EXPECTED_TENANT"]:
        assert principal.tenant_id == GENERIC["EXPECTED_TENANT"]
    assert principal.auth_method == "oidc"


@needs_generic
def test_provider_token_negative_cases() -> None:
    token = GENERIC["ACCESS_TOKEN"]
    h, p, s = token.split(".")
    with pytest.raises(InvalidIdentityError):
        run(generic_auth().authenticate(f"{h}.{p}.{s[:-4]}AAAA"))
    with pytest.raises(InvalidIdentityError, match="audience"):
        run(generic_auth(oidc_audience="not-this-api").authenticate(token))
