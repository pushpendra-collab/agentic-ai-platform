"""Graph definition.

    START
      ↓
    understand_request ──(error)──────────────────────────────────┐
      ↓  (LLM: classification + plan; untrusted)                   │
    supervise ──(error / no valid decision)────────────────────────┤
      ↓ ROUTE / UNSUPPORTED  ╲ CLARIFY (planner)   ╲ fixed reply    │
    process_request (domain router) ──(error)──────────────────────┤
      ↓          ╲(protected call)  │               │               │
      │        approval_gate ── interrupt() … resume ──(error)─────┤
      ↓          ↓                  ↓               │               │
    generate_response ◄─────────────┘ ──(error)─────────────────────┤
      ↓          ╲(business domain, Phase 10)       │               │
      │        verify_business_response ──(error)───────────────────┤
      ↓          ↓                                  ↓               ↓
    finalize_turn ◄─────────────────────────────────┘         handle_error
      ↓                                                             ↓
     END ◄──────────────────────────────────────────────────────────┘

`supervise` (Phase 12, ADR-061) is the Supervisor: a deterministic node that turns the
planner's classification into a typed `SupervisorDecision` (which domain, or none). It
runs no tool and reads no data. `process_request` is the domain router: it runs the
chosen domain handler once. `finalize_turn` writes the Supervisor's fixed replies,
appends the "not done" notice of a partial route and records the domain outcome; it never
changes a domain's answer. No edge leads back to `supervise`: one routing decision per turn.

`approval_gate` (Phase 5) pauses the turn for human approval with LangGraph's
`interrupt()` and, after resume, runs the approved call through the registry.

`verify_business_response` (Phase 10) checks that every figure in a business reply comes
from the ERP results (deterministic, no LLM).

Future domain agents plug in behind `process_request` through the DomainRegistry, once
their key leaves the reserved list (agents/routing.py); this topology does not change.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.edges import (
    route_after_approval,
    route_after_generation,
    route_after_processing,
    route_after_supervision,
    route_after_understanding,
    route_after_verification,
)
from agentic_platform.agents.nodes import (
    APPROVAL_GATE,
    FINALIZE_TURN,
    GENERATE_RESPONSE,
    HANDLE_ERROR,
    PROCESS_REQUEST,
    SUPERVISE,
    UNDERSTAND_REQUEST,
    VERIFY_BUSINESS_RESPONSE,
    make_approval_gate,
    make_finalize_turn,
    make_generate_response,
    make_handle_error,
    make_process_request,
    make_supervise,
    make_understand_request,
    make_verify_business_response,
)
from agentic_platform.agents.state import AgentState


def build_graph(deps: AgentDependencies) -> StateGraph[AgentState]:
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    graph.add_node(UNDERSTAND_REQUEST, make_understand_request(deps))
    graph.add_node(SUPERVISE, make_supervise(deps))
    graph.add_node(PROCESS_REQUEST, make_process_request(deps))
    graph.add_node(GENERATE_RESPONSE, make_generate_response(deps))
    graph.add_node(HANDLE_ERROR, make_handle_error(deps))
    graph.add_node(APPROVAL_GATE, make_approval_gate(deps))
    graph.add_node(VERIFY_BUSINESS_RESPONSE, make_verify_business_response(deps))
    graph.add_node(FINALIZE_TURN, make_finalize_turn(deps))

    graph.add_edge(START, UNDERSTAND_REQUEST)
    graph.add_conditional_edges(
        UNDERSTAND_REQUEST, route_after_understanding, [SUPERVISE, HANDLE_ERROR]
    )
    graph.add_conditional_edges(
        SUPERVISE,
        route_after_supervision,
        [PROCESS_REQUEST, GENERATE_RESPONSE, FINALIZE_TURN, HANDLE_ERROR],
    )
    graph.add_conditional_edges(
        PROCESS_REQUEST, route_after_processing, [APPROVAL_GATE, GENERATE_RESPONSE, HANDLE_ERROR]
    )
    graph.add_conditional_edges(
        APPROVAL_GATE, route_after_approval, [GENERATE_RESPONSE, HANDLE_ERROR]
    )
    graph.add_conditional_edges(
        GENERATE_RESPONSE,
        route_after_generation,
        [HANDLE_ERROR, VERIFY_BUSINESS_RESPONSE, FINALIZE_TURN],
    )
    graph.add_conditional_edges(
        VERIFY_BUSINESS_RESPONSE, route_after_verification, [HANDLE_ERROR, FINALIZE_TURN]
    )
    graph.add_edge(FINALIZE_TURN, END)
    graph.add_edge(HANDLE_ERROR, END)
    return graph


def compile_graph(
    deps: AgentDependencies, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
    return build_graph(deps).compile(checkpointer=checkpointer, name="agent")
