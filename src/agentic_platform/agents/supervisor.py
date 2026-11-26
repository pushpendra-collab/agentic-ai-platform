"""The Supervisor: decides which domain handles a turn (Phase 12, ADR-061).

    understand_request (LLM: classification + plan, untrusted)
            ↓
    supervise ── SupervisorDecision
            ├─► process_request → domain handler  (ROUTE, UNSUPPORTED)
            ├─► generate_response                 (CLARIFY asked by the planner)
            └─► finalize_turn: fixed reply        (UNSUPPORTED_CAPABILITY, CLARIFY by Supervisor)

The Supervisor is an orchestrator, not a reasoning layer. It is **deterministic** and has
no dependencies: no LLM client, tool registry, database, ERP port or principal. It reads
the planner's classification (`RequestUnderstanding`) and the names of the registered
domains, and returns a typed `SupervisorDecision`. It cannot run a tool, read business
data, compute a figure or grant anything: the domain handler, the tool registry and the
business service still check the principal's permissions on every call.

Rules, in order:

1. **Limits.** One routing decision and one domain run per turn
   (`MAX_ROUTING_DECISIONS_PER_TURN`, `MAX_DOMAIN_RUNS_PER_TURN`). Exceeding either raises
   `OrchestrationLimitError`: the turn ends as a controlled failure. The graph has no
   edge back to the Supervisor, so a domain can never hand a turn back to it.
2. **Typed capability wins over a free-text label.** If the planner filled a business
   read capability (`business_query.capability`, no write) but labelled the request
   `general` or a reserved domain such as `sales`, the turn goes to `business` (only if
   it is registered). The business domain still authorizes the read.
3. **Planner clarification** passes through (its question is already written).
4. **Registered domain:** routed. If the request also has parts that need a reserved
   domain (`routing.unavailable`): a `send` part makes the request ambiguous ("show the
   report here, or send it?"), so the Supervisor asks; a `write` part is reported as not
   done, and the read part runs (partial route).
5. **`unsupported`:** with unavailable parts listed → a fixed "not available" reply; without
   → the existing explained-unsupported reply (the writer explains, nothing runs).
6. **Reserved domain, or `business` while no ERP is configured:** fixed "not available"
   reply. **Anything else** is not a domain: rejected, fixed reply.

The conversation's earlier domain is recorded (`previous_domain`, `topic_changed`) but never
used to choose the domain: a follow-up is routed by what the planner made of the latest
message, so a stale context cannot pull a new question into the wrong domain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_platform.agents.routing import (
    BUSINESS_DOMAIN,
    DOMAIN_KEY_PATTERN,
    GENERAL_DOMAIN,
    IMPLEMENTED_DOMAINS,
    RESERVED_DOMAIN_LABELS,
    RESERVED_DOMAINS,
    UNSUPPORTED_DOMAIN,
    UnavailablePart,
    is_domain_key,
)
from agentic_platform.agents.schemas import RequestUnderstanding
from agentic_platform.core.errors import OrchestrationError, OrchestrationLimitError

# Explicit routing limits (structural invariants, not configuration).
MAX_ROUTING_DECISIONS_PER_TURN = 1
MAX_DOMAIN_RUNS_PER_TURN = 1


class SupervisorOutcome(StrEnum):
    ROUTE = "ROUTE"  # a registered domain handles the turn
    CLARIFY = "CLARIFY"  # ask the user first; nothing runs
    UNSUPPORTED = "UNSUPPORTED"  # out of scope; the writer explains, nothing runs
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"  # fixed reply, nothing runs


class Reason(StrEnum):
    CLASSIFIED = "classified"
    TYPED_BUSINESS_CAPABILITY = "typed_business_capability"
    PARTIAL_ROUTE = "partial_route"
    PLANNER_CLARIFICATION = "planner_clarification"
    SEND_NOT_AVAILABLE = "send_not_available"
    OUT_OF_SCOPE = "out_of_scope"
    NOT_AVAILABLE = "not_available"
    RESERVED_DOMAIN = "reserved_domain"
    DOMAIN_NOT_CONNECTED = "domain_not_connected"
    INVALID_DOMAIN = "invalid_domain"


RequestedAction = Literal["answer", "read", "write", "send", "mixed", "unknown"]

_NO_DOMAIN = (SupervisorOutcome.UNSUPPORTED, SupervisorOutcome.UNSUPPORTED_CAPABILITY)


class RoutingMetadata(BaseModel):
    """Safe, typed facts about the decision (no user text, names or figures)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_number: int = Field(ge=1)
    follow_up: bool = False
    previous_domain: str | None = Field(default=None, pattern=DOMAIN_KEY_PATTERN)
    topic_changed: bool = False
    rerouted_from: str | None = Field(default=None, pattern=DOMAIN_KEY_PATTERN)


class SupervisorDecision(BaseModel):
    """The Supervisor's typed routing decision, stored in the checkpoint as JSON.

    It holds no identity, permission, SQL, table or tool argument (unknown fields are
    refused): identity and authorization stay in trusted application state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: SupervisorOutcome
    # The registered domain that handles the turn (ROUTE), else the domain the planner's
    # question belongs to when it asks (CLARIFY), else None.
    domain: str | None = Field(default=None, pattern=DOMAIN_KEY_PATTERN)
    # The planner's label, sanitised ("invalid" when it is not even shaped like a key).
    requested_domain: str = Field(pattern=DOMAIN_KEY_PATTERN)
    reason: Reason
    confidence: Literal["high", "medium", "low"] | None = None
    clarification_required: bool = False
    requested_action: RequestedAction = "unknown"
    # Parts that need a reserved domain: reported to the user, never executed.
    unavailable: list[UnavailablePart] = Field(default_factory=list)
    routing_metadata: RoutingMetadata

    @field_validator("unavailable")
    @classmethod
    def _unique(cls, value: list[UnavailablePart]) -> list[UnavailablePart]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _consistent(self) -> SupervisorDecision:
        if self.outcome is SupervisorOutcome.ROUTE and self.domain is None:
            raise ValueError("a ROUTE decision names its domain")
        if self.outcome in _NO_DOMAIN and self.domain is not None:
            raise ValueError("an unsupported decision routes to no domain")
        if self.clarification_required != (self.outcome is SupervisorOutcome.CLARIFY):
            raise ValueError("clarification_required is set exactly for CLARIFY")
        return self

    @property
    def fixed_reply(self) -> bool:
        """The Supervisor answers the turn itself with a fixed message (nothing runs)."""
        return self.outcome is SupervisorOutcome.UNSUPPORTED_CAPABILITY or (
            self.outcome is SupervisorOutcome.CLARIFY
            and self.reason is not Reason.PLANNER_CLARIFICATION
        )

    @property
    def runs_domain(self) -> bool:
        """`process_request` runs for this decision (ROUTE, or the explained unsupported)."""
        return self.outcome in (SupervisorOutcome.ROUTE, SupervisorOutcome.UNSUPPORTED)

    def to_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def log_fields(self) -> dict[str, object]:
        """Metadata for logs: codes and counts only."""
        return {
            "outcome": self.outcome.value,
            "domain": self.domain,
            "requested_domain": self.requested_domain,
            "reason": self.reason.value,
            "confidence": self.confidence,
            "requested_action": self.requested_action,
            "unavailable": [f"{p.domain}:{p.action}" for p in self.unavailable],
            "follow_up": self.routing_metadata.follow_up,
            "topic_changed": self.routing_metadata.topic_changed,
            "rerouted_from": self.routing_metadata.rerouted_from,
        }


def load_decision(orchestration: dict[str, object] | None) -> SupervisorDecision:
    """The decision of this turn, re-validated from state. Missing/invalid = contract broken."""
    raw = (orchestration or {}).get("decision")
    if not isinstance(raw, dict):
        raise OrchestrationError("No supervisor decision in state.")
    try:
        return SupervisorDecision.model_validate(raw)
    except ValueError as exc:
        raise OrchestrationError(f"Invalid supervisor decision in state: {exc}") from None


class Supervisor:
    """Pure routing logic. `active_domains` returns the registered domain keys (read at
    decision time, so it always matches the registry)."""

    def __init__(self, active_domains: Callable[[], Iterable[str]]) -> None:
        self._active_domains = active_domains

    def decide(
        self,
        understanding: RequestUnderstanding,
        *,
        previous_domain: str | None = None,
        decisions_so_far: int = 0,
    ) -> SupervisorDecision:
        if decisions_so_far >= MAX_ROUTING_DECISIONS_PER_TURN:
            raise OrchestrationLimitError(
                f"Routing decision {decisions_so_far + 1} in one turn "
                f"(limit {MAX_ROUTING_DECISIONS_PER_TURN})."
            )
        active = set(self._active_domains())
        label = understanding.domain.strip().lower()
        requested = label if is_domain_key(label) else "invalid"
        query = understanding.business_query
        hints = understanding.routing
        unavailable = list(hints.unavailable) if hints else []
        confidence = hints.confidence if hints else (query.confidence if query else None)
        write_requested = query is not None and query.write_requested

        # Rule 2: a typed business read capability decides between business and a label
        # that cannot serve it (general, or a reserved domain such as `sales`).
        domain, rerouted_from = requested, None
        typed_read = query is not None and query.capability is not None and not write_requested
        if (
            typed_read
            and BUSINESS_DOMAIN in active
            and (requested == GENERAL_DOMAIN or requested in RESERVED_DOMAINS)
        ):
            domain, rerouted_from = BUSINESS_DOMAIN, requested

        def decision(
            outcome: SupervisorOutcome,
            reason: Reason,
            *,
            routed: str | None = None,
            action: RequestedAction = "unknown",
            parts: list[UnavailablePart] | None = None,
        ) -> SupervisorDecision:
            return SupervisorDecision(
                outcome=outcome,
                domain=routed,
                requested_domain=requested,
                reason=reason,
                confidence=confidence,
                clarification_required=outcome is SupervisorOutcome.CLARIFY,
                requested_action=action,
                unavailable=parts if parts is not None else unavailable,
                routing_metadata=RoutingMetadata(
                    decision_number=decisions_so_far + 1,
                    follow_up=query is not None and query.follow_up,
                    previous_domain=previous_domain
                    if previous_domain and is_domain_key(previous_domain) else None,
                    topic_changed=outcome is SupervisorOutcome.ROUTE
                    and previous_domain is not None
                    and routed != previous_domain,
                    rerouted_from=rerouted_from if outcome is SupervisorOutcome.ROUTE else None,
                ),
            )  # fmt: skip

        # Rule 3: the planner asks first.
        if understanding.needs_clarification:
            return decision(
                SupervisorOutcome.CLARIFY,
                Reason.PLANNER_CLARIFICATION,
                routed=domain if domain in active else None,
                action="unknown",
            )

        # Rule 4: a registered domain.
        if domain in active:
            if any(part.action == "send" for part in unavailable):
                return decision(
                    SupervisorOutcome.CLARIFY, Reason.SEND_NOT_AVAILABLE, action="mixed"
                )
            if unavailable:
                return decision(
                    SupervisorOutcome.ROUTE, Reason.PARTIAL_ROUTE, routed=domain, action="mixed"
                )
            reason = Reason.TYPED_BUSINESS_CAPABILITY if rerouted_from else Reason.CLASSIFIED
            action: RequestedAction = (
                "write" if write_requested else "read" if domain == BUSINESS_DOMAIN else "answer"
            )
            return decision(SupervisorOutcome.ROUTE, reason, routed=domain, action=action)

        # Rules 5 and 6: nothing runs.
        not_done = _action_of(unavailable, write_requested)
        if domain == UNSUPPORTED_DOMAIN:
            if unavailable:
                return decision(
                    SupervisorOutcome.UNSUPPORTED_CAPABILITY, Reason.NOT_AVAILABLE, action=not_done
                )
            return decision(SupervisorOutcome.UNSUPPORTED, Reason.OUT_OF_SCOPE, action=not_done)
        if domain in RESERVED_DOMAINS:
            parts = unavailable or [UnavailablePart.model_validate(
                {"domain": domain, "action": "write" if write_requested else "other"}
            )]  # fmt: skip
            return decision(
                SupervisorOutcome.UNSUPPORTED_CAPABILITY,
                Reason.RESERVED_DOMAIN,
                action=_action_of(parts, write_requested),
                parts=parts,
            )
        if domain in IMPLEMENTED_DOMAINS:  # implemented but not registered (business, no ERP)
            return decision(
                SupervisorOutcome.UNSUPPORTED_CAPABILITY, Reason.DOMAIN_NOT_CONNECTED,
                action=not_done,
            )  # fmt: skip
        return decision(
            SupervisorOutcome.UNSUPPORTED_CAPABILITY, Reason.INVALID_DOMAIN, action=not_done
        )


def _action_of(parts: list[UnavailablePart], write_requested: bool) -> RequestedAction:
    if any(p.action == "send" for p in parts):
        return "send"
    if write_requested or any(p.action == "write" for p in parts):
        return "write"
    return "unknown"


# --- fixed replies (no figures, no names, no action claims) ---------------------------------

NOT_CONNECTED_MESSAGE = (
    "Business data is not connected to this assistant, so I can't answer business "
    "questions here yet."
)
INVALID_DOMAIN_MESSAGE = "I can't handle this request here, so nothing was carried out."
# `{labels}`: what each missing domain would do, joined by "; " (RESERVED_DOMAIN_LABELS).
SEND_CLARIFICATION_MESSAGE = (
    "I can't do part of this here, because it is not available in this assistant yet: "
    "{labels}. Should I only show you the information here, without anything being "
    "delivered?"
)
NOT_AVAILABLE_MESSAGE = (
    "This isn't available in the assistant yet: {labels}. Nothing was carried out."
)
PARTIAL_NOTICE = (
    "Note: part of your request was not done, because it is not available in this "
    "assistant yet: {labels}."
)
# A question that only *reads* something no tool covers ("kitne user hain?"). The
# planner has to file such a part under some reserved domain, and often picks one
# that has nothing to do with it (`purchase` for a user count), so its label is not
# repeated back; the reply says what can be asked instead.
READ_NOT_AVAILABLE_MESSAGE = (
    "This information isn't available in the assistant yet, so nothing was looked up. "
    "You can ask about sales (today, yesterday, this week, month or year, or compared "
    "with last year), stock of a product or a godown, low-stock products, a customer's "
    "outstanding amount, pending or overdue bills, and how many customers you have."
)
READ_ALSO_MISSING = "Part of the information you asked for isn't available yet either."
READ_PARTIAL_NOTICE = (
    "Note: part of your request could not be answered, because that information isn't "
    "available in the assistant yet."
)


def _is_read_only(part: UnavailablePart) -> bool:
    # Live (2026-11-25): "kitne user hain?" was filed under `purchase` and under
    # `knowledge` ("answers from company documents"); neither label is repeated.
    return part.action == "read"


def _labels(parts: Iterable[UnavailablePart]) -> str:
    labels = dict.fromkeys(RESERVED_DOMAIN_LABELS[p.domain] for p in parts if not _is_read_only(p))
    return "; ".join(labels) or "this action"


def fixed_reply(decision: SupervisorDecision) -> str:
    """The message for a decision the Supervisor answers itself."""
    if decision.outcome is SupervisorOutcome.CLARIFY:
        return SEND_CLARIFICATION_MESSAGE.format(labels=_labels(decision.unavailable))
    if decision.reason is Reason.DOMAIN_NOT_CONNECTED:
        return NOT_CONNECTED_MESSAGE
    if decision.reason is Reason.INVALID_DOMAIN or not decision.unavailable:
        return INVALID_DOMAIN_MESSAGE
    return not_available_reply(decision.unavailable)


def not_available_reply(parts: Iterable[UnavailablePart]) -> str:
    parts = list(parts)
    if parts and all(_is_read_only(p) for p in parts):
        return READ_NOT_AVAILABLE_MESSAGE
    text = NOT_AVAILABLE_MESSAGE.format(labels=_labels(parts))
    if any(_is_read_only(p) for p in parts):
        text += " " + READ_ALSO_MISSING
    return text


def partial_notice(decision: SupervisorDecision) -> str | None:
    """Appended to a routed reply when part of the request was not done (never replaces it)."""
    if decision.outcome is not SupervisorOutcome.ROUTE or not decision.unavailable:
        return None
    if all(_is_read_only(p) for p in decision.unavailable):
        return READ_PARTIAL_NOTICE
    notice = PARTIAL_NOTICE.format(labels=_labels(decision.unavailable))
    if any(_is_read_only(p) for p in decision.unavailable):
        notice += " " + READ_ALSO_MISSING
    return notice


__all__ = [
    "MAX_DOMAIN_RUNS_PER_TURN",
    "MAX_ROUTING_DECISIONS_PER_TURN",
    "READ_NOT_AVAILABLE_MESSAGE",
    "READ_PARTIAL_NOTICE",
    "Reason",
    "RoutingMetadata",
    "Supervisor",
    "SupervisorDecision",
    "SupervisorOutcome",
    "fixed_reply",
    "load_decision",
    "not_available_reply",
    "partial_notice",
]
