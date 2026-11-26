"""Central tool registry (ADR-011): the only path from the LLM to services.

Every call goes through `ToolRegistry.execute`, which applies, in order:

    availability ─► domain visibility ─► permission (authenticated principal)
      ─► risk classification ─► approval needed?
            yes: validate input ─► create approval request ─► `pending_approval`
            no:  validate input ─► execute with timeout ─► output validation
      ─► audit ─► logging

A protected call that was approved runs through `execute_approved`, which never
trusts the approval alone (ADR-040):

    approval id matches ─► input re-validated ─► same arguments (hash) ─► approval
      valid for this tenant / user / workflow / operation, approved, unexpired,
      unused ─► permission re-checked with the principal's current permissions
      ─► idempotency claim (no double execution) ─► execute ─► mark executed ─► audit

Failures come back as a structured `ToolResult(status="error")`; they never
raise into the graph. With an `auditor`, every call, including refused and
failed ones, is appended to `audit_log` with a hash of the arguments (never the
arguments themselves), the principal's tenant and user, and the correlation ids.

Phase 13 (ADR-062): every call is a `tool` span (`tool.started`, then `tool.completed`
or `tool.failed` with duration and error category). Events carry the tool name, domain
and outcome only, never argument values. The handler runs under a strict deadline, so a
handler that swallows its cancellation cannot report success after its timeout.
Validation failures keep the error locations and types, not the offending input values.

Without an approval service, protected tools are refused outright (Phase 1-4
behaviour).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any, Protocol

from pydantic import ValidationError

from agentic_platform.core.config import ApprovalSettings
from agentic_platform.core.errors import (
    AgentPlatformError,
    ApprovalError,
    ApprovalInvalidError,
    ApprovalStaleError,
    IdempotencyError,
    ToolError,
    ToolExecutionError,
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolPermissionDeniedError,
    ToolRegistrationError,
    ToolValidationError,
)
from agentic_platform.core.hashing import stable_hash
from agentic_platform.core.logging import current_log_context, get_logger
from agentic_platform.observability.deadline import strict_deadline
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import Span, span
from agentic_platform.observability.taxonomy import ErrorCategory, classify_code
from agentic_platform.security.context import acting_as
from agentic_platform.security.identity import Principal
from agentic_platform.security.policy import approval_required
from agentic_platform.services.approvals import (
    ApprovalRecord,
    ApprovalService,
    OperationBinding,
)
from agentic_platform.services.audit import AuditEvent
from agentic_platform.services.idempotency import IdempotencyService
from agentic_platform.tools.base import (
    PendingAction,
    Tool,
    ToolCallOutcome,
    ToolInput,
    ToolKind,
    ToolResult,
    ToolSpec,
)

logger = get_logger(__name__)

_MAX_AUDIT_NAME = 200
APPROVED_EXECUTION_SCOPE = "approval.execute"


class Auditor(Protocol):
    async def record(self, event: AuditEvent) -> int: ...


def operation_hash(tool_name: str, tool_input: ToolInput) -> str:
    """Binds an approval to one tool and its exact validated arguments."""
    return stable_hash({"tool": tool_name, "arguments": tool_input.model_dump(mode="json")})


class ToolRegistry:
    def __init__(
        self,
        *,
        auditor: Auditor | None = None,
        approvals: ApprovalService | None = None,
        idempotency: IdempotencyService | None = None,
        approval_settings: ApprovalSettings | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._auditor = auditor
        self._approvals = approvals
        self._idempotency = idempotency
        self._min_risk = (approval_settings or ApprovalSettings()).min_risk

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ToolRegistrationError(f"Tool '{tool.spec.name}' is already registered.")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        return tool

    def specs(
        self, domains: Iterable[str] | None = None, *, principal: Principal | None = None
    ) -> list[ToolSpec]:
        """Registered tools, optionally limited to domains and to what `principal` may use
        (so the model is only shown tools the user is allowed to run)."""
        wanted = set(domains) if domains is not None else None
        return [
            t.spec
            for t in self._tools.values()
            if (wanted is None or t.spec.domain in wanted)
            and (principal is None or principal.has(t.spec.required_permission))
        ]

    def needs_approval(self, spec: ToolSpec) -> bool:
        return approval_required(
            is_write=spec.kind is ToolKind.WRITE,
            requires_approval=spec.requires_approval,
            risk=spec.risk,
            min_risk=self._min_risk,
        )

    # -- execution ----------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments_json: str,
        *,
        allowed_domains: Iterable[str],
        principal: Principal,
    ) -> ToolResult:
        outcome = await self.execute_call(
            name, arguments_json, allowed_domains=allowed_domains, principal=principal
        )
        return outcome.result

    async def execute_call(
        self,
        name: str,
        arguments_json: str,
        *,
        allowed_domains: Iterable[str],
        principal: Principal,
    ) -> ToolCallOutcome:
        """Run a call, or open an approval request for a protected one (`outcome.pending`)."""
        with _tool_span(name, self._tools.get(name)) as tool_span:
            outcome = await self._execute_call(
                name, arguments_json, allowed_domains, principal, tool_span
            )
            _settle_span(tool_span, outcome.result)
            return outcome

    async def _execute_call(
        self,
        name: str,
        arguments_json: str,
        allowed_domains: Iterable[str],
        principal: Principal,
        tool_span: Span,
    ) -> ToolCallOutcome:
        started = time.perf_counter()
        pending: PendingAction | None = None
        denied = False
        try:
            tool = self.get(name)
            if tool.spec.domain not in set(allowed_domains):
                raise ToolNotAllowedError(
                    f"Tool '{name}' (domain {tool.spec.domain}) not visible here."
                )
            if not principal.has(tool.spec.required_permission):
                raise ToolPermissionDeniedError(
                    f"{principal.user_id} lacks {tool.spec.required_permission} for '{name}'."
                )
            protected = self.needs_approval(tool.spec)
            if protected and self._approvals is None:
                raise ToolNotAllowedError(
                    f"Protected tool '{name}' refused: no approval service configured.",
                    public_message="This action needs approval, which is not available yet.",
                )
            tool_input = self._validate(tool, self._parse_arguments(arguments_json))
            if protected:
                pending = await self._request_approval(tool, tool_input, principal)
                result = ToolResult(
                    tool_name=name,
                    status="pending_approval",
                    approval_id=pending.approval_id,
                    output=pending.interrupt_payload(),
                    duration_ms=_elapsed_ms(started),
                )
            else:
                result = await self._invoke(tool, tool_input, principal, started, tool_span)
        except ToolError as exc:
            denied = isinstance(exc, ToolNotAllowedError)
            result = self._failed(name, exc, started)
        except AgentPlatformError as exc:  # e.g. the approval store is down
            result = self._failed(name, exc, started)
        except Exception:
            logger.exception("tool.call.crashed", extra={"fields": {"tool": name}})
            result = _crashed(name, started)
        await self._audit(
            result,
            stable_hash(arguments_json.strip() or "{}"),
            principal=principal,
            denied=denied,
        )
        return ToolCallOutcome(result=result, pending=pending)

    async def execute_approved(
        self,
        pending: PendingAction,
        *,
        principal: Principal,
        resume_value: Any,
        thread_id: str,
        request_id: str,
    ) -> ToolResult:
        """Run a protected call after its approval, re-validating everything first."""
        with _tool_span(pending.tool_name, self._tools.get(pending.tool_name)) as tool_span:
            tool_span.set(approved_execution=True)
            result = await self._execute_approved(
                pending,
                principal=principal,
                resume_value=resume_value,
                thread_id=thread_id,
                request_id=request_id,
                tool_span=tool_span,
            )
            _settle_span(tool_span, result)
            return result

    async def _execute_approved(
        self,
        pending: PendingAction,
        *,
        principal: Principal,
        resume_value: Any,
        thread_id: str,
        request_id: str,
        tool_span: Span,
    ) -> ToolResult:
        started = time.perf_counter()
        denied = False
        approval: ApprovalRecord | None = None
        try:
            if self._approvals is None:
                raise ToolNotAllowedError("No approval service configured.")
            resumed = _approval_id_of(resume_value)
            if resumed is not None and resumed != pending.approval_id:
                raise ApprovalInvalidError("Resumed with a different approval id.")
            tool = self.get(pending.tool_name)
            tool_input = self._validate(tool, self._parse_arguments(pending.arguments_json))
            if operation_hash(tool.spec.name, tool_input) != pending.operation_hash:
                raise ApprovalStaleError("Pending action does not match its own hash.")
            approval = await self._approvals.validate_for_execution(
                principal,
                pending.approval_id,
                OperationBinding(
                    operation=tool.spec.name,
                    operation_hash=pending.operation_hash,
                    thread_id=thread_id,
                    request_id=request_id,
                ),
            )
            # Approval never grants permission: check the principal's permissions now.
            if not principal.has(tool.spec.required_permission):
                raise ToolPermissionDeniedError(
                    f"{principal.user_id} no longer holds {tool.spec.required_permission}."
                )
            result = await self._run_once(
                tool, tool_input, principal, approval, started, tool_span
            )
        except ToolError as exc:
            denied = isinstance(exc, ToolNotAllowedError)
            result = self._failed(pending.tool_name, exc, started)
        except ApprovalError as exc:
            denied = True
            result = self._failed(pending.tool_name, exc, started)
        except AgentPlatformError as exc:
            result = self._failed(pending.tool_name, exc, started)
        except Exception:
            logger.exception(
                "tool.approved_call.crashed", extra={"fields": {"tool": pending.tool_name}}
            )
            result = _crashed(pending.tool_name, started)
        result = result.model_copy(update={"approval_id": pending.approval_id})
        await self._audit(
            result,
            pending.operation_hash,
            principal=principal,
            denied=denied,
            extra={"approval_id": pending.approval_id, "approved_execution": True},
        )
        return result

    async def _run_once(
        self,
        tool: Tool,
        tool_input: ToolInput,
        principal: Principal,
        approval: ApprovalRecord,
        started: float,
        tool_span: Span | None = None,
    ) -> ToolResult:
        """Execute an approved call at most once (idempotency + approval state)."""
        assert self._approvals is not None
        name = tool.spec.name
        if approval.status == "executed" and self._idempotency is None:
            return ToolResult(
                tool_name=name,
                status="error",
                error_code="APPROVAL_ALREADY_USED",
                error_message="This approved action has already been carried out.",
                duration_ms=_elapsed_ms(started),
            )
        claim = None
        if self._idempotency is not None:
            try:
                claim = await self._idempotency.claim(
                    APPROVED_EXECUTION_SCOPE,
                    f"{principal.tenant_id}/{approval.id}",
                    {"operation_hash": approval.operation_hash},
                    request_id=approval.request_id,
                    thread_id=approval.thread_id,
                )
            except IdempotencyError as exc:
                return self._failed(name, exc, started)
            if claim.replayed and claim.response is not None:
                replay = ToolResult.model_validate(claim.response)
                if approval.status == "approved":
                    await self._settle_approval(principal, approval, replay)
                return replay
            if approval.status == "executed":
                # Used, but its idempotency record expired or was purged (Phase 6
                # maintenance): the approval state alone forbids running it again.
                await self._idempotency.fail(claim, error_code="APPROVAL_ALREADY_USED")
                return ToolResult(
                    tool_name=name,
                    status="error",
                    error_code="APPROVAL_ALREADY_USED",
                    error_message="This approved action has already been carried out.",
                    duration_ms=_elapsed_ms(started),
                )
        result = await self._invoke(tool, tool_input, principal, started, tool_span)
        if claim is not None:
            try:
                await self._idempotency.complete(claim, result.model_dump(mode="json"))  # type: ignore[union-attr]
            except Exception:
                logger.error("tool.approved_call.idempotency_failed", exc_info=True)
        await self._settle_approval(principal, approval, result)
        return result

    async def _settle_approval(
        self, principal: Principal, approval: ApprovalRecord, result: ToolResult
    ) -> None:
        assert self._approvals is not None
        try:
            await self._approvals.complete(
                principal, approval, ok=result.status == "ok", error_code=result.error_code
            )
        except Exception:
            # The operation already ran; the idempotency record prevents a second run.
            logger.error("tool.approved_call.settle_failed", exc_info=True)

    async def _request_approval(
        self, tool: Tool, tool_input: ToolInput, principal: Principal
    ) -> PendingAction:
        assert self._approvals is not None
        context = current_log_context()
        thread_id, request_id = context.get("thread_id"), context.get("request_id")
        if not thread_id or not request_id:
            raise ToolNotAllowedError(
                "Protected tools need a conversation workflow (thread and request id)."
            )
        spec = tool.spec
        digest = operation_hash(spec.name, tool_input)
        summary = spec.describe_action(tool_input) if spec.describe_action else spec.description
        record = await self._approvals.request(
            principal,
            operation=spec.name,
            operation_hash=digest,
            summary=summary,
            required_permission=spec.required_permission,
            risk=spec.risk,
            policy=spec.approval,
            thread_id=str(thread_id),
            request_id=str(request_id),
            resource_refs=[{"type": "tool", "id": spec.name}],
        )
        return PendingAction(
            approval_id=str(record.id),
            tool_name=spec.name,
            arguments_json=tool_input.model_dump_json(),
            operation_hash=digest,
            summary=record.summary,
            risk=record.risk,
            expires_at=record.expires_at.isoformat(),
        )

    async def _invoke(
        self,
        tool: Tool,
        tool_input: ToolInput,
        principal: Principal,
        started: float,
        tool_span: Span | None = None,
    ) -> ToolResult:
        """Run the handler as `principal`. Never raises."""
        name = tool.spec.name
        try:
            try:
                with acting_as(principal):
                    async with strict_deadline(tool.spec.timeout_seconds, scope="tool", tool=name):
                        output = await tool.handler(tool_input)
            except TimeoutError as exc:
                if tool_span is not None:
                    tool_span.fail(
                        code=ToolExecutionError.code,
                        category=ErrorCategory.TIMEOUT,
                        status="timeout",
                        retryable=True,
                    )
                raise ToolExecutionError(
                    f"Tool '{name}' timed out after {tool.spec.timeout_seconds}s.",
                    public_message="The tool took too long to respond.",
                    retryable=True,
                ) from exc
            if not isinstance(output, tool.spec.output_model):
                raise ToolExecutionError(
                    f"Tool '{name}' returned {type(output).__name__}, "
                    f"expected {tool.spec.output_model.__name__}."
                )
        except AgentPlatformError as exc:
            return self._failed(name, exc, started)
        except Exception:
            logger.exception("tool.call.crashed", extra={"fields": {"tool": name}})
            return _crashed(name, started)
        result = ToolResult(
            tool_name=name,
            status="ok",
            output=output.model_dump(mode="json"),
            duration_ms=_elapsed_ms(started),
        )
        logger.info(
            "tool.call.completed",
            extra={"fields": {"tool": name, "duration_ms": result.duration_ms}},
        )
        return result

    @staticmethod
    def _validate(tool: Tool, payload: dict[str, object]) -> ToolInput:
        try:
            return tool.spec.input_model.model_validate(payload)
        except ValidationError as exc:
            # Locations and error types only: input values can be personal or business
            # data (Phase 13 redaction policy), so the detail never keeps them.
            problems = [
                f"{'.'.join(str(p) for p in error.get('loc', ())) or 'input'}:{error['type']}"
                for error in exc.errors(include_url=False, include_input=False)
            ]
            raise ToolValidationError(
                f"Invalid input for '{tool.spec.name}': {problems}",
                public_message=_first_validation_message(exc),
            ) from exc

    @staticmethod
    def _failed(name: str, exc: AgentPlatformError, started: float) -> ToolResult:
        logger.warning(
            "tool.call.failed",
            extra={"fields": {"tool": name, "code": exc.code, "detail": exc.detail}},
        )
        return ToolResult(
            tool_name=name,
            status="error",
            error_code=exc.code,
            error_message=exc.public_message,
            duration_ms=_elapsed_ms(started),
        )

    async def _audit(
        self,
        result: ToolResult,
        input_hash: str,
        *,
        principal: Principal,
        denied: bool,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._auditor is None:
            return
        context = current_log_context()
        tool = self._tools.get(result.tool_name)
        event = AuditEvent(
            action="tool.call",
            status="denied" if denied else ("error" if result.status == "error" else "ok"),
            actor_type="agent",  # the agent acting for the authenticated user (ADR-013)
            actor_id=principal.user_id,
            tenant_id=principal.tenant_id,
            tool_name=result.tool_name[:_MAX_AUDIT_NAME],
            error_code=result.error_code,
            input_hash=input_hash,
            entity_refs=[{"type": "approval", "id": result.approval_id}]
            if result.approval_id
            else [],
            request_id=context.get("request_id"),
            thread_id=context.get("thread_id"),
            details={
                "kind": tool.spec.kind.value if tool else None,
                "registered": tool is not None,
                "outcome": result.status,
                "duration_ms": result.duration_ms,
                **(extra or {}),
            },
        )
        try:
            await self._auditor.record(event)
        except Exception:
            logger.error(
                "tool.audit.failed", exc_info=True, extra={"fields": {"tool": result.tool_name}}
            )

    @staticmethod
    def _parse_arguments(arguments_json: str) -> dict[str, object]:
        text = arguments_json.strip() or "{}"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolValidationError(
                f"Tool arguments are not valid JSON: {exc}",
                public_message="The tool arguments were not valid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise ToolValidationError(
                "Tool arguments must be a JSON object.",
                public_message="The tool arguments must be a JSON object.",
            )
        return payload


def _approval_id_of(resume_value: Any) -> str | None:
    """The approval id a resume names, if any (`{"approval_id": ...}` or the bare id)."""
    if isinstance(resume_value, dict):
        value = resume_value.get("approval_id")
        return str(value) if value is not None else None
    if isinstance(resume_value, str) and resume_value.strip():
        return resume_value.strip()
    return None


def _tool_span(name: str, tool: Tool | None) -> Any:
    return span(
        Stage.TOOL,
        started=EventName.TOOL_STARTED,
        completed=EventName.TOOL_COMPLETED,
        failed=EventName.TOOL_FAILED,
        tool=name,
        domain=tool.spec.domain if tool else None,
    )


def _settle_span(tool_span: Span, result: ToolResult) -> None:
    """Record the call's outcome on its span (codes only)."""
    tool_span.set(outcome=result.status)
    if result.status == "error" and tool_span.status == "ok":
        tool_span.fail(code=result.error_code, category=classify_code(result.error_code))


def _crashed(name: str, started: float) -> ToolResult:
    return ToolResult(
        tool_name=name,
        status="error",
        error_code=ToolExecutionError.code,
        error_message=ToolExecutionError.default_public_message,
        duration_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _first_validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(p) for p in first.get("loc", ())) or "input"
    return f"Invalid tool input ({location}): {first.get('msg', 'invalid value')}."
