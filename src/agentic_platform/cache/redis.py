"""Redis connection management and the Redis key-value backend (ADR-030).

`RedisConnection` owns one `redis.asyncio.Redis` client and its connection pool
per process. The client is created lazily and bound to the event loop that
first uses it, like the database engine. `dispose()` closes it, and the next use
creates a new one (see `Agent.run`, which uses a fresh loop per call).

`RedisBackend` implements `KeyValueBackend`. Every key gets the configured
prefix, every write has a TTL, and redis-py errors become `CacheError`s.
Compare-and-delete / compare-and-expire (used by locks) are Lua scripts, so they
are atomic on the server.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from redis.asyncio import Redis

from agentic_platform.cache.backend import check_ttl
from agentic_platform.cache.errors import REDIS_ERRORS, translate_redis_error
from agentic_platform.core.config import RedisSettings
from agentic_platform.core.errors import CacheNotConfiguredError
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

_DELETE_IF_EQUALS = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_EXPIRE_IF_EQUALS = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# INCR, and set the TTL only when the counter was just created (fixed window).
_INCREMENT_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


class RedisConnection:
    def __init__(
        self,
        settings: RedisSettings,
        *,
        client_factory: Callable[[], Redis] | None = None,
    ) -> None:
        if client_factory is None and not settings.configured:
            raise CacheNotConfiguredError("REDIS_URL is not set.")
        self._settings = settings
        self._factory = client_factory or self._default_factory
        self._client: Redis | None = None

    @property
    def settings(self) -> RedisSettings:
        return self._settings

    def _default_factory(self) -> Redis:
        s = self._settings
        client: Redis = Redis.from_url(
            s.connection_url(),
            max_connections=s.max_connections,
            socket_timeout=s.socket_timeout_seconds,
            socket_connect_timeout=s.connect_timeout_seconds,
            health_check_interval=30,
            client_name=s.client_name,
            decode_responses=False,
        )
        return client

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def pool_status(self) -> dict[str, int]:
        if self._client is None:
            return {"created": 0}
        pool: Any = self._client.connection_pool
        status = {"max_connections": int(getattr(pool, "max_connections", 0))}
        for name in ("_in_use_connections", "_available_connections"):
            value = getattr(pool, name, None)
            if value is not None:
                status[name.strip("_")] = len(value)
        return status

    async def dispose(self) -> None:
        """Close pooled connections. The next use opens a new client."""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # closing a broken connection must not mask the real error
                logger.warning("redis.close.failed", exc_info=True)
            logger.debug("redis.pool.disposed")


class RedisBackend:
    name = "redis"

    def __init__(self, connection: RedisConnection) -> None:
        self._connection = connection
        self._prefix = connection.settings.key_prefix + ":"

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def _call(self, operation: Callable[[Redis], Awaitable[T]]) -> T:
        try:
            return await operation(self._connection.client)
        except REDIS_ERRORS as exc:
            raise translate_redis_error(exc) from exc

    async def get(self, key: str) -> bytes | None:
        value = await self._call(lambda r: r.get(self._key(key)))
        return value.encode("utf-8") if isinstance(value, str) else value

    async def set(
        self, key: str, value: bytes, *, ttl_ms: int, only_if_absent: bool = False
    ) -> bool:
        check_ttl(ttl_ms)
        result = await self._call(
            lambda r: r.set(self._key(key), value, px=ttl_ms, nx=only_if_absent)
        )
        return bool(result)

    async def delete(self, key: str) -> bool:
        return bool(await self._call(lambda r: r.delete(self._key(key))))

    async def ttl_ms(self, key: str) -> int | None:
        remaining = int(await self._call(lambda r: r.pttl(self._key(key))))
        if remaining == -2:  # no such key
            return None
        if remaining == -1:  # written without TTL by someone else: report as unbounded
            return None
        return remaining

    async def expire(self, key: str, ttl_ms: int) -> bool:
        check_ttl(ttl_ms)
        return bool(await self._call(lambda r: r.pexpire(self._key(key), ttl_ms)))

    async def delete_if_equals(self, key: str, value: bytes) -> bool:
        result = await self._call(lambda r: r.eval(_DELETE_IF_EQUALS, 1, self._key(key), value))
        return bool(result)

    async def expire_if_equals(self, key: str, value: bytes, ttl_ms: int) -> bool:
        check_ttl(ttl_ms)
        result = await self._call(
            lambda r: r.eval(_EXPIRE_IF_EQUALS, 1, self._key(key), value, ttl_ms)
        )
        return bool(result)

    async def increment(self, key: str, *, ttl_ms: int) -> tuple[int, int]:
        check_ttl(ttl_ms)
        result = await self._call(
            lambda r: r.eval(_INCREMENT_WITH_TTL, 1, self._key(key), ttl_ms)
        )
        count, remaining = result
        return int(count), int(remaining)

    async def ping(self) -> None:
        await self._call(lambda r: r.ping())
