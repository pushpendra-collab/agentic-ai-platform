"""Integration tests against a real Redis (and PostgreSQL where noted).

    docker compose up -d redis
    TEST_REDIS_URL=redis://:agentic_redis_dev@localhost:6379/15

Every test uses its own random key prefix, so tests never see each other's keys
and nothing needs to be flushed. Tests that also need PostgreSQL use
TEST_DATABASE_URL / TEST_DATABASE_APP_URL as in test_postgres.py.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from alembic import command

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.cache import (
    Cache,
    LockManager,
    RedisBackend,
    RedisConnection,
    TemporaryStateStore,
    build_cache_runtime,
)
from agentic_platform.core.config import (
    AgentSettings,
    AppSettings,
    DatabaseSettings,
    RedisSettings,
)
from agentic_platform.core.errors import (
    CacheUnavailableError,
    IdempotencyKeyReuseError,
    LockNotAcquiredError,
)
from agentic_platform.db import Database
from agentic_platform.db.migrations import alembic_config
from agentic_platform.services.clock import ClockService
from agentic_platform.services.idempotency import IdempotencyService
from agentic_platform.services.redis_health import RedisHealthService
from tests.cache_contract import CONTRACT
from tests.conftest import FIXED_NOW, LOCAL_TENANT, understanding
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.redis

T = TypeVar("T")


# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def redis_settings() -> RedisSettings:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not set")
    return RedisSettings(url=url, key_prefix=f"agentic-test-{uuid.uuid4().hex[:12]}")


@pytest.fixture
def pg_app_url() -> str:
    owner = os.environ.get("TEST_DATABASE_URL")
    app = os.environ.get("TEST_DATABASE_APP_URL")
    if not owner or not app:
        pytest.skip("TEST_DATABASE_URL / TEST_DATABASE_APP_URL not set")
    command.upgrade(alembic_config(owner), "head")
    return app


def with_redis(settings: RedisSettings, fn: Callable[[RedisConnection], Awaitable[T]]) -> T:
    async def body() -> T:
        connection = RedisConnection(settings)
        try:
            return await fn(connection)
        finally:
            await connection.dispose()

    return asyncio.run(body())


# --- backend --------------------------------------------------------------------------------


@pytest.mark.parametrize("check", CONTRACT, ids=lambda c: c.__name__)
def test_redis_backend_contract(
    redis_settings: RedisSettings, check: Callable[[Any], Awaitable[None]]
) -> None:
    with_redis(redis_settings, lambda conn: check(RedisBackend(conn)))


def test_keys_are_prefixed_and_always_expire(redis_settings: RedisSettings) -> None:
    async def body(conn: RedisConnection) -> None:
        backend = RedisBackend(conn)
        await Cache(backend, namespace="n", default_ttl_seconds=60).set("k", 1)
        await TemporaryStateStore(backend, default_ttl_seconds=60).put("s", "k", 1)
        async with LockManager(backend).hold("l"):
            pattern = f"{redis_settings.key_prefix}:*"
            keys = sorted([k.decode() async for k in conn.client.scan_iter(match=pattern)])
            prefix = redis_settings.key_prefix
            assert keys == [f"{prefix}:cache:n:k", f"{prefix}:lock:l", f"{prefix}:state:s:k"]
            for key in keys:
                assert await conn.client.pttl(key) > 0  # nothing lives forever

    with_redis(redis_settings, body)


def test_wrong_password_is_unavailable_not_retryable(redis_settings: RedisSettings) -> None:
    url = redis_settings.connection_url()
    if ":agentic_redis_dev@" not in url:
        pytest.skip("TEST_REDIS_URL does not use the default dev password")
    bad = redis_settings.model_copy(
        update={"url": RedisSettings(url=url.replace("agentic_redis_dev", "wrong")).url}
    )

    async def body(conn: RedisConnection) -> None:
        with pytest.raises(CacheUnavailableError) as info:
            await RedisBackend(conn).get("k")
        assert info.value.retryable is False

    with_redis(bad, body)


def test_health_ok(redis_settings: RedisSettings) -> None:
    health = with_redis(redis_settings, lambda conn: RedisHealthService(conn).check())
    assert health.status == "ok"
    assert health.server_version and health.server_version.startswith("7")
    assert health.latency_ms is not None


# --- distributed locks across "processes" (separate connections) -----------------------------


def test_lock_is_shared_across_connections(redis_settings: RedisSettings) -> None:
    async def body() -> None:
        a, b = RedisConnection(redis_settings), RedisConnection(redis_settings)
        try:
            locks_a = LockManager(RedisBackend(a), default_ttl_seconds=5)
            locks_b = LockManager(RedisBackend(b), default_ttl_seconds=5)
            async with locks_a.hold("shared"):
                with pytest.raises(LockNotAcquiredError):
                    async with locks_b.hold("shared"):
                        pass
            async with locks_b.hold("shared"):  # free again after A released
                pass
        finally:
            await a.dispose()
            await b.dispose()

    asyncio.run(body())


def test_lock_mutual_exclusion_under_contention(redis_settings: RedisSettings) -> None:
    async def body() -> None:
        connections = [RedisConnection(redis_settings) for _ in range(3)]
        managers = [
            LockManager(RedisBackend(c), default_ttl_seconds=5, retry_interval_seconds=0.005)
            for c in connections
        ]
        inside = 0
        max_inside = 0
        completed = 0

        async def worker(manager: LockManager) -> None:
            nonlocal inside, max_inside, completed
            for _ in range(5):
                async with manager.hold("counter", wait_seconds=10):
                    inside += 1
                    max_inside = max(max_inside, inside)
                    await asyncio.sleep(0.005)
                    inside -= 1
                    completed += 1

        try:
            await asyncio.gather(*(worker(m) for m in managers for _ in range(2)))
        finally:
            for c in connections:
                await c.dispose()
        assert completed == 30
        assert max_inside == 1

    asyncio.run(body())


def test_lock_renewal_keeps_a_long_critical_section(redis_settings: RedisSettings) -> None:
    async def body(conn: RedisConnection) -> None:
        locks = LockManager(RedisBackend(conn), default_ttl_seconds=0.3)
        async with locks.hold("long") as lock:
            await asyncio.sleep(1.0)  # > 3x TTL
            lock.ensure_held()
            with pytest.raises(LockNotAcquiredError):
                async with LockManager(RedisBackend(conn)).hold("long"):
                    pass

    with_redis(redis_settings, body)


# --- idempotency fast-path (PostgreSQL + Redis) -----------------------------------------------


def test_idempotency_fast_path(redis_settings: RedisSettings, pg_app_url: str) -> None:
    key = uuid.uuid4().hex

    async def body() -> None:
        db = Database(DatabaseSettings(url=pg_app_url))
        conn = RedisConnection(redis_settings)
        try:
            backend = RedisBackend(conn)
            service = IdempotencyService(db, fast_path=backend)
            claim = await service.claim("test.fast", key, {"a": 1})
            await service.complete(claim, {"result": 7})

            # The completed result is now in Redis, keyed by a hash (no raw key).
            idem_keys = f"{redis_settings.key_prefix}:idem:*"
            cached = [k async for k in conn.client.scan_iter(match=idem_keys)]
            assert len(cached) == 1 and key.encode() not in cached[0]

            # Replay is served by Redis even if PostgreSQL is unreachable.
            offline = IdempotencyService(
                Database(DatabaseSettings(url="postgresql://x:y@127.0.0.1:1/none")),
                fast_path=backend,
            )
            replay = await offline.claim("test.fast", key, {"a": 1})
            assert replay.replayed and replay.response == {"result": 7}

            # A different request with the same key falls through to PostgreSQL.
            with pytest.raises(IdempotencyKeyReuseError):
                await service.claim("test.fast", key, {"a": 2})

            # Redis flushed/down: PostgreSQL still answers (Redis is never the only copy).
            await conn.client.delete(*cached)
            again = await service.claim("test.fast", key, {"a": 1})
            assert again.replayed and again.response == {"result": 7}
            assert [k async for k in conn.client.scan_iter(match=idem_keys)]  # re-warmed
        finally:
            await db.dispose()
            await conn.dispose()

    asyncio.run(body())


def test_idempotency_works_when_redis_is_down(pg_app_url: str) -> None:
    key = uuid.uuid4().hex
    down = RedisSettings(url="redis://127.0.0.1:1/0", connect_timeout_seconds=0.3)

    async def body() -> None:
        db = Database(DatabaseSettings(url=pg_app_url))
        conn = RedisConnection(down)
        try:
            service = IdempotencyService(db, fast_path=RedisBackend(conn))
            claim = await service.claim("test.down", key, {"a": 1})
            await service.complete(claim, {"ok": True})
            replay = await service.claim("test.down", key, {"a": 1})
            assert replay.replayed and replay.response == {"ok": True}
        finally:
            await db.dispose()
            await conn.dispose()

    asyncio.run(body())


# --- agent end to end: PostgreSQL checkpoints + Redis locks and fast-path ---------------------


def _settings(redis_settings: RedisSettings, pg_app_url: str, **agent: Any) -> AppSettings:
    return AppSettings(
        database=DatabaseSettings(url=pg_app_url),
        redis=redis_settings,
        agent=AgentSettings(**agent),
    )


def _agent(settings: AppSettings, *script: object) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(script=list(script))
    deps = build_dependencies(settings, llm=llm, clock=ClockService(now_utc=lambda: FIXED_NOW))
    assert deps.database is not None and deps.cache.shared
    return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database)), llm


def test_agent_turn_idempotency_replays_without_rerunning(
    redis_settings: RedisSettings, pg_app_url: str
) -> None:
    settings = _settings(redis_settings, pg_app_url)
    thread = f"idem-{uuid.uuid4().hex}"
    agent, llm = _agent(settings, understanding(), "Hello!")

    first = agent.run("hi", thread_id=thread, idempotency_key="click-1")
    assert first.status == "completed" and not first.replayed

    # A double-click / client retry with the same key, from a "different process".
    other, other_llm = _agent(settings)
    second = other.run("hi", thread_id=thread, idempotency_key="click-1")
    assert second.replayed is True
    assert (second.request_id, second.message) == (first.request_id, first.message)
    assert other_llm.calls == []  # no LLM call, no new turn
    assert len(llm.calls) == 2

    reused = other.run("something else", thread_id=thread, idempotency_key="click-1")
    assert reused.status == "failed" and reused.errors[0].code == "IDEMPOTENCY_KEY_REUSED"


def test_agent_thread_lock_spans_processes(redis_settings: RedisSettings, pg_app_url: str) -> None:
    settings = _settings(redis_settings, pg_app_url, thread_lock_wait_seconds=0)
    thread = f"lock-{uuid.uuid4().hex}"
    agent, _ = _agent(settings)

    async def body() -> Any:
        # "Process A" holds the thread lock through its own Redis connection.
        conn = RedisConnection(redis_settings)
        try:
            # Lock keys are namespaced by tenant since Phase 5 (local identity's tenant).
            async with LockManager(RedisBackend(conn)).hold(f"thread:{LOCAL_TENANT}/{thread}"):
                return await agent.arun("hi", thread_id=thread)
        finally:
            await conn.dispose()
            await agent.aclose()

    response = asyncio.run(body())
    assert response.status == "failed"
    assert (response.errors[0].code, response.errors[0].retryable) == ("THREAD_BUSY", True)


def test_agent_crash_resume_after_restart(redis_settings: RedisSettings, pg_app_url: str) -> None:
    settings = _settings(redis_settings, pg_app_url)
    thread = f"crash-{uuid.uuid4().hex}"

    crashing, _ = _agent(
        settings,
        understanding(tool_calls=[("system.current_time", "{}")]),
        asyncio.CancelledError(),
    )
    crashed = crashing.run("What time is it?", thread_id=thread)
    assert crashed.errors[0].code == "TURN_INCOMPLETE"

    restarted, llm = _agent(settings, "It is 06:30 UTC.")  # new process: nothing in memory
    status = restarted.thread_status(thread)
    assert status.pending and status.next_nodes == ["generate_response"]
    response = restarted.resume(thread)
    assert response.status == "completed" and response.message == "It is 06:30 UTC."
    assert len(llm.calls) == 1
    assert restarted.thread_status(thread).pending is False

    # No lock is left behind in Redis after the turns.
    async def leftover(conn: RedisConnection) -> list[bytes]:
        pattern = f"{redis_settings.key_prefix}:lock:*"
        return [k async for k in conn.client.scan_iter(match=pattern)]

    assert with_redis(redis_settings, leftover) == []


def test_runtime_built_from_settings_uses_redis(redis_settings: RedisSettings) -> None:
    runtime = build_cache_runtime(redis_settings)
    assert runtime.shared and isinstance(runtime.backend, RedisBackend)
    assert runtime.redis is not None
    asyncio.run(runtime.redis.dispose())
