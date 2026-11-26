"""Conversation history read from the LangGraph checkpointer (`ConversationSource`).

The checkpointer already persists every message of a thread (PostgreSQL when
configured), so conversation memory reads it instead of keeping a second copy.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from agentic_platform.memory.models import ConversationTurn


class CheckpointConversationSource:
    def __init__(self, saver: BaseCheckpointSaver[Any]) -> None:
        self._saver = saver

    @property
    def saver(self) -> BaseCheckpointSaver[Any]:
        return self._saver

    async def turns(self, thread_id: str, limit: int) -> list[ConversationTurn]:
        latest = await self._saver.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        )
        if latest is None:
            return []
        turns: list[ConversationTurn] = []
        for message in latest.checkpoint["channel_values"].get("messages", []):
            if isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                role = "assistant"
            else:
                continue
            content = message.content if isinstance(message.content, str) else str(message.content)
            turns.append(ConversationTurn(role=role, content=content))
        return turns[-limit:] if limit > 0 else []

    async def delete(self, thread_id: str) -> None:
        await self._saver.adelete_thread(thread_id)
