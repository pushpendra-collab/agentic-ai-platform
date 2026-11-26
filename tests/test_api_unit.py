"""Phase 6 HTTP API without external servers: authentication, principal propagation,
authorization, error envelopes, agent turns, conversations, approvals, memory, audit,
request correlation and probes (in-memory stores, InMemorySaver, scripted LLM,
development tokens)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.api import create_app, runtime_of
from agentic_platform.core.config import (
    ApiSettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    MemorySettings,
)
from agentic_platform.core.errors import ConfigurationError, LLMTransientError
from agentic_platform.db.engine import Database
from agentic_platform.security import HmacTokenAuthenticator, IdentityResolver, Principal
from agentic_platform.services.approvals import InMemoryApprovalStore
from agentic_platform.services.clock import ClockService
from agentic_platform.services.readiness import ReadinessService
from agentic_platform.tools.memory import REMEMBER_FACT
from tests.api_helpers import (
    TOKEN_SECRET,
    RecordingAuditor,
    api_client,
    bearer,
    error_of,
    token,
)
from tests.conftest import understanding
from tests.fakes import ScriptedLLM

FACT_ARGS = '{"key": "reply_language", "content": "Asha prefers Hindi replies."}'
TIME_CALL = ("system.current_time", '{"timezone": "Asia/Kolkata"}')


@pytest.fixture
def api_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(
        update={
            "auth": AuthSettings(token_secret=TOKEN_SECRET),
            "memory": MemorySettings(agent_fact_writes="approval"),
        }
    )


class Harness:
    def __init__(self, settings: AppSettings, clock: ClockService, *script: object) -> None:
        self.settings = settings
        self.llm = ScriptedLLM(script=list(script))
        self.auditor = RecordingAuditor()
        deps = build_dependencies(settings, llm=self.llm, clock=clock, auditor=self.auditor)
        self.agent = Agent(deps, checkpointer=InMemorySaver())

    def token(self, tenant: str = "acme", user: str = "asha", *roles: str) -> str:
        return token(self.agent, tenant, user, roles or ("member",))

    def headers(self, tenant: str = "acme", user: str = "asha", *roles: str) -> dict[str, str]:
        return bearer(self.token(tenant, user, *roles))


@pytest.fixture
def harness(api_settings: AppSettings, clock: ClockService) -> Iterator[Any]:
    def make(*script: object, settings: AppSettings | None = None) -> Harness:
        return Harness(settings or api_settings, clock, *script)

    yield make


def remember_turn(h: Harness, client: TestClient, user: str = "asha") -> dict[str, Any]:
    response = client.post(
        "/api/v1/agent/turn",
        json={"message": "Remember I prefer Hindi replies", "thread_id": "t-hitl"},
        headers=h.headers("acme", user),
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


def remember_script(*after: object) -> list[object]:
    return [
        understanding(intent="remember_preference", tool_calls=[(REMEMBER_FACT, FACT_ARGS)]),
        *after,
    ]


# =============================================================================================
# Probes and the application
# =============================================================================================


def test_health_and_ready_need_no_credentials(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        health = client.get("/health")
        assert (health.status_code, health.json()) == (200, {"status": "ok"})
        assert len(health.headers["x-request-id"]) == 32
        ready = client.get("/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"]["status"] == "not_configured"  # optional in dev
        assert body["checks"]["redis"]["status"] == "not_configured"
        assert body["checks"]["authentication"]["status"] == "ok"
        assert "url" not in ready.text.lower() and "password" not in ready.text.lower()


def test_readiness_reports_dependency_failures_without_details() -> None:
    database = Database(
        DatabaseSettings(
            url="postgresql://agentic_app:secret-pw@127.0.0.1:1/agentic",
            connect_timeout_seconds=1,
            health_timeout_seconds=2,
        )
    )

    async def check() -> Any:
        try:
            return await ReadinessService(
                database=database, redis=None, production=False, authentication_configured=True
            ).check()
        finally:
            await database.dispose()

    import asyncio

    readiness = asyncio.run(check())
    assert readiness.status == "not_ready"
    assert readiness.checks["database"].status == "unavailable"
    assert readiness.checks["database"].error_code == "DATABASE_UNAVAILABLE"
    assert "secret-pw" not in readiness.model_dump_json()
    assert "127.0.0.1" not in readiness.model_dump_json()

    production = asyncio.run(
        ReadinessService(
            database=None, redis=None, production=True, authentication_configured=True
        ).check()
    )
    assert production.status == "not_ready"  # production needs its database
    assert production.checks["database"].ready is False


def test_ready_endpoint_answers_503_when_not_ready(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        runtime_of(client.app).readiness = ReadinessService(  # type: ignore[arg-type]
            database=None, redis=None, production=True, authentication_configured=True
        )
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_in_process_maintenance_is_opt_in_and_stops_with_the_app(
    harness: Any, api_settings: AppSettings
) -> None:
    h = harness()
    assert runtime_of(create_app(h.settings, agent=h.agent)).scheduler is None  # default off
    on = api_settings.model_copy(update={"api": ApiSettings(run_maintenance=True)})
    h2 = harness(settings=on)
    with api_client(on, h2.agent) as client:
        scheduler = runtime_of(client.app).scheduler  # type: ignore[arg-type]
        assert scheduler is not None
        deadline = time.monotonic() + 10
        while scheduler.runs < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scheduler.runs >= 1
        assert client.get("/health").status_code == 200  # requests unaffected
    assert scheduler._task is None  # stopped on shutdown
    runs = h2.auditor.of("maintenance.run")
    assert {e.tool_name for e in runs} == {"memory.expired", "approvals.expired"}


def test_unknown_route_uses_the_error_envelope(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        response = client.get("/api/v1/nope", headers=h.headers())
        assert response.status_code == 404
        assert error_of(response)["code"] == "NOT_FOUND"


def test_openapi_documents_bearer_auth_and_is_off_in_production(
    harness: Any, api_settings: AppSettings
) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    paths = schema["paths"]
    for path in (
        "/api/v1/me",
        "/api/v1/agent/turn",
        "/api/v1/conversations/{thread_id}/resume",
        "/api/v1/approvals/{approval_id}/approve",
        "/api/v1/memory/facts/{namespace}/{key}",
        "/api/v1/audit",
        "/health",
        "/ready",
    ):
        assert path in paths, path
    turn = paths["/api/v1/agent/turn"]["post"]["responses"]
    assert {"200", "202", "401", "403", "422"} <= set(turn)
    assert "ErrorResponse" in schema["components"]["schemas"]

    docs_off = api_settings.model_copy(update={"api": ApiSettings(docs_enabled=False)})
    h2 = harness(settings=docs_off)
    with api_client(docs_off, h2.agent) as client:
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404


def test_production_refuses_development_tokens_and_missing_auth(
    settings: AppSettings, clock: ClockService
) -> None:
    production = settings.model_copy(
        update={"env": "production", "auth": AuthSettings(token_secret=TOKEN_SECRET)}
    )
    agent = Agent(build_dependencies(production, llm=ScriptedLLM(), clock=clock))
    with pytest.raises(ConfigurationError, match="production"):
        create_app(production, agent=agent)
    explicit = production.model_copy(
        update={"auth": AuthSettings(token_secret=TOKEN_SECRET, provider="dev_token")}
    )
    with pytest.raises(ConfigurationError, match="production"):
        create_app(explicit, agent=agent)
    nothing = settings.model_copy(update={"auth": AuthSettings()})
    with pytest.raises(ConfigurationError, match="No HTTP authentication"):
        create_app(nothing, agent=Agent(build_dependencies(nothing, llm=ScriptedLLM())))


# =============================================================================================
# Authentication and the principal
# =============================================================================================


def test_me_returns_the_server_resolved_principal(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        response = client.get("/api/v1/me", headers=h.headers("acme", "asha", "member", "approver"))
    assert response.status_code == 200
    me = response.json()
    assert (me["user_id"], me["tenant_id"], me["auth_method"]) == ("asha", "acme", "token")
    assert me["roles"] == ["approver", "member"]
    assert "approval.decide" in me["permissions"] and "audit.read" not in me["permissions"]
    assert "session_id" not in me


def test_missing_credentials_are_401_and_not_audited(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        response = client.get("/api/v1/me")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert error_of(response)["code"] == "AUTHENTICATION_REQUIRED"
    assert h.auditor.of("auth.failed") == []


@pytest.mark.parametrize(
    "header",
    [
        "Basic YXNoYTpwYXNz",  # wrong scheme
        "Bearer",  # no token
        "Bearer not-a-token",  # malformed
        "Bearer a.b.c d",  # garbage
    ],
)
def test_malformed_credentials_are_401_and_audited(harness: Any, header: str) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        response = client.get("/api/v1/me", headers={"Authorization": header})
    assert response.status_code == 401
    assert error_of(response)["code"] == "INVALID_CREDENTIALS"
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    (event,) = h.auditor.of("auth.failed")
    assert event.tenant_id is None and event.actor_id is None


def test_forged_expired_and_foreign_tokens_are_rejected(
    harness: Any, caplog: pytest.LogCaptureFixture
) -> None:
    h = harness()
    good = h.token()
    resolver = IdentityResolver()
    expired = HmacTokenAuthenticator(
        TOKEN_SECRET, resolver, issuer="agentic-platform-dev", max_ttl_seconds=3600,
        now=lambda: time.time() - 7200,
    ).issue(user_id="asha", tenant_id="acme", roles=["member"], ttl_seconds=60)  # fmt: skip
    foreign_issuer = HmacTokenAuthenticator(
        TOKEN_SECRET, resolver, issuer="someone-else", max_ttl_seconds=3600
    ).issue(user_id="asha", tenant_id="acme", roles=["member"])
    other_key = HmacTokenAuthenticator(
        "another-secret-" + "y" * 32, resolver, issuer="agentic-platform-dev", max_ttl_seconds=3600
    ).issue(user_id="asha", tenant_id="acme", roles=["tenant_admin"])
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    caplog.set_level(logging.DEBUG, logger="agentic_platform")
    with api_client(h.settings, h.agent) as client:
        for bad in (expired, foreign_issuer, other_key, tampered):
            response = client.get("/api/v1/me", headers=bearer(bad))
            assert response.status_code == 401, bad
            assert error_of(response)["code"] == "INVALID_CREDENTIALS"
        assert client.get("/api/v1/me", headers=bearer(good)).status_code == 200
    reasons = {e.details["reason"] for e in h.auditor.of("auth.failed")}
    assert reasons == {"Token expired.", "Unexpected token issuer.", "Bad token signature."}
    # Tokens are never logged or audited.
    for secret in (good, expired, other_key, tampered, TOKEN_SECRET):
        assert secret not in caplog.text
        assert all(secret not in e.model_dump_json() for e in h.auditor.events)


def test_identity_never_comes_from_the_request_body(harness: Any) -> None:
    h = harness(understanding(), "Hello.")
    with api_client(h.settings, h.agent) as client:
        for extra in (
            {"tenant_id": "globex"},
            {"user_id": "boss"},
            {"roles": ["tenant_admin"]},
            {"permissions": ["audit.read"]},
        ):
            response = client.post(
                "/api/v1/agent/turn", json={"message": "hi", **extra}, headers=h.headers()
            )
            assert response.status_code == 422, extra
            error = error_of(response)
            assert error["code"] == "VALIDATION_ERROR"
            assert error["details"][0]["type"] == "extra_forbidden"
            assert "globex" not in response.text and "boss" not in response.text
        ok = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers())
        assert ok.status_code == 200
        thread_id = ok.json()["thread_id"]
    # The conversation belongs to the token's tenant and user.
    status = h.agent.thread_status(thread_id, principal=_p("acme", "asha"))
    assert status.exists


def test_token_claims_cannot_grant_permissions(harness: Any) -> None:
    h = harness()
    import base64
    import json

    tokens = h.agent.dependencies.security.tokens
    claims = {
        "v": 1, "iss": "agentic-platform-dev", "sub": "eve", "tid": "acme",
        "iat": int(time.time()), "exp": int(time.time()) + 600, "roles": ["superuser"],
        "permissions": ["audit.read", "approval.decide"],
    }  # fmt: skip
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    forged = f"{payload}.{tokens._sign(payload)}"
    with api_client(h.settings, h.agent) as client:
        me = client.get("/api/v1/me", headers=bearer(forged)).json()
        assert me["roles"] == [] and me["permissions"] == []
        denied = client.get("/api/v1/audit", headers=bearer(forged))
        assert denied.status_code == 403
        assert error_of(denied)["code"] == "PERMISSION_DENIED"


def _p(tenant: str, user: str, *roles: str) -> Principal:
    return IdentityResolver().resolve(
        user_id=user, tenant_id=tenant, roles=roles or ("member",), auth_method="token"
    )


# =============================================================================================
# Agent turns and correlation
# =============================================================================================


def test_completed_turn_and_request_correlation(harness: Any) -> None:
    h = harness(understanding(tool_calls=[TIME_CALL]), "It is 12:00 in Kolkata.")
    with api_client(h.settings, h.agent) as client:
        response = client.post(
            "/api/v1/agent/turn",
            json={"message": "What time is it in Kolkata?", "thread_id": "t-1"},
            headers={**h.headers(), "X-Request-ID": "client-chosen-id"},
        )
    assert response.status_code == 200
    body = response.json()
    request_id = response.headers["x-request-id"]
    assert request_id != "client-chosen-id"  # never reused as the correlation id
    assert body["request_id"] == request_id
    assert (body["status"], body["message"], body["thread_id"]) == (
        "completed",
        "It is 12:00 in Kolkata.",
        "t-1",
    )
    assert body["tool_calls"] == [
        {"tool_name": "system.current_time", "status": "ok", "error_code": None,
         "approval_id": None}
    ]  # fmt: skip
    assert body["error"] is None and body["approval_required"] is None
    # internal graph state is not exposed
    for internal in ("steps", "next_nodes", "intent", "domain", "output"):
        assert internal not in body
    # HTTP request → turn → tool → audit share one id
    (tool_call,) = h.auditor.of("tool.call")
    assert (tool_call.request_id, tool_call.thread_id) == (request_id, "t-1")
    assert (tool_call.tenant_id, tool_call.actor_id) == ("acme", "asha")


def test_failed_turn_maps_to_an_http_error_status(harness: Any) -> None:
    h = harness(LLMTransientError("429 from provider"), RuntimeError("db password is hunter2"))
    with api_client(h.settings, h.agent) as client:
        transient = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers())
        assert transient.status_code == 503
        assert transient.headers["retry-after"] == "2"
        body = transient.json()
        assert body["status"] == "failed"
        assert body["error"] == {
            "code": "LLM_UNAVAILABLE",
            "message": body["error"]["message"],
            "retryable": True,
        }
        # Random hex ids can contain "429" (flaked once in Phase 8): check everything else.
        visible = {k: v for k, v in body.items() if k not in ("request_id", "thread_id")}
        assert "429" not in json.dumps(visible)
        crashed = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers())
        assert crashed.status_code == 500
        assert crashed.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "hunter2" not in crashed.text


def test_validation_errors_are_422_without_echoing_input(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        for body in (
            {},
            {"message": ""},
            {"message": "hi", "thread_id": "acme/t-1"},  # cannot name a storage key
            {"message": "hi", "thread_id": "x" * 129},
        ):
            response = client.post("/api/v1/agent/turn", json=body, headers=h.headers())
            assert response.status_code == 422, body
            error = error_of(response)
            assert error["code"] == "VALIDATION_ERROR"
            assert all("input" not in d for d in error["details"])
        too_long = client.post(
            "/api/v1/agent/turn", json={"message": "x" * 9000}, headers=h.headers()
        )
        assert too_long.status_code == 422  # AGENT_MAX_INPUT_CHARS, from the agent
        assert error_of(too_long)["code"] == "INVALID_INPUT"


def test_oversized_bodies_are_refused_before_reading(
    harness: Any, api_settings: AppSettings
) -> None:
    small = api_settings.model_copy(update={"api": ApiSettings(max_body_bytes=2048)})
    h = harness(settings=small)
    with api_client(small, h.agent) as client:
        response = client.post(
            "/api/v1/agent/turn", json={"message": "x" * 5000}, headers=h.headers()
        )
    assert response.status_code == 413
    assert error_of(response)["code"] == "REQUEST_TOO_LARGE"
    assert h.llm.calls == []


def test_unexpected_errors_are_500_without_internals(harness: Any) -> None:
    class Exploding:
        async def authenticate(self, credential: str | None) -> Principal:
            raise RuntimeError("stack detail: password=hunter2")

    h = harness()
    app = create_app(h.settings, agent=h.agent, authenticator=Exploding())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/me", headers=bearer("anything"))
    assert response.status_code == 500
    error = error_of(response)
    assert (error["code"], error["message"]) == ("INTERNAL_ERROR", "An internal error occurred.")
    assert "hunter2" not in response.text and "Traceback" not in response.text


# =============================================================================================
# Conversations: ownership and tenant isolation
# =============================================================================================


def test_conversation_lifecycle_for_the_owner(harness: Any) -> None:
    h = harness(understanding(), "Hello Asha.", understanding(), "Still here.")
    with api_client(h.settings, h.agent) as client:
        started = client.post(
            "/api/v1/conversations", json={"message": "Hi"}, headers=h.headers()
        )
        assert started.status_code == 201
        thread_id = started.json()["thread_id"]
        assert len(thread_id) == 32  # chosen by the server

        more = client.post(
            f"/api/v1/conversations/{thread_id}/messages",
            json={"message": "Are you there?"},
            headers=h.headers(),
        )
        assert (more.status_code, more.json()["message"]) == (200, "Still here.")

        view = client.get(f"/api/v1/conversations/{thread_id}", headers=h.headers()).json()
        assert view == {
            "thread_id": thread_id,
            "pending": False,
            "awaiting_approval": None,
            "awaiting_input": False,
            "message_count": 4,
            "last_request_id": more.json()["request_id"],
            "updated_at": view["updated_at"],
        }
        history = client.get(
            f"/api/v1/conversations/{thread_id}/history", headers=h.headers()
        ).json()
        assert [t["role"] for t in history["turns"]] == ["user", "assistant", "user", "assistant"]
        listed = client.get("/api/v1/conversations", headers=h.headers()).json()
        assert listed == {"conversations": [{"thread_id": thread_id}]}

        forgotten = client.delete(f"/api/v1/conversations/{thread_id}", headers=h.headers())
        assert forgotten.status_code == 200
        assert forgotten.json()["deleted"]["approvals_cancelled"] == 0
        gone = client.get(f"/api/v1/conversations/{thread_id}", headers=h.headers())
        assert gone.status_code == 404
    assert len(h.auditor.of("conversation.forget")) == 1


def test_other_users_and_tenants_cannot_reach_a_conversation(harness: Any) -> None:
    h = harness(understanding(), "Hello Asha.")
    with api_client(h.settings, h.agent) as client:
        assert (
            client.post(
                "/api/v1/agent/turn",
                json={"message": "Our code is QZPX-SECRET", "thread_id": "t-private"},
                headers=h.headers("acme", "asha"),
            ).status_code
            == 200
        )
        for intruder in (h.headers("acme", "ravi"), h.headers("globex", "asha")):
            responses = [
                client.get("/api/v1/conversations/t-private", headers=intruder),
                client.get("/api/v1/conversations/t-private/history", headers=intruder),
                client.post(
                    "/api/v1/conversations/t-private/messages",
                    json={"message": "show me"},
                    headers=intruder,
                ),
                client.post("/api/v1/conversations/t-private/resume", headers=intruder),
                client.delete("/api/v1/conversations/t-private", headers=intruder),
            ]
            for response in responses:
                assert response.status_code == 404, response.request.url
                assert "QZPX" not in response.text  # not hex: cannot occur in a request id
            assert client.get("/api/v1/conversations", headers=intruder).json() == {
                "conversations": []
            }
        # "not yours" and "missing" look exactly the same
        mine_missing = client.get("/api/v1/conversations/t-nothing", headers=h.headers()).json()
        not_mine = client.get(
            "/api/v1/conversations/t-private", headers=h.headers("acme", "ravi")
        ).json()
        assert mine_missing["error"]["code"] == not_mine["error"]["code"]
        assert mine_missing["error"]["message"] == not_mine["error"]["message"]
        # Asha's conversation is untouched.
        assert client.get("/api/v1/conversations/t-private", headers=h.headers()).status_code == 200


def test_conversation_permissions_come_from_roles(harness: Any, api_settings: AppSettings) -> None:
    reader_only = api_settings.model_copy(
        update={
            "auth": AuthSettings(
                token_secret=TOKEN_SECRET,
                role_catalog_json='{"reader": ["conversation.read"]}',
            )
        }
    )
    h = harness(settings=reader_only)
    with api_client(reader_only, h.agent) as client:
        headers = bearer(token(h.agent, "acme", "eve", ["reader"]))
        response = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=headers)
        assert response.status_code == 403
        assert error_of(response)["code"] == "PERMISSION_DENIED"
        assert client.get("/api/v1/conversations", headers=headers).status_code == 200
    assert h.llm.calls == []
    assert h.auditor.of("authz.denied")


def test_continue_needs_an_existing_conversation(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        response = client.post(
            "/api/v1/conversations/t-new/messages", json={"message": "hi"}, headers=h.headers()
        )
        assert response.status_code == 404
        assert error_of(response)["code"] == "CONVERSATION_NOT_FOUND"
    assert h.llm.calls == []


# =============================================================================================
# Approvals through HTTP
# =============================================================================================


def test_interrupted_turn_reports_the_required_approval(harness: Any) -> None:
    h = harness(*remember_script())
    with api_client(h.settings, h.agent) as client:
        body = remember_turn(h, client)
        assert body["status"] == "interrupted"
        approval = body["approval_required"]
        assert approval["operation"] == REMEMBER_FACT and "Hindi" in approval["summary"]
        assert body["tool_calls"][0]["status"] == "pending_approval"
        assert body["input_request"] is None

        view = client.get("/api/v1/conversations/t-hitl", headers=h.headers()).json()
        assert view["pending"] and view["awaiting_approval"]["approval_id"] == approval[
            "approval_id"
        ]
        blocked = client.post(
            "/api/v1/conversations/t-hitl/messages",
            json={"message": "something else"},
            headers=h.headers(),
        )
        assert blocked.status_code == 409
        assert error_of(blocked)["code"] == "THREAD_HAS_PENDING_TURN"
        early = client.post("/api/v1/conversations/t-hitl/resume", headers=h.headers())
        assert early.status_code == 409
        assert error_of(early)["code"] == "APPROVAL_PENDING"

        listed = client.get("/api/v1/approvals", headers=h.headers()).json()["approvals"]
        assert [a["approval_id"] for a in listed] == [approval["approval_id"]]
        (mine,) = listed
        assert (mine["status"], mine["thread_id"], mine["can_decide"]) == (
            "pending",
            "t-hitl",
            True,
        )
    # nothing ran
    assert h.auditor.of("memory.fact.create") == []


def test_self_approval_with_resume_executes_once(harness: Any) -> None:
    h = harness(*remember_script("Done, I'll remember that."))
    with api_client(h.settings, h.agent) as client:
        approval_id = remember_turn(h, client)["approval_required"]["approval_id"]
        decided = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"note": "yes", "resume": True},
            headers=h.headers(),
        )
        assert decided.status_code == 200, decided.text
        body = decided.json()
        assert body["next_action"] == "resumed"
        assert body["approval"]["status"] == "approved"  # the record as decided
        turn = body["turn"]
        assert (turn["status"], turn["message"]) == ("completed", "Done, I'll remember that.")
        assert turn["tool_calls"][-1] == {
            "tool_name": REMEMBER_FACT, "status": "ok", "error_code": None,
            "approval_id": approval_id,
        }  # fmt: skip
        fact = client.get("/api/v1/memory/facts/preferences/reply_language", headers=h.headers())
        assert fact.json()["content"] == "Asha prefers Hindi replies."
        again = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=h.headers())
        assert again.status_code == 409
        assert error_of(again)["code"] == "APPROVAL_ALREADY_DECIDED"
        nothing = client.post("/api/v1/conversations/t-hitl/resume", headers=h.headers())
        assert nothing.status_code == 409
        assert error_of(nothing)["code"] == "NOTHING_TO_RESUME"
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=h.headers()).json()[
            "status"
        ] == "executed"
    actions = h.auditor.actions()
    for expected in ("approval.requested", "approval.decided", "memory.fact.create",
                     "operation.executed"):  # fmt: skip
        assert actions.count(expected) == 1, expected


def test_decision_then_resume_by_the_requester(harness: Any) -> None:
    h = harness(*remember_script("Okay, I won't remember it."))
    with api_client(h.settings, h.agent) as client:
        approval_id = remember_turn(h, client)["approval_required"]["approval_id"]
        decided = client.post(f"/api/v1/approvals/{approval_id}/reject", headers=h.headers())
        assert decided.status_code == 200
        assert decided.json()["next_action"] == "resume_conversation"
        assert decided.json()["approval"]["status"] == "rejected"
        resumed = client.post("/api/v1/conversations/t-hitl/resume", headers=h.headers())
        assert resumed.status_code == 200
        assert resumed.json()["tool_calls"][-1]["error_code"] == "APPROVAL_REJECTED"
        assert (
            client.get(
                "/api/v1/memory/facts/preferences/reply_language", headers=h.headers()
            ).status_code
            == 404
        )
    assert "memory.fact.create" not in h.auditor.actions()
    assert "operation.rejected" in h.auditor.actions()


def test_private_approvals_are_invisible_to_other_users(harness: Any) -> None:
    """Phase 6 fix: another member (who also holds memory.write) and an approver can
    neither see nor decide a requester-only request; other tenants cannot either."""
    h = harness(*remember_script())
    with api_client(h.settings, h.agent) as client:
        approval_id = remember_turn(h, client)["approval_required"]["approval_id"]
        for intruder in (
            h.headers("acme", "ravi"),
            h.headers("acme", "kim", "member", "approver"),
            h.headers("acme", "root", "tenant_admin"),
            h.headers("globex", "asha"),
        ):
            assert client.get("/api/v1/approvals", headers=intruder).json() == {"approvals": []}
            for response in (
                client.get(f"/api/v1/approvals/{approval_id}", headers=intruder),
                client.post(f"/api/v1/approvals/{approval_id}/approve", headers=intruder),
                client.post(f"/api/v1/approvals/{approval_id}/reject", headers=intruder),
                client.post(f"/api/v1/approvals/{approval_id}/cancel", headers=intruder),
            ):
                assert response.status_code == 404, response.request.url
                assert "Hindi" not in response.text
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=h.headers()).json()[
            "status"
        ] == "pending"


def test_approver_decides_shared_approvals_and_requester_resumes(harness: Any) -> None:
    """A normal (non-private) approval: the approver decides; only the requester resumes."""
    from agentic_platform.security.policy import ApprovalPolicy

    h = harness()
    asha, kim = _p("acme", "asha"), _p("acme", "kim", "member", "approver")
    approvals = h.agent.dependencies.security.approvals

    async def request() -> Any:
        return await approvals.request(
            asha, operation="system.test_op", operation_hash="h" * 64,
            summary="A shared test operation", required_permission="tool.execute",
            risk="high", policy=ApprovalPolicy(), thread_id="t-op", request_id="r-1",
        )  # fmt: skip

    import asyncio

    record = asyncio.run(request())
    with api_client(h.settings, h.agent) as client:
        kim_view = client.get(f"/api/v1/approvals/{record.id}", headers=h.headers("acme", "kim",
                              "member", "approver")).json()  # fmt: skip
        assert kim_view["can_decide"] is True and kim_view["thread_id"] is None
        ravi = client.post(
            f"/api/v1/approvals/{record.id}/approve", headers=h.headers("acme", "ravi")
        )
        assert ravi.status_code == 404  # no approver permission: not even visible
        no_permission = client.post(f"/api/v1/approvals/{record.id}/approve", headers=h.headers())
        assert no_permission.status_code == 403  # the requester is no approver
        assert error_of(no_permission)["code"] == "PERMISSION_DENIED"
        self_approve = client.post(
            f"/api/v1/approvals/{record.id}/approve",
            headers=h.headers("acme", "asha", "member", "approver"),
        )
        assert self_approve.status_code == 403  # an approver, but it is their own request
        assert error_of(self_approve)["message"] == "You cannot approve your own request."
        decided = client.post(
            f"/api/v1/approvals/{record.id}/approve",
            json={"resume": True},
            headers=h.headers("acme", "kim", "member", "approver"),
        )
        assert decided.status_code == 200
        body = decided.json()
        # The approver cannot resume someone else's turn: the requester must.
        assert body["next_action"] == "resume_conversation" and body["turn"] is None
        assert body["approval"]["decided_by"] == kim.user_id
    assert [e.approver_id for e in h.auditor.of("approval.decided")] == ["kim"]


def test_expired_and_invalid_approvals(harness: Any) -> None:
    h = harness(*remember_script("It expired."))
    with api_client(h.settings, h.agent) as client:
        approval_id = remember_turn(h, client)["approval_required"]["approval_id"]
        for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
            missing = client.get(f"/api/v1/approvals/{bad}", headers=h.headers())
            assert missing.status_code == 404
            assert error_of(missing)["code"] == "APPROVAL_NOT_FOUND"
        store = h.agent.dependencies.security.approvals.store
        assert isinstance(store, InMemoryApprovalStore)
        real_now = store.now
        store.now = lambda: real_now() + timedelta(days=2)  # past the 24 h TTL
        expired = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=h.headers())
        assert expired.status_code == 410
        assert error_of(expired)["code"] == "APPROVAL_EXPIRED"
        resumed = client.post("/api/v1/conversations/t-hitl/resume", headers=h.headers())
        assert resumed.status_code == 200
        assert resumed.json()["tool_calls"][-1]["error_code"] == "APPROVAL_EXPIRED"
    assert "memory.fact.create" not in h.auditor.actions()


def test_requester_can_cancel(harness: Any) -> None:
    h = harness(*remember_script())
    with api_client(h.settings, h.agent) as client:
        approval_id = remember_turn(h, client)["approval_required"]["approval_id"]
        cancelled = client.post(f"/api/v1/approvals/{approval_id}/cancel", headers=h.headers())
        assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")
        assert client.get("/api/v1/approvals", headers=h.headers()).json() == {"approvals": []}


# =============================================================================================
# Memory and audit
# =============================================================================================


def test_private_memory_read_write_delete(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        put = client.put(
            "/api/v1/memory/facts/prefs/lang",
            json={"content": "Prefers Tamil.", "ttl_seconds": 3600},
            headers=h.headers(),
        )
        assert put.status_code == 200
        fact = put.json()
        assert (fact["namespace"], fact["key"], fact["shared"]) == ("prefs", "lang", False)
        assert fact["expires_at"] is not None
        listed = client.get("/api/v1/memory/facts", headers=h.headers()).json()["facts"]
        assert [f["content"] for f in listed] == ["Prefers Tamil."]
        # other users and tenants see nothing of it
        for intruder in (h.headers("acme", "ravi"), h.headers("globex", "asha")):
            assert client.get("/api/v1/memory/facts", headers=intruder).json() == {"facts": []}
            assert (
                client.get("/api/v1/memory/facts/prefs/lang", headers=intruder).status_code == 404
            )
            assert (
                client.delete("/api/v1/memory/facts/prefs/lang", headers=intruder).status_code
                == 404
            )
        deleted = client.delete("/api/v1/memory/facts/prefs/lang", headers=h.headers())
        assert deleted.json() == {"deleted": 1}
        assert client.get("/api/v1/memory/facts/prefs/lang", headers=h.headers()).status_code == 404
        bad_name = client.put(
            "/api/v1/memory/facts/pre fs/lang", json={"content": "x"}, headers=h.headers()
        )
        assert bad_name.status_code == 422
    assert h.auditor.actions().count("memory.fact.create") == 1
    # Every delete attempt is audited; only the owner's deleted anything.
    deletes = [
        (e.tenant_id, e.actor_id, e.details["deleted"]) for e in h.auditor.of("memory.fact.delete")
    ]
    assert sorted(deletes) == [("acme", "asha", 1), ("acme", "ravi", 0), ("globex", "asha", 0)]
    assert all("Tamil" not in e.model_dump_json() for e in h.auditor.events)


def test_shared_memory_needs_shared_permissions(harness: Any) -> None:
    h = harness()
    member = h.headers("acme", "asha")
    manager = h.headers("acme", "kim", "member", "knowledge_manager")
    with api_client(h.settings, h.agent) as client:
        refused = [
            client.put("/api/v1/memory/facts/company/fy", json={"content": "FY starts April.",
                       "shared": True}, headers=member),
            client.put("/api/v1/memory/documents/hr", json={"text": "Leave is 18 days."},
                       headers=member),
        ]  # fmt: skip
        assert [r.status_code for r in refused] == [403, 403]
        assert client.put("/api/v1/memory/facts/company/fy", json={"content": "FY starts April.",
                          "shared": True}, headers=manager).status_code == 200  # fmt: skip
        doc = client.put(
            "/api/v1/memory/documents/hr",
            json={"text": "Paid leave is 18 days per year.", "title": "HR"},
            headers=manager,
        )
        assert doc.json() == {"source_ref": "hr", "chunks": 1, "shared": True}
        # members read shared memory but may not delete it
        shared = client.get("/api/v1/memory/facts", headers=member).json()["facts"]
        assert [(f["content"], f["shared"]) for f in shared] == [("FY starts April.", True)]
        hits = client.post(
            "/api/v1/memory/knowledge/search", json={"query": "paid leave days"}, headers=member
        ).json()["hits"]
        assert hits and hits[0]["source_ref"] == "hr" and hits[0]["title"] == "HR"
        assert client.delete("/api/v1/memory/documents/hr", headers=member).status_code == 403
        assert (
            client.delete("/api/v1/memory/facts/company/fy?shared=true", headers=member).status_code
            == 403
        )
        # another tenant sees none of it
        other = h.headers("globex", "kim", "member", "knowledge_manager")
        assert client.get("/api/v1/memory/facts", headers=other).json() == {"facts": []}
        assert client.post(
            "/api/v1/memory/knowledge/search", json={"query": "paid leave days"}, headers=other
        ).json() == {"hits": []}
        assert client.delete("/api/v1/memory/documents/hr", headers=other).status_code == 404
        assert client.delete("/api/v1/memory/documents/hr", headers=manager).json()["deleted"] >= 1
        private = client.put(
            "/api/v1/memory/documents/my-note",
            json={"text": "My own note.", "shared": False, "source_type": "note"},
            headers=member,
        )
        assert private.status_code == 200
        assert client.delete("/api/v1/memory/me", headers=member).json()["deleted"] >= 1
    denied = h.auditor.of("authz.denied")
    assert {e.details["permission"] for e in denied} == {
        "memory.shared.write",
        "memory.shared.delete",
    }


def test_memory_needs_memory_permissions(harness: Any, api_settings: AppSettings) -> None:
    chat_only = api_settings.model_copy(
        update={
            "auth": AuthSettings(
                token_secret=TOKEN_SECRET,
                role_catalog_json='{"chat": ["conversation.read", "conversation.write"]}',
            )
        }
    )
    h = harness(settings=chat_only)
    with api_client(chat_only, h.agent) as client:
        headers = bearer(token(h.agent, "acme", "eve", ["chat"]))
        assert client.get("/api/v1/memory/facts", headers=headers).status_code == 403
        assert (
            client.put(
                "/api/v1/memory/facts/a/b", json={"content": "x"}, headers=headers
            ).status_code
            == 403
        )
        assert client.delete("/api/v1/memory/me", headers=headers).status_code == 403


def test_memory_disabled_is_503(harness: Any, api_settings: AppSettings) -> None:
    off = api_settings.model_copy(update={"memory": MemorySettings(enabled=False)})
    h = harness(settings=off)
    with api_client(off, h.agent) as client:
        response = client.get("/api/v1/memory/facts", headers=h.headers())
    assert response.status_code == 503
    assert error_of(response)["code"] == "FEATURE_UNAVAILABLE"


def test_audit_needs_audit_read(harness: Any) -> None:
    h = harness()
    with api_client(h.settings, h.agent) as client:
        member = client.get("/api/v1/audit", headers=h.headers())
        assert member.status_code == 403
        auditor = client.get("/api/v1/audit", headers=h.headers("acme", "aud", "auditor"))
        assert auditor.status_code == 503  # no database in unit tests: no audit log
        assert error_of(auditor)["code"] == "FEATURE_UNAVAILABLE"
