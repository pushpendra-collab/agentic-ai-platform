"""The `general` domain: conversation about the assistant itself plus system tools.

It holds no business logic. It runs the tool calls planned by
`understand_request` through the tool registry, as the authenticated user,
limited to its tool domains (`system`, plus `memory` when agent fact writes are
enabled) and to the per-turn tool-call budget.

A protected call (approval needed) stops the plan: later calls are skipped and
the turn pauses at `approval_gate` until a human decides.
"""

from __future__ import annotations

from agentic_platform.agents.domains.base import (
    DomainHandler,
    DomainRequest,
    run_planned_tool_calls,
)
from agentic_platform.agents.routing import GENERAL_DOMAIN
from agentic_platform.agents.schemas import DomainResult
from agentic_platform.tools.registry import ToolRegistry
from agentic_platform.tools.system import SYSTEM_DOMAIN


class GeneralDomainHandler(DomainHandler):
    key = GENERAL_DOMAIN
    description = (
        "Greetings, questions about what this assistant can do, and requests that the "
        "listed system tools can answer (such as the current date and time)."
    )
    tool_domains: tuple[str, ...] = (SYSTEM_DOMAIN,)

    def __init__(
        self,
        tools: ToolRegistry,
        max_tool_calls: int,
        *,
        extra_tool_domains: tuple[str, ...] = (),
    ) -> None:
        self._tools = tools
        self._max_tool_calls = max_tool_calls
        self.tool_domains = (SYSTEM_DOMAIN, *extra_tool_domains)

    async def handle(self, request: DomainRequest) -> DomainResult:
        return await run_planned_tool_calls(
            self._tools,
            request,
            domain=self.key,
            tool_domains=self.tool_domains,
            max_tool_calls=self._max_tool_calls,
        )
