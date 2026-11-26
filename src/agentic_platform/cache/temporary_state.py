"""Temporary state: short-lived, rebuildable scratch data with a mandatory TTL (ADR-030).

Intended for working-memory scratch, draft form input, progress markers and
similar data that can be lost without harm. It is **not** for conversation
state (the PostgreSQL checkpointer holds that) or business data (PostgreSQL).

    state = TemporaryStateStore(backend, default_ttl_seconds=3600)
    await state.put(thread_id, "draft", {"step": 2})
    draft = await state.get(thread_id, "draft")

Keys are `state:<scope>:<key>`, where scope is typically a thread id. Unlike the
cache, failures **raise** `CacheError`: a caller that stores state usually needs
to know it was not stored. Values are JSON and capped in size.
"""

from __future__ import annotations

import json
from typing import Any

from agentic_platform.cache.backend import KeyValueBackend
from agentic_platform.cache.cache import check_key_part

DEFAULT_MAX_VALUE_BYTES = 64 * 1024


class TemporaryStateStore:
    def __init__(
        self,
        backend: KeyValueBackend,
        *,
        default_ttl_seconds: int,
        max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
    ) -> None:
        if default_ttl_seconds <= 0 or max_value_bytes <= 0:
            raise ValueError("default_ttl_seconds and max_value_bytes must be positive.")
        self._backend = backend
        self._ttl = default_ttl_seconds
        self._max = max_value_bytes

    @staticmethod
    def _key(scope: str, key: str) -> str:
        return f"state:{check_key_part('scope', scope)}:{check_key_part('state key', key)}"

    async def put(
        self, scope: str, key: str, value: Any, *, ttl_seconds: int | None = None
    ) -> None:
        if value is None:
            raise ValueError("Use delete() to remove state; None is not a value.")
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(data) > self._max:
            raise ValueError(f"Temporary state value is {len(data)} bytes; limit {self._max}.")
        await self._backend.set(
            self._key(scope, key), data, ttl_ms=(ttl_seconds or self._ttl) * 1000
        )

    async def get(self, scope: str, key: str) -> Any | None:
        raw = await self._backend.get(self._key(scope, key))
        return None if raw is None else json.loads(raw)

    async def delete(self, scope: str, key: str) -> bool:
        return await self._backend.delete(self._key(scope, key))

    async def touch(self, scope: str, key: str, *, ttl_seconds: int | None = None) -> bool:
        """Extend the lifetime. False if the state already expired."""
        return await self._backend.expire(self._key(scope, key), (ttl_seconds or self._ttl) * 1000)
