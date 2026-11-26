"""Distributed locks (ADR-031).

    async with locks.hold("thread:abc", ttl_seconds=30, wait_seconds=2) as lock:
        ...                      # critical section
        lock.ensure_held()       # optional: raise LockLostError if the lease was lost

* Acquire: `SET key token NX PX ttl`, retried until `wait_seconds` runs out,
  then `LockNotAcquiredError` (retryable).
* While held, a background task renews the lease every `ttl/3` with an atomic
  compare-and-expire, so long critical sections keep the lock. If renewal finds
  the lock gone (expired during a stall, or taken over), `lock.lost` becomes True.
* Release: atomic compare-and-delete, so a holder never deletes someone else's
  lock. If release fails, the TTL frees the lock anyway.
* **Fails closed:** if the backend is unreachable, acquiring raises `CacheError`.
  Work that needs the lock does not run without it.

With Redis the lock is shared by every process. With the in-memory backend it
only protects a single process (development / tests).

These are *efficiency and consistency* locks for short critical sections, not a
substitute for database constraints: PostgreSQL row locks and unique keys remain
the final guard for business data.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

from agentic_platform.cache.backend import KeyValueBackend
from agentic_platform.cache.cache import check_key_part
from agentic_platform.core.errors import CacheError, LockLostError, LockNotAcquiredError
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)


class Lock:
    def __init__(self, name: str, token: bytes) -> None:
        self.name = name
        self.token = token
        self.lost = False

    def ensure_held(self) -> None:
        if self.lost:
            raise LockLostError(f"Lock '{self.name}' was lost.")


class LockManager:
    def __init__(
        self,
        backend: KeyValueBackend,
        *,
        default_ttl_seconds: float = 30.0,
        retry_interval_seconds: float = 0.05,
    ) -> None:
        if default_ttl_seconds <= 0 or retry_interval_seconds <= 0:
            raise ValueError("TTL and retry interval must be positive.")
        self._backend = backend
        self._ttl = default_ttl_seconds
        self._retry = retry_interval_seconds

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @contextlib.asynccontextmanager
    async def hold(
        self,
        name: str,
        *,
        ttl_seconds: float | None = None,
        wait_seconds: float = 0.0,
        auto_renew: bool = True,
    ) -> AsyncIterator[Lock]:
        key = f"lock:{check_key_part('lock name', name)}"
        ttl_ms = max(1, int((ttl_seconds or self._ttl) * 1000))
        lock = Lock(name, uuid.uuid4().hex.encode("ascii"))

        deadline = time.monotonic() + max(0.0, wait_seconds)
        while not await self._backend.set(key, lock.token, ttl_ms=ttl_ms, only_if_absent=True):
            if time.monotonic() >= deadline:
                raise LockNotAcquiredError(f"Lock '{name}' is held by another owner.")
            await asyncio.sleep(self._retry)

        renewer = (
            asyncio.create_task(self._renew(key, lock, ttl_ms), name=f"lock-renew:{name}")
            if auto_renew
            else None
        )
        try:
            yield lock
        finally:
            if renewer is not None:
                renewer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewer
            await self._release(key, lock)

    async def _renew(self, key: str, lock: Lock, ttl_ms: int) -> None:
        interval = ttl_ms / 3000
        while True:
            await asyncio.sleep(interval)
            try:
                still_ours = await self._backend.expire_if_equals(key, lock.token, ttl_ms)
            except CacheError as exc:
                # Transient: keep trying while the current lease has not run out.
                logger.warning(
                    "lock.renew.failed", extra={"fields": {"lock": lock.name, "code": exc.code}}
                )
                continue
            if not still_ours:
                lock.lost = True
                logger.error("lock.lost", extra={"fields": {"lock": lock.name}})
                return

    async def _release(self, key: str, lock: Lock) -> None:
        try:
            released = await self._backend.delete_if_equals(key, lock.token)
        except CacheError as exc:
            logger.warning(
                "lock.release.failed", extra={"fields": {"lock": lock.name, "code": exc.code}}
            )
            return
        if not released and not lock.lost:
            lock.lost = True
            logger.error("lock.lost_before_release", extra={"fields": {"lock": lock.name}})
