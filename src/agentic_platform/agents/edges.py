"""Conditional edge functions. Pure functions of state: easy to unit-test.

Phase 12 (ADR-061): no edge leads back to `understand_request` or `supervise`, and no
domain node has an edge to itself, so one turn makes exactly one routing decision and
runs at most one domain. `handle_error` and `finalize_turn` are the only ways to END.
"""

from __future__ import annotations

from typing import Literal

from agentic_platform.agents.nodes import (
    APPROVAL_GATE,
    FINALIZE_TURN,
    GENERATE_RESPONSE,
    HANDLE_ERROR,
    PROCESS_REQUEST,
    SUPERVISE,
    VERIFY_BUSINESS_RESPONSE,
)
from agentic_platform.agents.state import AgentState
from agentic_platform.agents.supervisor import SupervisorOutcome, load_decision
from agentic_platform.core.errors import OrchestrationError

AfterUnderstanding = Literal["supervise", "handle_error"]
AfterSupervision = Literal["process_request", "generate_response", "finalize_turn", "handle_error"]
AfterProcessing = Literal["approval_gate", "generate_response", "handle_error"]
AfterApproval = Literal["generate_response", "handle_error"]
AfterGeneration = Literal["handle_error", "verify_business_response", "finalize_turn"]
AfterVerification = Literal["handle_error", "finalize_turn"]

# Turns routed to this domain get their figures checked (Phase 10, ADR-059).
BUSINESS_DOMAIN_KEY = "business"


def route_after_understanding(state: AgentState) -> AfterUnderstanding:
    if state.get("errors") or state.get("understanding") is None:
        return HANDLE_ERROR  # type: ignore[return-value]
    return SUPERVISE  # type: ignore[return-value]


def route_after_supervision(state: AgentState) -> AfterSupervision:
    if state.get("errors"):
        return HANDLE_ERROR  # type: ignore[return-value]
    try:
        decision = load_decision(state.get("orchestration"))
    except OrchestrationError:
        return HANDLE_ERROR  # type: ignore[return-value]
    if decision.fixed_reply:
        return FINALIZE_TURN  # type: ignore[return-value]
    if decision.outcome is SupervisorOutcome.CLARIFY:
        return GENERATE_RESPONSE  # type: ignore[return-value]  # the planner's question
    return PROCESS_REQUEST  # type: ignore[return-value]  # ROUTE, or explained UNSUPPORTED


def route_after_processing(state: AgentState) -> AfterProcessing:
    if state.get("errors"):
        return HANDLE_ERROR  # type: ignore[return-value]
    if state.get("pending_action") is not None:
        return APPROVAL_GATE  # type: ignore[return-value]
    return GENERATE_RESPONSE  # type: ignore[return-value]


def route_after_approval(state: AgentState) -> AfterApproval:
    if state.get("errors"):
        return HANDLE_ERROR  # type: ignore[return-value]
    return GENERATE_RESPONSE  # type: ignore[return-value]


def route_after_generation(state: AgentState) -> AfterGeneration:
    if state.get("errors"):
        return HANDLE_ERROR  # type: ignore[return-value]
    result = state.get("domain_result")
    # Phase 16: also a business turn without any tool result (no call could be built),
    # so a figure from the model or from recalled memory cannot slip through unverified.
    if result is not None and result.domain == BUSINESS_DOMAIN_KEY:
        return VERIFY_BUSINESS_RESPONSE  # type: ignore[return-value]
    # Phase 11: a business write request (never executed) is verified too, so the reply
    # cannot claim that an order, invoice, payment or message was created.
    understanding = state.get("understanding")
    query = understanding.business_query if understanding is not None else None
    if query is not None and query.write_requested:
        return VERIFY_BUSINESS_RESPONSE  # type: ignore[return-value]
    return FINALIZE_TURN  # type: ignore[return-value]


def route_after_verification(state: AgentState) -> AfterVerification:
    if state.get("errors"):
        return HANDLE_ERROR  # type: ignore[return-value]
    return FINALIZE_TURN  # type: ignore[return-value]
