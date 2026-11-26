"""Conversation business focus: what the last business answers were about (Phase 11).

"Rahul Gupta ka pending bill batao." … "Usme se kitna overdue hai?" The second question
only makes sense with the first one's subject. The focus carries that subject from turn
to turn in the thread's checkpointed state (`AgentState.business_focus`), and the
planner sees it, so a follow-up can name the exact reference (`customer_ref`) instead
of guessing.

The focus holds **references only**: entity kind, opaque ref, display name, the last
capability and period token, and the candidates of an AMBIGUOUS answer. It never holds
an amount, quantity, balance, count or date of business data. Business truth is
fetched again on every turn (memory = context; business tool = current truth). A
reference from the focus grants nothing: the service resolves it inside the caller's
tenant and warehouse scope (otherwise NOT_FOUND). The focus is conversation state, not
long-term memory: it is never written to the memory store.

Phase 16: `analysis_references` is the one thing from a business turn that does reach
long-term (episodic) memory. It is even narrower than the focus: no names and no refs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentic_platform.business.dates import DatePeriod
from agentic_platform.business.intent import BusinessQueryIntent

MAX_FOCUS_CANDIDATES = 10

FocusKind = Literal["customer", "product", "warehouse"]


class FocusEntity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: FocusKind
    ref: str
    display_name: str


class BusinessFocus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str | None = None
    customer: FocusEntity | None = None
    product: FocusEntity | None = None
    warehouse: FocusEntity | None = None
    period: DatePeriod | None = None
    # Candidates of the last AMBIGUOUS answer, so "the first one" / a name can be mapped
    # to an exact reference on the next turn. Display names and refs only.
    candidates: list[FocusEntity] = Field(default_factory=list)

    @classmethod
    def load(cls, value: Mapping[str, Any] | None) -> BusinessFocus:
        if not value:
            return cls()
        try:
            return cls.model_validate(value)
        except ValidationError:
            return cls()  # an unreadable focus is dropped, never trusted

    def to_state(self) -> dict[str, Any] | None:
        dumped = self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        return dumped or None


def _entity(kind: FocusKind, value: object) -> FocusEntity | None:
    if not isinstance(value, Mapping):
        return None
    ref, name = value.get("ref"), value.get("display_name")
    if isinstance(ref, str) and isinstance(name, str):
        return FocusEntity(entity=kind, ref=ref, display_name=name)
    return None


_CANDIDATE_KIND: dict[str, FocusKind] = {
    "customer": "customer",
    "product": "product",
    "warehouse": "warehouse",
}


def next_focus(
    previous: Mapping[str, Any] | None,
    results: Iterable[Mapping[str, Any]],
    intent: BusinessQueryIntent | None,
) -> BusinessFocus:
    """The focus after this turn's business results. Entities found in SUCCESS data
    replace earlier ones of the same kind; others stay. Figures are never copied."""
    focus = BusinessFocus.load(previous)
    update: dict[str, Any] = {"candidates": []}
    for result in results:
        capability = result.get("capability")
        status = result.get("status")
        if status == "AMBIGUOUS":
            kind = _CANDIDATE_KIND.get(str(result.get("entity")))
            if kind is not None:
                update["candidates"] = [
                    e
                    for c in result.get("candidates", [])[:MAX_FOCUS_CANDIDATES]
                    if (e := _entity(kind, c)) is not None
                ]
            update["capability"] = capability
            continue
        if status != "SUCCESS" or not isinstance(result.get("data"), Mapping):
            continue
        data = result["data"]
        update["capability"] = capability
        pairs: tuple[tuple[FocusKind, str], ...] = (
            ("customer", "customer"),
            ("product", "product"),
            ("warehouse", "warehouse"),
            ("product", "product_filter"),
            ("warehouse", "warehouse_filter"),
        )
        for kind, key in pairs:
            entity = _entity(kind, data.get(key))
            if entity is not None:
                update[kind] = entity
    if intent is not None and intent.period is not None:
        update["period"] = intent.period
    return focus.model_copy(update=update)


# --- Phase 16: references to past analyses, for episodic memory ------------------------------

MAX_ANALYSIS_REFERENCES = 5


def _span(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    start, last = value.get("start"), value.get("last_day")
    if isinstance(start, str) and isinstance(last, str):
        return {"start": start, "last_day": last}
    return None


def analysis_references(results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What this turn's business reads were, as references (Phase 16, ADR-065): the
    capability, status, the date range asked about (and the comparison range), and the
    source (adapter, TEST data or not, when it was read). Never a figure, a name, an
    entity reference or a tenant id: a later turn may learn that an analysis was done,
    never its numbers, which are always read again."""
    refs: list[dict[str, Any]] = []
    for result in results:
        if len(refs) >= MAX_ANALYSIS_REFERENCES:
            break
        ref: dict[str, Any] = {
            "capability": str(result.get("capability", "")),
            "status": str(result.get("status", "")),
        }
        if isinstance(result.get("reason"), str):
            ref["reason"] = result["reason"]
        data = result.get("data")
        if isinstance(data, Mapping):
            current = data.get("current")
            span = _span(data.get("date_range")) or (
                _span(current.get("date_range")) if isinstance(current, Mapping) else None
            )
            if span is not None:
                ref["period"] = span
            baseline = data.get("baseline")
            if isinstance(baseline, Mapping) and (earlier := _span(baseline.get("date_range"))):
                ref["compared_with"] = earlier
        source = result.get("source")
        if isinstance(source, Mapping):
            ref["source"] = {
                key: source[key]
                for key in ("adapter", "test_data", "retrieved_at")
                if key in source
            }
        refs.append(ref)
    return refs
