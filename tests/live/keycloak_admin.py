"""Bootstrap a throw-away verification realm in a running Keycloak (tests only).

Keycloak is used as a **reference OIDC implementation** to verify `OidcJwtProvider`
against real discovery, real JWKS, real RS256 signatures and real key rotation. It is
not the production identity-provider selection (PROJECT_STATUS Q6).

The realm is shaped the way the platform needs any provider to be configured:

* access tokens carry the API's audience (`agentic-api`) through an audience mapper,
  a **top-level** `tenant_id` claim from an admin-managed user attribute and a
  **top-level** `roles` claim (realm roles), so no nested-claim parsing is needed;
* user attributes are admin-editable only (`unmanagedAttributePolicy=ADMIN_EDIT`):
  users cannot change their own tenant;
* `agentic-cli` uses the password grant (test only; real clients use the
  authorization-code flow with PKCE, the API only ever sees the access token).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

REALM = "agentic-verify"
API_AUDIENCE = "agentic-api"
CLI_CLIENT = "agentic-cli"
OTHER_CLIENT = "other-app"  # no audience mapper: tokens for a different resource
SHORT_CLIENT = "agentic-cli-short"  # 10-second access tokens (expiry test)
CLIENT_SECRET = "verify-client-secret-not-a-real-secret"
PASSWORD = "Verify-Password-1"  # throw-away test users only

# External (Keycloak realm) role → platform role. Keycloak's own default roles
# (default-roles-*, offline_access, uma_authorization) are deliberately unmapped.
ROLE_MAP = {"member": ["member"], "kc-auditors": ["auditor"], "kc-approvers": ["approver"]}

USERS = {
    # username: (tenant attribute or None, realm roles)
    "asha": ("acme", ["member"]),
    "ravi": ("acme", ["member", "kc-auditors", "kc-forged-admin"]),
    "gita": ("globex", ["member"]),
    "nora": (None, ["member"]),  # no tenant attribute
}


def _mappers(*, audience: bool) -> list[dict[str, Any]]:
    mappers: list[dict[str, Any]] = [
        {
            "name": "tenant",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "tenant_id",
                "claim.name": "tenant_id",
                "jsonType.label": "String",
                "access.token.claim": "true",
                "id.token.claim": "false",
                "userinfo.token.claim": "false",
            },
        },
        {
            "name": "roles",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-realm-role-mapper",
            "config": {
                "claim.name": "roles",
                "multivalued": "true",
                "jsonType.label": "String",
                "access.token.claim": "true",
                "id.token.claim": "false",
                "userinfo.token.claim": "false",
            },
        },
    ]
    if audience:
        mappers.append(
            {
                "name": "api-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.custom.audience": API_AUDIENCE,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            }
        )
    return mappers


def _client(client_id: str, *, audience: bool, lifespan: int | None = None) -> dict[str, Any]:
    client: dict[str, Any] = {
        "clientId": client_id,
        "enabled": True,
        "publicClient": False,
        "secret": CLIENT_SECRET,
        "standardFlowEnabled": False,
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled": False,
        "fullScopeAllowed": True,
        "protocolMappers": _mappers(audience=audience),
    }
    if lifespan is not None:
        client["attributes"] = {"access.token.lifespan": str(lifespan)}
    return client


@dataclass
class KeycloakVerifier:
    base_url: str
    admin_password: str
    admin_user: str = "admin"

    @property
    def issuer(self) -> str:
        return f"{self.base_url}/realms/{REALM}"

    # -- admin API ----------------------------------------------------------------------

    def _admin_token(self) -> str:
        response = httpx.post(
            f"{self.base_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_user,
                "password": self.admin_password,
            },
            timeout=10,
        )
        response.raise_for_status()
        return str(response.json()["access_token"])

    def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = httpx.request(
            method,
            f"{self.base_url}/admin/realms{path}",
            headers={"Authorization": f"Bearer {self._admin_token()}"},
            timeout=15,
            **kwargs,
        )
        if response.status_code >= 400 and not (method == "DELETE" and response.status_code == 404):
            raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:300]}")
        return response

    def wait_ready(self, timeout: float = 120.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if httpx.get(f"{self.base_url}/realms/master", timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise AssertionError("Keycloak did not become ready.")
            time.sleep(2)

    def bootstrap(self) -> None:
        """(Re)create the verification realm, its clients, roles and users."""
        self.wait_ready()
        self.admin("DELETE", f"/{REALM}")
        self.admin(
            "POST",
            "",
            json={
                "realm": REALM,
                "enabled": True,
                "accessTokenLifespan": 300,
                "roles": {
                    "realm": [
                        {"name": name}
                        for name in ("member", "kc-auditors", "kc-approvers", "kc-forged-admin")
                    ]
                },
                "clients": [
                    _client(CLI_CLIENT, audience=True),
                    _client(OTHER_CLIENT, audience=False),
                    _client(SHORT_CLIENT, audience=True, lifespan=10),
                ],
            },
        )
        profile = self.admin("GET", f"/{REALM}/users/profile").json()
        profile["unmanagedAttributePolicy"] = "ADMIN_EDIT"
        self.admin("PUT", f"/{REALM}/users/profile", json=profile)
        for username, (tenant, roles) in USERS.items():
            self.create_user(username, tenant, roles)

    def create_user(self, username: str, tenant: str | None, roles: list[str]) -> None:
        body: dict[str, Any] = {
            "username": username,
            "enabled": True,
            "email": f"{username}@verify.example",
            "emailVerified": True,
            "firstName": username.title(),
            "lastName": "Verify",
            "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
        }
        if tenant is not None:
            body["attributes"] = {"tenant_id": [tenant]}
        self.admin("POST", f"/{REALM}/users", json=body)
        self.set_roles(username, add=roles)

    def user_id(self, username: str) -> str:
        users = self.admin(
            "GET", f"/{REALM}/users", params={"username": username, "exact": "true"}
        ).json()
        return str(users[0]["id"])

    def set_roles(
        self, username: str, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        uid = self.user_id(username)
        for method, names in (("POST", add), ("DELETE", remove)):
            if names:
                reps = [self.admin("GET", f"/{REALM}/roles/{n}").json() for n in names]
                self.admin(method, f"/{REALM}/users/{uid}/role-mappings/realm", json=reps)

    def set_enabled(self, username: str, enabled: bool) -> None:
        self.admin("PUT", f"/{REALM}/users/{self.user_id(username)}", json={"enabled": enabled})

    def user_attributes(self, username: str) -> dict[str, Any]:
        user = self.admin("GET", f"/{REALM}/users/{self.user_id(username)}").json()
        return dict(user.get("attributes") or {})

    def profile_policy(self) -> str | None:
        return self.admin("GET", f"/{REALM}/users/profile").json().get("unmanagedAttributePolicy")

    def realm_id(self) -> str:
        return str(self.admin("GET", f"/{REALM}").json()["id"])

    def add_signing_key(self, name: str, priority: int) -> None:
        """A new RS256 key with a higher priority: Keycloak signs new tokens with it."""
        self.admin(
            "POST",
            f"/{REALM}/components",
            json={
                "name": name,
                "providerId": "rsa-generated",
                "providerType": "org.keycloak.keys.KeyProvider",
                "parentId": self.realm_id(),
                "config": {
                    "priority": [str(priority)],
                    "keySize": ["2048"],
                    "algorithm": ["RS256"],
                    "active": ["true"],
                    "enabled": ["true"],
                },
            },
        )

    def remove_key_provider(self, name: str) -> None:
        components = self.admin(
            "GET",
            f"/{REALM}/components",
            params={"type": "org.keycloak.keys.KeyProvider", "name": name},
        ).json()
        for component in components:
            self.admin("DELETE", f"/{REALM}/components/{component['id']}")

    def key_provider_names(self) -> list[str]:
        components = self.admin(
            "GET", f"/{REALM}/components", params={"type": "org.keycloak.keys.KeyProvider"}
        ).json()
        return [str(c["name"]) for c in components]

    # -- tokens ---------------------------------------------------------------------------

    def token_response(
        self, username: str, *, client_id: str = CLI_CLIENT, scope: str | None = None
    ) -> httpx.Response:
        data = {
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": CLIENT_SECRET,
            "username": username,
            "password": PASSWORD,
        }
        if scope:
            data["scope"] = scope
        return httpx.post(
            f"{self.issuer}/protocol/openid-connect/token", data=data, timeout=10
        )

    def access_token(self, username: str, *, client_id: str = CLI_CLIENT) -> str:
        response = self.token_response(username, client_id=client_id)
        assert response.status_code == 200, response.text[:300]
        return str(response.json()["access_token"])

    def id_token(self, username: str) -> str:
        response = self.token_response(username, scope="openid")
        assert response.status_code == 200, response.text[:300]
        return str(response.json()["id_token"])
