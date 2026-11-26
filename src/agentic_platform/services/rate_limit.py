"""Per-caller request budgets (Phase 8, ADR-053).

    authenticated Principal ─► RateLimiter.check(principal, kind)
                                 key  = ratelimit:<kind>:<hash(tenant, user)>
                                 INCR + TTL on first hit (fixed window, shared in Redis)
                                 count > limit ─► RateLimitedError (429, Retry-After)

* **Not authorization.** The limiter only counts. It runs after authentication and
  before the handler; every request it lets through is still checked by the
  `Authorizer`, ownership and approval rules as before. Nothing is ever allowed
  because of a rate limit.
* **The key comes from the verified principal only** (tenant and user from the
  token). No header, query parameter or body field can choose or reset it. Ids are
  hashed so Redis never holds user identifiers.
* **Shared across replicas:** counters live in the platform's key-value backend
  (Redis when configured; the process-local fallback otherwise, which is per
  process and therefore only for development).
* **Backend failure:** `fail_open=True` (default) lets the request through and logs
  `ratelimit.unavailable`; `False` refuses with the cache error (503). Turns need
  Redis for the thread lock regardless, so they fail closed on their own.
"""

from __future__ import annotations

from enum import StrEnum

from agentic_platform.cache.backend import KeyValueBackend
from agentic_platform.core.config import RateLimitSettings
from agentic_platform.core.errors import CacheError, RateLimitedError
from agentic_platform.core.hashing import stable_hash
from agentic_platform.core.logging import get_logger
from agentic_platform.security.identity import Principal

logger = get_logger(__name__)


class LimitKind(StrEnum):
    TURN = "turn"
    APPROVAL_DECISION = "approval_decision"
    WRITE = "write"
    READ = "read"


class RateLimiter:
    def __init__(self, backend: KeyValueBackend, settings: RateLimitSettings) -> None:
        self._backend = backend
        self._settings = settings
        self._limits = {
            LimitKind.TURN: settings.turns_per_window,
            LimitKind.APPROVAL_DECISION: settings.approval_decisions_per_window,
            LimitKind.WRITE: settings.writes_per_window,
            LimitKind.READ: settings.reads_per_window,
        }

    def limit_for(self, kind: LimitKind) -> int:
        return self._limits[kind]

    @staticmethod
    def key_for(principal: Principal, kind: LimitKind) -> str:
        caller = stable_hash([principal.tenant_id, principal.user_id])[:32]
        return f"ratelimit:{kind.value}:{caller}"

    async def check(self, principal: Principal, kind: LimitKind) -> None:
        """Count one request; raise `RateLimitedError` when the window's budget is spent."""
        limit = self._limits[kind]
        if limit <= 0:
            return
        window_ms = self._settings.window_seconds * 1000
        try:
            count, remaining_ms = await self._backend.increment(
                self.key_for(principal, kind), ttl_ms=window_ms
            )
        except CacheError as exc:
            logger.warning(
                "ratelimit.unavailable",
                extra={
                    "fields": {
                        "kind": kind.value,
                        "code": exc.code,
                        "fail_open": self._settings.fail_open,
                    }
                },
            )
            if self._settings.fail_open:
                return
            raise
        if count > limit:
            retry_after = -(-remaining_ms // 1000)  # ceil to whole seconds
            logger.info(
                "ratelimit.exceeded",
                extra={
                    "fields": {
                        "kind": kind.value,
                        "limit": limit,
                        "retry_after_seconds": retry_after,
                        **principal.log_fields(),
                    }
                },
            )
            raise RateLimitedError(
                f"Rate limit '{kind.value}' exceeded ({limit} per "
                f"{self._settings.window_seconds}s).",
                retry_after_seconds=retry_after,
            )
