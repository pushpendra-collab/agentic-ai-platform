"""Storage for LangGraph checkpoints (ADR-009, ADR-027).

Three tables, in the same shape as LangGraph's own PostgreSQL saver so the data
model is familiar, but prefixed `agent_` so they never collide with it:

* `agent_checkpoints`: one row per checkpoint (serialized checkpoint without
  channel values, plus JSONB metadata for filtering).
* `agent_checkpoint_blobs`: channel values, stored once per (channel, version)
  and shared by every checkpoint that references that version.
* `agent_checkpoint_writes`: pending writes recorded by tasks between checkpoints.

Values are opaque serializer output (`type` + bytes); this layer never inspects
them. Phase 5: thread ids are tenant-namespaced storage keys (`<tenant>/<thread>`),
and row-level security (migration 0004) only shows the runtime role rows whose
key prefix is the tenant bound for the transaction. The repository does data
access only; `agents/checkpointer.py` adapts it to LangGraph's
`BaseCheckpointSaver` contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Integer,
    LargeBinary,
    Text,
    delete,
    func,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentic_platform.db.base import Base

# Checkpoint ids are UUIDv6 strings, ordered by time. Byte-wise ("C") collation
# makes PostgreSQL order them exactly as LangGraph does (Python string order).
_ID = Text(collation="C")

MAX_LIST_LIMIT = 1000


class CheckpointRow(Base):
    __tablename__ = "agent_checkpoints"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(_ID, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(_ID)
    checkpoint_type: Mapped[str] = mapped_column(Text)
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary)
    # `metadata` is reserved on declarative classes, hence the attribute name.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckpointBlobRow(Base):
    __tablename__ = "agent_checkpoint_blobs"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    value_type: Mapped[str] = mapped_column(Text)
    # NULL when value_type is "empty" (the channel had no value at that version).
    value: Mapped[bytes | None] = mapped_column(LargeBinary)


class CheckpointWriteRow(Base):
    __tablename__ = "agent_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(_ID, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(Text)
    value: Mapped[bytes] = mapped_column(LargeBinary)
    task_path: Mapped[str] = mapped_column(Text, server_default="")


@dataclass(frozen=True)
class CheckpointQuery:
    """Typed filter for `CheckpointRepository.list_checkpoints` (no free-form SQL)."""

    thread_id: str | None = None
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None
    before_checkpoint_id: str | None = None
    metadata_contains: Mapping[str, Any] | None = None
    limit: int | None = None


class CheckpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- reads -------------------------------------------------------------------------

    async def get_checkpoint(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str | None
    ) -> CheckpointRow | None:
        """The given checkpoint, or the latest in the namespace when `checkpoint_id` is None."""
        stmt = select(CheckpointRow).where(
            CheckpointRow.thread_id == thread_id, CheckpointRow.checkpoint_ns == checkpoint_ns
        )
        if checkpoint_id is not None:
            stmt = stmt.where(CheckpointRow.checkpoint_id == checkpoint_id)
        else:
            stmt = stmt.order_by(CheckpointRow.checkpoint_id.desc()).limit(1)
        row: CheckpointRow | None = await self._session.scalar(stmt)
        return row

    async def list_checkpoints(self, query: CheckpointQuery) -> Sequence[CheckpointRow]:
        """Newest first. `limit` is capped at MAX_LIST_LIMIT."""
        stmt = select(CheckpointRow)
        if query.thread_id is not None:
            stmt = stmt.where(CheckpointRow.thread_id == query.thread_id)
        if query.checkpoint_ns is not None:
            stmt = stmt.where(CheckpointRow.checkpoint_ns == query.checkpoint_ns)
        if query.checkpoint_id is not None:
            stmt = stmt.where(CheckpointRow.checkpoint_id == query.checkpoint_id)
        if query.before_checkpoint_id is not None:
            stmt = stmt.where(CheckpointRow.checkpoint_id < query.before_checkpoint_id)
        if query.metadata_contains:
            stmt = stmt.where(CheckpointRow.metadata_.contains(dict(query.metadata_contains)))
        limit = MAX_LIST_LIMIT if query.limit is None else max(0, min(query.limit, MAX_LIST_LIMIT))
        stmt = stmt.order_by(
            CheckpointRow.thread_id,
            CheckpointRow.checkpoint_ns,
            CheckpointRow.checkpoint_id.desc(),
        ).limit(limit)
        return (await self._session.scalars(stmt)).all()

    async def get_blobs(
        self, thread_id: str, checkpoint_ns: str, versions: Mapping[str, str]
    ) -> Sequence[CheckpointBlobRow]:
        if not versions:
            return []
        stmt = select(CheckpointBlobRow).where(
            CheckpointBlobRow.thread_id == thread_id,
            CheckpointBlobRow.checkpoint_ns == checkpoint_ns,
            tuple_(CheckpointBlobRow.channel, CheckpointBlobRow.version).in_(
                list(versions.items())
            ),
        )
        return (await self._session.scalars(stmt)).all()

    async def get_writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> Sequence[CheckpointWriteRow]:
        stmt = (
            select(CheckpointWriteRow)
            .where(
                CheckpointWriteRow.thread_id == thread_id,
                CheckpointWriteRow.checkpoint_ns == checkpoint_ns,
                CheckpointWriteRow.checkpoint_id == checkpoint_id,
            )
            .order_by(
                CheckpointWriteRow.task_path,
                CheckpointWriteRow.task_id,
                CheckpointWriteRow.idx,
            )
        )
        return (await self._session.scalars(stmt)).all()

    # -- writes (never commit; the unit of work does) ----------------------------------

    async def upsert_checkpoint(self, row: Mapping[str, Any]) -> None:
        stmt = insert(CheckpointRow).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["thread_id", "checkpoint_ns", "checkpoint_id"],
            set_={
                "checkpoint_type": stmt.excluded.checkpoint_type,
                "checkpoint": stmt.excluded.checkpoint,
                "metadata": stmt.excluded.metadata,
            },
        )
        await self._session.execute(stmt)

    async def insert_blobs(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """A (channel, version) value never changes, so existing rows are kept."""
        if rows:
            stmt = insert(CheckpointBlobRow).values(list(rows)).on_conflict_do_nothing()
            await self._session.execute(stmt)

    async def save_writes(self, rows: Iterable[Mapping[str, Any]], *, overwrite: bool) -> None:
        """Regular writes are kept if already stored (`overwrite=False`); special writes
        (errors, interrupts, resumes: negative `idx`) replace the stored value."""
        rows = list(rows)
        if not rows:
            return
        stmt = insert(CheckpointWriteRow).values(rows)
        if overwrite:
            stmt = stmt.on_conflict_do_update(
                index_elements=["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"],
                set_={
                    "channel": stmt.excluded.channel,
                    "value_type": stmt.excluded.value_type,
                    "value": stmt.excluded.value,
                    "task_path": stmt.excluded.task_path,
                },
            )
        else:
            stmt = stmt.on_conflict_do_nothing()
        await self._session.execute(stmt)

    async def delete_thread(self, thread_id: str) -> None:
        for model in (CheckpointWriteRow, CheckpointBlobRow, CheckpointRow):
            await self._session.execute(delete(model).where(model.thread_id == thread_id))
