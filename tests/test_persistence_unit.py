"""Phase 3 tests that need no PostgreSQL server: checkpointer selection, tool
auditing, hashing and idempotency argument checks."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import (
    PostgresCheckpointSaver,
    _metadata_json,
    create_checkpointer,
)
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AgentSettings, AppSettings, DatabaseSettings, LLMSettings
from agentic_platform.core.errors import AgentInputError, ConfigurationError
from agentic_platform.core.hashing import stable_hash
from agentic_platform.core.logging import log_context
from agentic_platform.db import Database
from agentic_platform.services.audit import AuditEvent, AuditService
from agentic_platform.services.clock import ClockService
from agentic_platform.services.idempotency import IdempotencyService
from agentic_platform.tools import Tool, ToolKind, ToolRegistry, ToolSpec
from agentic_platform.tools.base import ToolInput, ToolOutput
from agentic_platform.tools.system import SYSTEM_DOMAIN, register_system_tools
from tests.conftest import principal
from tests.fakes import ScriptedLLM

URL = "postgresql://agentic_app:pw@localhost:5432/agentic"


def _database() -> Database:
    return Database(DatabaseSettings(url=URL))  # lazy: never connects in these tests


# --- hashing --------------------------------------------------------------------------


def test_stable_hash_ignores_key_order_and_hides_content() -> None:
    a = stable_hash({"b": 1, "a": [1, 2], "c": {"y": 1, "x": 2}})
    b = stable_hash({"c": {"x": 2, "y": 1}, "a": [1, 2], "b": 1})
    assert a == b
    assert len(a) == 64
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})
    assert "secret" not in stable_hash({"note": "secret"})


def test_stable_hash_accepts_models_and_strings() -> None:
    class _M(ToolInput):
        x: int

    assert stable_hash(_M(x=1)) == stable_hash({"x": 1})
    assert stable_hash("{}") == stable_hash("{}")


# --- checkpointer selection -------------------------------------------------------------


def test_create_checkpointer_kinds() -> None:
    db = _database()
    assert create_checkpointer("none", db) is None
    assert isinstance(create_checkpointer("memory", db), InMemorySaver)
    assert isinstance(create_checkpointer("auto", None), InMemorySaver)
    assert isinstance(create_checkpointer("auto", db), PostgresCheckpointSaver)
    assert isinstance(create_checkpointer("postgres", db), PostgresCheckpointSaver)
    with pytest.raises(ConfigurationError):
        create_checkpointer("postgres", None)


def test_checkpointer_setting_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert AgentSettings().checkpointer == "auto"
    monkeypatch.setenv("AGENT_CHECKPOINTER", "none")
    assert AgentSettings().checkpointer == "none"
    monkeypatch.setenv("AGENT_CHECKPOINTER", "redis")
    with pytest.raises(ValueError):
        AgentSettings()


def test_from_settings_uses_configured_checkpointer() -> None:
    llm_settings = LLMSettings(anthropic_api_key="k")
    no_db = Agent.from_settings(
        AppSettings(llm=llm_settings), llm=ScriptedLLM(), configure_logs=False
    )
    assert isinstance(no_db.graph.checkpointer, InMemorySaver)

    with_db = Agent.from_settings(
        AppSettings(llm=llm_settings, database=DatabaseSettings(url=URL)),
        llm=ScriptedLLM(),
        configure_logs=False,
    )
    assert isinstance(with_db.graph.checkpointer, PostgresCheckpointSaver)

    stateless = Agent.from_settings(
        AppSettings(llm=llm_settings, agent=AgentSettings(checkpointer="none")),
        llm=ScriptedLLM(),
        configure_logs=False,
    )
    assert stateless.graph.checkpointer is None


def test_saver_is_async_only() -> None:
    saver = PostgresCheckpointSaver(_database())
    config: Any = {"configurable": {"thread_id": "t"}}
    with pytest.raises(NotImplementedError, match="async-only"):
        saver.get_tuple(config)
    with pytest.raises(NotImplementedError):
        saver.delete_thread("t")


def test_versions_increase() -> None:
    saver = PostgresCheckpointSaver(_database())
    v1 = saver.get_next_version(None, None)
    v2 = saver.get_next_version(v1, None)
    assert v1 < v2
    assert v1.startswith("0" * 31 + "1.")


def test_metadata_json_strips_nul_and_reserved_keys() -> None:
    config: Any = {
        "configurable": {"thread_id": "t", "checkpoint_ns": "", "user_tag": "a\x00b"},
        "metadata": {"run_label": "x"},
    }
    metadata: Any = {"source": "loop", "step": 2, "parents": {}, "writes": {"n": 1}}
    result = _metadata_json(config, metadata)
    assert result == {
        "source": "loop",
        "step": 2,
        "parents": {},
        "user_tag": "ab",
        "run_label": "x",
    }


# --- tool auditing ----------------------------------------------------------------------


class _RecordingAuditor:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self.fail = fail

    async def record(self, event: AuditEvent) -> int:
        if self.fail:
            raise RuntimeError("audit store down")
        self.events.append(event)
        return len(self.events)


class _In(ToolInput):
    note: str = ""


class _Out(ToolOutput):
    ok: bool = True


def _write_tool() -> Tool:
    async def handler(_: _In) -> _Out:
        return _Out()

    return Tool(
        spec=ToolSpec(
            name="system.test_write",
            description="test",
            domain=SYSTEM_DOMAIN,
            kind=ToolKind.WRITE,
            input_model=_In,
            output_model=_Out,
        ),
        handler=handler,
    )


@pytest.fixture
def audited(clock: ClockService) -> tuple[ToolRegistry, _RecordingAuditor]:
    auditor = _RecordingAuditor()
    registry = ToolRegistry(auditor=auditor)
    register_system_tools(registry, clock)
    registry.register(_write_tool())
    return registry, auditor


def _exec(registry: ToolRegistry, name: str, args: str) -> Any:
    async def body() -> Any:
        with log_context(request_id="req-1", thread_id="thr-1"):
            return await registry.execute(
                name, args, allowed_domains=(SYSTEM_DOMAIN,), principal=principal()
            )

    return asyncio.run(body())


def test_successful_call_is_audited(audited: tuple[ToolRegistry, _RecordingAuditor]) -> None:
    registry, auditor = audited
    args = '{"timezone": "Asia/Kolkata"}'
    result = _exec(registry, "system.current_time", args)

    assert result.status == "ok"
    (event,) = auditor.events
    assert event.action == "tool.call"
    assert event.status == "ok"
    assert event.actor_type == "agent"
    assert event.tool_name == "system.current_time"
    assert event.request_id == "req-1"
    assert event.thread_id == "thr-1"
    assert event.input_hash == stable_hash(args)
    assert event.details["kind"] == "read"
    assert "Kolkata" not in event.model_dump_json()  # arguments are never stored


def test_refused_write_is_audited_as_denied(
    audited: tuple[ToolRegistry, _RecordingAuditor],
) -> None:
    registry, auditor = audited
    result = _exec(registry, "system.test_write", '{"note": "x"}')
    assert result.status == "error"
    assert result.error_code == "TOOL_NOT_ALLOWED"
    (event,) = auditor.events
    assert (event.status, event.error_code, event.details["kind"]) == (
        "denied",
        "TOOL_NOT_ALLOWED",
        "write",
    )


def test_invalid_and_unknown_calls_are_audited(
    audited: tuple[ToolRegistry, _RecordingAuditor],
) -> None:
    registry, auditor = audited
    _exec(registry, "system.current_time", '{"timezone": 5}')
    _exec(registry, "x" * 500, "{}")
    invalid, unknown = auditor.events
    assert (invalid.status, invalid.error_code) == ("error", "TOOL_INVALID_INPUT")
    assert (unknown.status, unknown.error_code) == ("error", "TOOL_NOT_FOUND")
    assert unknown.tool_name is not None and len(unknown.tool_name) == 200
    assert unknown.details["registered"] is False


def test_audit_failure_does_not_fail_read_tool(clock: ClockService) -> None:
    registry = ToolRegistry(auditor=_RecordingAuditor(fail=True))
    register_system_tools(registry, clock)
    result = _exec(registry, "system.current_time", "{}")
    assert result.status == "ok"


def test_registry_audits_only_with_database(settings: AppSettings) -> None:
    without = build_dependencies(settings, llm=ScriptedLLM())
    assert without.tools._auditor is None
    with_db = build_dependencies(
        settings.model_copy(update={"database": DatabaseSettings(url=URL)}), llm=ScriptedLLM()
    )
    assert isinstance(with_db.tools._auditor, AuditService)


def test_audit_event_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        AuditEvent(action="tool.call", status="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AuditEvent(action="tool.call", status="ok", actor_type="root")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AuditEvent(action="tool.call", status="ok", surprise=1)  # type: ignore[call-arg]


# --- idempotency argument checks ----------------------------------------------------------


def test_idempotency_rejects_bad_keys_before_touching_the_database() -> None:
    service = IdempotencyService(_database())
    for scope, key in (("", "k"), ("s", ""), ("s", "k" * 256)):
        with pytest.raises(AgentInputError):
            asyncio.run(service.claim(scope, key, {}))


def test_idempotency_lease_must_fit_ttl() -> None:
    with pytest.raises(ValueError):
        IdempotencyService(_database(), lease_seconds=0)
    with pytest.raises(ValueError):
        IdempotencyService(_database(), lease_seconds=10, ttl_seconds=5)
