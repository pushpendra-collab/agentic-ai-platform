"""Phase 13: controlled failure injection (test infrastructure only; nothing random, nothing
in production code paths). Each injected failure must produce the right operational
outcome: response status, public error code, error category, events, and no figure,
no success claim and no write.

Redis and PostgreSQL "unavailable" use real clients pointed at a closed local port
(127.0.0.1:1): nothing is contacted, the connection is refused.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.supervisor import SupervisorDecision
from agentic_platform.cache import build_cache_runtime
from agentic_platform.core.config import DatabaseSettings, ErpSettings, RedisSettings
from agentic_platform.core.errors import LLMOutputError, LLMRefusalError
from agentic_platform.db.engine import Database
from agentic_platform.observability import EventName, capture_events
from agentic_platform.services.clock import ClockService
from tests.business_helpers import TZ, business_principal
from tests.conftest import FIXED_NOW, understanding
from tests.evaluation.harness import WHO, adapter_of, build_agent, plan, settings
from tests.fakes import ScriptedLLM

OUTSTANDING = ("business.customer_outstanding", {"customer_name": "Rahul Gupta"})
CLOSED_PORT = "127.0.0.1:1"
# The Redis/PostgreSQL outage tests fail before any ERP read: an ERP setting that never
# connects and a fixed principal keep them independent of the InvenLogic TEST replica.
OFFLINE_ERP = ErpSettings(
    adapter="postgres",
    database_url=f"postgresql://ro:x@{CLOSED_PORT}/erp",
    expected_database="erp",
    business_timezone=TZ,
)
OFFLINE_WHO = business_principal("acme")


def _business_status(response: Any) -> tuple[str, str | None]:
    result = response.tool_results[0].output["result"]
    return result["status"], result["reason"]


# =============================================================================================
# ERP failures (ErpFaults around the live adapter on the TEST replica)
# =============================================================================================


@pytest.mark.parametrize(
    ("mode", "status", "reason", "category"),
    [
        ("unavailable", "UNAVAILABLE", "ERP_UNAVAILABLE", "dependency_unavailable"),
        ("timeout", "UNAVAILABLE", "ERP_TIMEOUT", "timeout"),
        ("not_configured", "UNAVAILABLE", "ERP_NOT_CONFIGURED", "dependency_unavailable"),
        ("malformed", "DEPENDENCY_ERROR", "MALFORMED_RESPONSE", "non_retryable_dependency"),
        ("crash", "DEPENDENCY_ERROR", "INTERNAL_ERROR", "internal"),
    ],
)
def test_erp_failure_modes(mode: str, status: str, reason: str, category: str) -> None:
    agent, _ = build_agent(plan("outstanding", [OUTSTANDING]), "Outstanding ₹45,000 hai.")
    adapter = adapter_of(agent)
    assert adapter is not None
    adapter.failure = mode  # type: ignore[assignment]
    with capture_events() as captured:
        response = agent.run("Rahul Gupta ka outstanding?", principal=WHO())
    assert _business_status(response) == (status, reason)
    assert "45,000" not in response.message and "₹" not in response.message
    (read,) = captured.of(EventName.BUSINESS_READ)
    assert read.error_category == category
    retried = mode == "unavailable"  # only an unreachable ERP is retried
    assert len(captured.of(EventName.RETRY_ATTEMPTED)) == (1 if retried else 0)
    assert len(adapter.calls) == (2 if retried else 1)


def test_erp_timeout_budget_holds_across_retries() -> None:
    agent, _ = build_agent(plan("outstanding", [OUTSTANDING]), "x", query_timeout=0.1)
    adapter = adapter_of(agent)
    assert adapter is not None
    adapter.failure = "hang"
    started = time.perf_counter()
    response = agent.run("Rahul Gupta ka outstanding?", principal=WHO())
    assert _business_status(response) == ("UNAVAILABLE", "ERP_TIMEOUT")
    assert time.perf_counter() - started < 2.0  # one shared deadline, not one per attempt


def test_a_crashing_tool_is_contained() -> None:
    from agentic_platform.tools import Tool, ToolKind, ToolSpec
    from agentic_platform.tools.base import ToolInput, ToolOutput

    class _In(ToolInput):
        pass

    class _Out(ToolOutput):
        pass

    async def boom(_: _In) -> _Out:
        raise RuntimeError("internal detail: password=hunter2")

    agent, _ = build_agent(
        understanding(domain="general", tool_calls=[("system.boom", "{}")]), "The tool failed."
    )
    agent.dependencies.tools.register(
        Tool(
            spec=ToolSpec(
                name="system.boom",
                description="t",
                domain="system",
                kind=ToolKind.READ,
                input_model=_In,
                output_model=_Out,
            ),
            handler=boom,
        )
    )
    with capture_events() as captured:
        response = agent.run("run boom", principal=WHO())
    assert response.tool_results[0].error_code == "TOOL_EXECUTION_FAILED"
    assert "hunter2" not in response.model_dump_json()
    (failed,) = captured.of(EventName.TOOL_FAILED)
    assert failed.error_category == "tool"


# =============================================================================================
# model and planner/supervisor failures
# =============================================================================================


@pytest.mark.parametrize(
    ("error", "code", "category"),
    [
        (LLMOutputError("bad json"), "LLM_OUTPUT_ERROR", "model"),
        (LLMRefusalError("refused"), "LLM_REFUSAL", "model"),
    ],
)
def test_model_failures(error: Exception, code: str, category: str) -> None:
    agent, _ = build_agent(error)
    with capture_events() as captured:
        response = agent.run("hello", principal=WHO())
    assert response.status == "failed" and response.errors[0].code == code
    (turn,) = captured.of(EventName.TURN_FAILED)
    assert turn.error_category == category
    assert captured.of(EventName.SUPERVISOR_STARTED) == []  # nothing ran after the planner


def test_a_writer_failure_after_a_read_fails_the_turn_without_figures() -> None:
    agent, _ = build_agent(plan("outstanding", [OUTSTANDING]), LLMOutputError("truncated"))
    response = agent.run("Rahul Gupta ka outstanding?", principal=WHO())
    assert response.status == "failed" and "45,000" not in response.message


def test_malformed_supervisor_output_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # The decision written to state is incomplete and smuggles a tenant: every reader
    # re-validates it (load_decision) and refuses it.
    def broken_state(self: SupervisorDecision) -> dict[str, Any]:
        return {"outcome": "ROUTE", "domain": "purchase", "tenant_id": "globex"}

    agent, _ = build_agent(plan("outstanding", [OUTSTANDING]), "x")
    monkeypatch.setattr(SupervisorDecision, "to_state", broken_state)
    with capture_events() as captured:
        response = agent.run("Rahul Gupta ka outstanding?", principal=WHO())
    assert response.status == "failed" and response.errors[0].code == "ORCHESTRATION_FAILED"
    assert adapter_of(agent).calls == []  # type: ignore[union-attr]
    assert captured.of(EventName.TURN_FAILED)[0].error_category == "domain"


# =============================================================================================
# infrastructure: Redis and PostgreSQL unavailable (real clients, closed port)
# =============================================================================================


def test_redis_unavailable_fails_closed_and_retryable() -> None:
    redis = RedisSettings(
        url=f"redis://{CLOSED_PORT}/0", connect_timeout_seconds=0.5, socket_timeout_seconds=0.5
    )
    deps = build_dependencies(
        settings(erp=OFFLINE_ERP),
        llm=ScriptedLLM(),
        clock=ClockService(now_utc=lambda: FIXED_NOW),
        cache=build_cache_runtime(redis),
    )
    agent = Agent(deps)
    with capture_events() as captured:
        response = agent.run("hello there", principal=OFFLINE_WHO, thread_id="t-redis")
    assert response.status == "failed"
    (error,) = response.errors
    # Refused (CACHE_UNAVAILABLE) or, on Windows, a connect timeout (CACHE_TIMEOUT): both
    # are retryable, and the lock fails closed either way.
    assert error.code in ("CACHE_UNAVAILABLE", "CACHE_TIMEOUT") and error.retryable
    (turn,) = captured.of(EventName.TURN_FAILED)
    assert turn.error_category in ("dependency_unavailable", "timeout")
    assert turn.attributes["graph_ran"] is False
    assert captured.of(EventName.PLANNER_STARTED) == []  # no turn without the thread lock


def test_postgres_checkpoint_store_unavailable() -> None:
    database = Database(
        DatabaseSettings(
            url=f"postgresql://u:p@{CLOSED_PORT}/x",
            connect_timeout_seconds=0.5,
            pool_timeout_seconds=1,
        )
    )
    deps = build_dependencies(
        settings(erp=OFFLINE_ERP), llm=ScriptedLLM(), clock=ClockService(now_utc=lambda: FIXED_NOW)
    )
    agent = Agent(deps, checkpointer=PostgresCheckpointSaver(database))
    with capture_events() as captured:
        response = agent.run("hello there", principal=OFFLINE_WHO, thread_id="t-pg")
    assert response.status == "failed"
    (error,) = response.errors
    assert (error.code, error.retryable) == ("DATABASE_UNAVAILABLE", True)
    assert "p@" not in error.message and "u:p" not in response.model_dump_json()
    (load,) = captured.of(EventName.CHECKPOINT_LOAD_FAILED)
    assert load.error_category == "dependency_unavailable"
    assert captured.of(EventName.TURN_FAILED)[0].error_code == "DATABASE_UNAVAILABLE"
