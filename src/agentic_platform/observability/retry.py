"""Retry policy (Phase 13, ADR-062).

Retries are opt-in per operation and limited to operations proven safe to repeat:

* **ERP/business reads**: `retry_async` with `erp_read_policy`, on
  `dependency_unavailable` only, up to `ERP_READ_MAX_ATTEMPTS` (default 2). Read-only and
  idempotent; all attempts share the one read deadline (`ERP_QUERY_TIMEOUT_SECONDS`).
* **LLM calls**: retried by the provider SDK only (`LLM_MAX_RETRIES`; 408/409/429/5xx and
  connection errors). A second layer here would multiply calls and cost.
* **Tool calls, domain runs, graph nodes**: never. A tool may be protected or a write; a
  domain run spans several tools and an LLM call.
* **Approved (protected) executions**: never. The idempotency claim and the approval
  state allow at most one execution (ADR-040).
* **Checkpoint writes, audit, memory**: never here. The stores have their own
  transaction semantics; a crashed turn resumes from its last finished step instead.

Timeouts are **not** retried for ERP reads: a timed-out read already used the shared
deadline, and repeating it would put more load on a slow dependency. A retry never starts
when its backoff would end after the deadline. Backoff is exponential and deterministic
(no jitter: one process, few callers; jitter can be added when the ERP is shared).

There are no business writes in the platform. If one is ever added it must not be wrapped
in `retry_async` unless it carries an idempotency key the ERP honours (ADR-012).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from agentic_platform.observability.events import EventName
from agentic_platform.observability.hub import emit
from agentic_platform.observability.taxonomy import ErrorCategory, classify_exception

T = TypeVar("T")

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    base_delay_seconds: float = 0.1
    multiplier: float = 2.0
    max_delay_seconds: float = 2.0
    retry_on: frozenset[ErrorCategory] = frozenset()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0 or self.multiplier < 1:
            raise ValueError("delays must be >= 0 and multiplier >= 1")

    def delay(self, retry_number: int) -> float:
        """Backoff before retry `retry_number` (1 = the first retry)."""
        raw = self.base_delay_seconds * self.multiplier ** (retry_number - 1)
        return min(raw, self.max_delay_seconds)

    def should_retry(self, category: ErrorCategory) -> bool:
        return category in self.retry_on


NO_RETRY = RetryPolicy()


def erp_read_policy(max_attempts: int, base_delay_seconds: float) -> RetryPolicy:
    """Business reads: retry an unreachable ERP, nothing else (not timeouts, not data errors)."""
    return RetryPolicy(
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        retry_on=frozenset({ErrorCategory.DEPENDENCY_UNAVAILABLE}),
    )


async def retry_async(
    operation: str,
    call: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    classify: Callable[[BaseException], ErrorCategory] = classify_exception,
    deadline: float | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Clock | None = None,
) -> T:
    """Run `call`, retrying per `policy`. The last error is re-raised unchanged.

    `deadline` is an absolute `clock()` time (default: the running loop's clock); a retry
    whose backoff would end after it is not attempted. Cancellation is never retried."""
    now = clock or asyncio.get_running_loop().time
    attempt = 1
    while True:
        try:
            return await call()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:
            category = classify(exc)
            code = getattr(exc, "code", None) or type(exc).__name__
            if not policy.should_retry(category):
                raise
            if attempt >= policy.max_attempts:
                if policy.max_attempts > 1:
                    emit(EventName.RETRY_EXHAUSTED, status="error", attempt=attempt,
                         error_category=category, error_code=code,
                         attributes={"operation": operation})  # fmt: skip
                raise
            delay = policy.delay(attempt)
            if deadline is not None and now() + delay >= deadline:
                emit(EventName.RETRY_EXHAUSTED, status="error", attempt=attempt,
                     error_category=category, error_code=code,
                     attributes={"operation": operation, "reason": "deadline"})  # fmt: skip
                raise
            attempt += 1
            emit(
                EventName.RETRY_ATTEMPTED,
                status="retry",
                attempt=attempt,
                error_category=category,
                error_code=code,
                attributes={"operation": operation, "delay_ms": round(delay * 1000, 3)},
            )
            await sleep(delay)


__all__ = ["NO_RETRY", "RetryPolicy", "erp_read_policy", "retry_async"]
