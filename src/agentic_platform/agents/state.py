"""Typed graph state.

`messages` accumulates across turns of a thread (via `add_messages`), so it
survives in a checkpointer. Every other field is per-turn: it is overwritten by
the nodes that own it and reset by `initial_turn_state()` at the start of each
turn. The graph runs nodes sequentially, so overwrite semantics are safe; a
node that adds to a list writes the full new list.

Rules (ARCHITECTURE.md § B.3.2): state holds references and summaries, not
business records. Bump `STATE_VERSION` on any incompatible change so stored
checkpoints can be migrated.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

from agentic_platform.agents.schemas import (
    AgentError,
    AgentStatus,
    DomainResult,
    RequestUnderstanding,
)
from agentic_platform.tools.base import PendingAction, ToolResult

# v2: memory_context (Phase 4); v3: actor, pending_action (Phase 5);
# v4: business_focus (Phase 11, additive: older checkpoints simply have no focus).
# v5: orchestration, last_domain (Phase 12, additive: plain JSON, no new Python types).
STATE_VERSION = 5


class AgentState(TypedDict):
    state_version: int
    request_id: str
    thread_id: str
    user_input: str
    messages: Annotated[list[AnyMessage], add_messages]
    understanding: RequestUnderstanding | None
    domain_result: DomainResult | None
    tool_results: list[ToolResult]
    final_response: str | None
    status: AgentStatus
    errors: list[AgentError]
    step_count: int
    # Recalled memory for this turn (MemoryContext.to_prompt()); context, not truth.
    memory_context: dict[str, Any] | None
    # Who started the turn: {"tenant_id", "user_id"} of the authenticated principal.
    # Identifiers only (never permissions); a resume must come from the same actor.
    # Written by the agent entry point, never by the LLM.
    actor: dict[str, str] | None
    # A protected tool call waiting for approval (approval_gate pauses on it).
    pending_action: PendingAction | None
    # Phase 11: what the conversation's business answers were about (business/focus.py).
    # References only, never figures. NOT per-turn: `initial_turn_state` leaves it out,
    # so it carries over between turns in the checkpoint. Written by
    # verify_business_response, never by the LLM.
    business_focus: NotRequired[dict[str, Any] | None]
    # Phase 12 (ADR-061), per turn: {"decision": SupervisorDecision as JSON, "decisions":
    # routing decisions made, "domain_runs": domain handler runs, "domain_outcome":
    # DomainResult.outcome}. Plain JSON only. Written by supervise / process_request /
    # finalize_turn, never by the LLM.
    orchestration: NotRequired[dict[str, Any] | None]
    # Phase 12: the domain that handled the conversation's last routed turn. Carried
    # across turns like business_focus. Recorded for routing metadata only: it never
    # decides the next route.
    last_domain: NotRequired[str | None]


def initial_turn_state(
    *,
    request_id: str,
    thread_id: str,
    user_input: str,
    memory_context: dict[str, Any] | None = None,
    actor: dict[str, str] | None = None,
) -> AgentState:
    return AgentState(
        state_version=STATE_VERSION,
        request_id=request_id,
        thread_id=thread_id,
        user_input=user_input,
        messages=[HumanMessage(content=user_input)],
        understanding=None,
        domain_result=None,
        tool_results=[],
        final_response=None,
        status="in_progress",
        errors=[],
        step_count=0,
        memory_context=memory_context,
        actor=actor,
        pending_action=None,
        orchestration=None,
    )
