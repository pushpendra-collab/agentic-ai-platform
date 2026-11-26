"""Helpers for the HTTP API tests (unit and integration). Never imported by application code."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from agentic_platform.agents import Agent
from agentic_platform.api import create_app
from agentic_platform.core.config import AppSettings
from agentic_platform.security.authentication import HmacTokenAuthenticator
from agentic_platform.services.audit import AuditEvent

TOKEN_SECRET = "api-test-secret-" + "x" * 32


class RecordingAuditor:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> int:
        self.events.append(event)
        return len(self.events)

    def actions(self) -> list[str]:
        return [e.action for e in self.events]

    def of(self, action: str) -> list[AuditEvent]:
        return [e for e in self.events if e.action == action]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def tokens_of(agent: Agent) -> HmacTokenAuthenticator:
    tokens = agent.dependencies.security.tokens
    assert tokens is not None, "settings need AUTH_TOKEN_SECRET"
    return tokens


def token(
    agent: Agent, tenant: str, user: str, roles: Sequence[str] = ("member",), **kw: Any
) -> str:
    return tokens_of(agent).issue(user_id=user, tenant_id=tenant, roles=list(roles), **kw)


@contextmanager
def api_client(settings: AppSettings, agent: Agent) -> Iterator[TestClient]:
    """A client running the app's lifespan (one event loop for the whole block)."""
    app = create_app(settings, agent=agent)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def error_of(response: Any) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}, body
    error: dict[str, Any] = body["error"]
    assert error["request_id"] == response.headers["x-request-id"]
    return error
