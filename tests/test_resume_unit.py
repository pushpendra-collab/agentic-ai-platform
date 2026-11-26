"""Agent persistence behaviour without external servers (InMemorySaver and the
in-memory lock backend): thread ids, one turn per thread, pending-turn guard,
crash resume, interrupt resume, thread status and persistence error handling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.domains.base import DomainHandler, DomainRequest
from agentic_platform.agents.schemas import DomainResult
from agentic_platform.agents.threads import validate_thread_id
from agentic_platform.core.config import AgentSettings, AppSettings
from agentic_platform.core.errors import (
    AgentInputError,
    CacheUnavailableError,
    ConfigurationError,
    ConversationNotFoundError,
    DatabaseUnavailableError,
    InvalidThreadIdError,
    NothingToResumeError,
    ThreadPendingError,
)
from agentic_platform.services.clock import ClockService
from tests.conftest import LOCAL_TENANT, understanding
from tests.fakes import ScriptedLLM

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


@pytest.fixture
def no_wait_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(
        update={"agent": AgentSettings(max_tool_calls_per_turn=2, thread_lock_wait_seconds=0)}
    )


# --- thread ids ----------------------------------------------------------------------------


@pytest.mark.parametrize("thread_id", ["abc", "a" * 128, "tenant-1:user_2.conv-3"])
def test_valid_thread_ids(thread_id: str) -> None:
    assert validate_thread_id(thread_id) == thread_id


@pytest.mark.parametrize("thread_id", ["", "a" * 129, "has space", "x/y", "naïve", "a\nb"])
def test_invalid_thread_ids(thread_id: str, make_agent: MakeAgent) -> None:
    with pytest.raises(InvalidThreadIdError):
        validate_thread_id(thread_id)
    agent, _ = make_agent()
    with pytest.raises(InvalidThreadIdError):
        agent.run("hello", thread_id=thread_id)


def test_generated_thread_id_is_valid(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(), "hi")
    response = agent.run("hello")
    assert validate_thread_id(response.thread_id)


# --- one turn at a time per thread ---------------------------------------------------------


def test_busy_thread_returns_retryable_failure(
    no_wait_settings: AppSettings, clock: ClockService
) -> None:
    agent = Agent(build_dependencies(no_wait_settings, llm=ScriptedLLM(), clock=clock))

    async def body() -> Any:
        # Another turn holds the thread's lock (key namespaced by tenant since Phase 5).
        async with agent.dependencies.locks.hold(f"thread:{LOCAL_TENANT}/t-busy"):
            return await agent.arun("hello", thread_id="t-busy")

    response = asyncio.run(body())
    assert response.status == "failed"
    (error,) = response.errors
    assert (error.code, error.retryable) == ("THREAD_BUSY", True)


def test_other_threads_are_not_blocked(no_wait_settings: AppSettings, clock: ClockService) -> None:
    llm = ScriptedLLM(script=[understanding(), "hi"])
    agent = Agent(build_dependencies(no_wait_settings, llm=llm, clock=clock))

    async def body() -> Any:
        async with agent.dependencies.locks.hold(f"thread:{LOCAL_TENANT}/t-a"):
            return await agent.arun("hello", thread_id="t-b")

    assert asyncio.run(body()).status == "completed"


def test_lock_backend_failure_is_a_retryable_failure(
    settings: AppSettings, clock: ClockService
) -> None:
    deps = build_dependencies(settings, llm=ScriptedLLM(), clock=clock)

    class _DownLocks:
        def hold(self, *args: Any, **kwargs: Any) -> Any:
            raise CacheUnavailableError("redis down")

    agent = Agent(deps)
    object.__setattr__(deps, "locks", _DownLocks())
    response = agent.run("hello", thread_id="t-lockdown")
    assert response.status == "failed"
    assert (response.errors[0].code, response.errors[0].retryable) == ("CACHE_UNAVAILABLE", True)


# --- crash resume ------------------------------------------------------------------------


def test_crashed_turn_blocks_new_input_and_resumes_from_last_step(make_agent: MakeAgent) -> None:
    saver = InMemorySaver()
    agent, llm = make_agent(
        understanding(tool_calls=[("system.current_time", "{}")]),
        asyncio.CancelledError(),  # the process "dies" while generating the answer
        "It is 06:30 UTC.",
        checkpointer=saver,
    )
    # LangGraph reports the cancelled node as an error; progress so far is checkpointed.
    crashed = agent.run("What time is it?", thread_id="t-crash")
    assert crashed.status == "failed"
    (error,) = crashed.errors
    assert (error.code, error.retryable) == ("TURN_INCOMPLETE", True)

    status = agent.thread_status("t-crash")
    assert status.exists and status.pending and not status.interrupted
    assert status.next_nodes == ["generate_response"]

    with pytest.raises(ThreadPendingError):
        agent.run("Something else", thread_id="t-crash")

    calls_before = len(llm.calls)
    response = agent.resume("t-crash")
    assert response.status == "completed"
    assert response.message == "It is 06:30 UTC."
    assert len(response.tool_results) == 1  # the tool ran before the crash, not again
    assert len(llm.calls) == calls_before + 1  # only the unfinished step called the LLM
    assert response.request_id == status.last_request_id  # same turn, same request id

    after = agent.thread_status("t-crash")
    assert not after.pending
    with pytest.raises(NothingToResumeError):
        agent.resume("t-crash")


def test_resume_needs_a_checkpointer(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    with pytest.raises(NothingToResumeError):
        agent.resume("t-none")
    assert agent.thread_status("t-none").exists is False


def test_resume_of_unknown_thread(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(checkpointer=InMemorySaver())
    # Phase 5: an unknown thread is "not found" (same as another user's thread).
    with pytest.raises(ConversationNotFoundError):
        agent.resume("t-unknown")
    assert agent.thread_status("t-unknown").exists is False


# --- interrupt resume (the mechanism the HITL approval gate will use) -----------------------


class _AskingHandler(DomainHandler):
    """Test-only domain: pauses the turn to ask the user a question."""

    key = "asking"
    description = "test"

    async def handle(self, request: DomainRequest) -> DomainResult:
        answer = interrupt({"question": "Which timezone?"})
        assert answer == "Asia/Kolkata"
        return DomainResult(domain=self.key, status="completed")


def test_interrupted_turn_resumes_with_a_value(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(domain="asking"), "Noted: Asia/Kolkata.", checkpointer=InMemorySaver()
    )
    agent.dependencies.domains.register(_AskingHandler())

    paused = agent.run("Set my timezone", thread_id="t-ask")
    assert paused.status == "interrupted"
    (pending,) = paused.interrupts
    assert pending.value == {"question": "Which timezone?"}

    status = agent.thread_status("t-ask")
    assert status.interrupted and status.next_nodes == ["process_request"]
    with pytest.raises(ThreadPendingError):
        agent.run("hello?", thread_id="t-ask")
    with pytest.raises(AgentInputError):
        agent.resume("t-ask")  # an interrupt needs an answer

    resumed = agent.resume("t-ask", resume_value="Asia/Kolkata")
    assert resumed.status == "completed"
    assert resumed.message == "Noted: Asia/Kolkata."
    assert len(llm.calls) == 2  # understanding ran once, before the pause


def test_resume_value_rejected_when_nothing_asked(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        understanding(), asyncio.CancelledError(), "done", checkpointer=InMemorySaver()
    )
    assert agent.run("hi", thread_id="t-val").errors[0].code == "TURN_INCOMPLETE"
    with pytest.raises(AgentInputError):
        agent.resume("t-val", resume_value="unexpected")


# --- persistence errors ----------------------------------------------------------------------


class _BrokenSaver(InMemorySaver):
    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        raise DatabaseUnavailableError("checkpoint store down")


def test_checkpoint_store_failure_is_a_retryable_failure(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(), "hi", checkpointer=_BrokenSaver())
    response = agent.run("hello", thread_id="t-broken")
    assert response.status == "failed"
    (error,) = response.errors
    assert (error.code, error.retryable) == ("DATABASE_UNAVAILABLE", True)
    assert "checkpoint store down" not in error.message  # internal detail stays internal


def test_idempotency_key_needs_a_database(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    with pytest.raises(ConfigurationError):
        agent.run("hello", idempotency_key="k1")
