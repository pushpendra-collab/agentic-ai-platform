"""LangGraph checkpoint state as a memory type (read and delete only).

Checkpoints are the exact, resumable execution state of a thread (ADR-009,
ADR-027, ADR-032). The graph writes them; this view lets the memory manager
inspect and delete them (retention, "forget this conversation").
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel


class CheckpointInfo(BaseModel):
    thread_id: str
    checkpoint_id: str
    created_at: str
    step: int | None = None
    source: str | None = None
    channels: list[str]
    pending_writes: int


class CheckpointMemory:
    def __init__(self, saver: BaseCheckpointSaver[Any] | None = None) -> None:
        self._saver = saver

    def bind(self, saver: BaseCheckpointSaver[Any] | None) -> None:
        self._saver = saver

    @property
    def available(self) -> bool:
        return self._saver is not None

    async def latest(self, thread_id: str) -> CheckpointInfo | None:
        history = await self.history(thread_id, limit=1)
        return history[0] if history else None

    async def history(self, thread_id: str, *, limit: int = 20) -> list[CheckpointInfo]:
        """Newest first."""
        if self._saver is None:
            return []
        config: Any = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        out: list[CheckpointInfo] = []
        async for item in self._saver.alist(config, limit=limit):
            out.append(
                CheckpointInfo(
                    thread_id=thread_id,
                    checkpoint_id=item.checkpoint["id"],
                    created_at=item.checkpoint["ts"],
                    step=item.metadata.get("step"),
                    source=item.metadata.get("source"),
                    channels=sorted(item.checkpoint["channel_values"]),
                    pending_writes=len(item.pending_writes or []),
                )
            )
        return out

    async def delete(self, thread_id: str) -> None:
        if self._saver is not None:
            await self._saver.adelete_thread(thread_id)
