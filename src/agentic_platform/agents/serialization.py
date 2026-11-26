"""Checkpoint serializer with an explicit type allow-list (Phase 12, ADR-061).

LangGraph's msgpack serializer revives Python objects from checkpoints by importing the
module and class named in the stored bytes. By default it allows any type and only warns.
Its future strict mode will block every type that is not allow-listed. This module makes
that list explicit now, so loading a checkpoint can construct only:

* LangGraph's built-in safe types (datetime, Decimal, UUID, langchain messages, ...), and
* the platform types below, which are the only non-JSON values `AgentState` holds.

Anything else in a checkpoint (a tampered row, a type added to the state without being
listed here) is **blocked**, not imported. Phase 12 state (`orchestration`, `last_domain`)
is plain JSON and needs no entry. A test runs representative turns (general, business,
approval, focus, supervisor outcomes) in strict mode and fails on any blocked or
unlisted type, so this list cannot silently fall behind the state.
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agentic_platform.agents.schemas import AgentError, DomainResult, RequestUnderstanding
from agentic_platform.business.dates import DatePeriod
from agentic_platform.business.intent import BusinessOperation, InvoiceFilter
from agentic_platform.business.metrics import BusinessMetric
from agentic_platform.tools.base import PendingAction, ToolResult

# Pydantic models are stored with `model_dump()`: nested models become dicts that the
# outer class re-validates, but enum values inside them keep their enum type, so the
# enums used in state models are listed too.
CHECKPOINT_TYPES: tuple[type, ...] = (
    # agents/state.py: understanding, domain_result, errors
    RequestUnderstanding,
    DomainResult,
    AgentError,
    # agents/state.py: tool_results, pending_action
    ToolResult,
    PendingAction,
    # enums inside RequestUnderstanding.business_query (Phase 11)
    BusinessOperation,
    InvoiceFilter,
    DatePeriod,
    BusinessMetric,
)


def checkpoint_serializer() -> JsonPlusSerializer:
    """The serializer every platform checkpointer uses: allow-listed types only."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


__all__ = ["CHECKPOINT_TYPES", "checkpoint_serializer"]
