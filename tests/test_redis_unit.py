"""Redis layer tests that need no Redis server: configuration, the in-memory
backend contract, cache / temporary state / lock semantics, error translation
and health reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import ValidationError
from redis import exceptions as redis_exc

from agentic_platform.cache import (
    Cache,
    InMemoryBackend,
    LockManager,
    RedisBackend,
    RedisConnection,
    TemporaryStateStore,
    build_cache_runtime,
)
from agentic_platform.cache.errors import translate_redis_error
from agentic_platform.core.config import AppSettings, RedisSettings
from agentic_platform.core.errors import (
    CacheError,
    CacheNotConfiguredError,
    CacheTimeoutError,
    CacheUnavailableError,
    LockLostError,
    LockNotAcquiredError,
)
from agentic_platform.services.redis_health import RedisHealthService
from tests.cache_contract import CONTRACT

# --- configuration ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://:pw@localhost:6379/0", "redis://:pw@localhost:6379/0"),
        ("REDISS://h:6380/1", "rediss://h:6380/1"),
    ],
)
def test_redis_url_accepted(url: str, expected: str) -> None:
    assert RedisSettings(url=url).connection_url() == expected


@pytest.mark.parametrize("url", ["http://h:6379", "memcached://h", "localhost:6379", "redis://"])
def test_redis_url_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        RedisSettings(url=url)


def test_redis_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert RedisSettings().configured is False
    assert RedisSettings(url="  ").configured is False
    monkeypatch.setenv("REDIS_URL", "redis://:pw@cache:6379/2")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "staging")
    loaded = AppSettings().redis
    assert loaded.configured and loaded.key_prefix == "staging"
    assert "pw" not in repr(loaded)  # secret is masked
    with pytest.raises(ValidationError):
        RedisSettings(key_prefix="has space")


def test_cache_runtime_selection() -> None:
    local = build_cache_runtime(RedisSettings())
    assert isinstance(local.backend, InMemoryBackend) and local.redis is None
    assert local.shared is False
    shared = build_cache_runtime(RedisSettings(url="redis://localhost:6379/0"))
    assert isinstance(shared.backend, RedisBackend) and shared.shared is True
    with pytest.raises(CacheNotConfiguredError):
        RedisConnection(RedisSettings())


def test_redis_connection_is_lazy() -> None:
    connection = RedisConnection(RedisSettings(url="redis://localhost:1/0"))
    assert connection.pool_status() == {"created": 0}  # no client until first use


# --- in-memory backend honours the shared contract ------------------------------------


@pytest.mark.parametrize("check", CONTRACT, ids=lambda c: c.__name__)
def test_in_memory_backend_contract(check: Callable[[Any], Awaitable[None]]) -> None:
    asyncio.run(check(InMemoryBackend()))


# --- error translation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected", "retryable"),
    [
        (redis_exc.ConnectionError("refused"), CacheUnavailableError, True),
        (redis_exc.TimeoutError("slow"), CacheTimeoutError, True),
        (redis_exc.AuthenticationError("bad pw"), CacheUnavailableError, False),
        (redis_exc.BusyLoadingError("loading"), CacheUnavailableError, True),
        (redis_exc.ResponseError("OOM command not allowed"), CacheUnavailableError, True),
        (redis_exc.ReadOnlyError("replica"), CacheUnavailableError, True),
        (OSError("unreachable"), CacheUnavailableError, True),
        (redis_exc.ResponseError("WRONGTYPE secret-value"), CacheError, False),
    ],
)
def test_redis_error_mapping(
    exc: BaseException, expected: type[CacheError], retryable: bool
) -> None:
    translated = translate_redis_error(exc)
    assert type(translated) is expected
    assert translated.retryable is retryable
    assert "secret" not in translated.detail  # server text is never copied


# --- failing backend double ----------------------------------------------------------------


class _DownBackend(InMemoryBackend):
    name = "down"

    async def get(self, key: str) -> bytes | None:
        raise CacheUnavailableError("down")

    async def set(
        self, key: str, value: bytes, *, ttl_ms: int, only_if_absent: bool = False
    ) -> bool:
        raise CacheUnavailableError("down")

    async def delete(self, key: str) -> bool:
        raise CacheUnavailableError("down")


# --- cache --------------------------------------------------------------------------------


def test_cache_round_trip_and_get_or_load() -> None:
    async def body() -> None:
        cache = Cache(InMemoryBackend(), namespace="t", default_ttl_seconds=60)
        assert await cache.get("k") is None
        assert await cache.set("k", {"a": [1, 2]}) is True
        assert await cache.get("k") == {"a": [1, 2]}
        assert await cache.delete("k") is True

        calls = 0

        async def loader() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"n": calls}

        assert await cache.get_or_load("x", loader) == {"n": 1}
        assert await cache.get_or_load("x", loader) == {"n": 1}  # served from cache
        assert calls == 1

        with pytest.raises(ValueError):
            await cache.set("k", None)
        with pytest.raises(ValueError):
            await cache.get("has space")

    asyncio.run(body())


def test_cache_fails_open() -> None:
    async def body() -> None:
        cache = Cache(_DownBackend(), namespace="t", default_ttl_seconds=60)
        assert await cache.get("k") is None
        assert await cache.set("k", 1) is False
        assert await cache.delete("k") is False

        async def loader() -> int:
            return 42

        assert await cache.get_or_load("k", loader) == 42  # the source still answers

    asyncio.run(body())


def test_cache_namespaces_are_isolated() -> None:
    async def body() -> None:
        backend = InMemoryBackend()
        a = Cache(backend, namespace="a", default_ttl_seconds=60)
        b = Cache(backend, namespace="b", default_ttl_seconds=60)
        await a.set("k", "from-a")
        assert await b.get("k") is None

    asyncio.run(body())


# --- temporary state ----------------------------------------------------------------------


def test_temporary_state_lifecycle() -> None:
    async def body() -> None:
        state = TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=60, max_value_bytes=100)
        await state.put("thread-1", "draft", {"step": 2})
        assert await state.get("thread-1", "draft") == {"step": 2}
        assert await state.get("thread-2", "draft") is None  # scoped
        assert await state.touch("thread-1", "draft", ttl_seconds=120) is True
        assert await state.delete("thread-1", "draft") is True
        assert await state.touch("thread-1", "draft") is False
        with pytest.raises(ValueError):
            await state.put("thread-1", "big", "x" * 200)
        with pytest.raises(ValueError):
            await state.put("thread-1", "none", None)

    asyncio.run(body())


def test_temporary_state_expires() -> None:
    async def body() -> None:
        state = TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=60)
        await state.put("s", "k", 1, ttl_seconds=1)
        await asyncio.sleep(1.1)
        assert await state.get("s", "k") is None

    asyncio.run(body())


def test_temporary_state_fails_closed() -> None:
    async def body() -> None:
        state = TemporaryStateStore(_DownBackend(), default_ttl_seconds=60)
        with pytest.raises(CacheUnavailableError):
            await state.put("s", "k", 1)
        with pytest.raises(CacheUnavailableError):
            await state.get("s", "k")

    asyncio.run(body())


# --- locks --------------------------------------------------------------------------------


def test_lock_is_exclusive_and_released() -> None:
    async def body() -> None:
        locks = LockManager(InMemoryBackend(), default_ttl_seconds=5)
        async with locks.hold("job"):
            with pytest.raises(LockNotAcquiredError) as busy:
                async with locks.hold("job"):
                    pass
            assert busy.value.retryable is True
            async with locks.hold("other-job"):  # different name: independent
                pass
        async with locks.hold("job"):  # released on exit
            pass

    asyncio.run(body())


def test_lock_waits_for_release() -> None:
    async def body() -> None:
        locks = LockManager(InMemoryBackend(), default_ttl_seconds=5, retry_interval_seconds=0.01)
        order: list[str] = []

        async def first() -> None:
            async with locks.hold("job"):
                order.append("first-in")
                await asyncio.sleep(0.1)
                order.append("first-out")

        async def second() -> None:
            await asyncio.sleep(0.02)
            async with locks.hold("job", wait_seconds=2):
                order.append("second-in")

        await asyncio.gather(first(), second())
        assert order == ["first-in", "first-out", "second-in"]

    asyncio.run(body())


def test_lock_is_renewed_while_held() -> None:
    async def body() -> None:
        backend = InMemoryBackend()
        locks = LockManager(backend, default_ttl_seconds=0.15)
        async with locks.hold("job") as lock:
            await asyncio.sleep(0.5)  # > 3x the TTL: survives only through renewal
            lock.ensure_held()
            with pytest.raises(LockNotAcquiredError):
                async with locks.hold("job"):
                    pass

    asyncio.run(body())


def test_lost_lock_is_detected_and_not_deleted_by_old_holder() -> None:
    async def body() -> None:
        backend = InMemoryBackend()
        locks = LockManager(backend, default_ttl_seconds=0.15)
        async with locks.hold("job", auto_renew=False) as lock:
            await asyncio.sleep(0.25)  # lease expired (e.g. a long GC pause)
            async with locks.hold("job", auto_renew=False):  # someone else takes over
                pass
            await backend.set("lock:job", b"new-owner", ttl_ms=10_000)
        assert lock.lost is True
        with pytest.raises(LockLostError):
            lock.ensure_held()
        assert await backend.get("lock:job") == b"new-owner"  # not deleted by the old holder

    asyncio.run(body())


def test_lock_fails_closed_when_backend_is_down() -> None:
    async def body() -> None:
        with pytest.raises(CacheUnavailableError):
            async with LockManager(_DownBackend()).hold("job"):
                raise AssertionError("must not run without the lock")

    asyncio.run(body())


# --- health -------------------------------------------------------------------------------


def test_redis_health_not_configured() -> None:
    health = asyncio.run(RedisHealthService(None).check())
    assert health.status == "not_configured"
    assert health.error_code == "CACHE_NOT_CONFIGURED"


def test_redis_health_unreachable_server() -> None:
    settings = RedisSettings(
        url="redis://127.0.0.1:1/0", connect_timeout_seconds=0.5, health_timeout_seconds=1
    )

    async def body() -> Any:
        connection = RedisConnection(settings)
        try:
            return await RedisHealthService(connection).check()
        finally:
            await connection.dispose()

    health = asyncio.run(body())
    assert health.status == "unavailable"
    assert health.error_code in {"CACHE_UNAVAILABLE", "CACHE_TIMEOUT"}
