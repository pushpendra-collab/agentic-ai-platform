"""Local test chat page (2026-10-30): mounted in development only, sessions only for the
fixed personas, a decimal company id and a loopback client."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.api import create_app
from agentic_platform.api.routes.chat_ui import PERSONAS
from agentic_platform.core.config import AppSettings, AuthSettings
from tests.api_helpers import TOKEN_SECRET, tokens_of
from tests.fakes import ScriptedLLM

LOCAL = ("127.0.0.1", 50000)


def _client(settings: AppSettings, client: tuple[str, int] = LOCAL) -> tuple[TestClient, Agent]:
    agent = Agent(build_dependencies(settings, llm=ScriptedLLM()))
    app = create_app(settings, agent=agent)
    return TestClient(app, raise_server_exceptions=False, client=client), agent


def _dev(settings: AppSettings) -> AppSettings:
    return settings.model_copy(
        update={"env": "development", "auth": AuthSettings(token_secret=TOKEN_SECRET)}
    )


def test_chat_page_and_session_in_development(settings: AppSettings) -> None:
    client, agent = _client(_dev(settings))
    with client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert "Business Agent Chat" in page.text
        assert page.headers["cache-control"] == "no-store"

        res = client.post("/chat/session", json={"persona": "owner", "company_id": "900000001"})
        assert res.status_code == 200
        body = res.json()
        user_id, roles = PERSONAS["owner"]
        assert (body["user_id"], body["roles"], body["tenant_id"]) == (
            user_id,
            list(roles),
            "900000001",
        )
        principal = asyncio.run(tokens_of(agent).authenticate(body["token"]))
        assert principal.tenant_id == "900000001"
        assert principal.user_id == user_id
        # Roles missing from the role catalog are dropped on the server; never added.
        assert set(principal.roles) <= set(roles)


def test_session_refuses_unknown_persona_and_bad_company(settings: AppSettings) -> None:
    client, _ = _client(_dev(settings))
    with client:
        for payload in (
            {"persona": "admin", "company_id": "900000001"},
            {"persona": "owner", "company_id": "abc"},
            {"persona": "owner", "company_id": "0123"},
            {"persona": "owner", "company_id": "900000001", "roles": ["admin"]},
        ):
            assert client.post("/chat/session", json=payload).status_code == 422, payload


def test_session_refuses_remote_clients(settings: AppSettings) -> None:
    client, _ = _client(_dev(settings), client=("10.0.0.5", 50000))
    with client:
        res = client.post("/chat/session", json={"persona": "owner", "company_id": "1"})
        assert res.status_code == 403


def test_not_mounted_outside_development(settings: AppSettings) -> None:
    tested = settings.model_copy(update={"auth": AuthSettings(token_secret=TOKEN_SECRET)})
    client, _ = _client(tested)  # env="test"
    with client:
        assert client.get("/chat").status_code == 404
        res = client.post("/chat/session", json={"persona": "owner", "company_id": "1"})
        assert res.status_code == 404
