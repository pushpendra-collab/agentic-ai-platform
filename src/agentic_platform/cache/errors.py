"""Translate redis-py exceptions into the platform `CacheError` hierarchy.

The `detail` (logged, never shown to users) holds the exception class only:
never keys, values or the server's message, which can echo arguments.
"""

from __future__ import annotations

import asyncio

from redis import exceptions as redis_exc

from agentic_platform.core.errors import (
    CacheError,
    CacheTimeoutError,
    CacheUnavailableError,
)


def translate_redis_error(exc: BaseException) -> CacheError:
    if isinstance(exc, CacheError):
        return exc
    name = type(exc).__name__
    if isinstance(exc, redis_exc.AuthenticationError | redis_exc.AuthorizationError):
        return CacheUnavailableError(f"Misconfigured Redis connection: {name}", retryable=False)
    if isinstance(exc, redis_exc.TimeoutError | asyncio.TimeoutError | TimeoutError):
        return CacheTimeoutError(name)
    if isinstance(exc, redis_exc.ConnectionError | OSError):
        # Includes refused connections, MaxConnectionsError and BusyLoadingError.
        return CacheUnavailableError(name)
    if isinstance(exc, redis_exc.ResponseError) and str(exc).startswith("OOM"):
        return CacheUnavailableError(f"{name}: server out of memory")
    if isinstance(exc, redis_exc.ReadOnlyError):
        return CacheUnavailableError(f"{name}: replica is read-only")
    return CacheError(name)


REDIS_ERRORS: tuple[type[BaseException], ...] = (redis_exc.RedisError, OSError, TimeoutError)
