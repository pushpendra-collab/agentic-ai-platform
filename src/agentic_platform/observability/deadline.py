"""Strict deadlines (Phase 13, ADR-062).

`asyncio.wait_for` on Python 3.11 returns the inner result when the inner coroutine
swallows the cancellation and finishes anyway, and `asyncio.timeout` exits without raising
in the same case. Either way a deadline that fired could still yield an apparently
successful result *after* the caller's time was up.

`strict_deadline` closes that gap: when the deadline fired, the block ends with
`TimeoutError` even if the code inside caught the cancellation and returned normally.
The work is cancelled in the caller's own task, so nothing keeps running in the
background after the caller has received the failure. Every fired deadline emits one
`timeout` event with its scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agentic_platform.observability.events import EventName
from agentic_platform.observability.hub import emit
from agentic_platform.observability.taxonomy import ErrorCategory


@asynccontextmanager
async def strict_deadline(seconds: float, *, scope: str, **fields: Any) -> AsyncIterator[None]:
    """Run the block within `seconds`; `TimeoutError` once the deadline has fired."""
    expired = False
    try:
        async with asyncio.timeout(seconds) as timer:
            try:
                yield
            finally:
                expired = timer.expired()
    except TimeoutError:
        expired = True
    if expired:
        emit(
            EventName.TIMEOUT,
            status="timeout",
            error_category=ErrorCategory.TIMEOUT,
            attributes={"scope": scope, "limit_ms": round(seconds * 1000, 3)},
            **fields,
        )
        raise TimeoutError(f"{scope} exceeded {seconds} s")


__all__ = ["strict_deadline"]
