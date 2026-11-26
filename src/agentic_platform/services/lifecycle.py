"""Process lifecycle: in-flight work, the concurrent-turn cap and a bounded shutdown
(Phase 15, ADR-064).

Shutdown of the API process (SIGTERM / SIGINT; uvicorn owns the signals):

    1. uvicorn stops accepting connections and asks open ones to close
    2. in-flight requests finish, for up to API_GRACEFUL_SHUTDOWN_SECONDS
    3. uvicorn cancels the requests still running (a cancelled turn records its failure:
       idempotency claim failed, thread lock released, no success state)
    4. the app's shutdown runs `run_shutdown(...)` within API_SHUTDOWN_CLEANUP_SECONDS:
         in_flight_requests   wait until the cancelled requests have unwound
         maintenance          stop the in-process scheduler (development only)
         redis                close the Redis pool
         database             close the Agent DB pool
         erp                  close the ERP read-only pool (only with ERP_ADAPTER=postgres)
         observability        flush exporters and log handlers
    5. the process exits

Every step is bounded: a step that does not finish in its share of the budget is
abandoned (not awaited), logged as `timeout`, and the next step still runs, so a hung
dependency cannot keep a stopping process alive. Each step logs one `shutdown.step` line
(name, status, duration, error code) and the sequence one `shutdown.completed` line; no
values, no secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from agentic_platform.core.errors import AgentPlatformError, ServerBusyError
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Every step gets at least this long, even when earlier steps used the whole budget:
# closing pools must always be attempted.
MIN_STEP_SECONDS = 0.5


async def abandon_after(coro: Coroutine[Any, Any, T], seconds: float) -> T:
    """Await `coro` for at most `seconds`, then raise TimeoutError WITHOUT waiting for the
    cancelled work to unwind (a hung server never acknowledges asyncpg's cancel request;
    see services/database_health.py)."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(_discard_outcome)
    raise TimeoutError


def _discard_outcome(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()  # retrieved: no "exception was never retrieved" warning


async def in_daemon_thread(fn: Callable[[], None]) -> None:
    """Run blocking `fn` in a daemon thread and await it.

    Not `asyncio.to_thread`: the default executor's threads are joined when the
    interpreter exits, so a call that never returns (an exporter stuck on a dead
    collector) would keep the process alive after its shutdown step was abandoned. A
    daemon thread is not joined: the process exits on time."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def finish(error: BaseException | None) -> None:
        if done.done():
            return
        if error is None:
            done.set_result(None)
        else:
            done.set_exception(error)

    def target() -> None:
        error: BaseException | None = None
        try:
            fn()
        except BaseException as exc:  # reported to the awaiting coroutine
            error = exc
        # RuntimeError: the loop closed meanwhile, so nobody is waiting any more.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(finish, error)

    threading.Thread(target=target, name="shutdown-flush", daemon=True).start()
    await done


# --- in-flight requests ---------------------------------------------------------------------


class InFlight:
    """Requests in progress in this process (the HTTP middleware counts them).

    Polling instead of an `asyncio.Event`: the counter is created before the server's
    event loop exists and must work in whichever loop serves the requests."""

    def __init__(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def enter(self) -> None:
        self._count += 1

    def exit(self) -> None:
        self._count = max(0, self._count - 1)

    async def wait_idle(self, *, poll_seconds: float = 0.05) -> None:
        """Return once nothing is in flight (callers bound it with a deadline)."""
        while self._count > 0:
            await asyncio.sleep(poll_seconds)


class TurnSlots:
    """At most `limit` agent turns at once in this process; 0 = unlimited.

    Non-blocking on purpose: a turn over the limit is refused at once (`SERVER_BUSY`,
    retryable) instead of waiting, so a burst cannot pile up requests that hold
    connections while they wait. Acquire and release never await, so they are atomic
    within the event loop."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        self.limit = limit
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def acquire(self) -> None:
        if self.limit and self._active >= self.limit:
            logger.warning("api.turns.busy", extra={"fields": {"limit": self.limit}})
            raise ServerBusyError(f"{self._active} of {self.limit} turn slots in use.")
        self._active += 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)


# --- shutdown -------------------------------------------------------------------------------

StepStatus = Literal["ok", "timeout", "error"]


@dataclass(frozen=True)
class ShutdownStep:
    name: str
    action: Callable[[], Awaitable[None]]
    # Upper bound for this step alone (None: whatever is left of the budget).
    max_seconds: float | None = None


class StepResult(BaseModel):
    step: str
    status: StepStatus
    duration_ms: int
    error_code: str | None = None


class ShutdownReport(BaseModel):
    status: Literal["ok", "degraded"]
    duration_ms: int
    steps: list[StepResult]


async def _run(action: Callable[[], Awaitable[None]]) -> None:
    await action()


async def run_shutdown(steps: Sequence[ShutdownStep], *, budget_seconds: float) -> ShutdownReport:
    """Run `steps` in order within `budget_seconds` (each at least MIN_STEP_SECONDS).

    Never raises: a failing or hung step is recorded and the next step runs."""
    started = time.perf_counter()
    deadline = started + budget_seconds
    results: list[StepResult] = []
    for step in steps:
        remaining = deadline - time.perf_counter()
        if step.max_seconds is not None:
            remaining = min(remaining, step.max_seconds)
        limit = max(MIN_STEP_SECONDS, remaining)
        step_started = time.perf_counter()
        status: StepStatus = "ok"
        code: str | None = None
        try:
            await abandon_after(_run(step.action), limit)
        except TimeoutError:
            status, code = "timeout", "SHUTDOWN_STEP_TIMEOUT"
        except Exception as exc:  # shutdown reports, it does not stop half-way
            status = "error"
            code = exc.code if isinstance(exc, AgentPlatformError) else type(exc).__name__
        result = StepResult(
            step=step.name,
            status=status,
            duration_ms=int((time.perf_counter() - step_started) * 1000),
            error_code=code,
        )
        results.append(result)
        level = logger.info if status == "ok" else logger.warning
        level("shutdown.step", extra={"fields": result.model_dump()})
    report = ShutdownReport(
        status="ok" if all(r.status == "ok" for r in results) else "degraded",
        duration_ms=int((time.perf_counter() - started) * 1000),
        steps=results,
    )
    logger.info(
        "shutdown.completed",
        extra={"fields": {"status": report.status, "duration_ms": report.duration_ms}},
    )
    return report


__all__ = [
    "InFlight",
    "ShutdownReport",
    "ShutdownStep",
    "StepResult",
    "TurnSlots",
    "abandon_after",
    "in_daemon_thread",
    "run_shutdown",
]
