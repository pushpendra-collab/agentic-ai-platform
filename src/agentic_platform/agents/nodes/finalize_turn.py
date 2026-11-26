"""finalize_turn: the last node of every turn that did not fail (Phase 12, ADR-061).

Deterministic (no LLM call). It never changes what a domain answered:

* When the Supervisor answers the turn itself (a reserved or unknown domain, business
  not connected, or a request that mixes a read with sending something), it writes the
  fixed reply. Nothing ran.
* When a domain ran and part of the request needs a reserved domain (partial route), it
  **appends** a fixed notice that this part was not done. The domain's reply, figures and
  status are kept as they are; only a reply that claims the missing action happened
  (possible on a general turn, which has no business verification) is replaced, because
  that claim is always false.
* It records the domain-level outcome (`DomainResult.outcome`, not business statuses) and
  the conversation's `last_domain`, and logs `supervisor.turn.completed` (codes only).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node
from agentic_platform.agents.state import AgentState
from agentic_platform.agents.supervisor import (
    SupervisorOutcome,
    fixed_reply,
    load_decision,
    not_available_reply,
    partial_notice,
)
from agentic_platform.business.grounding import claims_action
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)


def make_finalize_turn(deps: AgentDependencies) -> NodeFn:  # deps unused; uniform signature
    @instrumented_node("finalize_turn")
    async def finalize_turn(state: AgentState) -> dict[str, Any]:
        orchestration = dict(state.get("orchestration") or {})
        decision = load_decision(orchestration)
        domain_result = state.get("domain_result")
        update: dict[str, Any] = {}
        replaced = appended = False

        if decision.fixed_reply:
            text = fixed_reply(decision)
            status = (
                "needs_clarification"
                if decision.outcome is SupervisorOutcome.CLARIFY
                else "unsupported"
            )
            update |= {
                "final_response": text,
                "status": status,
                "messages": [AIMessage(content=text)],
            }
        elif (notice := partial_notice(decision)) is not None and domain_result is not None:
            reply = state.get("final_response") or ""
            if claims_action(reply):
                reply, replaced = not_available_reply(decision.unavailable), True
            else:
                reply, appended = f"{reply.rstrip()}\n\n{notice}".lstrip(), True
            last = state["messages"][-1] if state["messages"] else None
            message_id = last.id if isinstance(last, AIMessage) else None
            # Same message id: the add_messages reducer replaces the domain's reply.
            update |= {
                "final_response": reply,
                "messages": [AIMessage(content=reply, id=message_id)],
            }

        orchestration["domain_outcome"] = domain_result.outcome if domain_result else None
        update["orchestration"] = orchestration
        if decision.outcome is SupervisorOutcome.ROUTE and domain_result is not None:
            update["last_domain"] = decision.domain
        logger.info(
            "supervisor.turn.completed",
            extra={
                "fields": {
                    "outcome": decision.outcome.value,
                    "reason": decision.reason.value,
                    "domain": decision.domain,
                    "domain_outcome": orchestration["domain_outcome"],
                    "domain_runs": orchestration.get("domain_runs", 0),
                    "fixed_reply": decision.fixed_reply,
                    "notice_appended": appended,
                    "action_claim_replaced": replaced,
                }
            },
        )
        return update

    return finalize_turn
