"""Phase 7: first live LLM run, through the existing abstraction (never the SDK directly).

    HTTP API → Agent → LangGraph → LLMProvider (llm/factory) → AnthropicProvider → Claude

Skipped unless `RUN_LIVE_LLM_TESTS=1` (each run makes a few real, billed requests):

* `test_invalid_key_against_the_real_endpoint` needs network access only (a fake key
  is rejected by the real API; nothing is billed);
* the smoke test also needs a real credential: `ANTHROPIC_API_KEY` (or
  `ANTHROPIC_AUTH_TOKEN`) in the environment of the pytest process.

With `TEST_DATABASE_URL` + `TEST_DATABASE_APP_URL` (+ `TEST_REDIS_URL`) the smoke test
runs on PostgreSQL (and Redis) and also checks audit correlation; otherwise it uses
the in-memory stores. Harmless requests only: no business data, no business tools.

    $env:RUN_LIVE_LLM_TESTS="1"; $env:ANTHROPIC_API_KEY="..."
    .venv\\Scripts\\python -m pytest tests/live/test_llm_live.py -m live_llm -s
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import pytest

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import (
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    LLMSettings,
    MemorySettings,
    RedisSettings,
)
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token

# Read at import time: the autouse fixture in conftest.py removes ANTHROPIC_API_KEY.
RUN = os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
MODEL = os.environ.get("LLM_MODEL", "").strip() or "claude-opus-5-5"
OWNER_URL = os.environ.get("TEST_DATABASE_URL", "")
APP_URL = os.environ.get("TEST_DATABASE_APP_URL", "")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "")

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not RUN, reason="set RUN_LIVE_LLM_TESTS=1 (makes real LLM requests)"),
]
needs_credential = pytest.mark.skipif(
    not (API_KEY or AUTH_TOKEN), reason="needs ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN"
)

FACT_KEY = "favourite_colour"
FACT = "My favourite colour is teal."
REMEMBER = "Please remember for the future that my project codename is Bluebird."
FAKE_KEY = "sk-ant-api03-PHASE7-INVALID-" + "0" * 40


def live_settings(**llm: Any) -> AppSettings:
    database, redis = DatabaseSettings(), RedisSettings()
    if OWNER_URL and APP_URL:
        from alembic import command

        from agentic_platform.db.migrations import alembic_config

        command.upgrade(alembic_config(OWNER_URL), "head")
        database = DatabaseSettings(url=APP_URL)
        if REDIS_URL:
            redis = RedisSettings(url=REDIS_URL, key_prefix=f"agentic-live-{uuid.uuid4().hex[:8]}")
    return AppSettings(
        env="test",
        llm=LLMSettings(model=MODEL, anthropic_api_key=API_KEY or None, max_retries=2, **llm),
        database=database,
        redis=redis,
        auth=AuthSettings(token_secret=TOKEN_SECRET),
        memory=MemorySettings(agent_fact_writes="approval"),
    )


def live_agent(settings: AppSettings) -> Agent:
    deps = build_dependencies(settings)  # the real LLM from llm/factory, nothing injected
    if deps.database is not None:
        return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database))
    return Agent(deps)


def _logs(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> str:
    return caplog.text + capsys.readouterr().err


def test_invalid_key_against_the_real_endpoint(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real API rejects a fake key; the client gets a controlled 502, no key anywhere."""
    settings = live_settings().model_copy(
        update={"llm": LLMSettings(model=MODEL, anthropic_api_key=FAKE_KEY, max_retries=0)}
    )
    caplog.set_level(logging.DEBUG, logger="agentic_platform")
    agent = live_agent(settings)
    with api_client(settings, agent) as client:
        response = client.post(
            "/api/v1/agent/turn",
            headers=bearer(token(agent, "live", "smoke")),
            json={"message": "Say hello."},
        )
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "LLM_AUTH_ERROR"
    assert FAKE_KEY not in response.text
    failure = next(r for r in caplog.records if r.getMessage() == "llm.call.failed")
    fields = failure.fields  # type: ignore[attr-defined]
    # Answered by the real API (not a local credential error). Observed 2026-10-02: an
    # invalid key is rejected before a request id is assigned, so none is logged.
    assert (fields["status_code"], fields["error_type"]) == (401, "AuthenticationError")
    logs = _logs(caplog, capsys) + " ".join(str(r.__dict__) for r in caplog.records)
    assert FAKE_KEY not in logs
    print(
        "\nLIVE invalid-key check: real endpoint answered 401 AuthenticationError "
        f"(provider request id: {fields['provider_request_id']}) -> API 502 LLM_AUTH_ERROR"
    )


@needs_credential
def test_live_smoke(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.INFO, logger="agentic_platform")
    settings = live_settings()
    agent = live_agent(settings)
    report: dict[str, Any] = {"model": MODEL, "database": agent.dependencies.database is not None}
    with api_client(settings, agent) as client:  # 1. the API starts
        asha = token(agent, "live-smoke", "asha")
        ravi = token(agent, "live-smoke", "ravi")

        me = client.get("/api/v1/me", headers=bearer(asha))  # 2. authenticated principal
        assert me.status_code == 200 and me.json()["tenant_id"] == "live-smoke"

        # 3-5. LangGraph runs; the real model answers; structured output (understanding)
        # plans the time tool, which runs, then the answer is generated.
        turn = client.post(
            "/api/v1/conversations",
            headers=bearer(asha),
            json={"message": "What is the current time in UTC? Answer in one short sentence."},
        )
        assert turn.status_code == 201, turn.text
        body = turn.json()
        assert body["status"] == "completed", body
        assert body["message"].strip()
        report["turn_ms"] = body["duration_ms"]
        report["tools"] = [c["name"] for c in body["tool_calls"]]
        assert "system.current_time" in report["tools"], body  # structured plan was parsed
        thread = body["thread_id"]
        # 9. correlation: one id from the HTTP header through the turn.
        assert body["request_id"] == turn.headers["x-request-id"]

        # 6. memory retrieval still works: a stored private fact is recalled into the answer.
        put = client.put(
            f"/api/v1/memory/facts/profile/{FACT_KEY}",
            headers=bearer(asha),
            json={"content": FACT},
        )
        assert put.status_code == 200, put.text
        recall = client.post(
            f"/api/v1/conversations/{thread}/messages",
            headers=bearer(asha),
            json={"message": "What is my favourite colour? Answer with the colour only."},
        )
        assert recall.status_code == 200, recall.text
        report["recall_answer"] = recall.json()["message"][:80]
        assert "teal" in recall.json()["message"].lower(), recall.json()

        # 7. authorization still works: another user cannot see the conversation.
        other = client.get(f"/api/v1/conversations/{thread}", headers=bearer(ravi))
        assert other.status_code == 404
        assert client.get("/api/v1/audit", headers=bearer(asha)).status_code == 403

        # 8. approval behaviour: a memory write the model proposes waits for approval;
        # nothing is written without it.
        remember = client.post(
            "/api/v1/conversations",
            headers=bearer(asha),
            json={"message": REMEMBER},
        )
        report["remember_status"] = remember.json().get("status")
        facts = client.get("/api/v1/memory/facts", headers=bearer(asha)).json()["facts"]
        assert not any("bluebird" in f["content"].lower() for f in facts)
        if remember.status_code == 202:
            approval = remember.json()["approval_required"]
            report["approval_operation"] = approval["operation"]
            rejected = client.post(
                f"/api/v1/approvals/{approval['approval_id']}/reject",
                headers=bearer(asha),
                json={"resume": True},
            )
            assert rejected.status_code == 200, rejected.text
            facts = client.get("/api/v1/memory/facts", headers=bearer(asha)).json()["facts"]
            assert not any("bluebird" in f["content"].lower() for f in facts)

        if agent.dependencies.database is not None:  # 9. audit rows carry the request id
            from agentic_platform.services.audit import AuditService

            records = asyncio.run(
                AuditService(agent.dependencies.database).for_thread(
                    thread, tenant_id="live-smoke"
                )
            )
            assert any(r.request_id == body["request_id"] for r in records)

    # 10. no secrets in the logs.
    logs = _logs(caplog, capsys)
    for secret in (API_KEY, AUTH_TOKEN, asha, ravi):
        if secret:
            assert secret not in logs
    assert "llm.call.completed" in logs
    print(f"\nLIVE smoke report: {report}")
