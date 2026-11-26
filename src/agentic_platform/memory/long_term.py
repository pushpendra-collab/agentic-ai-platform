"""Long-term memory: durable facts and preferences about a user or a tenant.

Examples: "prefers replies in Hindi", "reports are due on Fridays". Each fact has
a (namespace, key) and is updated in place, so remembering the same key again
replaces the fact. Facts are embedded so the agent can recall relevant ones.

A fact belongs to the scope's user, or to the whole tenant with `shared=True`
(or when the scope has no user). It never stores business records: an amount,
status or balance must be read fresh through a tool (ARCHITECTURE.md § B.4).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from agentic_platform.core.errors import MemoryPolicyError
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

_NAME = re.compile(r"^[A-Za-z0-9_.:\-]{1,100}$")


def check_name(label: str, value: str) -> str:
    if not _NAME.fullmatch(value):
        raise MemoryPolicyError(
            f"Invalid {label}.",
            public_message=f"The {label} must be 1-100 characters of letters, digits, '_.:-'.",
        )
    return value


class LongTermMemory:
    def __init__(
        self,
        store: MemoryStore,
        vectors: VectorStore,
        embedder: Embedder,
        *,
        max_chars: int,
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._embedder = embedder
        self._max_chars = max_chars

    async def remember(
        self,
        scope: MemoryScope,
        namespace: str,
        key: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        ttl: timedelta | None = None,
        shared: bool = False,
        source_type: str = "user",
    ) -> MemoryRecord:
        """Create or replace the fact (namespace, key)."""
        content = content.strip()
        if not content or len(content) > self._max_chars:
            raise MemoryPolicyError(f"Fact must be 1-{self._max_chars} characters.")
        embedding = await self._embedder.embed_query(content)
        return await self._store.upsert(
            scope,
            NewMemory(
                kind=MemoryKind.LONG_TERM,
                namespace=check_name("namespace", namespace),
                key=check_name("key", key),
                content=content,
                metadata=metadata or {},
                source_type=source_type,
                embedding=embedding,
                embedding_model=self._embedder.model_id,
                expires_at=datetime.now(UTC) + ttl if ttl else None,
                shared=shared,
            ),
        )

    async def get(
        self, scope: MemoryScope, namespace: str, key: str, *, shared: bool = False
    ) -> MemoryRecord | None:
        found = await self._store.find(
            MemoryScope(scope.tenant_id, None) if shared else scope,
            MemoryQuery(kinds=(MemoryKind.LONG_TERM,), namespace=namespace, key=key, limit=1),
        )
        return found[0] if found else None

    async def list_facts(
        self, scope: MemoryScope, *, namespace: str | None = None, limit: int = 50
    ) -> list[MemoryRecord]:
        """The user's facts and the tenant's shared facts."""
        return await self._store.find(
            scope,
            MemoryQuery(
                kinds=(MemoryKind.LONG_TERM,), namespace=namespace, include_shared=True, limit=limit
            ),
        )

    async def forget(
        self, scope: MemoryScope, namespace: str, key: str, *, shared: bool = False
    ) -> bool:
        target = MemoryScope(scope.tenant_id, None) if shared else scope
        deleted = await self._store.delete(
            target, MemoryQuery(kinds=(MemoryKind.LONG_TERM,), namespace=namespace, key=key)
        )
        return deleted > 0

    async def search(
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
                kinds=(MemoryKind.LONG_TERM,),
                k=k,
                min_score=min_score,
                include_shared=True,
            ),
        )
