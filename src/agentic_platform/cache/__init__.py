"""Redis layer: ephemeral, rebuildable, fast-access data only (ADR-007, ADR-030, ADR-031).

    KeyValueBackend ── RedisBackend (REDIS_URL set; shared by all processes)
                    └─ InMemoryBackend (fallback; this process only)
          ▲
          ├── Cache                 cache-aside, JSON, fails open
          ├── TemporaryStateStore   scratch state with mandatory TTL, fails closed
          ├── LockManager           distributed locks with renewal, fails closed
          └── IdempotencyService    fast-path for completed results (services/idempotency.py)

PostgreSQL remains the source of truth. Nothing here is the only copy of
anything that matters, and every key expires.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_platform.cache.backend import InMemoryBackend, KeyValueBackend
from agentic_platform.cache.cache import Cache
from agentic_platform.cache.locks import Lock, LockManager
from agentic_platform.cache.redis import RedisBackend, RedisConnection
from agentic_platform.cache.temporary_state import TemporaryStateStore
from agentic_platform.core.config import RedisSettings


@dataclass(frozen=True)
class CacheRuntime:
    backend: KeyValueBackend
    # None when REDIS_URL is not set (process-local fallback in use).
    redis: RedisConnection | None

    @property
    def shared(self) -> bool:
        """True when state is shared across processes (Redis)."""
        return self.redis is not None


def build_cache_runtime(settings: RedisSettings) -> CacheRuntime:
    if settings.configured:
        connection = RedisConnection(settings)
        return CacheRuntime(backend=RedisBackend(connection), redis=connection)
    return CacheRuntime(backend=InMemoryBackend(), redis=None)


__all__ = [
    "Cache",
    "CacheRuntime",
    "InMemoryBackend",
    "KeyValueBackend",
    "Lock",
    "LockManager",
    "RedisBackend",
    "RedisConnection",
    "TemporaryStateStore",
    "build_cache_runtime",
]
