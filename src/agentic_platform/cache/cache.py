"""Cache-aside abstraction (ADR-030).

    cache = Cache(backend, namespace="example", default_ttl_seconds=300)
    value = await cache.get_or_load("key", load_from_service)

* Values are JSON. Keys are `cache:<namespace>:<key>`.
* **Fails open:** if the backend is down, `get` is a miss and `set` / `delete`
  return False. A cache outage slows the system down but never breaks it, and
  the loader (the real source) is always the fallback.
* Never cache anything a business decision depends on without re-validating it
  against PostgreSQL (ARCHITECTURE.md § B.4: memory is context, not truth).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from agentic_platform.cache.backend import KeyValueBackend
from agentic_platform.core.errors import CacheError
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)

MAX_KEY_LENGTH = 200


def check_key_part(name: str, value: str) -> str:
    if not value or len(value) > MAX_KEY_LENGTH or any(c.isspace() for c in value):
        raise ValueError(f"Invalid {name}: 1-{MAX_KEY_LENGTH} characters, no whitespace.")
    return value


class Cache:
    def __init__(
        self, backend: KeyValueBackend, *, namespace: str, default_ttl_seconds: int
    ) -> None:
        self._backend = backend
        self._namespace = check_key_part("namespace", namespace)
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive.")
        self._ttl = default_ttl_seconds

    def _key(self, key: str) -> str:
        return f"cache:{self._namespace}:{check_key_part('cache key', key)}"

    async def get(self, key: str) -> Any | None:
        """The cached value, or None on a miss or backend failure."""
        full = self._key(key)
        try:
            raw = await self._backend.get(full)
        except CacheError as exc:
            self._degraded("get", exc)
            return None
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        if value is None:
            raise ValueError("None cannot be cached (it means 'miss').")
        full = self._key(key)
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        try:
            return await self._backend.set(full, data, ttl_ms=(ttl_seconds or self._ttl) * 1000)
        except CacheError as exc:
            self._degraded("set", exc)
            return False

    async def delete(self, key: str) -> bool:
        """Invalidate. Call after the source of truth changes."""
        full = self._key(key)
        try:
            return await self._backend.delete(full)
        except CacheError as exc:
            self._degraded("delete", exc)
            return False

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Cached value if present, else `await loader()` (cached unless it returns None)."""
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await loader()
        if value is not None:
            await self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    def _degraded(self, operation: str, exc: CacheError) -> None:
        logger.warning(
            "cache.degraded",
            extra={
                "fields": {
                    "namespace": self._namespace,
                    "operation": operation,
                    "code": exc.code,
                    "detail": exc.detail,
                }
            },
        )
