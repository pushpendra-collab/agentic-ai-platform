"""Non-business system tools (domain `system`).

These exist so the agent runtime can be exercised end to end in Phase 1
without inventing business features.
"""

from __future__ import annotations

from pydantic import Field

from agentic_platform.core.errors import ToolValidationError
from agentic_platform.services.clock import ClockService, UnknownTimezoneError
from agentic_platform.tools.base import Tool, ToolInput, ToolKind, ToolOutput, ToolSpec
from agentic_platform.tools.registry import ToolRegistry

SYSTEM_DOMAIN = "system"


class CurrentTimeInput(ToolInput):
    timezone: str = Field(
        default="UTC", description="IANA timezone name, e.g. 'Asia/Kolkata'."
    )


class CurrentTimeOutput(ToolOutput):
    timezone: str
    iso_timestamp: str
    weekday: str


def current_time_tool(clock: ClockService) -> Tool:
    async def handler(tool_input: CurrentTimeInput) -> CurrentTimeOutput:
        try:
            now = clock.now(tool_input.timezone)
        except UnknownTimezoneError as exc:
            raise ToolValidationError(
                str(exc),
                public_message=f"'{tool_input.timezone}' is not a recognised timezone.",
            ) from exc
        return CurrentTimeOutput(
            timezone=tool_input.timezone,
            iso_timestamp=now.isoformat(timespec="seconds"),
            weekday=now.strftime("%A"),
        )

    return Tool(
        spec=ToolSpec(
            name="system.current_time",
            description="Returns the current date and time in a timezone.",
            domain=SYSTEM_DOMAIN,
            kind=ToolKind.READ,
            input_model=CurrentTimeInput,
            output_model=CurrentTimeOutput,
            timeout_seconds=2.0,
        ),
        handler=handler,
    )


def register_system_tools(registry: ToolRegistry, clock: ClockService) -> None:
    registry.register(current_time_tool(clock))
