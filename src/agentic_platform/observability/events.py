"""Typed observability events (Phase 13, ADR-062).

An **observability event** says what the platform did and how long it took: a stage
started or finished, a tool ran, a retry or a timeout happened, a checkpoint was saved or
rejected. It is diagnostic data: best effort, process-local, never a decision input.

A **security audit event** (`services/audit.AuditEvent`, `security/audit.py`) is the
authoritative, append-only record of security-relevant actions (who did what, refused or
allowed). Audit keeps its own pipeline and storage; observability never writes audit rows
and audit never depends on observability. An authorization refusal therefore produces
both: an audit row (authoritative) and an `authz.denied` event (for metrics/diagnostics),
and the event carries no more than the audit row does.

Every field is an identifier, a code, a count, a duration or a flag. `attributes` is
reduced by `core.redaction.safe_attributes`: free text can never enter an event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentic_platform.core.redaction import safe_attributes, safe_scalar

EVENT_KIND = "observability"


class Stage(StrEnum):
    """A stage of one agent turn. Graph nodes map onto stages one to one."""

    TURN = "turn"
    PLANNER = "planner"  # understand_request
    SUPERVISOR = "supervisor"  # supervise
    DOMAIN = "domain"  # process_request
    APPROVAL = "approval"  # approval_gate
    RESPONSE = "response"  # generate_response
    VERIFICATION = "verification"  # verify_business_response
    FINALIZATION = "finalization"  # finalize_turn
    ERROR_HANDLER = "error_handler"  # handle_error
    TOOL = "tool"
    LLM = "llm"
    CHECKPOINT = "checkpoint"
    BUSINESS = "business"
    SECURITY = "security"


NODE_STAGES: dict[str, Stage] = {
    "understand_request": Stage.PLANNER,
    "supervise": Stage.SUPERVISOR,
    "process_request": Stage.DOMAIN,
    "approval_gate": Stage.APPROVAL,
    "generate_response": Stage.RESPONSE,
    "verify_business_response": Stage.VERIFICATION,
    "finalize_turn": Stage.FINALIZATION,
    "handle_error": Stage.ERROR_HANDLER,
}


class EventName(StrEnum):
    REQUEST_RECEIVED = "request.received"
    AUTH_SUCCEEDED = "auth.succeeded"
    AUTH_FAILED = "auth.failed"
    AUTHZ_DENIED = "authz.denied"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_REJECTED = "turn.rejected"
    TURN_INTERRUPTED = "turn.interrupted"
    # one triple per graph stage (node)
    PLANNER_STARTED = "planner.started"
    PLANNER_COMPLETED = "planner.completed"
    PLANNER_FAILED = "planner.failed"
    SUPERVISOR_STARTED = "supervisor.started"
    SUPERVISOR_COMPLETED = "supervisor.completed"
    SUPERVISOR_FAILED = "supervisor.failed"
    SUPERVISOR_DECIDED = "supervisor.decision"
    DOMAIN_STARTED = "domain.started"
    DOMAIN_COMPLETED = "domain.completed"
    DOMAIN_FAILED = "domain.failed"
    APPROVAL_STARTED = "approval.started"
    APPROVAL_COMPLETED = "approval.completed"
    APPROVAL_FAILED = "approval.failed"
    RESPONSE_STARTED = "response.started"
    RESPONSE_COMPLETED = "response.completed"
    RESPONSE_FAILED = "response.failed"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_COMPLETED = "verification.completed"
    VERIFICATION_FAILED = "verification.failed"
    VERIFICATION_RESULT = "verification.result"
    FINALIZATION_STARTED = "finalization.started"
    FINALIZATION_COMPLETED = "finalization.completed"
    FINALIZATION_FAILED = "finalization.failed"
    ERROR_HANDLER_STARTED = "error_handler.started"
    ERROR_HANDLER_COMPLETED = "error_handler.completed"
    ERROR_HANDLER_FAILED = "error_handler.failed"
    # inside stages
    DOMAIN_RUN_STARTED = "domain.run.started"
    DOMAIN_RUN_COMPLETED = "domain.run.completed"
    DOMAIN_RUN_FAILED = "domain.run.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    LLM_CALL_COMPLETED = "llm.call.completed"
    LLM_CALL_FAILED = "llm.call.failed"
    BUSINESS_READ = "business.read"
    RETRY_ATTEMPTED = "retry.attempted"
    RETRY_EXHAUSTED = "retry.exhausted"
    TIMEOUT = "timeout"
    CHECKPOINT_LOADED = "checkpoint.loaded"
    CHECKPOINT_LOAD_FAILED = "checkpoint.load_failed"
    CHECKPOINT_SAVED = "checkpoint.saved"
    CHECKPOINT_SAVE_FAILED = "checkpoint.save_failed"
    CHECKPOINT_REJECTED = "checkpoint.rejected"


def stage_event(stage: Stage, phase: Literal["started", "completed", "failed"]) -> EventName:
    """The lifecycle event of a graph stage (`planner.started`, `domain.failed`, ...)."""
    return EventName(f"{stage.value}.{phase}")


GRAPH_STAGES = frozenset(NODE_STAGES.values())


def is_stage_end(name: EventName, stage: Stage | None) -> bool:
    """True for the end of a graph stage (`planner.completed`, `domain.failed`), not for
    events inside one (`domain.run.completed`, `tool.completed`, `llm.call.completed`)."""
    return stage in GRAPH_STAGES and name.value in (f"{stage}.completed", f"{stage}.failed")


EventStatus = Literal["started", "ok", "error", "rejected", "timeout", "retry", "interrupted"]


class ObsEvent(BaseModel):
    """One observability event. Immutable; unknown fields are refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["observability"] = "observability"
    name: EventName
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Correlation: request (logical turn, from the API or the agent), turn (one graph
    # execution: a run or a resume of that request), thread (conversation, public id),
    # span (one node, tool call, LLM call or checkpoint operation) and its parent.
    request_id: str | None = None
    turn_id: str | None = None
    thread_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    stage: Stage | None = None
    node: str | None = None
    domain: str | None = None
    tool: str | None = None
    status: EventStatus | None = None
    duration_ms: float | None = None
    error_category: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    attempt: int | None = None
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("attributes", mode="before")
    @classmethod
    def _safe_attributes(cls, value: Any) -> dict[str, str | int | float | bool | None]:
        return safe_attributes(value if isinstance(value, dict) else None)

    @field_validator(
        "request_id", "turn_id", "thread_id", "span_id", "parent_span_id", "node", "domain",
        "tool", "error_category", "error_code", mode="before",
    )  # fmt: skip
    @classmethod
    def _safe_identifier(cls, value: Any) -> Any:
        if value is None:
            return None
        return safe_scalar(str(value))

    def log_fields(self) -> dict[str, Any]:
        """The event as flat JSON log fields (None values dropped)."""
        data = self.model_dump(mode="json", exclude_none=True, exclude={"name", "ts"})
        if not data.get("attributes"):
            data.pop("attributes", None)
        return data


__all__ = [
    "EVENT_KIND",
    "GRAPH_STAGES",
    "NODE_STAGES",
    "EventName",
    "EventStatus",
    "ObsEvent",
    "Stage",
    "is_stage_end",
    "stage_event",
]
