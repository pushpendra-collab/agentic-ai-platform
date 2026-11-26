"""approval_gate: pause for human approval, then run the approved action safely (ADR-040).

Uses the Phase 3 pause/resume mechanism: `interrupt()` stops the turn, the
checkpointer keeps the state (including the `pending_action`), and
`Agent.aresume` continues it with `Command(resume=...)`.

On resume this node runs again from the top. Before anything executes:

1. the request's principal comes from authentication (`require_principal`),
   never from the checkpoint or the resume value;
2. it must be the actor who started the turn (same tenant and user);
3. `ToolRegistry.execute_approved` then re-validates the approval (tenant, user,
   workflow, operation, arguments, status, expiry, single use), re-checks the
   principal's current permission, executes at most once and audits the result.

A rejected, expired, stale or otherwise invalid approval becomes an error tool
result; the reply explains it and nothing runs.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node
from agentic_platform.agents.state import AgentState
from agentic_platform.core.errors import AgentStateError, InvalidResumeContextError
from agentic_platform.core.logging import get_logger
from agentic_platform.security.context import require_principal

logger = get_logger(__name__)


def make_approval_gate(deps: AgentDependencies) -> NodeFn:
    @instrumented_node("approval_gate")
    async def approval_gate(state: AgentState) -> dict[str, Any]:
        pending = state.get("pending_action")
        if pending is None:
            raise AgentStateError("approval_gate reached without a pending action.")
        principal = require_principal()
        if state.get("actor") != principal.actor:
            raise InvalidResumeContextError("Turn actor and resuming principal differ.")

        answer = interrupt(pending.interrupt_payload())

        logger.info("approval.resumed", extra={"fields": {"approval_id": pending.approval_id}})
        result = await deps.tools.execute_approved(
            pending,
            principal=principal,
            resume_value=answer,
            thread_id=state["thread_id"],
            request_id=state["request_id"],
        )
        return {
            "tool_results": [*state.get("tool_results", []), result],
            "pending_action": None,
        }

    return approval_gate
