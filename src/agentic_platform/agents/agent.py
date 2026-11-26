"""Agent entry point: the only API the outside world (CLI now, HTTP later) calls.

Persistence model (ARCHITECTURE.md § A.7):

* Conversation state lives in the checkpointer (PostgreSQL when configured),
  keyed by the thread's storage key. Every step is persisted before the next one
  starts (`durability="sync"`), so a crashed turn can be resumed from its last
  finished step.
* One turn at a time per thread: a distributed lock (Redis when configured)
  guards `arun` / `aresume` (ADR-031).
* A thread with an unfinished turn (crashed or interrupted) refuses new input
  until it is resumed (`aresume`).
* Optional turn idempotency: a repeated `idempotency_key` returns the stored
  response instead of running the turn again (ADR-029, Redis fast-path ADR-030).
* Memory (ADR-033): before a new turn the agent recalls relevant memory for the
  principal into the turn state; after a finished turn it records an episode.
  Memory failures never fail a turn.

Identity (Phase 5, ARCHITECTURE.md § A.9, ADR-037 to ADR-041):

* Every call runs as an authenticated `Principal` (from an `Authenticator`, or the
  local development identity when allowed). Tenant and user come only from it:
  there is no parameter for a tenant, user or memory scope, and the LLM never
  sees or sets identity.
* The principal is bound for the call (`security.acting_as`), so nodes, tools
  and the checkpoint saver (row-level security) all use the same identity.
* Conversations are owned (`ConversationService`): only the owner can continue,
  resume, read or forget one; anything else is "not found". Storage keys are
  `<tenant>/<thread>`, so tenants can never share or reach each other's state.
* Resume re-checks ownership and that the resuming principal is the turn's
  actor. An approval pause resumes only after a human decided, and
  `approval_gate` re-validates the approval before anything runs.
* Security errors (`SecurityError`) propagate to the caller like `AgentInputError`.

Observability (Phase 13, ARCHITECTURE.md § A.17, ADR-062): every call binds its
`request_id`, and every graph execution (a run or a resume) gets a `turn_id`. The turn
emits `request.received`, `turn.started` and exactly one of `turn.completed`,
`turn.failed`, `turn.interrupted` or `turn.rejected`; the checkpointer is instrumented
(`checkpoint.*` events). `diagnose(request_id)` renders the recent events of a request.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentic_platform.agents.checkpointer import create_checkpointer
from agentic_platform.agents.dependencies import AgentDependencies, build_dependencies
from agentic_platform.agents.graph import compile_graph
from agentic_platform.agents.nodes._common import INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE
from agentic_platform.agents.schemas import (
    AgentResponse,
    InterruptInfo,
    PublicError,
    ThreadStatus,
)
from agentic_platform.agents.state import AgentState, initial_turn_state
from agentic_platform.agents.threads import (
    new_thread_id,
    thread_config,
    thread_lock_name,
    validate_thread_id,
)
from agentic_platform.business.focus import analysis_references
from agentic_platform.business.grounding import business_results
from agentic_platform.core.config import AppSettings, get_settings
from agentic_platform.core.errors import (
    AgentInputError,
    AgentPlatformError,
    ApprovalPendingError,
    ConfigurationError,
    InvalidResumeContextError,
    LockNotAcquiredError,
    NothingToResumeError,
    SecurityError,
    ThreadBusyError,
    ThreadPendingError,
    UnauthenticatedError,
)
from agentic_platform.core.logging import configure_logging, get_logger, log_context
from agentic_platform.llm.base import LLMProvider
from agentic_platform.memory.models import Episode
from agentic_platform.observability.checkpoints import instrument_checkpointer
from agentic_platform.observability.diagnostics import TurnDiagnostics
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import HUB, bind_trace, emit, new_id
from agentic_platform.observability.metrics import MetricsRegistry
from agentic_platform.observability.taxonomy import classify_code, classify_exception
from agentic_platform.security.audit import AuditAction
from agentic_platform.security.context import acting_as
from agentic_platform.security.identity import Principal
from agentic_platform.security.permissions import Permission
from agentic_platform.services.conversations import ConversationRef
from agentic_platform.services.idempotency import IdempotencyClaim

logger = get_logger(__name__)

T = TypeVar("T")

_TERMINAL = {"completed", "needs_clarification", "unsupported", "failed"}
# Turn outcomes worth remembering as episodes (not failures or pauses).
_REMEMBERED = {"completed", "needs_clarification", "unsupported"}
TURN_IDEMPOTENCY_SCOPE = "agent.turn"
INTERRUPTED_MESSAGE = "The request is paused and waiting for input."
APPROVAL_MESSAGE = "This action needs approval before it can run."
APPROVAL_INTERRUPT = "approval_required"
TURN_INCOMPLETE_CODE = "TURN_INCOMPLETE"
TURN_INCOMPLETE_MESSAGE = "The request did not finish. Resume the conversation to complete it."


class Agent:
    """Runs user turns through the graph and returns `AgentResponse`s.

    Without a checkpointer every call is independent. With one, calls that share
    a `thread_id` continue the same conversation. `from_settings` picks the
    checkpointer from AGENT_CHECKPOINTER (PostgreSQL when a database is
    configured, ADR-009 / ADR-027).
    """

    def __init__(
        self,
        deps: AgentDependencies,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._deps = deps
        observability = deps.settings.observability
        HUB.configure(
            log_events=observability.log_events,
            trace_buffer_requests=observability.trace_buffer_requests,
        )
        # Phase 13: load/save events on whichever saver is used (the class is unchanged).
        checkpointer = instrument_checkpointer(checkpointer)
        self._graph = compile_graph(deps, checkpointer)
        if deps.memory is not None:
            deps.memory.bind_checkpointer(checkpointer)

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings | None = None,
        *,
        llm: LLMProvider | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        configure_logs: bool = True,
    ) -> Agent:
        """Build from settings. Without an explicit `checkpointer`, AGENT_CHECKPOINTER decides."""
        settings = settings or get_settings()
        if configure_logs:
            configure_logging(settings.logging)
        deps = build_dependencies(settings, llm=llm)
        if checkpointer is None:
            checkpointer = create_checkpointer(settings.agent.checkpointer, deps.database)
        return cls(deps, checkpointer=checkpointer)

    @property
    def graph(self) -> CompiledStateGraph[AgentState, Any, AgentState, AgentState]:
        return self._graph

    @property
    def dependencies(self) -> AgentDependencies:
        return self._deps

    @property
    def metrics(self) -> MetricsRegistry:
        """The process's observability metrics (Phase 13; in-process, no labels with ids)."""
        return HUB.metrics

    def diagnose(self, request_id: str) -> TurnDiagnostics:
        """Safe diagnostics of a recent request (ids, statuses, codes, durations only).

        Operator tooling: reads the process-local trace buffer, not exposed over HTTP."""
        return TurnDiagnostics.from_events(HUB.traces.get(request_id))

    # -- public async API ------------------------------------------------------------

    async def arun(
        self,
        user_input: str,
        *,
        principal: Principal | None = None,
        thread_id: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentResponse:
        """Run one turn as `principal`.

        Raises `AgentInputError` (and subclasses) for caller mistakes, including
        `ThreadPendingError`, and `SecurityError` (and subclasses) for identity,
        permission and ownership failures. Runtime failures come back as
        `status="failed"`. Without `principal`, the local development identity is
        used when allowed (never in production by default), else `UnauthenticatedError`.
        """
        request_id = request_id or uuid.uuid4().hex
        with bind_trace(request_id=request_id):
            emit(
                EventName.REQUEST_RECEIVED,
                stage=Stage.TURN,
                status="started",
                attributes={"operation": "run", "input_chars": len(user_input or "")},
            )
            try:
                principal = self._principal(principal)
                text = self._validate_input(user_input)
                thread_id = (
                    validate_thread_id(thread_id) if thread_id is not None else new_thread_id()
                )
                if idempotency_key is not None and self._deps.idempotency is None:
                    raise ConfigurationError("Turn idempotency keys need DATABASE_URL.")
                public_id = thread_id

                async def body() -> AgentResponse:
                    conversation = await self._deps.security.conversations.open_for_turn(
                        principal, public_id
                    )
                    return await self._locked(
                        conversation,
                        lambda: self._turn(
                            text, conversation, request_id, idempotency_key, principal
                        ),
                    )

                with (
                    acting_as(principal),
                    log_context(request_id=request_id, thread_id=thread_id),
                    bind_trace(thread_id=thread_id),
                ):
                    return await self._guarded(request_id, thread_id, body)
            except (AgentInputError, SecurityError, ConfigurationError) as exc:
                _rejected(exc)
                raise

    async def aresume(
        self,
        thread_id: str,
        *,
        principal: Principal | None = None,
        resume_value: Any = None,
    ) -> AgentResponse:
        """Continue the principal's thread's unfinished turn.

        * Interrupted (the graph called `interrupt()`): `resume_value` becomes the
          value `interrupt()` returns. For an approval pause it may be omitted: the
          decision is read from the approval record, never from the resume value.
          Resuming before anyone decided raises `ApprovalPendingError`.
        * Crashed mid-turn (process died, connection lost): resumes from the last
          finished step; completed steps (and their LLM calls) are not repeated.
        """
        emit(EventName.REQUEST_RECEIVED, stage=Stage.TURN, status="started",
             attributes={"operation": "resume"})  # fmt: skip
        try:
            principal = self._principal(principal)
            thread_id = validate_thread_id(thread_id)
            if self._graph.checkpointer is None:
                raise NothingToResumeError("No checkpointer configured.")
            public_id = thread_id

            async def body() -> AgentResponse:
                conversation = await self._deps.security.conversations.require_access(
                    principal, public_id, Permission.CONVERSATION_WRITE
                )
                return await self._locked(
                    conversation, lambda: self._resume(conversation, resume_value, principal)
                )

            with acting_as(principal), log_context(thread_id=thread_id), bind_trace(
                thread_id=thread_id
            ):
                return await self._guarded("resume", thread_id, body)
        except (AgentInputError, SecurityError) as exc:
            _rejected(exc)
            raise

    async def athread_status(
        self, thread_id: str, *, principal: Principal | None = None
    ) -> ThreadStatus:
        """What the checkpointer knows about one of the principal's threads.

        Another user's or tenant's thread reports `exists=False`, exactly like a
        missing one. Persistence errors propagate."""
        principal = self._principal(principal)
        thread_id = validate_thread_id(thread_id)
        if self._graph.checkpointer is None:
            return ThreadStatus(thread_id=thread_id, exists=False)
        with acting_as(principal), log_context(thread_id=thread_id):
            conversation = await self._deps.security.conversations.find(principal, thread_id)
            if conversation is None:
                return ThreadStatus(thread_id=thread_id, exists=False)
            state = await self._graph.aget_state(self._config(conversation.storage_key))
        values: dict[str, Any] = state.values or {}
        return ThreadStatus(
            thread_id=thread_id,
            exists=state.created_at is not None,
            pending=bool(state.next),
            interrupted=bool(state.interrupts),
            next_nodes=list(state.next),
            interrupts=[_interrupt_info(i) for i in state.interrupts],
            message_count=len(values.get("messages", [])),
            last_request_id=values.get("request_id"),
            updated_at=state.created_at,
        )

    async def aforget_conversation(
        self, thread_id: str, *, principal: Principal | None = None
    ) -> dict[str, int]:
        """Delete one of the principal's conversations and what was derived from it:
        checkpoints, summary, working memory and episodes; open approval requests of
        the conversation are cancelled. Needs `conversation.delete` and ownership."""
        principal = self._principal(principal)
        thread_id = validate_thread_id(thread_id)
        security = self._deps.security
        with acting_as(principal), log_context(thread_id=thread_id):
            conversation = await security.conversations.require_access(
                principal, thread_id, Permission.CONVERSATION_DELETE
            )
            settings = self._deps.settings.agent
            try:
                async with self._deps.locks.hold(
                    thread_lock_name(conversation.storage_key),
                    ttl_seconds=settings.thread_lock_ttl_seconds,
                    wait_seconds=settings.thread_lock_wait_seconds,
                ):
                    counts = await self._forget(conversation, principal)
            except LockNotAcquiredError as exc:
                raise ThreadBusyError() from exc
            await security.audit.record_required(
                security.audit.event(
                    AuditAction.CONVERSATION_FORGET,
                    "ok",
                    principal,
                    entity_refs=[{"type": "conversation", "id": thread_id}],
                    details=dict(counts),
                )
            )
            return counts

    async def aclose(self) -> None:
        """Release external resources (Redis, database and, Phase 15, ERP read-only
        connections). The agent stays usable: pools reconnect on the next call."""
        for _, close in self._deps.closers():
            await close()

    # -- sync wrappers (scripts, CLI) ------------------------------------------------

    def run(
        self,
        user_input: str,
        *,
        principal: Principal | None = None,
        thread_id: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentResponse:
        """Synchronous wrapper for scripts. Do not call inside an event loop.

        Each call runs in a fresh event loop, so pooled database and Redis
        connections are released at the end of the call (they cannot be reused
        across loops). Long-running callers should use `arun` in one loop and
        `aclose` on shutdown.
        """
        return self._sync(
            lambda: self.arun(
                user_input,
                principal=principal,
                thread_id=thread_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        )

    def resume(
        self, thread_id: str, *, principal: Principal | None = None, resume_value: Any = None
    ) -> AgentResponse:
        return self._sync(
            lambda: self.aresume(thread_id, principal=principal, resume_value=resume_value)
        )

    def thread_status(self, thread_id: str, *, principal: Principal | None = None) -> ThreadStatus:
        return self._sync(lambda: self.athread_status(thread_id, principal=principal))

    def forget_conversation(
        self, thread_id: str, *, principal: Principal | None = None
    ) -> dict[str, int]:
        return self._sync(lambda: self.aforget_conversation(thread_id, principal=principal))

    def _sync(self, call: Callable[[], Coroutine[Any, Any, T]]) -> T:
        async def once() -> T:
            try:
                return await call()
            finally:
                await self.aclose()

        return asyncio.run(once())

    # -- turn orchestration -----------------------------------------------------------

    async def _guarded(
        self, request_id: str, thread_id: str, body: Callable[[], Awaitable[AgentResponse]]
    ) -> AgentResponse:
        """Platform errors become failed responses. Caller mistakes (`AgentInputError`)
        and security refusals (`SecurityError`) propagate."""
        try:
            return await body()
        except LockNotAcquiredError:
            logger.warning("agent.thread.busy")
            busy = ThreadBusyError()
            _turn_failed_outside_graph(busy)
            return self._error_response(request_id, thread_id, busy)
        except (AgentInputError, SecurityError):
            raise
        except AgentPlatformError as exc:  # lock backend, idempotency or a store down
            logger.error(
                "agent.turn.persistence_failed",
                extra={"fields": {"code": exc.code, "detail": exc.detail}},
            )
            _turn_failed_outside_graph(exc)
            return self._error_response(request_id, thread_id, exc)

    async def _locked(
        self, conversation: ConversationRef, body: Callable[[], Awaitable[AgentResponse]]
    ) -> AgentResponse:
        """Run `body` holding the thread lock (one turn at a time per thread, ADR-031)."""
        settings = self._deps.settings.agent
        async with self._deps.locks.hold(
            thread_lock_name(conversation.storage_key),
            ttl_seconds=settings.thread_lock_ttl_seconds,
            wait_seconds=settings.thread_lock_wait_seconds,
        ):
            return await body()

    async def _turn(
        self,
        text: str,
        conversation: ConversationRef,
        request_id: str,
        idempotency_key: str | None,
        principal: Principal,
    ) -> AgentResponse:
        thread_id, storage_key = conversation.thread_id, conversation.storage_key
        claim: IdempotencyClaim | None = None
        idempotency = self._deps.idempotency
        if idempotency_key is not None and idempotency is not None:
            claim = await idempotency.claim(
                TURN_IDEMPOTENCY_SCOPE,
                f"{storage_key}:{idempotency_key}",  # tenant-namespaced
                {"thread_id": thread_id, "input": text},
                request_id=request_id,
                thread_id=thread_id,
            )
            if claim.replayed:
                logger.info("agent.turn.replayed")
                replay = AgentResponse.model_validate(claim.response)
                return replay.model_copy(update={"replayed": True})

        try:
            await self._ensure_no_pending_turn(storage_key)
            memory_context = await self._recall(principal, thread_id, text)
            response, final = await self._invoke(
                initial_turn_state(
                    request_id=request_id,
                    thread_id=thread_id,
                    user_input=text,
                    memory_context=memory_context,
                    actor=principal.actor,
                ),
                conversation,
                request_id,
            )
        except BaseException as exc:
            if claim is not None and idempotency is not None:
                code = exc.code if isinstance(exc, AgentPlatformError) else INTERNAL_ERROR_CODE
                await _settle(idempotency.fail(claim, error_code=code))
            raise

        if claim is not None and idempotency is not None:
            if response.status == "failed":
                code = response.errors[0].code if response.errors else INTERNAL_ERROR_CODE
                await _settle(idempotency.fail(claim, error_code=code))
            else:
                await _settle(idempotency.complete(claim, response.model_dump(mode="json")))
        await self._remember_turn(principal, response, final, text)
        return response

    async def _resume(
        self, conversation: ConversationRef, resume_value: Any, principal: Principal
    ) -> AgentResponse:
        state = await self._graph.aget_state(self._config(conversation.storage_key))
        if not state.next:
            raise NothingToResumeError(f"Thread has no unfinished turn (next={state.next}).")
        if state.values.get("actor") != principal.actor:
            await self._deps.security.authorizer.deny(
                principal,
                None,
                reason="resume_actor_mismatch",
                resource=f"conversation:{conversation.thread_id}",
            )
            raise InvalidResumeContextError("Resuming principal is not the turn's actor.")
        graph_input: Command[Any] | None
        if state.interrupts:
            approval_id = _approval_interrupt_id(state.interrupts)
            if approval_id is not None:
                await self._ensure_approval_decided(principal, approval_id)
                if resume_value is None:
                    resume_value = {"approval_id": approval_id}
            elif resume_value is None:
                raise AgentInputError(
                    "Interrupted thread resumed without a value.",
                    public_message="This conversation is waiting for an answer. "
                    "Provide it to continue.",
                )
            graph_input = Command(resume=resume_value)
        else:
            if resume_value is not None:
                raise AgentInputError(
                    "Resume value given, but the turn is not waiting for input.",
                    public_message="This conversation is not waiting for an answer.",
                )
            graph_input = None  # continue a crashed turn from its last finished step
        request_id = str(state.values.get("request_id") or uuid.uuid4().hex)
        with log_context(request_id=request_id), bind_trace(request_id=request_id):
            logger.info(
                "agent.turn.resuming",
                extra={"fields": {"next": list(state.next), "interrupted": bool(state.interrupts)}},
            )
            response, final = await self._invoke(graph_input, conversation, request_id)
        await self._remember_turn(
            principal, response, final, str(state.values.get("user_input", ""))
        )
        return response

    async def _ensure_approval_decided(self, principal: Principal, approval_id: str) -> None:
        """Resuming an approval pause before a human decided would only pause again."""
        record = await self._deps.security.approvals.get(principal, approval_id)
        if record.status == "pending" and not record.expired:
            raise ApprovalPendingError("Approval is still pending.")

    async def _forget(self, conversation: ConversationRef, principal: Principal) -> dict[str, int]:
        counts: dict[str, int] = {}
        if self._deps.memory is not None:
            counts.update(
                await self._deps.memory.forget_thread(
                    principal.memory_scope,
                    conversation.thread_id,
                    checkpoint_key=conversation.storage_key,
                )
            )
        elif isinstance(self._graph.checkpointer, BaseCheckpointSaver):
            await self._graph.checkpointer.adelete_thread(conversation.storage_key)
        counts["approvals_cancelled"] = await self._deps.security.approvals.cancel_for_thread(
            principal, conversation.thread_id
        )
        await self._deps.security.conversations.remove(conversation)
        return counts

    async def _ensure_no_pending_turn(self, storage_key: str) -> None:
        if await self._is_pending(storage_key):
            raise ThreadPendingError("Thread has an unfinished turn.")

    async def _invoke(
        self, graph_input: Any, conversation: ConversationRef, request_id: str
    ) -> tuple[AgentResponse, dict[str, Any] | None]:
        """Run the graph. Returns the response and the final state (None on failure)."""
        with bind_trace(turn_id=new_id()):
            emit(
                EventName.TURN_STARTED,
                stage=Stage.TURN,
                status="started",
                attributes={"resume": not isinstance(graph_input, dict)},
            )
            response, final = await self._invoke_graph(graph_input, conversation, request_id)
            _turn_finished(response)
            return response, final

    async def _invoke_graph(
        self, graph_input: Any, conversation: ConversationRef, request_id: str
    ) -> tuple[AgentResponse, dict[str, Any] | None]:
        started = time.perf_counter()
        thread_id, storage_key = conversation.thread_id, conversation.storage_key
        logger.info("agent.turn.started")
        final: dict[str, Any] | None = None
        try:
            # "sync": every step is persisted before the next starts, so a crash
            # leaves a resumable checkpoint (only meaningful with a checkpointer).
            options: dict[str, Any] = (
                {"durability": "sync"} if self._graph.checkpointer is not None else {}
            )
            final = await self._graph.ainvoke(graph_input, self._config(storage_key), **options)
            response = self._to_response(final, request_id, thread_id)
        except GraphRecursionError:
            logger.error("agent.turn.recursion_limit")
            response = self._failure(request_id, thread_id, "RECURSION_LIMIT")
        except AgentPlatformError as exc:  # e.g. the checkpoint store failed mid-turn
            logger.error(
                "agent.turn.persistence_failed",
                extra={"fields": {"code": exc.code, "detail": exc.detail}},
            )
            response = self._error_response(request_id, thread_id, exc)
        except Exception:
            logger.exception("agent.turn.crashed")
            response = await self._crash_response(request_id, thread_id, storage_key)
        response.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "agent.turn.finished",
            extra={
                "fields": {
                    "status": response.status,
                    "steps": response.steps,
                    "duration_ms": response.duration_ms,
                }
            },
        )
        return response, final

    # -- identity and memory ------------------------------------------------------------

    def _principal(self, principal: Principal | None) -> Principal:
        if principal is not None:
            return principal
        local = self._deps.security.local_identity
        if not local.allowed:
            raise UnauthenticatedError("No principal given and local identity is disabled.")
        return local.principal()

    async def _recall(
        self, principal: Principal, thread_id: str, text: str
    ) -> dict[str, Any] | None:
        memory = self._deps.authorized_memory()
        if memory is None:
            return None
        context = await memory.recall(principal, thread_id=thread_id, query=text)
        logger.info(
            "agent.memory.recalled",
            extra={
                "fields": {
                    "facts": len(context.facts),
                    "knowledge": len(context.knowledge),
                    "episodes": len(context.episodes),
                    "errors": context.errors,
                }
            },
        )
        return context.to_prompt(self._deps.settings.memory.context_max_chars)

    async def _remember_turn(
        self,
        principal: Principal,
        response: AgentResponse,
        final: dict[str, Any] | None,
        user_input: str,
    ) -> None:
        memory = self._deps.authorized_memory()
        if memory is None or final is None or response.status not in _REMEMBERED:
            return
        understanding = final.get("understanding")
        await memory.record_turn(
            principal,
            Episode(
                thread_id=response.thread_id,
                request_id=response.request_id,
                intent=response.intent,
                domain=response.domain,
                outcome=response.status,
                summary=understanding.summary if understanding else user_input,
                tools=[r.tool_name for r in response.tool_results],
                analyses=analysis_references(business_results(response.tool_results)),
            ),
        )

    async def _crash_response(
        self, request_id: str, thread_id: str, storage_key: str
    ) -> AgentResponse:
        """A crashed turn whose progress is checkpointed can be resumed: say so."""
        try:
            pending = await self._is_pending(storage_key)
        except Exception:
            logger.warning("agent.turn.pending_check_failed", exc_info=True)
            pending = False
        if not pending:
            return self._failure(request_id, thread_id, INTERNAL_ERROR_CODE)
        return AgentResponse(
            request_id=request_id,
            thread_id=thread_id,
            status="failed",
            message=TURN_INCOMPLETE_MESSAGE,
            errors=[
                PublicError(
                    code=TURN_INCOMPLETE_CODE, message=TURN_INCOMPLETE_MESSAGE, retryable=True
                )
            ],
        )

    async def _is_pending(self, storage_key: str) -> bool:
        if self._graph.checkpointer is None:
            return False
        return bool((await self._graph.aget_state(self._config(storage_key))).next)

    # -- helpers ------------------------------------------------------------------

    def _config(self, storage_key: str) -> Any:
        return thread_config(storage_key, self._deps.settings.agent.recursion_limit)

    def _validate_input(self, user_input: str) -> str:
        text = (user_input or "").strip()
        if not text:
            raise AgentInputError("Empty input.", public_message="Please enter a message.")
        limit = self._deps.settings.agent.max_input_chars
        if len(text) > limit:
            raise AgentInputError(
                f"Input of {len(text)} chars exceeds {limit}.",
                public_message=f"Messages are limited to {limit} characters.",
            )
        return text

    @staticmethod
    def _to_response(final: dict[str, Any], request_id: str, thread_id: str) -> AgentResponse:
        understanding = final.get("understanding")
        common: dict[str, Any] = {
            "request_id": request_id,
            "thread_id": thread_id,
            "intent": understanding.intent if understanding else None,
            "domain": understanding.domain if understanding else None,
            "tool_results": final.get("tool_results", []),
            "steps": final.get("step_count", 0),
        }
        interrupts = final.get("__interrupt__") or []
        if interrupts:
            approval = _approval_interrupt_id(interrupts) is not None
            return AgentResponse(
                status="interrupted",
                message=APPROVAL_MESSAGE if approval else INTERRUPTED_MESSAGE,
                interrupts=[_interrupt_info(i) for i in interrupts],
                **common,
            )

        status = final.get("status")
        message = final.get("final_response")
        errors = [
            PublicError(code=e.code, message=e.message, retryable=e.retryable)
            for e in final.get("errors", [])
        ]
        if status not in _TERMINAL or not message:
            status, message = "failed", INTERNAL_ERROR_MESSAGE
            errors = errors or [PublicError(code=INTERNAL_ERROR_CODE, message=message)]
        return AgentResponse(status=status, message=message, errors=errors, **common)

    @staticmethod
    def _failure(request_id: str, thread_id: str, code: str) -> AgentResponse:
        return AgentResponse(
            request_id=request_id,
            thread_id=thread_id,
            status="failed",
            message=INTERNAL_ERROR_MESSAGE,
            errors=[PublicError(code=code, message=INTERNAL_ERROR_MESSAGE)],
        )

    @staticmethod
    def _error_response(request_id: str, thread_id: str, exc: AgentPlatformError) -> AgentResponse:
        return AgentResponse(
            request_id=request_id,
            thread_id=thread_id,
            status="failed",
            message=exc.public_message,
            errors=[
                PublicError(code=exc.code, message=exc.public_message, retryable=exc.retryable)
            ],
        )


def _interrupt_info(interrupt: Any) -> InterruptInfo:
    value = getattr(interrupt, "value", None)
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        value = str(value)
    return InterruptInfo(id=str(getattr(interrupt, "id", "")), value=value)


def _approval_interrupt_id(interrupts: Any) -> str | None:
    """The approval id when the turn is paused at approval_gate."""
    for item in interrupts:
        value = getattr(item, "value", None)
        if isinstance(value, dict) and value.get("type") == APPROVAL_INTERRUPT:
            approval_id = value.get("approval_id")
            return str(approval_id) if approval_id else None
    return None


def _rejected(exc: AgentPlatformError) -> None:
    """A call refused before or instead of running a turn (input, identity, ownership)."""
    emit(
        EventName.TURN_REJECTED,
        stage=Stage.TURN,
        status="rejected",
        error_category=classify_exception(exc),
        error_code=exc.code,
        retryable=exc.retryable,
    )


def _turn_failed_outside_graph(exc: AgentPlatformError) -> None:
    """The turn could not start or finish around the graph (thread busy, a store down)."""
    emit(
        EventName.TURN_FAILED,
        stage=Stage.TURN,
        status="error",
        error_category=classify_exception(exc),
        error_code=exc.code,
        retryable=exc.retryable,
        attributes={"outcome": "failed", "graph_ran": False},
    )


def _turn_finished(response: AgentResponse) -> None:
    error = response.errors[0] if response.errors else None
    if response.status == "interrupted":
        name, status = EventName.TURN_INTERRUPTED, "interrupted"
    elif response.status == "failed":
        name, status = EventName.TURN_FAILED, "error"
    else:
        name, status = EventName.TURN_COMPLETED, "ok"
    emit(
        name,
        stage=Stage.TURN,
        status=status,
        duration_ms=float(response.duration_ms),
        domain=response.domain,
        error_category=classify_code(error.code) if error is not None else None,
        error_code=error.code if error is not None else None,
        retryable=error.retryable if error is not None else None,
        attributes={
            "outcome": response.status,
            "steps": response.steps,
            "tool_calls": len(response.tool_results),
            "graph_ran": True,
        },
    )


async def _settle(operation: Awaitable[None]) -> None:
    """Record the idempotency outcome. The turn already happened, so a failure to
    record it is logged, not raised; the claim's lease expiry makes it retryable."""
    try:
        await operation
    except Exception:
        logger.error("agent.turn.idempotency_record_failed", exc_info=True)


__all__ = ["Agent"]
