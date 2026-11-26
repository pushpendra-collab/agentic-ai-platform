"""Domain catalogue and the typed routing hints the planner may give (Phase 12, ADR-061).

The catalogue is closed. A request is handled by an **implemented** domain that is
registered in the `DomainRegistry` (`general` always; `business` only when an ERP
adapter is configured), or it is not handled at all:

* **Reserved** domains (`purchase`, `sales`, `inventory`, `accounting`, `gst`,
  `knowledge`, `notification`) name future domain agents. They have no handler, and the
  registry refuses to register one until the domain is implemented. A request that
  needs one gets a deterministic "not available" answer and nothing runs. Reading sales
  figures and stock is *not* one of them: the `business` domain answers those today.
* Anything else is not a domain. The Supervisor rejects it.

`RoutingHints` is LLM output (part of `RequestUnderstanding`), so it is untrusted. It can
only describe which parts of a request no implemented domain can do. It has no field for
a tenant, user, permission, table, SQL or tool argument, and unknown fields are refused.
All values are `Literal` strings, so the checkpoint stores them as plain strings.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Implemented domains (handlers exist in agents/domains/).
GENERAL_DOMAIN = "general"
BUSINESS_DOMAIN = "business"
IMPLEMENTED_DOMAINS: tuple[str, ...] = (GENERAL_DOMAIN, BUSINESS_DOMAIN)

# Planner label for "no domain can do this" (agents/schemas.py re-exports it).
UNSUPPORTED_DOMAIN = "unsupported"

ReservedDomainKey = Literal[
    "purchase", "sales", "inventory", "accounting", "gst", "knowledge", "notification"
]
RESERVED_DOMAINS: tuple[str, ...] = get_args(ReservedDomainKey)

# What the user wanted from a reserved domain. `send` means delivering something
# (WhatsApp, SMS, email); the Supervisor treats it specially (see supervisor.py).
UnavailableAction = Literal["write", "send", "read", "other"]

# What each reserved domain would do. Shown to the planner and used in fixed replies.
RESERVED_DOMAIN_LABELS: dict[str, str] = {
    "purchase": "purchase orders and other purchase actions",
    "sales": "creating or changing sales orders, quotations or sales invoices",
    "inventory": "stock adjustments, transfers and reorders",
    "accounting": "accounting entries and payments",
    "gst": "GST returns and GST entries",
    "knowledge": "answers from company documents",
    "notification": "sending WhatsApp, SMS or email messages",
}

# A domain key, as it may appear in state and logs.
DOMAIN_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"

MAX_UNAVAILABLE_PARTS = 5


def is_domain_key(value: str) -> bool:
    return re.fullmatch(DOMAIN_KEY_PATTERN, value) is not None


class UnavailablePart(BaseModel):
    """One part of the request that no implemented domain can carry out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: ReservedDomainKey = Field(description="The reserved domain this part needs.")
    action: UnavailableAction = Field(
        description="`write` (create/change/cancel), `send` (deliver a message or file), "
        "`read`, or `other`."
    )


class RoutingHints(BaseModel):
    """Planner hints for the Supervisor. Untrusted LLM output: it selects, never grants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unavailable: list[UnavailablePart] = Field(
        default_factory=list,
        description="Parts of the request that need a domain listed as not available. "
        "Empty when the chosen domain can do everything asked.",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="high", description="How sure the domain choice is."
    )

    @field_validator("unavailable")
    @classmethod
    def _bounded(cls, value: list[UnavailablePart]) -> list[UnavailablePart]:
        unique = list(dict.fromkeys(value))
        if len(unique) > MAX_UNAVAILABLE_PARTS:
            raise ValueError(f"at most {MAX_UNAVAILABLE_PARTS} unavailable parts")
        return unique


def describe_unavailable_for_llm(active: set[str]) -> str:
    """The domains the planner must not route to, with what they would do."""
    lines = [f"- `{key}`: {label}" for key, label in RESERVED_DOMAIN_LABELS.items()]
    if BUSINESS_DOMAIN not in active:
        lines.insert(0, f"- `{BUSINESS_DOMAIN}`: company business data (not connected here)")
    return "\n".join(lines)


__all__ = [
    "BUSINESS_DOMAIN",
    "DOMAIN_KEY_PATTERN",
    "GENERAL_DOMAIN",
    "IMPLEMENTED_DOMAINS",
    "RESERVED_DOMAINS",
    "RESERVED_DOMAIN_LABELS",
    "UNSUPPORTED_DOMAIN",
    "RoutingHints",
    "UnavailablePart",
    "describe_unavailable_for_llm",
    "is_domain_key",
]
