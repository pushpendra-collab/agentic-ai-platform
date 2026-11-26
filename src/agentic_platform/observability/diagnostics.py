"""Safe turn diagnostics (Phase 13, ADR-062).

Builds a readable view of one request from its observability events: which stages ran,
how long each took, which domain was chosen, which tools ran, whether the reply was
grounded, retries, timeouts, checkpoint problems and the final outcome. Input is either
the in-process trace buffer (`HUB.traces`) or JSON log lines (`LOG_FORMAT=json`), so an
operator can reconstruct a turn from shipped logs:

    python -m agentic_platform.observability.diagnose --request-id <id> < agent.log

Only event fields are shown (ids, codes, counts, durations, flags). Events cannot hold
prompts, replies, memory, ERP records or tool argument values (core/redaction.py), so
neither can this view.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agentic_platform.observability.events import (
    EVENT_KIND,
    EventName,
    ObsEvent,
    Stage,
    is_stage_end,
)


@dataclass
class StageView:
    stage: str
    status: str
    duration_ms: float | None
    error_category: str | None = None
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnDiagnostics:
    request_id: str | None
    turn_ids: list[str] = field(default_factory=list)
    thread_id: str | None = None
    outcome: str | None = None
    total_ms: float | None = None
    error_category: str | None = None
    error_code: str | None = None
    domain: str | None = None
    routing: dict[str, Any] = field(default_factory=dict)
    stages: list[StageView] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    business_reads: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    retries: list[dict[str, Any]] = field(default_factory=list)
    timeouts: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, int] = field(default_factory=dict)
    rejected: bool = False

    @classmethod
    def from_events(cls, events: Sequence[ObsEvent]) -> TurnDiagnostics:
        diag = cls(request_id=next((e.request_id for e in events if e.request_id), None))
        for e in events:
            a = e.attributes
            if e.turn_id and e.turn_id not in diag.turn_ids:
                diag.turn_ids.append(e.turn_id)
            diag.thread_id = diag.thread_id or e.thread_id
            n = e.name
            if n in (EventName.TURN_COMPLETED, EventName.TURN_FAILED, EventName.TURN_INTERRUPTED):
                diag.outcome = str(a.get("outcome") or e.status)
                diag.total_ms = e.duration_ms
                diag.error_category, diag.error_code = e.error_category, e.error_code
            elif n is EventName.TURN_REJECTED:
                diag.rejected, diag.outcome = True, "rejected"
                diag.error_category, diag.error_code = e.error_category, e.error_code
            elif n is EventName.SUPERVISOR_DECIDED:
                diag.domain = e.domain
                keys = ("outcome", "reason", "requested_domain", "fixed_reply", "follow_up")
                diag.routing = {k: a[k] for k in keys if k in a}
            elif n in (EventName.TOOL_COMPLETED, EventName.TOOL_FAILED):
                diag.tools.append(
                    {"tool": e.tool, "status": e.status, "duration_ms": e.duration_ms,
                     "category": e.error_category, "code": e.error_code}
                )  # fmt: skip
            elif n in (EventName.LLM_CALL_COMPLETED, EventName.LLM_CALL_FAILED):
                diag.llm_calls.append(
                    {"stage": a.get("llm_stage"), "model": a.get("model"), "status": e.status,
                     "duration_ms": e.duration_ms, "code": e.error_code,
                     "input_tokens": a.get("input_tokens"),
                     "output_tokens": a.get("output_tokens")}
                )  # fmt: skip
            elif n is EventName.BUSINESS_READ:
                diag.business_reads.append(
                    {"capability": a.get("capability"), "status": a.get("result_status"),
                     "reason": a.get("reason"), "attempts": e.attempt,
                     "duration_ms": e.duration_ms}
                )  # fmt: skip
            elif n is EventName.VERIFICATION_RESULT:
                diag.verification = dict(a)
            elif n is EventName.RETRY_ATTEMPTED:
                diag.retries.append({"operation": a.get("operation"), "attempt": e.attempt,
                                     "category": e.error_category})  # fmt: skip
            elif n is EventName.TIMEOUT:
                diag.timeouts.append({"scope": a.get("scope"), "limit_ms": a.get("limit_ms")})
            elif e.stage is Stage.CHECKPOINT:
                diag.checkpoint[n.value] = diag.checkpoint.get(n.value, 0) + 1
            elif e.stage is not None and is_stage_end(n, e.stage):
                diag.stages.append(
                    StageView(
                        stage=e.stage.value,
                        status=e.status or "?",
                        duration_ms=e.duration_ms,
                        error_category=e.error_category,
                        error_code=e.error_code,
                        details={k: v for k, v in a.items() if k != "node"},
                    )
                )
        return diag

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "turn_ids": self.turn_ids,
            "thread_id": self.thread_id,
            "outcome": self.outcome,
            "total_ms": self.total_ms,
            "error_category": self.error_category,
            "error_code": self.error_code,
            "domain": self.domain,
            "routing": self.routing,
            "stages": [s.__dict__ for s in self.stages],
            "tools": self.tools,
            "llm_calls": self.llm_calls,
            "business_reads": self.business_reads,
            "verification": self.verification,
            "retries": self.retries,
            "timeouts": self.timeouts,
            "checkpoint": self.checkpoint,
        }

    def render(self) -> str:
        lines = ["Request", f"  request_id={self.request_id}"]
        lines += ["Turn", f"  turn_id={','.join(self.turn_ids) or None}",
                  f"  thread_id={self.thread_id}"]  # fmt: skip
        for stage in self.stages:
            lines.append(stage.stage.replace("_", " ").title())
            lines.append(f"  status={stage.status}")
            lines.append(f"  duration={_ms(stage.duration_ms)}")
            if stage.stage == Stage.SUPERVISOR and self.domain is not None:
                lines.append(f"  domain={self.domain}")
            if stage.stage == Stage.SUPERVISOR and self.routing:
                lines.append("  " + " ".join(f"{k}={v}" for k, v in self.routing.items()))
            if stage.stage == Stage.DOMAIN:
                for tool in self.tools:
                    lines.append(
                        f"  tool={tool['tool']} status={tool['status']} "
                        f"duration={_ms(tool['duration_ms'])}"
                        + (f" category={tool['category']}" if tool["category"] else "")
                    )
                for read in self.business_reads:
                    lines.append(
                        f"  business={read['capability']} status={read['status']}"
                        + (f" reason={read['reason']}" if read["reason"] else "")
                        + (f" attempts={read['attempts']}" if read["attempts"] else "")
                    )
            if stage.stage == Stage.VERIFICATION and self.verification:
                grounded = "replaced" if self.verification.get("replaced") else "grounded"
                lines.append(f"  result={grounded}")
            if stage.error_code:
                lines.append(f"  error={stage.error_code} category={stage.error_category}")
        for call in self.llm_calls:
            lines.append("LLM")
            lines.append(
                f"  stage={call['stage']} model={call['model']} status={call['status']} "
                f"duration={_ms(call['duration_ms'])}"
            )
        for retry in self.retries:
            lines.append(f"Retry\n  operation={retry['operation']} attempt={retry['attempt']} "
                         f"category={retry['category']}")  # fmt: skip
        for timeout in self.timeouts:
            lines.append(f"Timeout\n  scope={timeout['scope']} limit_ms={timeout['limit_ms']}")
        if self.checkpoint:
            lines.append("Checkpoint")
            lines.append("  " + " ".join(f"{k}={v}" for k, v in sorted(self.checkpoint.items())))
        lines.append("Response")
        lines.append(f"  status={self.outcome}")
        if self.error_code:
            lines.append(f"  error={self.error_code} category={self.error_category}")
        lines += ["Total", f"  duration={_ms(self.total_ms)}"]
        return "\n".join(lines)


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}ms"


def events_from_log_lines(lines: Iterable[str], request_id: str) -> list[ObsEvent]:
    """Observability events of one request from JSON log lines (other lines are skipped)."""
    events: list[ObsEvent] = []
    names = {n.value for n in EventName}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") != EVENT_KIND or record.get("event") not in names:
            continue
        if record.get("request_id") != request_id:
            continue
        fields = {k: v for k, v in record.items() if k in ObsEvent.model_fields}
        fields.pop("name", None)
        try:
            events.append(ObsEvent(name=EventName(record["event"]), **fields))
        except ValidationError:
            continue
    return events


__all__ = ["StageView", "TurnDiagnostics", "events_from_log_lines"]
