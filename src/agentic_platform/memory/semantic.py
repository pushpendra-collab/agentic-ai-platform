"""Semantic memory: knowledge the agent can look things up in (ADR-034).

Documents (SOPs, policies, FAQs, reference notes) are chunked, embedded and
stored as `semantic` records with a pointer back to their source
(`source_ref`). Retrieval is similarity search through the `VectorStore`
(pgvector in PostgreSQL).

**Never transactional business truth.** Only knowledge source types are
accepted (`KNOWLEDGE_SOURCE_TYPES`). Hits are context: anything a business
decision depends on must be re-read from the system of record through a tool
(ADR-006, ADR-008).

Re-ingesting a `source_ref` atomically replaces its previous chunks.
"""

from __future__ import annotations

from typing import Any

from agentic_platform.core.errors import MemoryPolicyError
from agentic_platform.memory.chunking import chunk_text
from agentic_platform.memory.long_term import check_name
from agentic_platform.memory.models import (
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
    ScoredMemory,
    VectorQuery,
)
from agentic_platform.memory.ports import Embedder, MemoryStore, VectorStore

KNOWLEDGE_SOURCE_TYPES = frozenset({"document", "policy", "procedure", "faq", "reference", "note"})
DEFAULT_NAMESPACE = "knowledge"
MAX_DOCUMENT_CHARS = 2_000_000


class SemanticMemory:
    def __init__(
        self,
        store: MemoryStore,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        chunk_chars: int,
        overlap_chars: int,
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._embedder = embedder
        self._chunk_chars = chunk_chars
        self._overlap = overlap_chars

    async def ingest(
        self,
        scope: MemoryScope,
        *,
        source_ref: str,
        text: str,
        source_type: str = "document",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        shared: bool = True,
    ) -> list[MemoryRecord]:
        """Chunk, embed and store a document; replaces earlier chunks of `source_ref`.

        `shared=True` (default) makes it tenant knowledge, visible to every user of
        the tenant; `shared=False` keeps it private to the scope's user.
        """
        if source_type not in KNOWLEDGE_SOURCE_TYPES:
            raise MemoryPolicyError(
                f"Source type '{source_type}' is not knowledge.",
                public_message="Only knowledge documents (policies, procedures, FAQs, reference "
                "notes) can be stored in semantic memory, not business records.",
            )
        check_name("source reference", source_ref)
        check_name("namespace", namespace)
        if not text.strip() or len(text) > MAX_DOCUMENT_CHARS:
            raise MemoryPolicyError(f"Document must be 1-{MAX_DOCUMENT_CHARS} characters.")

        chunks = chunk_text(text, max_chars=self._chunk_chars, overlap_chars=self._overlap)
        vectors = await self._embedder.embed_documents(chunks)
        items = [
            NewMemory(
                kind=MemoryKind.SEMANTIC,
                namespace=namespace,
                content=chunk,
                metadata={
                    **(metadata or {}),
                    "title": title,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                },
                source_type=source_type,
                source_ref=source_ref,
                embedding=vector,
                embedding_model=self._embedder.model_id,
                shared=shared,
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        owner = MemoryScope(scope.tenant_id, None) if shared else scope
        return await self._store.replace(
            owner,
            MemoryQuery(kinds=(MemoryKind.SEMANTIC,), source_ref=source_ref, namespace=namespace),
            items,
        )

    async def search(
        self,
        scope: MemoryScope,
        query: str,
        *,
        k: int = 5,
        min_score: float = -1.0,
        namespace: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[ScoredMemory]:
        """Most similar chunks among the tenant's shared and the user's own knowledge."""
        embedding = query_embedding or await self._embedder.embed_query(query)
        return await self._vectors.search(
            scope,
            VectorQuery(
                embedding=embedding,
                model=self._embedder.model_id,
                kinds=(MemoryKind.SEMANTIC,),
                k=k,
                min_score=min_score,
                namespace=namespace,
                include_shared=True,
            ),
        )

    async def chunks(
        self, scope: MemoryScope, source_ref: str, *, limit: int = 200
    ) -> list[MemoryRecord]:
        found = await self._store.find(
            scope,
            MemoryQuery(
                kinds=(MemoryKind.SEMANTIC,),
                source_ref=source_ref,
                include_shared=True,
                limit=limit,
                newest_first=False,
            ),
        )
        return sorted(found, key=lambda r: int(r.metadata.get("chunk_index", 0)))

    async def delete_source(
        self, scope: MemoryScope, source_ref: str, *, shared: bool = True
    ) -> int:
        owner = MemoryScope(scope.tenant_id, None) if shared else scope
        return await self._store.delete(
            owner, MemoryQuery(kinds=(MemoryKind.SEMANTIC,), source_ref=source_ref)
        )
