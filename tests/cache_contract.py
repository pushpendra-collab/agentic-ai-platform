"""Behaviour every `KeyValueBackend` must have. Run against InMemoryBackend (unit
tests) and RedisBackend (integration tests), so both implementations are held to
the same semantics."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from agentic_platform.cache.backend import KeyValueBackend


def _key() -> str:
    return f"contract:{uuid.uuid4().hex}"


async def check_set_get_delete(backend: KeyValueBackend) -> None:
    key = _key()
    assert await backend.get(key) is None
    assert await backend.set(key, b"v1", ttl_ms=10_000) is True
    assert await backend.get(key) == b"v1"
    assert await backend.set(key, b"v2", ttl_ms=10_000) is True  # overwrite
    assert await backend.get(key) == b"v2"
    assert await backend.delete(key) is True
    assert await backend.delete(key) is False
    assert await backend.get(key) is None


async def check_only_if_absent(backend: KeyValueBackend) -> None:
    key = _key()
    assert await backend.set(key, b"first", ttl_ms=10_000, only_if_absent=True) is True
    assert await backend.set(key, b"second", ttl_ms=10_000, only_if_absent=True) is False
    assert await backend.get(key) == b"first"


async def check_ttl_expiry(backend: KeyValueBackend) -> None:
    key = _key()
    await backend.set(key, b"v", ttl_ms=150)
    remaining = await backend.ttl_ms(key)
    assert remaining is not None and 0 < remaining <= 150
    await asyncio.sleep(0.3)
    assert await backend.get(key) is None
    assert await backend.ttl_ms(key) is None
    # An expired key is absent for only_if_absent too.
    assert await backend.set(key, b"again", ttl_ms=10_000, only_if_absent=True) is True


async def check_expire(backend: KeyValueBackend) -> None:
    key = _key()
    assert await backend.expire(key, 10_000) is False  # missing key
    await backend.set(key, b"v", ttl_ms=150)
    assert await backend.expire(key, 10_000) is True
    await asyncio.sleep(0.3)
    assert await backend.get(key) == b"v"


async def check_compare_and_delete(backend: KeyValueBackend) -> None:
    key = _key()
    await backend.set(key, b"mine", ttl_ms=10_000)
    assert await backend.delete_if_equals(key, b"theirs") is False
    assert await backend.get(key) == b"mine"
    assert await backend.delete_if_equals(key, b"mine") is True
    assert await backend.get(key) is None
    assert await backend.delete_if_equals(key, b"mine") is False


async def check_compare_and_expire(backend: KeyValueBackend) -> None:
    key = _key()
    await backend.set(key, b"mine", ttl_ms=150)
    assert await backend.expire_if_equals(key, b"theirs", 10_000) is False
    assert await backend.expire_if_equals(key, b"mine", 10_000) is True
    await asyncio.sleep(0.3)
    assert await backend.get(key) == b"mine"
    assert await backend.expire_if_equals(_key(), b"mine", 10_000) is False


async def check_ttl_is_mandatory(backend: KeyValueBackend) -> None:
    with pytest.raises(ValueError):
        await backend.set(_key(), b"v", ttl_ms=0)
    with pytest.raises(ValueError):
        await backend.expire(_key(), -1)


async def check_ping(backend: KeyValueBackend) -> None:
    await backend.ping()


async def check_increment_fixed_window(backend: KeyValueBackend) -> None:
    """Phase 8: the rate-limit counter. TTL starts at the first hit and is not extended."""
    key = _key()
    count, remaining = await backend.increment(key, ttl_ms=200)
    assert count == 1 and 0 < remaining <= 200
    await asyncio.sleep(0.05)
    count, later = await backend.increment(key, ttl_ms=200)
    assert count == 2 and later < remaining  # the window was not extended
    assert await backend.get(key) == b"2"
    await asyncio.sleep(0.25)
    count, remaining = await backend.increment(key, ttl_ms=200)
    assert count == 1 and 0 < remaining <= 200  # a new window
    with pytest.raises(ValueError):
        await backend.increment(_key(), ttl_ms=0)


CONTRACT = [
    check_set_get_delete,
    check_only_if_absent,
    check_ttl_expiry,
    check_expire,
    check_compare_and_delete,
    check_compare_and_expire,
    check_ttl_is_mandatory,
    check_ping,
    check_increment_fixed_window,
]
