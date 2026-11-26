from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.schemas import PlannedToolCall, RequestUnderstanding
from agentic_platform.core.config import AgentSettings, AppSettings, LLMSettings
from agentic_platform.security import IdentityResolver, Principal
from agentic_platform.services.clock import ClockService
from tests.fakes import ScriptedLLM

FIXED_NOW = datetime(2026, 10, 1, 6, 30, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Keep a developer's real .env / env vars out of the tests."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for var in (
        "ANTHROPIC_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_EFFORT",
        "APP_ENV",
        "DATABASE_URL",
        "DATABASE_MIGRATION_URL",
        "REDIS_URL",
        "AGENT_CHECKPOINTER",
        "AUTH_TOKEN_SECRET",
        "AUTH_ALLOW_LOCAL_IDENTITY",
        "AUTH_LOCAL_ROLES",
        "AUTH_ROLE_CATALOG_JSON",
        "MEMORY_AGENT_FACT_WRITES",
    ):
        monkeypatch.delenv(var, raising=False)
    # Phase 6: identity provider, HTTP API and maintenance settings.
    for var in list(os.environ):
        if var.startswith(("AUTH_", "API_", "MAINTENANCE_", "RATE_LIMIT_", "ERP_")):
            monkeypatch.delenv(var, raising=False)


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings(
        env="test",
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=2),
    )


@pytest.fixture
def clock() -> ClockService:
    return ClockService(now_utc=lambda: FIXED_NOW)


@pytest.fixture
def make_agent(
    settings: AppSettings, clock: ClockService
) -> Callable[..., tuple[Agent, ScriptedLLM]]:
    def factory(*script: object, checkpointer: object = None) -> tuple[Agent, ScriptedLLM]:
        llm = ScriptedLLM(script=list(script))
        deps = build_dependencies(settings, llm=llm, clock=clock)
        return Agent(deps, checkpointer=checkpointer), llm  # type: ignore[arg-type]

    return factory


def understanding(
    *,
    intent: str = "ask_current_time",
    domain: str = "general",
    needs_clarification: bool = False,
    clarification_question: str | None = None,
    tool_calls: list[tuple[str, str]] | None = None,
) -> RequestUnderstanding:
    return RequestUnderstanding(
        intent=intent,
        domain=domain,
        summary="test summary",
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        tool_calls=[
            PlannedToolCall(tool_name=name, arguments_json=args) for name, args in tool_calls or []
        ],
    )


def principal(
    tenant_id: str = "acme",
    user_id: str = "asha",
    roles: tuple[str, ...] = ("member",),
) -> Principal:
    """An authenticated principal with the built-in role catalogue (tests only)."""
    return IdentityResolver().resolve(
        user_id=user_id, tenant_id=tenant_id, roles=roles, auth_method="token"
    )


# The identity `Agent` uses when no principal is passed (local development identity:
# MEMORY_DEFAULT_TENANT_ID / MEMORY_DEFAULT_USER_ID with AUTH_LOCAL_ROLES).
LOCAL_TENANT = "default"
