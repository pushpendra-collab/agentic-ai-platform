"""Storage interfaces (ports). Memory types depend only on these; adapters implement
them, so any store can be replaced without touching memory logic (ADR-033).

    MemoryStore        durable records    PostgresMemoryStore | InMemoryMemoryStore
    VectorStore        similarity search  PostgresMemoryStore (pgvector) | InMemoryMemoryStore
    Embedder           text -> vectors    HashingEmbedder | VoyageEmbedder
    ConversationSource history by thread  CheckpointConversationSource (LangGraph checkpointer)
    (working memory)   TTL scratch        cache.TemporaryStateStore (Redis | in-memory)

Every method takes a `MemoryScope`; adapters must never return or modify
records of another tenant, and must apply the user rules in `MemoryScope`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from agentic_platform.memory.models import (
    ConversationTurn,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
    ScoredMemory,
    VectorQuery,
)


class MemoryStore(Protocol):
    name: str

    async def insert(self, scope: MemoryScope, items: Sequence[NewMemory]) -> list[MemoryRecord]:
        """Insert new records (one transaction)."""
        ...

    async def upsert(self, scope: MemoryScope, item: NewMemory) -> MemoryRecord:
        """Insert or replace by the kind's natural key: long-term (namespace, key),
        conversation summary (thread_id). Other kinds are rejected."""
        ...

    async def find(self, scope: MemoryScope, query: MemoryQuery) -> list[MemoryRecord]: ...

    async def update(
        self, scope: MemoryScope, memory_id: uuid.UUID, changes: dict[str, Any]
    ) -> MemoryRecord | None:
        """Change content/metadata/expires_at (and embedding) of one of the scope's records."""
        ...

    async def delete(self, scope: MemoryScope, query: MemoryQuery) -> int:
        """Delete the scope's matching records (`query.kinds` is required)."""
        ...

    async def replace(
        self, scope: MemoryScope, remove: MemoryQuery, items: Sequence[NewMemory]
    ) -> list[MemoryRecord]:
        """Atomically delete `remove` matches and insert `items` (document re-ingest)."""
        ...

    async def next_version(self, scope: MemoryScope, kind: str, namespace: str) -> int: ...

    async def purge_expired(self) -> int:
        """Retention job: delete expired records of all tenants."""
        ...

    async def purge_expired_batch(self, limit: int) -> dict[str, int]:
        """Bounded retention step (Phase 6 maintenance): delete at most `limit` expired
        records of all tenants, in one transaction. Returns deleted counts by kind."""
        ...


class VectorStore(Protocol):
    name: str

    async def search(self, scope: MemoryScope, query: VectorQuery) -> list[ScoredMemory]: ...


class Embedder(Protocol):
    """Turns text into vectors. `model_id` is stored with every vector; vectors of
    different models are never compared."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class ConversationSource(Protocol):
    async def turns(self, thread_id: str, limit: int) -> list[ConversationTurn]: ...

    async def delete(self, thread_id: str) -> None: ...
