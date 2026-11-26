"""Key-value backend contract shared by the cache, temporary state, locks and the
idempotency fast-path (ADR-030).

Two implementations:

* `RedisBackend` (cache/redis.py): shared by every process; used when REDIS_URL is set.
* `InMemoryBackend` (this module): one process only; the fallback without Redis
  and the double used by unit tests. It honours the same TTL rules.

Rules every backend enforces:

* Every key has a TTL. Nothing written here lives forever, because Redis holds
  only ephemeral, rebuildable data (ADR-007). PostgreSQL is the source of truth.
* Values are bytes. Higher layers own serialization.
* Failures raise `CacheError` subclasses. Higher layers decide whether to fail
  open (cache) or closed (locks).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class KeyValueBackend(Protocol):
    name: str

    async def get(self, key: str) -> bytes | None: ...

    async def set(
        self, key: str, value: bytes, *, ttl_ms: int, only_if_absent: bool = False
    ) -> bool:
        """Store `value`. With `only_if_absent`, return False if the key already exists."""
        ...

    async def delete(self, key: str) -> bool: ...

    async def ttl_ms(self, key: str) -> int | None:
        """Remaining lifetime, or None if the key does not exist."""
        ...

    async def expire(self, key: str, ttl_ms: int) -> bool:
        """Reset the lifetime of an existing key. False if it does not exist."""
        ...

    async def delete_if_equals(self, key: str, value: bytes) -> bool:
        """Atomically delete the key only if it currently holds `value`."""
        ...

    async def expire_if_equals(self, key: str, value: bytes, ttl_ms: int) -> bool:
        """Atomically reset the lifetime only if the key currently holds `value`."""
        ...

    async def increment(self, key: str, *, ttl_ms: int) -> tuple[int, int]:
        """Atomically add 1 to a counter and return (new value, remaining ms).

        The TTL is set only when the counter is created, so the key expires `ttl_ms`
        after its first increment (a fixed window, used by rate limiting)."""
        ...

    async def ping(self) -> None: ...


def check_ttl(ttl_ms: int) -> int:
    if ttl_ms <= 0:
        raise ValueError("Every key needs a positive TTL (ADR-007: Redis data is ephemeral).")
    return ttl_ms


class InMemoryBackend:
    """Process-local backend. Safe within one event loop (no awaits inside methods)."""

    name = "memory"

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[bytes, float]] = {}

    def _live(self, key: str) -> tuple[bytes, float] | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] <= self._clock():
            del self._data[key]
            return None
        return item

    async def get(self, key: str) -> bytes | None:
        item = self._live(key)
        return item[0] if item else None

    async def set(
        self, key: str, value: bytes, *, ttl_ms: int, only_if_absent: bool = False
    ) -> bool:
        check_ttl(ttl_ms)
        if only_if_absent and self._live(key) is not None:
            return False
        self._data[key] = (value, self._clock() + ttl_ms / 1000)
        return True

    async def delete(self, key: str) -> bool:
        return self._live(key) is not None and self._data.pop(key, None) is not None

    async def ttl_ms(self, key: str) -> int | None:
        item = self._live(key)
        return None if item is None else max(0, int((item[1] - self._clock()) * 1000))

    async def expire(self, key: str, ttl_ms: int) -> bool:
        check_ttl(ttl_ms)
        item = self._live(key)
        if item is None:
            return False
        self._data[key] = (item[0], self._clock() + ttl_ms / 1000)
        return True

    async def delete_if_equals(self, key: str, value: bytes) -> bool:
        item = self._live(key)
        if item is None or item[0] != value:
            return False
        del self._data[key]
        return True

    async def expire_if_equals(self, key: str, value: bytes, ttl_ms: int) -> bool:
        item = self._live(key)
        if item is None or item[0] != value:
            return False
        return await self.expire(key, ttl_ms)

    async def increment(self, key: str, *, ttl_ms: int) -> tuple[int, int]:
        check_ttl(ttl_ms)
        item = self._live(key)
        if item is None:
            count, expires_at = 1, self._clock() + ttl_ms / 1000
        else:
            count, expires_at = int(item[0]) + 1, item[1]
        self._data[key] = (str(count).encode("ascii"), expires_at)
        return count, max(0, int((expires_at - self._clock()) * 1000))

    async def ping(self) -> None:
        return None
