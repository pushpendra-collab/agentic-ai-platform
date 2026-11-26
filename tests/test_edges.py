from __future__ import annotations

from typing import Any

from agentic_platform.agents.edges import (
    route_after_generation,
    route_after_processing,
    route_after_supervision,
    route_after_understanding,
    route_after_verification,
)
from agentic_platform.agents.schemas import AgentError
from agentic_platform.agents.state import AgentState
from agentic_platform.agents.supervisor import Supervisor
from tests.conftest import understanding

ERR = AgentError(code="X", message="x", node="n")


def state(**fields: Any) -> AgentState:
    return fields  # type: ignore[return-value]


def decided(**kw: Any) -> dict[str, Any]:
    """The orchestration state `supervise` writes for this understanding."""
    decision = Supervisor(lambda: ["general", "business"]).decide(understanding(**kw))
    return {"decision": decision.to_state(), "decisions": 1, "domain_runs": 0}


def test_after_understanding() -> None:
    # Phase 12: every understood request goes to the Supervisor (clarification included;
    # its routing moved to route_after_supervision, below).
    assert route_after_understanding(state(understanding=understanding())) == "supervise"
    assert (
        route_after_understanding(state(understanding=understanding(needs_clarification=True)))
        == "supervise"
    )
    assert route_after_understanding(state(understanding=None)) == "handle_error"
    assert (
        route_after_understanding(state(understanding=understanding(), errors=[ERR]))
        == "handle_error"
    )


def test_after_supervision() -> None:
    assert route_after_supervision(state(orchestration=decided())) == "process_request"
    # The planner's clarification is still answered by generate_response (no model call).
    assert (
        route_after_supervision(
            state(orchestration=decided(needs_clarification=True, clarification_question="?"))
        )
        == "generate_response"
    )
    # Reserved / unknown domains: the Supervisor's fixed reply, nothing runs.
    assert route_after_supervision(state(orchestration=decided(domain="purchase"))) == (
        "finalize_turn"
    )
    assert route_after_supervision(state(orchestration=decided(domain="hack"))) == ("finalize_turn")
    # The explained unsupported reply keeps its Phase 1 path through process_request.
    assert route_after_supervision(state(orchestration=decided(domain="unsupported"))) == (
        "process_request"
    )
    assert route_after_supervision(state(orchestration=decided(), errors=[ERR])) == ("handle_error")
    # No (valid) decision: a broken contract fails the turn instead of guessing a route.
    assert route_after_supervision(state()) == "handle_error"
    assert route_after_supervision(state(orchestration={"decision": {"outcome": "ROUTE"}})) == (
        "handle_error"
    )


def test_after_processing() -> None:
    assert route_after_processing(state(errors=[])) == "generate_response"
    assert route_after_processing(state(errors=[ERR])) == "handle_error"


def test_after_generation() -> None:
    assert route_after_generation(state(errors=[])) == "finalize_turn"  # was END (Phase 12)
    assert route_after_generation(state(errors=[ERR])) == "handle_error"


def test_after_verification() -> None:
    assert route_after_verification(state(errors=[])) == "finalize_turn"
    assert route_after_verification(state(errors=[ERR])) == "handle_error"
