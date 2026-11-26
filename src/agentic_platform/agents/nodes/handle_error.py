"""handle_error: end the turn gracefully with a safe message.

Retries of transient LLM failures already happen inside the provider SDK
(`LLM_MAX_RETRIES`), so by the time an error reaches this node it is final for
this turn. Later phases can route retryable errors back to the failed node.

Phase 13: the edges route here without a recorded error only when the routing contract
is broken (no understanding after the planner, or a missing/invalid Supervisor decision
in state). The node then records ORCHESTRATION_FAILED, so a failed turn always carries an
error code (before: a failed response with an empty `errors` list).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import (
    INTERNAL_ERROR_MESSAGE,
    NodeFn,
    instrumented_node,
)
from agentic_platform.agents.schemas import AgentError
from agentic_platform.agents.state import AgentState
from agentic_platform.core.errors import OrchestrationError


def make_handle_error(deps: AgentDependencies) -> NodeFn:  # deps unused; uniform factory signature
    @instrumented_node("handle_error")
    async def handle_error(state: AgentState) -> dict[str, Any]:
        errors = state.get("errors", [])
        update: dict[str, Any] = {}
        if not errors:
            errors = [AgentError.from_exception(OrchestrationError(), "handle_error")]
            update["errors"] = errors
        message = errors[-1].message or INTERNAL_ERROR_MESSAGE
        return {
            **update,
            "final_response": message,
            "status": "failed",
            "messages": [AIMessage(content=message)],
        }

    return handle_error
