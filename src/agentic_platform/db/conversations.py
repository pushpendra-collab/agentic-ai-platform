"""Conversation ownership (ADR-039).

One row per conversation thread: which tenant and which user own it. The
public `thread_id` is unique per tenant; checkpoints are stored under the key
`"<tenant_id>/<thread_id>"` (agents/threads.py), so two tenants can use the same
public id without ever sharing state.

RLS (migration 0004) restricts the runtime role to the bound tenant.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import CheckConstraint, Index, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentic_platform.db.base import Base, TimestampMixin

MAX_LIST = 200


class ConversationThread(TimestampMixin, Base):
    __tablename__ = "conversation_thread"
    __table_args__ = (
        CheckConstraint("char_length(tenant_id) BETWEEN 1 AND 128", name="tenant_length"),
        CheckConstraint("char_length(thread_id) BETWEEN 1 AND 128", name="thread_length"),
        CheckConstraint("char_length(owner_user_id) BETWEEN 1 AND 128", name="owner_length"),
        Index("ix_conversation_thread_owner", "tenant_id", "owner_user_id", "updated_at"),
    )

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(Text)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open(
        self, tenant_id: str, thread_id: str, owner_user_id: str
    ) -> ConversationThread | None:
        """Create the thread for `owner_user_id`, or touch it if they already own it.

        One statement, race-free. Returns None when another user of the tenant owns it.
        """
        base = insert(ConversationThread).values(
            tenant_id=tenant_id, thread_id=thread_id, owner_user_id=owner_user_id
        )
        stmt = base.on_conflict_do_update(
            index_elements=["tenant_id", "thread_id"],
            set_={"updated_at": func.now()},
            where=ConversationThread.owner_user_id == base.excluded.owner_user_id,
        ).returning(ConversationThread)
        row: ConversationThread | None = (await self._session.scalars(stmt)).first()
        return row

    async def get(self, tenant_id: str, thread_id: str) -> ConversationThread | None:
        stmt = select(ConversationThread).where(
            ConversationThread.tenant_id == tenant_id, ConversationThread.thread_id == thread_id
        )
        row: ConversationThread | None = await self._session.scalar(stmt)
        return row

    async def delete(self, tenant_id: str, thread_id: str) -> bool:
        stmt = (
            delete(ConversationThread)
            .where(
                ConversationThread.tenant_id == tenant_id,
                ConversationThread.thread_id == thread_id,
            )
            .returning(ConversationThread.thread_id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def list_for_owner(
        self, tenant_id: str, owner_user_id: str, *, limit: int = 50
    ) -> Sequence[ConversationThread]:
        stmt = (
            select(ConversationThread)
            .where(
                ConversationThread.tenant_id == tenant_id,
                ConversationThread.owner_user_id == owner_user_id,
            )
            .order_by(ConversationThread.updated_at.desc(), ConversationThread.thread_id)
            .limit(max(1, min(limit, MAX_LIST)))
        )
        return (await self._session.scalars(stmt)).all()
