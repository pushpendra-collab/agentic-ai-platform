"""Shared node plumbing: instrumentation, error capture and message conversion."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, Protocol

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.errors import GraphBubbleUp

from agentic_platform.agents.schemas import AgentError
from agentic_platform.agents.state import AgentState
from agentic_platform.core.errors import AgentPlatformError
from agentic_platform.core.logging import get_logger, log_context
from agentic_platform.llm.base import ChatMessage
from agentic_platform.observability.events import NODE_STAGES, Stage, stage_event
from agentic_platform.observability.hub import span
from agentic_platform.observability.taxonomy import ErrorCategory

logger = get_logger("agents.nodes")


class NodeFn(Protocol):
    """A graph node: reads the state, returns a partial state update."""

    async def __call__(self, state: AgentState) -> dict[str, Any]: ...


INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "Something went wrong while handling your request."


def instrumented_node(name: str) -> Callable[[NodeFn], NodeFn]:
    """Log node start/end, count steps, and turn exceptions into recorded `errors`.

    Nodes never raise into LangGraph: a failure is appended to `errors` and the
    conditional edges route to `handle_error`. The exception is LangGraph's own
    control flow (`GraphBubbleUp`, e.g. `interrupt()`), which must propagate.

    Phase 13 (ADR-062): each run is a span of the node's stage (`planner.started`,
    `planner.completed` / `planner.failed`, ...) with duration and error category.
    """
    stage = NODE_STAGES.get(name, Stage.DOMAIN)

    def decorator(fn: NodeFn) -> NodeFn:
        @wraps(fn)
        async def wrapper(state: AgentState) -> dict[str, Any]:
            started = time.perf_counter()
            with log_context(
                request_id=state.get("request_id"),
                thread_id=state.get("thread_id"),
                node=name,
            ), span(
                stage,
                started=stage_event(stage, "started"),
                completed=stage_event(stage, "completed"),
                failed=stage_event(stage, "failed"),
                node=name,
            ) as node_span:
                logger.debug("node.started")
                try:
                    update = await fn(state)
                except GraphBubbleUp:
                    # interrupt() and other LangGraph control flow: not an error. The
                    # graph pauses and the checkpointer keeps the state for resume.
                    logger.info("node.interrupted")
                    node_span.status = "interrupted"
                    raise
                except AgentPlatformError as exc:
                    logger.warning(
                        "node.failed",
                        extra={"fields": {"code": exc.code, "detail": exc.detail}},
                    )
                    node_span.fail(exc)
                    error = AgentError.from_exception(exc, name)
                    update = {"errors": [*state.get("errors", []), error]}
                except Exception as exc:
                    logger.exception("node.crashed")
                    node_span.fail(exc, code=INTERNAL_ERROR_CODE, category=ErrorCategory.INTERNAL)
                    update = {
                        "errors": [
                            *state.get("errors", []),
                            AgentError(
                                code=INTERNAL_ERROR_CODE, message=INTERNAL_ERROR_MESSAGE, node=name
                            ),
                        ]
                    }
                update["step_count"] = state.get("step_count", 0) + 1
                logger.info(
                    "node.completed",
                    extra={"fields": {"duration_ms": int((time.perf_counter() - started) * 1000)}},
                )
                return update

        return wrapper

    return decorator


def to_chat_messages(messages: Sequence[AnyMessage], max_messages: int) -> list[ChatMessage]:
    """Convert LangGraph messages to provider-neutral ones (short-term memory window).

    Keeps the last `max_messages` user/assistant turns and drops leading assistant
    turns so the conversation starts with the user.
    """
    converted: list[ChatMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            converted.append(ChatMessage(role="user", content=message.text))
        elif isinstance(message, AIMessage):
            converted.append(ChatMessage(role="assistant", content=message.text))
    window = converted[-max_messages:]
    while window and window[0].role != "user":
        window.pop(0)
    return window
