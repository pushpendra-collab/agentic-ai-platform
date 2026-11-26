"""Durable agent memory: one table, `memory_item`, for every PostgreSQL-backed memory
kind (ADR-033, ADR-034).

* `kind` discriminates long-term facts, semantic knowledge chunks, episodes,
  procedural playbooks and conversation summaries.
* `tenant_id` is mandatory on every row. `user_id` is `''` for memory shared by a
  whole tenant (for example knowledge documents), or the user's id.
* `embedding` is a pgvector `vector` without a fixed dimension, plus
  `embedding_model`. Searches always filter on the model, so vectors from
  different models are never compared.
* Row-level security (migration 0003) restricts every statement of the runtime
  role to the tenant set by `MemoryRepository.bind_tenant()`. That is defence in
  depth on top of the explicit `tenant_id` filters below.

No business records live here (ADR-006): memory is context, not truth.

`Vector` is a small SQLAlchemy type that exchanges vectors with PostgreSQL as
text (`'[0.1,0.2]'::vector`), so no compiled extension (numpy, pgvector-python)
is needed on locked-down machines.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    and_,
    bindparam,
    cast,
    delete,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, UserDefinedType

from agentic_platform.db.base import Base, TimestampMixin
from agentic_platform.db.tenancy import bind_tenant, enable_maintenance

MEMORY_KINDS = ("long_term", "semantic", "episodic", "procedural", "conversation_summary")
SHARED_USER = ""  # user_id of tenant-wide memory
MAX_RESULTS = 200


def _parse_vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        inner = value.strip()[1:-1]
        return [float(x) for x in inner.split(",")] if inner else []
    return [float(x) for x in value]


class _VectorText(TypeDecorator[list[float]]):
    """Result type of a vector selected as text: parses '[1,2,3]' into floats."""

    impl = Text
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Any) -> list[float] | None:
        return _parse_vector(value)


class Vector(UserDefinedType[list[float]]):
    """pgvector `vector` column, transported as text."""

    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: Any) -> str:
        return "vector" if self.dimensions is None else f"vector({self.dimensions})"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Sequence[float] | None) -> str | None:
            if value is None:
                return None
            return "[" + ",".join(repr(float(x)) for x in value) + "]"

        return process

    def bind_expression(self, bindvalue: Any) -> Any:
        # Send text, cast on the server: the driver needs no codec for `vector`.
        return cast(cast(bindvalue, Text), self)

    def column_expression(self, col: Any) -> Any:
        return cast(col, _VectorText())

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        return _parse_vector


# Let reflection (Alembic autogenerate / drift test) recognise the column type.
ischema_names["vector"] = Vector


class MemoryItem(TimestampMixin, Base):
    __tablename__ = "memory_item"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('long_term', 'semantic', 'episodic', 'procedural', 'conversation_summary')",
            name="kind",
        ),
        CheckConstraint("char_length(content) BETWEEN 1 AND 100000", name="content_length"),
        CheckConstraint("char_length(tenant_id) BETWEEN 1 AND 128", name="tenant_length"),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)", name="embedding_has_model"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_memory_item_scope", "tenant_id", "kind", "user_id", "namespace"),
        Index("ix_memory_item_thread", "tenant_id", "kind", "thread_id"),
        Index("ix_memory_item_source", "tenant_id", "kind", "source_ref"),
        Index("ix_memory_item_expires_at", "expires_at"),
        Index(
            "uq_memory_item_long_term_key",
            "tenant_id",
            "user_id",
            "namespace",
            "key",
            unique=True,
            postgresql_where=text("kind = 'long_term'"),
        ),
        Index(
            "uq_memory_item_procedural_version",
            "tenant_id",
            "namespace",
            "version",
            unique=True,
            postgresql_where=text("kind = 'procedural'"),
        ),
        Index(
            "uq_memory_item_conversation_summary",
            "tenant_id",
            "thread_id",
            unique=True,
            postgresql_where=text("kind = 'conversation_summary'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text, server_default="")
    kind: Mapped[str] = mapped_column(Text)
    namespace: Mapped[str] = mapped_column(Text, server_default="")
    key: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    thread_id: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    source_type: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class MemoryFilter:
    """Typed filter (no free-form SQL). `tenant_id` is mandatory."""

    tenant_id: str
    kinds: tuple[str, ...] = ()
    user_ids: tuple[str, ...] | None = None  # None = any user in the tenant
    namespace: str | None = None
    key: str | None = None
    thread_id: str | None = None
    source_ref: str | None = None
    version: int | None = None
    ids: tuple[uuid.UUID, ...] = ()
    include_expired: bool = False
    limit: int = 50
    newest_first: bool = True

    def conditions(self) -> list[ColumnElement[bool]]:
        c: list[ColumnElement[bool]] = [MemoryItem.tenant_id == self.tenant_id]
        if self.kinds:
            c.append(MemoryItem.kind.in_(self.kinds))
        if self.user_ids is not None:
            c.append(MemoryItem.user_id.in_(self.user_ids))
        for column, value in (
            (MemoryItem.namespace, self.namespace),
            (MemoryItem.key, self.key),
            (MemoryItem.thread_id, self.thread_id),
            (MemoryItem.source_ref, self.source_ref),
            (MemoryItem.version, self.version),
        ):
            if value is not None:
                c.append(column == value)
        if self.ids:
            c.append(MemoryItem.id.in_(self.ids))
        if not self.include_expired:
            c.append(or_(MemoryItem.expires_at.is_(None), MemoryItem.expires_at > func.now()))
        return c


@dataclass(frozen=True)
class UniqueTarget:
    """Which partial unique index an upsert targets."""

    columns: tuple[str, ...]
    kind: str
    update_columns: tuple[str, ...] = field(
        default=(
            "content",
            "metadata",
            "source_type",
            "source_ref",
            "embedding",
            "embedding_model",
            "expires_at",
            "thread_id",
        )
    )


UPSERT_TARGETS = {
    "long_term": UniqueTarget(("tenant_id", "user_id", "namespace", "key"), "long_term"),
    "conversation_summary": UniqueTarget(("tenant_id", "thread_id"), "conversation_summary"),
}


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bind_tenant(self, tenant_id: str) -> None:
        """Set the tenant for row-level security for the rest of this transaction."""
        await bind_tenant(self._session, tenant_id)

    async def enable_maintenance(self) -> None:
        """Cross-tenant access for maintenance jobs (retention purge) in this transaction."""
        await enable_maintenance(self._session, "app.memory_maintenance")

    async def insert(self, rows: Sequence[dict[str, Any]]) -> list[MemoryItem]:
        if not rows:
            return []
        result = await self._session.scalars(
            insert(MemoryItem).values(list(rows)).returning(MemoryItem)
        )
        return list(result.all())

    async def upsert(self, row: dict[str, Any]) -> MemoryItem:
        target = UPSERT_TARGETS[row["kind"]]
        base = insert(MemoryItem).values(**row)
        values: dict[str, Any] = {name: base.excluded[name] for name in target.update_columns}
        values["updated_at"] = func.now()
        stmt = base.on_conflict_do_update(
            index_elements=list(target.columns),
            index_where=text(f"kind = '{target.kind}'"),
            set_=values,
        ).returning(MemoryItem)
        item: MemoryItem = (await self._session.scalars(stmt)).one()
        return item

    async def find(self, flt: MemoryFilter) -> list[MemoryItem]:
        order = MemoryItem.created_at.desc() if flt.newest_first else MemoryItem.created_at
        stmt = (
            select(MemoryItem)
            .where(and_(*flt.conditions()))
            .order_by(order, MemoryItem.id)
            .limit(max(1, min(flt.limit, MAX_RESULTS)))
        )
        return list((await self._session.scalars(stmt)).all())

    async def update(self, flt: MemoryFilter, values: dict[str, Any]) -> list[MemoryItem]:
        values = {**values, "updated_at": func.now()}
        stmt = (
            update(MemoryItem)
            .where(and_(*flt.conditions()))
            .values(**values)
            .returning(MemoryItem)
            .execution_options(synchronize_session=False)
        )
        return list((await self._session.scalars(stmt)).all())

    async def delete(self, flt: MemoryFilter) -> int:
        stmt = (
            delete(MemoryItem)
            .where(and_(*flt.conditions()))
            .returning(MemoryItem.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self._session.execute(stmt)).all())

    async def max_version(self, tenant_id: str, kind: str, namespace: str) -> int:
        stmt = select(func.max(MemoryItem.version)).where(
            MemoryItem.tenant_id == tenant_id,
            MemoryItem.kind == kind,
            MemoryItem.namespace == namespace,
        )
        return int(await self._session.scalar(stmt) or 0)

    async def search(
        self, flt: MemoryFilter, query_vector: Sequence[float], model: str, k: int
    ) -> list[tuple[MemoryItem, float]]:
        """Nearest rows by cosine distance among rows embedded with `model`."""
        distance = MemoryItem.embedding.op("<=>", return_type=Float)(
            bindparam("query_vector", list(query_vector), type_=Vector())
        )
        stmt = (
            select(MemoryItem, distance.label("distance"))
            .where(
                and_(
                    *flt.conditions(),
                    MemoryItem.embedding.is_not(None),
                    MemoryItem.embedding_model == model,
                    func.vector_dims(MemoryItem.embedding) == len(query_vector),
                )
            )
            .order_by(distance)
            .limit(max(1, min(k, MAX_RESULTS)))
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], float(row[1])) for row in rows]

    async def purge_expired(self) -> int:
        stmt = (
            delete(MemoryItem)
            .where(MemoryItem.expires_at <= func.now())
            .returning(MemoryItem.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self._session.execute(stmt)).all())

    async def purge_expired_batch(self, limit: int) -> dict[str, int]:
        """Delete at most `limit` expired rows (oldest expiry first) and count them by
        kind. Rows locked by a concurrent purge are skipped, not waited for."""
        doomed = (
            select(MemoryItem.id)
            .where(MemoryItem.expires_at <= func.now())
            .order_by(MemoryItem.expires_at)
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
        stmt = (
            delete(MemoryItem)
            .where(MemoryItem.id.in_(doomed))
            .returning(MemoryItem.kind)
            .execution_options(synchronize_session=False)
        )
        counts: dict[str, int] = {}
        for (kind,) in (await self._session.execute(stmt)).all():
            counts[kind] = counts.get(kind, 0) + 1
        return counts
