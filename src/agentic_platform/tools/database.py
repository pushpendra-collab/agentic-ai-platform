"""Database system tools (domain `system`).

Agent → Tool → Service → Repository → PostgreSQL, with no SQL from the model:
the tool takes no arguments and returns a summary, not raw probe output.
"""

from __future__ import annotations

from typing import Literal

from agentic_platform.services.database_health import DatabaseHealthService
from agentic_platform.tools.base import Tool, ToolInput, ToolKind, ToolOutput, ToolSpec
from agentic_platform.tools.system import SYSTEM_DOMAIN


class DatabaseHealthInput(ToolInput):
    pass


class DatabaseHealthOutput(ToolOutput):
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    latency_ms: int | None
    schema_up_to_date: bool | None


def database_health_tool(service: DatabaseHealthService) -> Tool:
    async def handler(_: DatabaseHealthInput) -> DatabaseHealthOutput:
        health = await service.check()
        return DatabaseHealthOutput(
            status=health.status,
            latency_ms=health.latency_ms,
            schema_up_to_date=health.migrations.up_to_date if health.migrations else None,
        )

    return Tool(
        spec=ToolSpec(
            name="system.database_health",
            description="Checks whether the assistant's database is reachable and up to date.",
            domain=SYSTEM_DOMAIN,
            kind=ToolKind.READ,
            input_model=DatabaseHealthInput,
            output_model=DatabaseHealthOutput,
            timeout_seconds=10.0,
        ),
        handler=handler,
    )
