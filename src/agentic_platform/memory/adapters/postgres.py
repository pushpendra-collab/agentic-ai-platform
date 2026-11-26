"""PostgreSQL + pgvector adapter: implements `MemoryStore` and `VectorStore` (ADR-034).

Every operation runs in one unit of work bound to the scope's tenant for
row-level security (`Database.unit_of_work(tenant_id=...)`; free for reads), and
also filters on `tenant_id` explicitly. Reads run in read-only transactions.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from typing import Any

from agentic_platform.core.errors import MemoryPolicyError
from agentic_platform.db.engine import Database
from agentic_platform.db.memory import (
    UPSERT_TARGETS,
    MemoryFilter,
    MemoryItem,
    MemoryRepository,
)
from agentic_platform.db.unit_of_work import UnitOfWork
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


def to_row(scope: MemoryScope, item: NewMemory) -> dict[str, Any]:
    return {
        "tenant_id": scope.tenant_id,
        "user_id": "" if item.shared else scope.user_key,
        "kind": item.kind.value,
        "namespace": item.namespace,
        "key": item.key,
        "version": item.version,
        "thread_id": item.thread_id,
        "content": item.content,
        "metadata": item.metadata,
        "source_type": item.source_type,
        "source_ref": item.source_ref,
        "embedding": item.embedding,
        "embedding_model": item.embedding_model if item.embedding is not None else None,
        "expires_at": item.expires_at,
    }


def to_record(item: MemoryItem) -> MemoryRecord:
    return MemoryRecord(
        id=item.id,
        kind=MemoryKind(item.kind),
        tenant_id=item.tenant_id,
        user_id=item.user_id or None,
        namespace=item.namespace,
        key=item.key,
        version=item.version,
        thread_id=item.thread_id,
        content=item.content,
        metadata=item.metadata_,
        source_type=item.source_type,
        source_ref=item.source_ref,
        embedding_model=item.embedding_model,
        created_at=item.created_at,
        updated_at=item.updated_at,
        expires_at=item.expires_at,
    )


def to_filter(scope: MemoryScope, query: MemoryQuery) -> MemoryFilter:
    return MemoryFilter(
        tenant_id=scope.tenant_id,
        kinds=tuple(k.value for k in query.kinds),
        user_ids=scope.readable_user_keys(include_shared=query.include_shared),
        namespace=query.namespace,
        key=query.key,
        thread_id=query.thread_id,
        source_ref=query.source_ref,
        version=query.version,
        ids=query.ids,
        include_expired=query.include_expired,
        limit=query.limit,
        newest_first=query.newest_first,
    )


class PostgresMemoryStore:
    name = "postgres"

    def __init__(self, database: Database) -> None:
        self._database = database

    def _uow(self, scope: MemoryScope, *, read_only: bool = False) -> UnitOfWork:
        return self._database.unit_of_work(read_only=read_only, tenant_id=scope.tenant_id)

    async def insert(self, scope: MemoryScope, items: Sequence[NewMemory]) -> list[MemoryRecord]:
        async with self._uow(scope) as uow:
            repo = MemoryRepository(uow.session)
            rows = await repo.insert([to_row(scope, item) for item in items])
            return [to_record(r) for r in rows]

    async def upsert(self, scope: MemoryScope, item: NewMemory) -> MemoryRecord:
        if item.kind.value not in UPSERT_TARGETS:
            raise MemoryPolicyError(f"Kind {item.kind} has no natural key to upsert on.")
        async with self._uow(scope) as uow:
            repo = MemoryRepository(uow.session)
            return to_record(await repo.upsert(to_row(scope, item)))

    async def find(self, scope: MemoryScope, query: MemoryQuery) -> list[MemoryRecord]:
        async with self._uow(scope, read_only=True) as uow:
            repo = MemoryRepository(uow.session)
            return [to_record(r) for r in await repo.find(to_filter(scope, query))]

    async def update(
        self, scope: MemoryScope, memory_id: uuid.UUID, changes: dict[str, Any]
    ) -> MemoryRecord | None:
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise MemoryPolicyError(f"Fields cannot be updated: {sorted(unknown)}")
        flt = to_filter(scope, MemoryQuery(ids=(memory_id,), include_expired=True))
        async with self._uow(scope) as uow:
            repo = MemoryRepository(uow.session)
            rows = await repo.update(flt, changes)
            return to_record(rows[0]) if rows else None

    async def delete(self, scope: MemoryScope, query: MemoryQuery) -> int:
        if not query.kinds:
            raise MemoryPolicyError("Deleting memory requires at least one kind.")
        async with self._uow(scope) as uow:
            repo = MemoryRepository(uow.session)
            return await repo.delete(_unbounded(to_filter(scope, query)))

    async def replace(
        self, scope: MemoryScope, remove: MemoryQuery, items: Sequence[NewMemory]
    ) -> list[MemoryRecord]:
        if not remove.kinds:
            raise MemoryPolicyError("Replacing memory requires at least one kind.")
        async with self._uow(scope) as uow:
            repo = MemoryRepository(uow.session)
            await repo.delete(_unbounded(to_filter(scope, remove)))
            rows = await repo.insert([to_row(scope, item) for item in items])
            return [to_record(r) for r in rows]

    async def next_version(self, scope: MemoryScope, kind: str, namespace: str) -> int:
        async with self._uow(scope, read_only=True) as uow:
            repo = MemoryRepository(uow.session)
            return await repo.max_version(scope.tenant_id, kind, namespace) + 1

    async def purge_expired(self) -> int:
        async with self._database.unit_of_work() as uow:
            repo = MemoryRepository(uow.session)
            await repo.enable_maintenance()
            return await repo.purge_expired()

    async def purge_expired_batch(self, limit: int) -> dict[str, int]:
        async with self._database.unit_of_work() as uow:
            repo = MemoryRepository(uow.session)
            await repo.enable_maintenance()
            return await repo.purge_expired_batch(limit)

    async def search(self, scope: MemoryScope, query: VectorQuery) -> list[ScoredMemory]:
        flt = MemoryFilter(
            tenant_id=scope.tenant_id,
            kinds=tuple(k.value for k in query.kinds),
            user_ids=scope.readable_user_keys(include_shared=query.include_shared),
            namespace=query.namespace,
        )
        async with self._uow(scope, read_only=True) as uow:
            repo = MemoryRepository(uow.session)
            rows = await repo.search(flt, query.embedding, query.model, query.k)
            # Convert inside the transaction: ending a read-only unit of work rolls
            # back, which expires ORM instances.
            hits = [
                ScoredMemory(record=to_record(item), score=1.0 - distance)
                for item, distance in rows
            ]
        return [h for h in hits if h.score >= query.min_score]


def _unbounded(flt: MemoryFilter) -> MemoryFilter:
    """Deletes are never paged and include expired rows."""
    return dataclasses.replace(flt, include_expired=True)
