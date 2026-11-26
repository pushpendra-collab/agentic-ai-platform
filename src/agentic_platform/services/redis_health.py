"""Redis health service. Same shape and rules as `DatabaseHealthService`: it never raises.

* `ok`: PING succeeded and a TTL'd probe key round-tripped.
* `unavailable`: cannot connect, timed out, or the probe failed.
* `not_configured`: REDIS_URL is not set (process-local fallbacks are in use).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Literal

from pydantic import BaseModel

from agentic_platform.cache.errors import translate_redis_error
from agentic_platform.cache.redis import RedisBackend, RedisConnection
from agentic_platform.core.errors import CacheError, CacheTimeoutError
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)


class RedisHealth(BaseModel):
    status: Literal["ok", "unavailable", "not_configured"]
    latency_ms: int | None = None
    server_version: str | None = None
    pool: dict[str, int] | None = None
    error_code: str | None = None


class RedisHealthService:
    def __init__(self, connection: RedisConnection | None) -> None:
        self._connection = connection

    async def check(self) -> RedisHealth:
        if self._connection is None:
            return RedisHealth(status="not_configured", error_code="CACHE_NOT_CONFIGURED")

        backend = RedisBackend(self._connection)
        timeout = self._connection.settings.health_timeout_seconds
        probe_key = f"health:{uuid.uuid4().hex}"
        started = time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                await backend.ping()
                await backend.set(probe_key, b"1", ttl_ms=10_000)
                if await backend.get(probe_key) != b"1":
                    raise CacheError("Probe value did not round-trip.")
                await backend.delete(probe_key)
                info = await self._connection.client.info("server")
        except TimeoutError:
            error: CacheError = CacheTimeoutError(f"Health check exceeded {timeout}s.")
            return self._unavailable(error, started)
        except Exception as exc:  # health checks report, they do not raise
            return self._unavailable(translate_redis_error(exc), started)

        return RedisHealth(
            status="ok",
            latency_ms=_elapsed_ms(started),
            server_version=str(info.get("redis_version")) if info else None,
            pool=self._connection.pool_status(),
        )

    def _unavailable(self, error: CacheError, started: float) -> RedisHealth:
        logger.warning(
            "redis.health.unavailable",
            extra={"fields": {"code": error.code, "detail": error.detail}},
        )
        return RedisHealth(
            status="unavailable", latency_ms=_elapsed_ms(started), error_code=error.code
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
