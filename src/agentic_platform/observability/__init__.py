"""Observability, reliability and diagnostics (Phase 13, ADR-062).

* `events`: typed observability events (distinct from security audit events).
* `hub`: trace correlation (request → turn → span), `emit`, sinks (logs, metrics, traces).
* `taxonomy`: the operational error categories.
* `retry`: the retry policy; `deadline`: strict deadlines.
* `metrics`: bounded in-process counters and histograms.
* `llm`, `checkpoints`: instrumentation of LLM calls and checkpoint operations.
* `diagnostics`: a safe per-request view (from the trace buffer or JSON logs).
"""

from agentic_platform.observability.events import EventName, ObsEvent, Stage
from agentic_platform.observability.hub import (
    HUB,
    CapturingSink,
    bind_trace,
    capture_events,
    current_trace,
    emit,
    span,
)
from agentic_platform.observability.taxonomy import (
    ErrorCategory,
    classify_business,
    classify_code,
    classify_exception,
)

__all__ = [
    "HUB",
    "CapturingSink",
    "ErrorCategory",
    "EventName",
    "ObsEvent",
    "Stage",
    "bind_trace",
    "capture_events",
    "classify_business",
    "classify_code",
    "classify_exception",
    "current_trace",
    "emit",
    "span",
]
