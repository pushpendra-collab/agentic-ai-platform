"""Domain handler contract and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from pydantic import BaseModel

from agentic_platform.agents.routing import RESERVED_DOMAINS
from agentic_platform.agents.schemas import (
    UNSUPPORTED_DOMAIN,
    DomainOutcome,
    DomainResult,
    RequestUnderstanding,
)
from agentic_platform.core.errors import DomainRegistrationError, ToolNotAllowedError
from agentic_platform.security.identity import Principal
from agentic_platform.tools.base import PendingAction, ToolResult, ToolSpec
from agentic_platform.tools.registry import ToolRegistry


class DomainRequest(BaseModel):
    request_id: str
    thread_id: str
    user_input: str
    understanding: RequestUnderstanding
    # The authenticated user the agent acts for (ADR-013). Handlers pass it to every
    # tool call; they never derive identity from the request text or LLM output.
    principal: Principal


class DomainHandler(ABC):
    key: str
    description: str
    # Tool domains this handler may call (ToolSpec.domain values).
    tool_domains: tuple[str, ...] = ()

    @abstractmethod
    async def handle(self, request: DomainRequest) -> DomainResult: ...


class DomainRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, DomainHandler] = {}

    def register(self, handler: DomainHandler) -> None:
        if handler.key == UNSUPPORTED_DOMAIN:
            raise DomainRegistrationError(f"'{UNSUPPORTED_DOMAIN}' is a reserved domain key.")
        # Phase 12 (ADR-061): a future domain is activated by implementing it and moving
        # its key out of RESERVED_DOMAINS (agents/routing.py), never by registering a stub.
        if handler.key in RESERVED_DOMAINS:
            raise DomainRegistrationError(
                f"'{handler.key}' is reserved for a domain that is not implemented yet."
            )
        if handler.key in self._handlers:
            raise DomainRegistrationError(f"Domain '{handler.key}' is already registered.")
        self._handlers[handler.key] = handler

    def get(self, key: str) -> DomainHandler | None:
        return self._handlers.get(key)

    def keys(self) -> list[str]:
        return list(self._handlers)

    def describe_for_llm(self) -> str:
        return "\n".join(f"- `{h.key}`: {h.description}" for h in self._handlers.values())


def resolve_tool_name(name: str, specs: list[ToolSpec]) -> str:
    """The registered name for `name`: itself when registered, else the one tool of these
    specs whose name ends in `.<name>` (a local model often drops the domain prefix:
    `daily_sales` for `business.daily_sales`). Unknown or ambiguous names stay as given,
    so the registry still answers TOOL_NOT_FOUND; permissions are checked there as usual."""
    if any(s.name == name for s in specs):
        return name
    matches = [s.name for s in specs if s.name.endswith(f".{name}")]
    return matches[0] if len(matches) == 1 else name


def _skipped(tool_name: str, message: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name, status="error", error_code=ToolNotAllowedError.code,
        error_message=message,
    )  # fmt: skip


async def run_planned_tool_calls(
    tools: ToolRegistry,
    request: DomainRequest,
    *,
    domain: str,
    tool_domains: tuple[str, ...],
    max_tool_calls: int,
    allowed: Callable[[ToolSpec], bool] = lambda _: True,
) -> DomainResult:
    """Run the calls planned by `understand_request`, in order, as the request's principal,
    limited to `tool_domains`, `allowed` and the per-turn budget. A protected call (approval
    needed) stops the plan: later calls are skipped and the turn pauses at `approval_gate`."""
    results: list[ToolResult] = []
    pending: PendingAction | None = None
    for index, call in enumerate(request.understanding.tool_calls):
        if pending is not None:
            results.append(
                _skipped(call.tool_name, "Skipped: an earlier action is waiting for approval.")
            )
            continue
        if index >= max_tool_calls:
            results.append(
                _skipped(call.tool_name, "Skipped: tool-call limit for this request reached.")
            )
            continue
        specs = tools.specs(tool_domains)
        name = resolve_tool_name(call.tool_name, specs)
        spec = next((s for s in specs if s.name == name), None)
        if spec is not None and not allowed(spec):
            results.append(_skipped(name, "This tool is not allowed here."))
            continue
        outcome = await tools.execute_call(
            name,
            call.arguments_json,
            allowed_domains=tool_domains,
            principal=request.principal,
        )
        results.append(outcome.result)
        pending = outcome.pending
    return with_tool_outcome(
        DomainResult(
            domain=domain, status="completed", tool_results=results, pending_action=pending
        )
    )


# Tool error codes → domain-level outcome, when no planned call succeeded.
_UNAUTHORIZED_CODES = frozenset({"PERMISSION_DENIED"})
_VALIDATION_CODES = frozenset({"TOOL_INVALID_INPUT", "TOOL_NOT_FOUND", "TOOL_NOT_ALLOWED"})
_DEPENDENCY_CODES = frozenset({"TOOL_EXECUTION_FAILED"})


def with_tool_outcome(result: DomainResult) -> DomainResult:
    """Fill the domain-level fields from the tool results (codes and counts only).

    A turn where at least one call succeeded is SUCCESS (the reply reports the failed
    ones). When none succeeded, the most telling failure decides: permission, then
    invalid input / unknown or refused tool, then a failed or timed-out tool."""
    codes = [r.error_code or "TOOL_ERROR" for r in result.tool_results if r.status == "error"]
    succeeded = any(r.status == "ok" for r in result.tool_results)
    outcome: DomainOutcome = "SUCCESS"
    if codes and not succeeded and result.pending_action is None:
        found = set(codes)
        if found & _UNAUTHORIZED_CODES:
            outcome = "UNAUTHORIZED"
        elif found <= _VALIDATION_CODES:
            outcome = "VALIDATION_ERROR"
        elif found & _DEPENDENCY_CODES:
            outcome = "DEPENDENCY_ERROR"
        else:
            outcome = "FAILED"
    return result.model_copy(
        update={
            "outcome": outcome,
            "next_action": "await_approval" if result.pending_action is not None else "respond",
            "errors": codes,
            "metadata": {
                "tool_calls": len(result.tool_results),
                "failed_calls": len(codes),
            },
        }
    )
