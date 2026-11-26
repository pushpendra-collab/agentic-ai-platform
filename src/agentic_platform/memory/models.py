"""Memory domain types shared by memory types, adapters and the manager."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentic_platform.core.errors import InvalidMemoryScopeError

_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class MemoryKind(StrEnum):
    """Durable kinds stored by a `MemoryStore`. Working memory and checkpoint state
    have their own stores (Redis/in-memory and the LangGraph checkpointer)."""

    LONG_TERM = "long_term"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    CONVERSATION_SUMMARY = "conversation_summary"


@dataclass(frozen=True)
class MemoryScope:
    """Who the memory belongs to. Every memory operation takes one.

    `tenant_id` (company) is mandatory; nothing ever crosses tenants.
    `user_id` narrows to one person; None means "no particular user", which can
    only read and write tenant-shared memory.

    The scope comes from the caller (later: the authenticated principal), never
    from LLM output.
    """

    tenant_id: str
    user_id: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("tenant_id", self.tenant_id), ("user_id", self.user_id)):
            if value is None and name == "user_id":
                continue
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise InvalidMemoryScopeError(f"Invalid {name}.")

    @property
    def user_key(self) -> str:
        """Stored user_id: '' for tenant-shared memory."""
        return self.user_id or ""

    def readable_user_keys(self, *, include_shared: bool) -> tuple[str, ...]:
        """The user's own memory plus, optionally, tenant-shared memory."""
        keys = {self.user_key}
        if include_shared:
            keys.add("")
        return tuple(sorted(keys))


class MemoryRecord(BaseModel):
    """A stored memory as returned to callers (without the embedding vector)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    kind: MemoryKind
    tenant_id: str
    user_id: str | None
    namespace: str
    key: str | None = None
    version: int = 1
    thread_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_type: str | None = None
    source_ref: str | None = None
    embedding_model: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class NewMemory(BaseModel):
    """A memory to write. The scope is passed separately and always wins."""

    kind: MemoryKind
    content: str = Field(min_length=1)
    namespace: str = ""
    key: str | None = None
    version: int = Field(default=1, ge=1)
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_type: str | None = None
    source_ref: str | None = None
    embedding: list[float] | None = None
    embedding_model: str | None = None
    expires_at: datetime | None = None
    shared: bool = False  # True: tenant-wide (user_id '') instead of the scope's user


class MemoryQuery(BaseModel):
    """Structured lookup (no free-form filtering)."""

    kinds: tuple[MemoryKind, ...] = ()
    namespace: str | None = None
    key: str | None = None
    thread_id: str | None = None
    source_ref: str | None = None
    version: int | None = None
    ids: tuple[uuid.UUID, ...] = ()
    include_shared: bool = False
    include_expired: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    newest_first: bool = True


class VectorQuery(BaseModel):
    embedding: list[float]
    model: str
    kinds: tuple[MemoryKind, ...]
    k: int = Field(default=5, ge=1, le=200)
    min_score: float = -1.0
    namespace: str | None = None
    include_shared: bool = True


class ScoredMemory(BaseModel):
    record: MemoryRecord
    score: float  # cosine similarity, 1.0 = identical direction


# Phase 16: how a recalled item is known (MemoryContext.to_prompt `basis`).
#   stated_by_user     a fact the user saved themselves
#   approved_by_user   a fact the agent proposed and the user approved (HITL)
#   document           tenant knowledge or a procedure someone with permission stored
#   interaction_summary  an automatic summary of a past turn: inferred, never confirmed
#   unconfirmed        anything else
_BASIS = {
    "user": "stated_by_user",
    "agent": "approved_by_user",
    "agent_turn": "interaction_summary",
}


def memory_basis(record: MemoryRecord) -> str:
    if record.kind in (MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL):
        return "document"
    return _BASIS.get(record.source_type or "", "unconfirmed")


class Episode(BaseModel):
    """One finished agent turn, as remembered (ADR-036)."""

    thread_id: str
    request_id: str
    intent: str | None = None
    domain: str | None = None
    outcome: str
    summary: str
    tools: list[str] = Field(default_factory=list)
    # Phase 16 (ADR-065): references to the business analyses of the turn (capability,
    # status, date ranges, source); never figures, names or entity references.
    analyses: list[dict[str, Any]] = Field(default_factory=list)


class ConversationTurn(BaseModel):
    role: str
    content: str


class MemoryContext(BaseModel):
    """What the agent recalled for one turn. Rendered into the prompt as context only."""

    working: dict[str, Any] = Field(default_factory=dict)
    conversation_summary: str | None = None
    facts: list[ScoredMemory] = Field(default_factory=list)
    knowledge: list[ScoredMemory] = Field(default_factory=list)
    episodes: list[ScoredMemory] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)  # sections that failed (codes only)

    def is_empty(self) -> bool:
        return not (
            self.working
            or self.conversation_summary
            or self.facts
            or self.knowledge
            or self.episodes
        )

    def to_prompt(self, max_chars: int) -> dict[str, Any] | None:
        """Compact, size-bounded JSON-able view for the model, or None if empty."""
        if self.is_empty():
            return None
        budget = [max_chars]

        def take(text: str) -> str | None:
            if budget[0] <= 0:
                return None
            clipped = text if len(text) <= budget[0] else text[: max(0, budget[0] - 1)] + "…"
            budget[0] -= len(clipped)
            return clipped

        def items(hits: list[ScoredMemory]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for hit in hits:
                content = take(hit.record.content)
                if content is None:
                    break
                entry: dict[str, Any] = {
                    "content": content,
                    "relevance": round(hit.score, 3),
                    "basis": memory_basis(hit.record),
                    "recorded_at": hit.record.updated_at.date().isoformat(),
                }
                if hit.record.key:
                    entry["key"] = hit.record.key
                if hit.record.source_ref:
                    entry["source"] = hit.record.source_ref
                analyses = hit.record.metadata.get("analyses")
                if isinstance(analyses, list) and analyses:
                    entry["analyses"] = analyses
                out.append(entry)
            return out

        view: dict[str, Any] = {
            "note": "Recalled memory. Background context only, not a source of truth for "
            "business records; it may be outdated. `basis` says how each item is known; "
            "`analyses` only say which business reads were done, never their figures.",
        }
        if self.conversation_summary and (summary := take(self.conversation_summary)):
            view["conversation_summary"] = summary
        if self.facts:
            view["user_facts"] = items(self.facts)
        if self.knowledge:
            view["knowledge"] = items(self.knowledge)
        if self.episodes:
            view["past_interactions"] = items(self.episodes)
        if self.working:
            view["working_memory"] = self.working
        return view
