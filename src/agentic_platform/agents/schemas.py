"""Pydantic models exchanged between nodes and returned to callers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_platform.agents.routing import UNSUPPORTED_DOMAIN as UNSUPPORTED_DOMAIN
from agentic_platform.agents.routing import RoutingHints
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.core.errors import AgentPlatformError
from agentic_platform.tools.base import PendingAction, ToolResult

AgentStatus = Literal["in_progress", "completed", "needs_clarification", "unsupported", "failed"]
TerminalStatus = Literal["completed", "needs_clarification", "unsupported", "failed"]
# "interrupted": the turn paused (LangGraph interrupt) and waits for `Agent.aresume`.
ResponseStatus = Literal["completed", "needs_clarification", "unsupported", "failed", "interrupted"]

# Phase 12 (ADR-061): the domain-level outcome every domain handler reports. It is what
# the Supervisor interprets; a domain's own result semantics (BusinessResult statuses)
# stay inside the domain, which maps them onto this closed set.
DomainOutcome = Literal[
    "SUCCESS",
    "CLARIFICATION_REQUIRED",
    "UNSUPPORTED",
    "UNAUTHORIZED",
    "DEPENDENCY_ERROR",
    "VALIDATION_ERROR",
    "FAILED",
]
DomainNextAction = Literal["respond", "clarify", "await_approval"]


class PlannedToolCall(BaseModel):
    tool_name: str = Field(description="Exact name of a listed tool.")
    arguments_json: str = Field(description="Tool arguments as a JSON object string.")


class RequestUnderstanding(BaseModel):
    """Structured output of the `understand_request` node (LLM-produced, untrusted)."""

    intent: str = Field(description="Short snake_case label for what the user wants.")
    domain: str = Field(description="A listed domain key, or 'unsupported'.")
    summary: str = Field(description="One-sentence neutral restatement of the request.")
    needs_clarification: bool
    clarification_question: str | None = Field(
        description="The single question to ask when needs_clarification is true, else null."
    )
    tool_calls: list[PlannedToolCall] = Field(
        description="Tools to run before answering, in order. Empty if none are needed."
    )
    # Phase 11 (ADR-060): typed description of a business question; null otherwise.
    business_query: BusinessQueryIntent | None = Field(
        default=None,
        description="For business questions (or business write requests): the typed "
        "query. Null for anything else.",
    )
    # Phase 12 (ADR-061): what the Supervisor needs beyond `domain`; null = nothing extra.
    routing: RoutingHints | None = Field(
        default=None,
        description="Parts of the request that need a domain listed as not available, "
        "and how sure the domain choice is. Null when there is nothing to add.",
    )


class DomainResult(BaseModel):
    """Outcome of `process_request` for one domain (the common domain result contract).

    `tool_results` are the response context the reply is written from. `outcome`,
    `next_action`, `errors` and `metadata` are domain-level: codes and counts only, never
    names, figures or business records, so they are safe to log and to checkpoint."""

    domain: str
    status: Literal["completed", "unsupported"]
    tool_results: list[ToolResult] = Field(default_factory=list)
    # A protected tool call waiting for human approval (routes to approval_gate).
    pending_action: PendingAction | None = None
    outcome: DomainOutcome = "SUCCESS"
    next_action: DomainNextAction = "respond"
    # Error codes of failed calls, in order (no messages: those are in tool_results).
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, int | str | bool] = Field(default_factory=dict)


class AgentError(BaseModel):
    """An error recorded in graph state. `message` is safe to show to users."""

    code: str
    message: str
    node: str
    retryable: bool = False

    @classmethod
    def from_exception(cls, exc: AgentPlatformError, node: str) -> AgentError:
        return cls(code=exc.code, message=exc.public_message, node=node, retryable=exc.retryable)


class PublicError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class InterruptInfo(BaseModel):
    """A pause point waiting for input. `value` is what the graph asked (JSON-safe)."""

    id: str
    value: Any = None


class AgentResponse(BaseModel):
    """What the agent entry point returns to its caller (CLI now, API later)."""

    request_id: str
    thread_id: str
    status: ResponseStatus
    message: str
    interrupts: list[InterruptInfo] = Field(default_factory=list)
    # True when this response was replayed for a repeated idempotency key.
    replayed: bool = False
    intent: str | None = None
    domain: str | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    errors: list[PublicError] = Field(default_factory=list)
    steps: int = 0
    duration_ms: int = 0


class ThreadStatus(BaseModel):
    """Persistence view of one conversation thread (from the checkpointer)."""

    thread_id: str
    exists: bool
    # A turn started but did not finish: it crashed, or it is interrupted.
    pending: bool = False
    interrupted: bool = False
    next_nodes: list[str] = Field(default_factory=list)
    interrupts: list[InterruptInfo] = Field(default_factory=list)
    message_count: int = 0
    last_request_id: str | None = None
    updated_at: str | None = None
