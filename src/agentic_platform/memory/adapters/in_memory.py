"""Process-local `MemoryStore` + `VectorStore` (ADR-033).

Used when DATABASE_URL is not set (memory then lasts for the process only) and
as the unit-test double. It follows the same rules as the PostgreSQL adapter:
tenant and user isolation, natural-key upserts, expiry, model-filtered cosine
search.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from agentic_platform.core.errors import DatabaseConflictError, MemoryPolicyError
from agentic_platform.memory.models import (
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
    ScoredMemory,
    VectorQuery,
)

_UPDATABLE = {"content", "metadata", "expires_at", "embedding", "embedding_model"}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class _Row:
    def __init__(self, record: MemoryRecord, embedding: list[float] | None, seq: int) -> None:
        self.record = record
        self.embedding = embedding
        self.seq = seq  # insertion order: breaks created_at ties (coarse Windows clock)


class InMemoryMemoryStore:
    name = "memory"

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._rows: dict[uuid.UUID, _Row] = {}
        self._seq = 0

    # -- helpers -------------------------------------------------------------------

    def _new(self, scope: MemoryScope, item: NewMemory) -> _Row:
        now = self._clock()
        record = MemoryRecord(
            id=uuid.uuid4(),
            kind=item.kind,
            tenant_id=scope.tenant_id,
            user_id=None if item.shared else scope.user_id,
            namespace=item.namespace,
            key=item.key,
            version=item.version,
            thread_id=item.thread_id,
            content=item.content,
            metadata=dict(item.metadata),
            source_type=item.source_type,
            source_ref=item.source_ref,
            embedding_model=item.embedding_model if item.embedding is not None else None,
            created_at=now,
            updated_at=now,
            expires_at=item.expires_at,
        )
        self._seq += 1
        embedding = list(item.embedding) if item.embedding is not None else None
        return _Row(record, embedding, self._seq)

    def _matches(
        self, row: _Row, scope: MemoryScope, query: MemoryQuery, *, include_expired: bool
    ) -> bool:
        r = row.record
        if r.tenant_id != scope.tenant_id:
            return False
        if (r.user_id or "") not in scope.readable_user_keys(include_shared=query.include_shared):
            return False
        if query.kinds and r.kind not in query.kinds:
            return False
        for attr in ("namespace", "key", "thread_id", "source_ref", "version"):
            wanted = getattr(query, attr)
            if wanted is not None and getattr(r, attr) != wanted:
                return False
        if query.ids and r.id not in query.ids:
            return False
        return include_expired or r.expires_at is None or r.expires_at > self._clock()

    def _natural_key(self, record: MemoryRecord) -> tuple[Any, ...] | None:
        if record.kind is MemoryKind.LONG_TERM:
            return ("lt", record.tenant_id, record.user_id or "", record.namespace, record.key)
        if record.kind is MemoryKind.CONVERSATION_SUMMARY:
            return ("cs", record.tenant_id, record.thread_id)
        if record.kind is MemoryKind.PROCEDURAL:
            return ("pr", record.tenant_id, record.namespace, record.version)
        return None

    def _existing(self, record: MemoryRecord) -> _Row | None:
        key = self._natural_key(record)
        if key is None:
            return None
        for row in self._rows.values():
            if self._natural_key(row.record) == key:
                return row
        return None

    # -- MemoryStore -------------------------------------------------------------------

    async def insert(self, scope: MemoryScope, items: Sequence[NewMemory]) -> list[MemoryRecord]:
        rows = [self._new(scope, item) for item in items]
        for row in rows:
            if self._existing(row.record) is not None:
                raise DatabaseConflictError("Unique memory key already exists.")
        for row in rows:
            self._rows[row.record.id] = row
        return [row.record for row in rows]

    async def upsert(self, scope: MemoryScope, item: NewMemory) -> MemoryRecord:
        if item.kind not in (MemoryKind.LONG_TERM, MemoryKind.CONVERSATION_SUMMARY):
            raise MemoryPolicyError(f"Kind {item.kind} has no natural key to upsert on.")
        new = self._new(scope, item)
        existing = self._existing(new.record)
        if existing is None:
            self._rows[new.record.id] = new
            return new.record
        updated = existing.record.model_copy(
            update={
                "content": new.record.content,
                "metadata": new.record.metadata,
                "source_type": new.record.source_type,
                "source_ref": new.record.source_ref,
                "embedding_model": new.record.embedding_model,
                "expires_at": new.record.expires_at,
                "thread_id": new.record.thread_id,
                "updated_at": self._clock(),
            }
        )
        self._rows[updated.id] = _Row(updated, new.embedding, existing.seq)
        return updated

    async def find(self, scope: MemoryScope, query: MemoryQuery) -> list[MemoryRecord]:
        rows = [
            row
            for row in self._rows.values()
            if self._matches(row, scope, query, include_expired=query.include_expired)
        ]
        rows.sort(key=lambda r: (r.record.created_at, r.seq), reverse=query.newest_first)
        return [row.record for row in rows[: query.limit]]

    async def update(
        self, scope: MemoryScope, memory_id: uuid.UUID, changes: dict[str, Any]
    ) -> MemoryRecord | None:
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise MemoryPolicyError(f"Fields cannot be updated: {sorted(unknown)}")
        row = self._rows.get(memory_id)
        query = MemoryQuery(ids=(memory_id,))
        if row is None or not self._matches(row, scope, query, include_expired=True):
            return None
        embedding = changes.get("embedding", row.embedding)
        fields = {k: v for k, v in changes.items() if k in ("content", "metadata", "expires_at")}
        if "embedding_model" in changes:
            fields["embedding_model"] = changes["embedding_model"]
        updated = row.record.model_copy(update={**fields, "updated_at": self._clock()})
        self._rows[memory_id] = _Row(updated, embedding, row.seq)
        return updated

    async def delete(self, scope: MemoryScope, query: MemoryQuery) -> int:
        if not query.kinds:
            raise MemoryPolicyError("Deleting memory requires at least one kind.")
        doomed = [
            rid
            for rid, row in self._rows.items()
            if self._matches(row, scope, query, include_expired=True)
        ]
        for rid in doomed:
            del self._rows[rid]
        return len(doomed)

    async def replace(
        self, scope: MemoryScope, remove: MemoryQuery, items: Sequence[NewMemory]
    ) -> list[MemoryRecord]:
        await self.delete(scope, remove)
        return await self.insert(scope, items)

    async def next_version(self, scope: MemoryScope, kind: str, namespace: str) -> int:
        versions = [
            row.record.version
            for row in self._rows.values()
            if row.record.tenant_id == scope.tenant_id
            and row.record.kind.value == kind
            and row.record.namespace == namespace
        ]
        return max(versions, default=0) + 1

    async def purge_expired(self) -> int:
        now = self._clock()
        doomed = [
            rid
            for rid, row in self._rows.items()
            if row.record.expires_at is not None and row.record.expires_at <= now
        ]
        for rid in doomed:
            del self._rows[rid]
        return len(doomed)

    async def purge_expired_batch(self, limit: int) -> dict[str, int]:
        now = self._clock()
        expired = sorted(
            (
                (row.record.expires_at, rid)
                for rid, row in self._rows.items()
                if row.record.expires_at is not None and row.record.expires_at <= now
            ),
            key=lambda item: item[0],
        )
        counts: dict[str, int] = {}
        for _, rid in expired[: max(1, limit)]:
            kind = self._rows.pop(rid).record.kind.value
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    # -- VectorStore -------------------------------------------------------------------

    async def search(self, scope: MemoryScope, query: VectorQuery) -> list[ScoredMemory]:
        filt = MemoryQuery(
            kinds=query.kinds, namespace=query.namespace, include_shared=query.include_shared
        )
        hits = [
            ScoredMemory(record=row.record, score=cosine(row.embedding, query.embedding))
            for row in self._rows.values()
            if row.embedding is not None
            and row.record.embedding_model == query.model
            and len(row.embedding) == len(query.embedding)
            and self._matches(row, scope, filt, include_expired=False)
        ]
        hits = [h for h in hits if h.score >= query.min_score]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: query.k]
