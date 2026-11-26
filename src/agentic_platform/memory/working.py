"""Working (short-term) memory: per-thread scratch with a TTL (ADR-033).

Holds what the agent is juggling *right now* in a conversation: entities in
focus, partially collected fields, intermediate notes. It lives in Redis
(`cache.TemporaryStateStore`; in-memory fallback without Redis), expires on
its own, and is never the only copy of anything that matters.

The other half of short-term memory, the recent message window, is the
`messages` channel of the graph state (checkpointed; trimmed to
AGENT_MAX_HISTORY_MESSAGES for each LLM call).

Storage: one JSON object per (tenant, user, thread). Writes are
read-modify-write; turns on a thread are serialized by the thread lock
(ADR-031), so they do not race.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from agentic_platform.cache.temporary_state import TemporaryStateStore
from agentic_platform.memory.models import MemoryScope

_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{1,100}$")
_STATE_KEY = "working"


def _check_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ValueError("Working-memory keys are 1-100 characters of [A-Za-z0-9_.:-].")
    return key


class WorkingMemory:
    def __init__(self, store: TemporaryStateStore, *, ttl_seconds: int) -> None:
        self._store = store
        self._ttl = ttl_seconds

    @staticmethod
    def _scope(scope: MemoryScope, thread_id: str) -> str:
        owner = hashlib.sha256(f"{scope.tenant_id}\x00{scope.user_key}".encode()).hexdigest()[:16]
        return f"wm:{owner}:{thread_id}"

    async def get_all(self, scope: MemoryScope, thread_id: str) -> dict[str, Any]:
        value = await self._store.get(self._scope(scope, thread_id), _STATE_KEY)
        return dict(value) if isinstance(value, dict) else {}

    async def get(self, scope: MemoryScope, thread_id: str, key: str) -> Any | None:
        return (await self.get_all(scope, thread_id)).get(_check_key(key))

    async def set(self, scope: MemoryScope, thread_id: str, key: str, value: Any) -> None:
        if value is None:
            raise ValueError("Use delete() to remove a working-memory entry.")
        current = await self.get_all(scope, thread_id)
        current[_check_key(key)] = value
        await self._store.put(
            self._scope(scope, thread_id), _STATE_KEY, current, ttl_seconds=self._ttl
        )

    async def delete(self, scope: MemoryScope, thread_id: str, key: str) -> bool:
        current = await self.get_all(scope, thread_id)
        if current.pop(_check_key(key), None) is None:
            return False
        if current:
            await self._store.put(
                self._scope(scope, thread_id), _STATE_KEY, current, ttl_seconds=self._ttl
            )
        else:
            await self._store.delete(self._scope(scope, thread_id), _STATE_KEY)
        return True

    async def clear(self, scope: MemoryScope, thread_id: str) -> None:
        await self._store.delete(self._scope(scope, thread_id), _STATE_KEY)
