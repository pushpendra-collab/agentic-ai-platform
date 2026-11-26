"""Episodic memory: what happened in past agent turns (ADR-036).

After each finished turn the agent records an episode (intent, outcome, a
one-line summary of the request, tools used). Episodes are embedded, so the
agent can recall similar past interactions ("last time you asked about X ...").

Episodes belong to the scope's user and expire after
MEMORY_EPISODIC_RETENTION_DAYS (default 90). They describe interactions; they
are not a record of business facts.

Phase 16 (ADR-065): an episode of a business turn also keeps `analyses`, references
to the reads it made (capability, status, date ranges, source adapter, TEST data or
not), so a later session can know that "October against October last year" was looked
at. Never the figures: those are read again from the ERP every time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic_platform.memory.models import (
    Episode,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
    ScoredMemory,
    VectorQuery,
)
from agentic_platform.memory.ports import Embedder, MemoryStore, VectorStore

NAMESPACE = "agent_turn"
MAX_SUMMARY_CHARS = 1000


class EpisodicMemory:
    def __init__(
        self,
        store: MemoryStore,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        retention_days: int,
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._embedder = embedder
        self._retention = timedelta(days=retention_days)

    async def record(self, scope: MemoryScope, episode: Episode) -> MemoryRecord:
        summary = " ".join(episode.summary.split())[:MAX_SUMMARY_CHARS] or "(no summary)"
        content = f"{summary} (outcome: {episode.outcome})"
        embedding = await self._embedder.embed_query(summary)
        (record,) = await self._store.insert(
            scope,
            [
                NewMemory(
                    kind=MemoryKind.EPISODIC,
                    namespace=NAMESPACE,
                    content=content,
                    thread_id=episode.thread_id,
                    metadata={
                        "request_id": episode.request_id,
                        "intent": episode.intent,
                        "domain": episode.domain,
                        "outcome": episode.outcome,
                        "tools": episode.tools,
                        # Phase 16: references only (business/focus.py analysis_references).
                        **({"analyses": episode.analyses} if episode.analyses else {}),
                    },
                    source_type="agent_turn",
                    source_ref=episode.request_id,
                    embedding=embedding,
                    embedding_model=self._embedder.model_id,
                    expires_at=datetime.now(UTC) + self._retention,
                )
            ],
        )
        return record

    async def recall(
        self,
        scope: MemoryScope,
        query: str,
        *,
        k: int = 5,
        min_score: float = -1.0,
        query_embedding: list[float] | None = None,
    ) -> list[ScoredMemory]:
        embedding = query_embedding or await self._embedder.embed_query(query)
        return await self._vectors.search(
            scope,
            VectorQuery(
                embedding=embedding,
                model=self._embedder.model_id,
                kinds=(MemoryKind.EPISODIC,),
                k=k,
                min_score=min_score,
                include_shared=False,  # episodes are personal
            ),
        )

    async def recent(
        self, scope: MemoryScope, *, limit: int = 10, thread_id: str | None = None
    ) -> list[MemoryRecord]:
        return await self._store.find(
            scope,
            MemoryQuery(kinds=(MemoryKind.EPISODIC,), thread_id=thread_id, limit=limit),
        )

    async def forget(self, scope: MemoryScope, *, thread_id: str | None = None) -> int:
        """Delete the user's episodes (of one thread, or all)."""
        return await self._store.delete(
            scope, MemoryQuery(kinds=(MemoryKind.EPISODIC,), thread_id=thread_id)
        )
