from __future__ import annotations

import asyncio

import pytest

from agentic_platform.core.errors import ToolRegistrationError
from agentic_platform.services.clock import ClockService, UnknownTimezoneError
from agentic_platform.tools import Tool, ToolKind, ToolRegistry, ToolSpec
from agentic_platform.tools.base import ToolInput, ToolOutput
from agentic_platform.tools.system import SYSTEM_DOMAIN, register_system_tools
from tests.conftest import FIXED_NOW, principal


@pytest.fixture
def registry(clock: ClockService) -> ToolRegistry:
    reg = ToolRegistry()
    register_system_tools(reg, clock)
    return reg


def run(registry: ToolRegistry, name: str, args: str, domains: tuple[str, ...] = (SYSTEM_DOMAIN,)):  # type: ignore[no-untyped-def]
    return asyncio.run(
        registry.execute(name, args, allowed_domains=domains, principal=principal())
    )


class _In(ToolInput):
    pass


class _Out(ToolOutput):
    ok: bool = True


def _tool(name: str, kind: ToolKind = ToolKind.READ, handler=None, timeout: float = 1.0) -> Tool:  # type: ignore[no-untyped-def]
    async def default(_: _In) -> _Out:
        return _Out()

    return Tool(
        spec=ToolSpec(
            name=name,
            description="test",
            domain=SYSTEM_DOMAIN,
            kind=kind,
            input_model=_In,
            output_model=_Out,
            timeout_seconds=timeout,
        ),
        handler=handler or default,
    )


def test_clock_service_converts_timezone(clock: ClockService) -> None:
    assert clock.now("Asia/Kolkata").isoformat() == "2026-10-01T12:00:00+05:30"
    with pytest.raises(UnknownTimezoneError):
        clock.now("Mars/Olympus")


def test_current_time_ok(registry: ToolRegistry) -> None:
    result = run(registry, "system.current_time", '{"timezone": "Asia/Kolkata"}')
    assert result.status == "ok"
    assert result.output == {
        "timezone": "Asia/Kolkata",
        "iso_timestamp": "2026-10-01T12:00:00+05:30",
        "weekday": "Thursday",
    }


def test_current_time_defaults_to_utc(registry: ToolRegistry) -> None:
    result = run(registry, "system.current_time", "")
    assert result.status == "ok"
    assert result.output is not None
    assert result.output["iso_timestamp"] == FIXED_NOW.isoformat(timespec="seconds")


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ('{"timezone": "Mars/Olympus"}', "TOOL_INVALID_INPUT"),
        ('{"timezone": "UTC", "drop_tables": true}', "TOOL_INVALID_INPUT"),
        ("not json", "TOOL_INVALID_INPUT"),
        ("[1, 2]", "TOOL_INVALID_INPUT"),
        ('{"timezone": 5}', "TOOL_INVALID_INPUT"),
    ],
)
def test_invalid_input_returns_structured_error(
    registry: ToolRegistry, args: str, code: str
) -> None:
    result = run(registry, "system.current_time", args)
    assert result.status == "error"
    assert result.error_code == code
    assert result.error_message
    assert "Traceback" not in result.error_message


def test_unknown_tool(registry: ToolRegistry) -> None:
    result = run(registry, "sql.execute", "{}")
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_FOUND")


def test_tool_outside_allowed_domains(registry: ToolRegistry) -> None:
    result = run(registry, "system.current_time", "{}", domains=("purchase",))
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_ALLOWED")


def test_write_tools_are_refused_until_hitl_exists() -> None:
    reg = ToolRegistry()
    reg.register(_tool("system.write_something", kind=ToolKind.WRITE))
    result = run(reg, "system.write_something", "{}")
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_ALLOWED")


def test_duplicate_registration_rejected(registry: ToolRegistry, clock: ClockService) -> None:
    with pytest.raises(ToolRegistrationError):
        register_system_tools(registry, clock)


def test_tool_timeout() -> None:
    async def slow(_: _In) -> _Out:
        await asyncio.sleep(1)
        return _Out()

    reg = ToolRegistry()
    reg.register(_tool("system.slow", handler=slow, timeout=0.01))
    result = run(reg, "system.slow", "{}")
    assert (result.status, result.error_code) == ("error", "TOOL_EXECUTION_FAILED")


def test_tool_crash_is_contained() -> None:
    async def boom(_: _In) -> _Out:
        raise RuntimeError("secret internal detail")

    reg = ToolRegistry()
    reg.register(_tool("system.boom", handler=boom))
    result = run(reg, "system.boom", "{}")
    assert result.status == "error"
    assert "secret" not in (result.error_message or "")


def test_specs_filter_by_domain(registry: ToolRegistry) -> None:
    assert [s.name for s in registry.specs([SYSTEM_DOMAIN])] == ["system.current_time"]
    assert registry.specs(["purchase"]) == []
    description = registry.specs()[0].describe_for_llm()
    assert "system.current_time" in description
    assert "timezone" in description
