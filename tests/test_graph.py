"""End-to-end graph runs with a scripted LLM (no network)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.core.errors import AgentInputError, LLMRefusalError, LLMTransientError
from tests.conftest import understanding
from tests.fakes import ScriptedLLM

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


def test_graph_structure(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    graph = agent.graph.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "understand_request",
        "process_request",
        "generate_response",
        "handle_error",
        "approval_gate",
        "verify_business_response",  # Phase 10: figures of business replies
        "supervise",  # Phase 12: the Supervisor's routing decision
        "finalize_turn",  # Phase 12: fixed replies, partial-route notice, outcome
        "__end__",
    }
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    assert ("__start__", "understand_request", False) in edges
    # Phase 12: understand_request → supervise → {process_request, generate_response};
    # these replace the direct understand_request → process/generate edges.
    assert ("understand_request", "supervise", True) in edges
    assert ("supervise", "process_request", True) in edges
    assert ("supervise", "generate_response", True) in edges
    assert ("process_request", "generate_response", True) in edges
    # Phase 12: turns end through finalize_turn (was generate_response → END).
    assert ("generate_response", "finalize_turn", True) in edges
    assert ("finalize_turn", "__end__", False) in edges
    assert ("handle_error", "__end__", False) in edges
    assert ("process_request", "approval_gate", True) in edges  # Phase 5: HITL
    assert ("approval_gate", "generate_response", True) in edges
    assert ("generate_response", "verify_business_response", True) in edges  # Phase 10
    assert ("verify_business_response", "finalize_turn", True) in edges  # was → END


def test_happy_path_with_tool(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(tool_calls=[("system.current_time", '{"timezone": "Asia/Kolkata"}')]),
        "It is 12:00 in Kolkata.",
    )
    response = agent.run("What time is it in Kolkata?")

    assert response.status == "completed"
    assert response.message == "It is 12:00 in Kolkata."
    assert (response.intent, response.domain) == ("ask_current_time", "general")
    assert [r.status for r in response.tool_results] == ["ok"]
    assert response.tool_results[0].output["iso_timestamp"] == "2026-10-01T12:00:00+05:30"  # type: ignore[index]
    assert response.errors == []
    assert response.steps == 5  # understand, supervise, process, generate, finalize (Phase 12)

    understand_call, generate_call = llm.calls
    assert understand_call.kind == "structured"
    assert understand_call.effort == "low"
    assert "system.current_time" in understand_call.system
    assert "`general`" in understand_call.system
    assert generate_call.kind == "text"
    final_user = generate_call.messages[-1].content
    assert final_user.startswith("What time is it in Kolkata?")
    assert "<agent_context>" in final_user
    assert "2026-10-01T12:00:00+05:30" in final_user


def test_clarification_skips_processing(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(
            intent="unclear",
            needs_clarification=True,
            clarification_question="Which timezone do you mean?",
        )
    )
    response = agent.run("time?")
    assert response.status == "needs_clarification"
    assert response.message == "Which timezone do you mean?"
    assert response.tool_results == []
    assert len(llm.calls) == 1  # no second model call
    assert response.steps == 4  # understand, supervise, generate, finalize (Phase 12)


def test_clarification_without_question_gets_default(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(needs_clarification=True, clarification_question=None))
    response = agent.run("hmm")
    assert response.status == "needs_clarification"
    assert response.message


def test_unsupported_business_domain_is_reported_honestly(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(intent="create_purchase_order", domain="unsupported"),
        "Purchase orders are not available in the assistant yet.",
    )
    response = agent.run("Create a PO for 10 chairs")
    assert response.status == "unsupported"
    assert response.domain == "unsupported"
    assert response.tool_results == []
    assert '"status": "unsupported"' in llm.calls[1].messages[-1].content


def test_unregistered_domain_key_is_unsupported(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(domain="purchase"), "Not available yet.")
    assert agent.run("Show unpaid invoices").status == "unsupported"


def test_tool_errors_do_not_fail_the_turn(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        understanding(tool_calls=[("system.current_time", '{"timezone": "Mars/Olympus"}')]),
        "I don't recognise that timezone.",
    )
    response = agent.run("Time on Mars?")
    assert response.status == "completed"
    assert response.tool_results[0].error_code == "TOOL_INVALID_INPUT"


def test_planned_tool_calls_are_capped(make_agent: MakeAgent) -> None:
    calls = [("system.current_time", "{}")] * 3  # settings fixture allows 2
    agent, _ = make_agent(understanding(tool_calls=calls), "ok")
    results = agent.run("time x3").tool_results
    assert [r.status for r in results] == ["ok", "ok", "error"]


def test_llm_failure_in_understanding_routes_to_handle_error(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(LLMTransientError("429 from provider"))
    response = agent.run("hello")
    assert response.status == "failed"
    assert response.errors[0].code == "LLM_UNAVAILABLE"
    assert response.errors[0].retryable is True
    assert "429" not in response.message  # internals stay in logs
    assert len(llm.calls) == 1


def test_llm_failure_in_generation_routes_to_handle_error(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(), LLMRefusalError("category=cyber"))
    response = agent.run("hello")
    assert response.status == "failed"
    assert response.errors[0].code == "LLM_REFUSAL"
    assert "cyber" not in response.message


def test_unexpected_exception_is_contained(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(RuntimeError("db password is hunter2"))
    response = agent.run("hello")
    assert response.status == "failed"
    assert response.errors[0].code == "INTERNAL_ERROR"
    assert "hunter2" not in response.model_dump_json()


def test_input_validation(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    with pytest.raises(AgentInputError):
        agent.run("   ")
    with pytest.raises(AgentInputError):
        agent.run("x" * 10_000)


def test_multi_turn_with_checkpointer_resets_per_turn_state(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(tool_calls=[("system.current_time", "{}")]),
        "It is 06:30 UTC.",
        understanding(intent="thanks", tool_calls=[]),
        "You're welcome.",
        checkpointer=InMemorySaver(),
    )
    first = agent.run("What time is it?", thread_id="t-1")
    second = agent.run("Thanks!", thread_id="t-1")

    assert first.thread_id == second.thread_id == "t-1"
    assert len(first.tool_results) == 1
    assert second.tool_results == []  # per-turn fields were reset
    assert second.steps == 5  # + supervise, finalize_turn (Phase 12)
    # The second understanding call sees the whole conversation so far.
    history = [(m.role, m.content) for m in llm.calls[2].messages]
    assert history == [
        ("user", "What time is it?"),
        ("assistant", "It is 06:30 UTC."),
        ("user", "Thanks!"),
    ]


def test_threads_are_isolated(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(), "a", understanding(), "b", checkpointer=InMemorySaver()
    )
    agent.run("first thread", thread_id="t-a")
    agent.run("second thread", thread_id="t-b")
    assert [m.content for m in llm.calls[2].messages] == ["second thread"]
