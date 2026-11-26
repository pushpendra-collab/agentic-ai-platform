"""Event emission, trace correlation and sinks (Phase 13, ADR-062).

Correlation extends the existing `log_context` model: a `TraceContext` lives in a
contextvar, so every event emitted inside a turn (graph nodes, tool calls, LLM calls,
business reads, checkpoint operations) carries the same `request_id`, `turn_id` and
`thread_id` without passing them around. LangGraph runs nodes as tasks of the same event
loop, and tasks copy the context, exactly as `log_context` already relies on.

    request (request_id: API / agent)
      └── turn (turn_id: one graph execution, a run or a resume)
           └── span (span_id: node / domain run / tool / LLM call / checkpoint op)
                └── span ...

`emit()` never raises and never blocks on I/O: sinks are in-process (a JSON log line,
metric counters, a bounded per-request trace buffer). A failing sink is skipped.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agentic_platform.core.logging import get_logger
from agentic_platform.observability.events import (
    EventName,
    EventStatus,
    ObsEvent,
    Stage,
    is_stage_end,
)
from agentic_platform.observability.metrics import MetricsRegistry
from agentic_platform.observability.taxonomy import ErrorCategory, classify_exception

_logger = get_logger("observability")
_internal = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceContext:
    request_id: str | None = None
    turn_id: str | None = None
    thread_id: str | None = None
    span_id: str | None = None
    stage: Stage | None = None


_trace: ContextVar[TraceContext] = ContextVar("obs_trace", default=TraceContext())  # noqa: B039


def current_trace() -> TraceContext:
    return _trace.get()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class EventSink(Protocol):
    def handle(self, event: ObsEvent) -> None: ...


# -- sinks ------------------------------------------------------------------------------

_WARNING_STATUSES = {"error", "timeout", "rejected"}
_DEBUG_EVENTS = {EventName.CHECKPOINT_LOADED, EventName.CHECKPOINT_SAVED}


class LoggingSink:
    """One structured log line per event (logger `agentic_platform.observability`).

    Failures, timeouts and rejections at WARNING; routine checkpoint loads/saves at DEBUG;
    everything else at INFO. Fields are the event's own (already allow-listed)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or _logger

    def handle(self, event: ObsEvent) -> None:
        if event.status in _WARNING_STATUSES:
            level = logging.WARNING
        elif event.name in _DEBUG_EVENTS:
            level = logging.DEBUG
        else:
            level = logging.INFO
        if self._logger.isEnabledFor(level):
            self._logger.log(level, event.name.value, extra={"fields": event.log_fields()})


class MetricsSink:
    """Turns events into counters and latency histograms (labels from closed sets)."""

    def __init__(self, registry: MetricsRegistry) -> None:
        self.registry = registry

    def handle(self, event: ObsEvent) -> None:
        r, n, a = self.registry, event.name, event.attributes
        duration = event.duration_ms
        category = event.error_category or "none"
        if n in (EventName.TURN_COMPLETED, EventName.TURN_FAILED, EventName.TURN_INTERRUPTED):
            outcome = a.get("outcome") or event.status or "unknown"
            r.inc("agent_turns_total", outcome=outcome)
            if duration is not None:
                r.observe("agent_turn_duration_ms", duration, outcome=outcome)
        elif n is EventName.TURN_REJECTED:
            r.inc("agent_turns_total", outcome="rejected")
        elif event.stage is not None and is_stage_end(n, event.stage):
            status = "error" if n.value.endswith(".failed") else "ok"
            if duration is not None:
                r.observe("agent_stage_duration_ms", duration, stage=event.stage, status=status)
            if status == "error":
                r.inc("agent_stage_failures_total", stage=event.stage, category=category)
        if n is EventName.SUPERVISOR_DECIDED:
            r.inc("agent_routing_decisions_total", outcome=a.get("outcome"),
                  domain=event.domain or "none")  # fmt: skip
        elif n in (EventName.DOMAIN_RUN_COMPLETED, EventName.DOMAIN_RUN_FAILED):
            outcome = a.get("outcome") or ("FAILED" if event.status != "ok" else "SUCCESS")
            r.inc("agent_domain_runs_total", domain=event.domain, outcome=outcome)
            if duration is not None:
                r.observe("agent_domain_duration_ms", duration, domain=event.domain)
        elif n in (EventName.TOOL_COMPLETED, EventName.TOOL_FAILED):
            r.inc("agent_tool_calls_total", tool=event.tool, status=event.status,
                  category=category)  # fmt: skip
            if duration is not None:
                r.observe("agent_tool_duration_ms", duration, tool=event.tool)
        elif n in (EventName.LLM_CALL_COMPLETED, EventName.LLM_CALL_FAILED):
            stage = a.get("llm_stage") or "unknown"
            model = a.get("model") or "unknown"
            r.inc("agent_llm_calls_total", stage=stage, model=model, status=event.status,
                  category=category)  # fmt: skip
            if duration is not None:
                r.observe("agent_llm_latency_ms", duration, stage=stage, model=model)
            for direction in ("input", "output"):
                tokens = a.get(f"{direction}_tokens")
                if isinstance(tokens, int) and tokens > 0:
                    r.inc("agent_llm_tokens_total", tokens, model=model, direction=direction)
        elif n is EventName.RETRY_ATTEMPTED:
            r.inc("agent_retries_total", operation=a.get("operation"), category=category)
        elif n is EventName.RETRY_EXHAUSTED:
            r.inc("agent_retry_exhausted_total", operation=a.get("operation"), category=category)
        elif n is EventName.TIMEOUT:
            r.inc("agent_timeouts_total", scope=a.get("scope"))
        elif n in (EventName.CHECKPOINT_LOADED, EventName.CHECKPOINT_SAVED):
            if duration is not None:
                r.observe("agent_checkpoint_duration_ms", duration, operation=a.get("operation"))
        elif n in (
            EventName.CHECKPOINT_LOAD_FAILED,
            EventName.CHECKPOINT_SAVE_FAILED,
            EventName.CHECKPOINT_REJECTED,
        ):
            r.inc("agent_checkpoint_failures_total", operation=a.get("operation"),
                  category=category)  # fmt: skip
        elif n is EventName.BUSINESS_READ:
            r.inc("agent_business_reads_total", capability=a.get("capability"),
                  status=a.get("result_status"))  # fmt: skip
        elif n is EventName.VERIFICATION_RESULT:
            r.inc("agent_grounding_total", result="replaced" if a.get("replaced") else "grounded")
        elif n in (EventName.AUTH_FAILED, EventName.AUTHZ_DENIED):
            kind = "authentication" if n is EventName.AUTH_FAILED else "authorization"
            r.inc("agent_security_refusals_total", kind=kind, code=event.error_code or "none")
        if (
            n in (EventName.TOOL_FAILED, EventName.BUSINESS_READ)
            and event.error_category == ErrorCategory.AUTHORIZATION
        ):
            r.inc("agent_security_refusals_total", kind="authorization",
                  code=event.error_code or "none")  # fmt: skip


class TraceBuffer:
    """The events of the most recent requests, for diagnostics (bounded, in-process)."""

    def __init__(self, max_requests: int = 200, max_events: int = 500) -> None:
        self._lock = threading.Lock()
        self._max_requests = max_requests
        self._max_events = max_events
        self._traces: OrderedDict[str, list[ObsEvent]] = OrderedDict()

    def handle(self, event: ObsEvent) -> None:
        if not event.request_id or self._max_requests <= 0:
            return
        with self._lock:
            events = self._traces.get(event.request_id)
            if events is None:
                events = self._traces[event.request_id] = []
                while len(self._traces) > self._max_requests:
                    self._traces.popitem(last=False)
            else:
                self._traces.move_to_end(event.request_id)
            if len(events) < self._max_events:
                events.append(event)

    def get(self, request_id: str) -> list[ObsEvent]:
        with self._lock:
            return list(self._traces.get(request_id, ()))

    def request_ids(self) -> list[str]:
        with self._lock:
            return list(self._traces)

    def resize(self, max_requests: int) -> None:
        with self._lock:
            self._max_requests = max_requests
            while len(self._traces) > max(max_requests, 0):
                self._traces.popitem(last=False)


@dataclass
class CapturingSink:
    """Collects events (tests, evaluation, benchmark)."""

    events: list[ObsEvent] = field(default_factory=list)

    def handle(self, event: ObsEvent) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name.value for e in self.events]

    def of(self, *names: EventName) -> list[ObsEvent]:
        wanted = set(names)
        return [e for e in self.events if e.name in wanted]


# -- the hub ------------------------------------------------------------------------------


class Exporter(EventSink, Protocol):
    """Phase 15 (ADR-064): the export boundary for a metrics/tracing backend.

    None ships with the platform: which backend (Prometheus, OpenTelemetry, a vendor
    agent) depends on the deployment target (PROJECT_STATUS Q3). An exporter receives
    every event like the built-in sinks (`handle` must not block on I/O: buffer and send
    from its own thread) and is flushed at shutdown within
    OBSERVABILITY_FLUSH_TIMEOUT_SECONDS. A failing exporter is skipped, like any sink: an
    observability outage never fails a request or a turn. Logs need no exporter: they are
    JSON lines on stderr, which every container platform collects."""

    def flush(self, timeout_seconds: float) -> None: ...


class Observability:
    """The process's sinks. `enabled=False` keeps metrics and traces but drops log lines."""

    def __init__(self) -> None:
        self.metrics = MetricsRegistry()
        self.traces = TraceBuffer()
        self._logging = LoggingSink()
        self.log_events = True
        self._exporters: list[Exporter] = []

    def sinks(self) -> Sequence[EventSink]:
        base: list[EventSink] = [MetricsSink(self.metrics), self.traces]
        if self.log_events:
            base.append(self._logging)
        return [*base, *self._exporters]

    def configure(self, *, log_events: bool, trace_buffer_requests: int) -> None:
        self.log_events = log_events
        self.traces.resize(trace_buffer_requests)

    def add_exporter(self, exporter: Exporter) -> None:
        if exporter not in self._exporters:
            self._exporters.append(exporter)

    def remove_exporter(self, exporter: Exporter) -> None:
        if exporter in self._exporters:
            self._exporters.remove(exporter)

    def flush(self, timeout_seconds: float) -> None:
        """Flush exporters, then the log handlers. Never raises (shutdown must go on)."""
        for exporter in list(self._exporters):
            try:
                exporter.flush(timeout_seconds)
            except Exception:
                _internal.warning("observability.exporter.flush_failed", exc_info=True)
        for handler in logging.getLogger("agentic_platform").handlers:
            try:
                handler.flush()
            except Exception:  # a closed stream at interpreter exit
                _internal.debug("observability.log_flush_failed", exc_info=True)


HUB = Observability()
_extra_sinks: ContextVar[tuple[EventSink, ...]] = ContextVar("obs_extra_sinks", default=())


@contextmanager
def capture_events() -> Iterator[CapturingSink]:
    """Also collect the events emitted in this context (and tasks started from it)."""
    capturing = CapturingSink()
    token = _extra_sinks.set((*_extra_sinks.get(), capturing))
    try:
        yield capturing
    finally:
        _extra_sinks.reset(token)


def emit(name: EventName, **fields: Any) -> ObsEvent | None:
    """Build an event in the current trace context and deliver it. Never raises."""
    try:
        ctx = _trace.get()
        values: dict[str, Any] = {
            "request_id": ctx.request_id,
            "turn_id": ctx.turn_id,
            "thread_id": ctx.thread_id,
            "parent_span_id": ctx.span_id,
        }
        values.update(fields)
        event = ObsEvent(name=name, **values)
    except Exception:
        _internal.debug("observability.event_invalid", exc_info=True)
        return None
    for sink in (*HUB.sinks(), *_extra_sinks.get()):
        try:
            sink.handle(event)
        except Exception:
            _internal.debug("observability.sink_failed", exc_info=True)
    return event


@contextmanager
def bind_trace(
    *, request_id: str | None = None, thread_id: str | None = None, turn_id: str | None = None
) -> Iterator[TraceContext]:
    """Bind correlation ids for the block (fields not given keep their current value)."""
    current = _trace.get()
    updated = replace(
        current,
        request_id=request_id if request_id is not None else current.request_id,
        thread_id=thread_id if thread_id is not None else current.thread_id,
        turn_id=turn_id if turn_id is not None else current.turn_id,
    )
    token = _trace.set(updated)
    try:
        yield updated
    finally:
        _trace.reset(token)


class Span:
    """A timed operation. The block may record attributes or an explicit failure."""

    def __init__(self, stage: Stage, span_id: str) -> None:
        self.stage = stage
        self.span_id = span_id
        self.started = time.perf_counter()
        self.attributes: dict[str, Any] = {}
        self.fields: dict[str, Any] = {}
        self.status: EventStatus = "ok"
        self.error_category: str | None = None
        self.error_code: str | None = None
        self.retryable: bool | None = None

    def set(self, **attributes: Any) -> None:
        self.attributes.update(attributes)

    def fail(
        self,
        exc: BaseException | None = None,
        *,
        code: str | None = None,
        category: ErrorCategory | str | None = None,
        status: EventStatus = "error",
        retryable: bool | None = None,
    ) -> None:
        self.status = status
        if exc is not None:
            self.error_category = str(category or classify_exception(exc))
            self.error_code = code or getattr(exc, "code", None) or type(exc).__name__
            self.retryable = retryable if retryable is not None else getattr(exc, "retryable", None)
        else:
            self.error_category = str(category) if category else None
            self.error_code = code
            self.retryable = retryable

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 3)


@contextmanager
def span(
    stage: Stage,
    *,
    started: EventName | None,
    completed: EventName,
    failed: EventName,
    **fields: Any,
) -> Iterator[Span]:
    """Emit `started`, then `completed` or `failed` with the duration.

    An exception escaping the block is classified, recorded and re-raised. Events emitted
    inside the block have this span as their parent."""
    current = _trace.get()
    item = Span(stage, new_id())
    item.fields.update(fields)
    if started is not None:
        emit(started, stage=stage, span_id=item.span_id, status="started", **fields)
    token = _trace.set(replace(current, span_id=item.span_id, stage=stage))
    try:
        yield item
    except BaseException as exc:
        if item.status == "ok":
            item.fail(exc)
        raise
    finally:
        _trace.reset(token)
        failed_now = item.status in ("error", "timeout", "rejected")
        emit(
            failed if failed_now else completed,
            stage=stage,
            span_id=item.span_id,
            status=item.status,
            duration_ms=item.elapsed_ms,
            error_category=item.error_category,
            error_code=item.error_code,
            retryable=item.retryable,
            attributes=item.attributes,
            **item.fields,
        )


__all__ = [
    "HUB",
    "CapturingSink",
    "EventSink",
    "LoggingSink",
    "MetricsSink",
    "Observability",
    "Span",
    "TraceBuffer",
    "TraceContext",
    "bind_trace",
    "capture_events",
    "current_trace",
    "emit",
    "new_id",
    "span",
]
