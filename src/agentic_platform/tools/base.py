"""Tool contract.

A tool is a thin, typed adapter over a service method. It declares strict input
and output schemas and metadata the registry enforces. Tools hold no business
rules themselves.

Phase 5 metadata (enforced by `ToolRegistry`, never by prompts):

* `required_permission`: checked against the authenticated principal before the
  tool is shown to the model and again before it runs (default `tool.execute`).
* `risk`: `low` / `medium` / `high` classification.
* `requires_approval` / `approval`: human approval (HITL). Every `write` tool
  needs approval (ADR-010); so does any tool at or above APPROVAL_MIN_RISK.
* `describe_action`: the human-readable summary an approver sees.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agentic_platform.security.permissions import Permission
from agentic_platform.security.policy import DEFAULT_APPROVAL_POLICY, ApprovalPolicy, RiskLevel


class ToolKind(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolInput(BaseModel):
    """Base for tool inputs: unknown fields are rejected (ARCHITECTURE.md § B.6)."""

    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    domain: str
    kind: ToolKind
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    timeout_seconds: float = 10.0
    required_permission: str = Permission.TOOL_EXECUTE
    risk: RiskLevel = "low"
    requires_approval: bool = False
    approval: ApprovalPolicy = DEFAULT_APPROVAL_POLICY
    describe_action: Callable[[Any], str] | None = None

    def describe_for_llm(self) -> str:
        schema = self.input_model.model_json_schema()
        props = schema.get("properties", {})
        args = ", ".join(
            f"{name}: {prop.get('type', 'any')}"
            + (f" (default {prop['default']!r})" if "default" in prop else "")
            + (f" - {prop['description']}" if "description" in prop else "")
            for name, prop in props.items()
        )
        return f"- `{self.name}`: {self.description} Arguments: {args or 'none'}."


ToolHandler = Callable[[Any], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: ToolHandler


class ToolResult(BaseModel):
    """Structured outcome of one tool call. Safe to show to the LLM.

    `pending_approval`: the call is protected; an approval request was created
    (`approval_id`) and nothing has run yet.
    """

    tool_name: str
    status: Literal["ok", "error", "pending_approval"]
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    approval_id: str | None = None
    duration_ms: int = 0


class PendingAction(BaseModel):
    """A protected tool call waiting for approval, kept in graph state (checkpointed).

    It holds the validated arguments so the approved call runs exactly what was
    approved; `operation_hash` binds it to the approval record. Identity is never
    stored here: the principal comes from authentication on resume.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: str
    tool_name: str
    arguments_json: str
    operation_hash: str
    summary: str
    risk: str
    expires_at: str

    def interrupt_payload(self) -> dict[str, Any]:
        """What the paused turn shows its caller (no arguments, no hashes)."""
        return {
            "type": "approval_required",
            "approval_id": self.approval_id,
            "operation": self.tool_name,
            "summary": self.summary,
            "risk": self.risk,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ToolCallOutcome:
    result: ToolResult
    pending: PendingAction | None = None
