"""Conversation memory: the history of a thread plus a stored rolling summary.

* History comes from the LangGraph checkpointer (`ConversationSource`); it is
  not copied anywhere else.
* The summary is a durable record (`conversation_summary`, one per thread and
  tenant) that callers write with `save_summary`. Automatic LLM summarisation of
  long threads is planned, not implemented.
"""

from __future__ import annotations

from typing import Any

from agentic_platform.memory.models import (
    ConversationTurn,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
)
from agentic_platform.memory.ports import ConversationSource, MemoryStore


class ConversationMemory:
    def __init__(self, store: MemoryStore, source: ConversationSource | None = None) -> None:
        self._store = store
        self._source = source

    def bind_source(self, source: ConversationSource | None) -> None:
        """Attach the checkpointer-backed history (done by the Agent once it is compiled)."""
        self._source = source

    @property
    def has_history(self) -> bool:
        return self._source is not None

    async def history(self, thread_id: str, *, limit: int = 20) -> list[ConversationTurn]:
        """Most recent turns (oldest first). Empty without a checkpointer.

        `thread_id` is the checkpointer key (Phase 5: `<tenant>/<thread>`). This
        low-level read does not check ownership: use `AuthorizedMemory.history`.
        """
        if self._source is None:
            return []
        return await self._source.turns(thread_id, limit)

    async def summary(self, scope: MemoryScope, thread_id: str) -> MemoryRecord | None:
        found = await self._store.find(
            scope,
            MemoryQuery(
                kinds=(MemoryKind.CONVERSATION_SUMMARY,),
                thread_id=thread_id,
                include_shared=True,
                limit=1,
            ),
        )
        return found[0] if found else None

    async def save_summary(
        self,
        scope: MemoryScope,
        thread_id: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return await self._store.upsert(
            scope,
            NewMemory(
                kind=MemoryKind.CONVERSATION_SUMMARY,
                content=text,
                thread_id=thread_id,
                metadata=metadata or {},
                source_type="conversation",
                source_ref=thread_id,
            ),
        )

    async def forget(
        self, scope: MemoryScope, thread_id: str, *, checkpoint_key: str | None = None
    ) -> int:
        """Delete the thread's summary and its checkpointed history (stored under
        `checkpoint_key`, by default the thread id)."""
        deleted = await self._store.delete(
            scope,
            MemoryQuery(
                kinds=(MemoryKind.CONVERSATION_SUMMARY,), thread_id=thread_id, include_shared=True
            ),
        )
        if self._source is not None:
            await self._source.delete(checkpoint_key or thread_id)
        return deleted
