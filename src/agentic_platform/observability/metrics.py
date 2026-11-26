"""Minimal in-process metrics (Phase 13, ADR-062).

Counters and latency histograms, fed by observability events (`MetricsSink`). No metrics
platform is bundled: `MetricsRegistry.snapshot()` returns plain JSON for tests, the
benchmark and diagnostics, and every event is also a JSON log line an aggregator can
count. An exporter (Prometheus, OTLP) is a deployment decision (Q3).

Label policy (enforced, not advised):

* Every metric is declared in `METRICS` with a fixed tuple of label keys. Using an
  undeclared key raises `ValueError`, so a tenant, user, customer, invoice, amount or
  prompt label cannot be added at a call site; none is declared.
* Label values must be short lowercase tokens (`_LABEL_VALUE`); anything else becomes
  `other`. Each metric keeps at most `MAX_SERIES` label combinations; later new
  combinations are folded into one `other` series, so cardinality stays bounded.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

MetricKind = Literal["counter", "histogram"]

_LABEL_VALUE = re.compile(r"^[a-z0-9_.:\-]{1,48}$")
OTHER = "other"
MAX_SERIES = 200
RESERVOIR = 2048  # latency samples kept per series for percentiles
BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000)


@dataclass(frozen=True)
class MetricDef:
    name: str
    kind: MetricKind
    labels: tuple[str, ...]
    help: str


METRICS: dict[str, MetricDef] = {
    d.name: d
    for d in (
        MetricDef("agent_turns_total", "counter", ("outcome",),
                  "Turns by outcome: completed, needs_clarification, unsupported, failed, "
                  "interrupted, rejected."),
        MetricDef("agent_turn_duration_ms", "histogram", ("outcome",), "Turn duration."),
        MetricDef("agent_stage_duration_ms", "histogram", ("stage", "status"),
                  "Duration of each graph stage."),
        MetricDef("agent_stage_failures_total", "counter", ("stage", "category"),
                  "Graph stages that failed, by error category."),
        MetricDef("agent_routing_decisions_total", "counter", ("outcome", "domain"),
                  "Supervisor decisions."),
        MetricDef("agent_domain_runs_total", "counter", ("domain", "outcome"),
                  "Domain handler runs by domain outcome."),
        MetricDef("agent_domain_duration_ms", "histogram", ("domain",), "Domain run duration."),
        MetricDef("agent_tool_calls_total", "counter", ("tool", "status", "category"),
                  "Tool calls."),
        MetricDef("agent_tool_duration_ms", "histogram", ("tool",), "Tool call duration."),
        MetricDef("agent_llm_calls_total", "counter", ("stage", "model", "status", "category"),
                  "LLM calls."),
        MetricDef("agent_llm_latency_ms", "histogram", ("stage", "model"), "LLM call latency."),
        MetricDef("agent_llm_tokens_total", "counter", ("model", "direction"),
                  "LLM tokens reported by the provider (input/output)."),
        MetricDef("agent_retries_total", "counter", ("operation", "category"),
                  "Retry attempts (not first attempts)."),
        MetricDef("agent_retry_exhausted_total", "counter", ("operation", "category"),
                  "Operations that failed after their last allowed attempt."),
        MetricDef("agent_timeouts_total", "counter", ("scope",), "Deadlines that fired."),
        MetricDef("agent_checkpoint_duration_ms", "histogram", ("operation",),
                  "Checkpoint load/save duration."),
        MetricDef("agent_checkpoint_failures_total", "counter", ("operation", "category"),
                  "Checkpoint loads/saves that failed or were rejected."),
        MetricDef("agent_business_reads_total", "counter", ("capability", "status"),
                  "Business reads by result status."),
        MetricDef("agent_grounding_total", "counter", ("result",),
                  "Business reply verification: grounded or replaced."),
        MetricDef("agent_security_refusals_total", "counter", ("kind", "code"),
                  "Authentication failures and authorization refusals."),
    )
}  # fmt: skip


def _label(value: Any) -> str:
    text = str(value).lower() if value is not None else "none"
    return text if _LABEL_VALUE.fullmatch(text) else OTHER


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS_MS) + 1))
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=RESERVOIR))

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        index = next((i for i, b in enumerate(BUCKETS_MS) if value <= b), len(BUCKETS_MS))
        self.buckets[index] += 1
        self.samples.append(value)

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.samples)
        return {
            "count": self.count,
            "sum": round(self.total, 3),
            "min": round(self.minimum, 3) if self.count else None,
            "max": round(self.maximum, 3) if self.count else None,
            "p50": percentile(ordered, 50),
            "p95": percentile(ordered, 95),
            "buckets_ms": dict(zip([*map(str, BUCKETS_MS), "+inf"], self.buckets, strict=True)),
        }


def percentile(ordered: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an already sorted list."""
    if not ordered:
        return None
    rank = max(1, -(-len(ordered) * pct // 100))  # ceil
    return round(ordered[int(rank) - 1], 3)


class MetricsRegistry:
    """Thread-safe counters and histograms with declared, bounded labels."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[str, ...], float]] = {}
        self._histograms: dict[str, dict[tuple[str, ...], _Histogram]] = {}

    def inc(self, name: str, amount: float = 1, **labels: Any) -> None:
        definition = self._definition(name, "counter", labels)
        key = self._key(definition, labels)
        with self._lock:
            series = self._counters.setdefault(name, {})
            key = self._bounded(series, key)
            series[key] = series.get(key, 0) + amount

    def observe(self, name: str, value: float, **labels: Any) -> None:
        definition = self._definition(name, "histogram", labels)
        key = self._key(definition, labels)
        with self._lock:
            series = self._histograms.setdefault(name, {})
            key = self._bounded(series, key)
            series.setdefault(key, _Histogram()).observe(float(value))

    def counter(self, name: str, **labels: Any) -> float:
        """Current value of one counter series (0 when never incremented)."""
        definition = self._definition(name, "counter", labels, partial=True)
        with self._lock:
            series = self._counters.get(name, {})
            return sum(v for k, v in series.items() if _matches(definition, k, labels))

    def histogram(self, name: str, **labels: Any) -> dict[str, Any] | None:
        definition = self._definition(name, "histogram", labels, partial=True)
        with self._lock:
            merged = _Histogram()
            for key, hist in self._histograms.get(name, {}).items():
                if _matches(definition, key, labels):
                    for sample in hist.samples:
                        merged.observe(sample)
            return merged.summary() if merged.count else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = {
                name: [
                    {"labels": dict(zip(METRICS[name].labels, key, strict=True)), "value": value}
                    for key, value in sorted(series.items())
                ]
                for name, series in sorted(self._counters.items())
            }
            histograms = {
                name: [
                    {"labels": dict(zip(METRICS[name].labels, key, strict=True)),
                     **hist.summary()}
                    for key, hist in sorted(series.items())
                ]
                for name, series in sorted(self._histograms.items())
            }  # fmt: skip
        return {"counters": counters, "histograms": histograms}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    # -- internals ---------------------------------------------------------------------

    @staticmethod
    def _definition(
        name: str, kind: MetricKind, labels: dict[str, Any], *, partial: bool = False
    ) -> MetricDef:
        definition = METRICS.get(name)
        if definition is None or definition.kind != kind:
            raise ValueError(f"Undeclared {kind} metric '{name}'.")
        unknown = set(labels) - set(definition.labels)
        missing = set(definition.labels) - set(labels)
        if unknown or (missing and not partial):
            raise ValueError(
                f"Metric '{name}' takes labels {definition.labels}; got {sorted(labels)}."
            )
        return definition

    @staticmethod
    def _key(definition: MetricDef, labels: dict[str, Any]) -> tuple[str, ...]:
        return tuple(_label(labels[k]) for k in definition.labels)

    @staticmethod
    def _bounded(series: dict[tuple[str, ...], Any], key: tuple[str, ...]) -> tuple[str, ...]:
        if key in series or len(series) < MAX_SERIES:
            return key
        return tuple(OTHER for _ in key)


def _matches(definition: MetricDef, key: tuple[str, ...], labels: dict[str, Any]) -> bool:
    return all(
        key[definition.labels.index(name)] == _label(value) for name, value in labels.items()
    )


__all__ = ["METRICS", "MetricDef", "MetricsRegistry", "percentile"]
