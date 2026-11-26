"""supervise: turn the planner's classification into a typed routing decision (Phase 12).

The node gives the `Supervisor` only the names of the registered domains (not the
registry, tools, LLM, database or ERP port). It writes the validated domain back into
`understanding.domain` (the routed domain, or the sanitised label), so every later node
and the response agree on the domain that handled the turn, and free text the planner
put in `domain` is never echoed to the caller. See `agents/supervisor.py`.
"""

from __future__ import annotations

from typing import Any

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node
from agentic_platform.agents.state import AgentState
from agentic_platform.agents.supervisor import Supervisor, SupervisorOutcome
from agentic_platform.core.errors import AgentStateError
from agentic_platform.core.logging import get_logger
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit

logger = get_logger(__name__)


def make_supervise(deps: AgentDependencies) -> NodeFn:
    supervisor = Supervisor(deps.domains.keys)

    @instrumented_node("supervise")
    async def supervise(state: AgentState) -> dict[str, Any]:
        understanding = state.get("understanding")
        if understanding is None:
            raise AgentStateError("supervise reached without an understanding.")
        orchestration = state.get("orchestration") or {}
        decisions = int(orchestration.get("decisions", 0))
        decision = supervisor.decide(
            understanding,
            previous_domain=state.get("last_domain"),
            decisions_so_far=decisions,
        )
        logger.info("supervisor.decided", extra={"fields": decision.log_fields()})
        emit(
            EventName.SUPERVISOR_DECIDED,
            stage=Stage.SUPERVISOR,
            status="ok",
            domain=decision.domain,
            attributes={
                "outcome": decision.outcome.value,
                "reason": decision.reason.value,
                "requested_domain": decision.requested_domain,
                "requested_action": decision.requested_action,
                "fixed_reply": decision.fixed_reply,
                "unavailable_parts": len(decision.unavailable),
                "follow_up": decision.routing_metadata.follow_up,
                "topic_changed": decision.routing_metadata.topic_changed,
            },
        )
        update: dict[str, Any] = {
            "orchestration": {
                "decision": decision.to_state(),
                "decisions": decisions + 1,
                "domain_runs": int(orchestration.get("domain_runs", 0)),
                "domain_outcome": None,
            }
        }
        # The domain the rest of the turn (and the response) reports: the routed domain, or
        # the planner's label sanitised (an invalid label becomes "invalid", never echoed).
        reported = (
            decision.domain
            if decision.outcome is SupervisorOutcome.ROUTE
            else decision.requested_domain
        )
        if reported != understanding.domain:
            update["understanding"] = understanding.model_copy(update={"domain": reported})
        return update

    return supervise
