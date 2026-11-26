"""Idempotency service (ADR-012, ADR-029).

Protocol for any write command:

    claim = await idempotency.claim(scope, key, request)
    if claim.replayed:
        return claim.response                    # already done: return the stored result
    try:
        result = ...                             # perform the write
    except Exception as exc:
        await idempotency.fail(claim, error_code=...)
        raise
    await idempotency.complete(claim, result)    # pass uow=... to commit with the write

`claim` outcomes:

* first use of the key (or the old record expired): a new claim, `replayed=False`
* same key + same request, already completed: `replayed=True` with the stored response
* same key + same request, still running under a live lease: `IdempotencyInProgressError`
* same key + same request, previous attempt failed or its lease expired: a new claim
* same key + different request: `IdempotencyKeyReuseError`, never executed

Exactly-once needs `complete(..., uow=<the write's unit of work>)`, so that the
business change and the stored result commit in one transaction. Without it the
guarantee is at-least-once with duplicate detection.

Redis fast-path (ADR-012, ADR-030): with a `fast_path` backend, completed
results are also cached under `idem:<scope>:<sha256(key)>` together with their
request hash. A replay whose hash matches is answered without touching
PostgreSQL. Anything else (miss, hash mismatch, Redis down) falls through to
PostgreSQL, which stays authoritative. The cache is written only after the
result is committed: by `complete()` without a caller's unit of work, or when a
replay is served from PostgreSQL. A result completed inside a caller's unit of
work is therefore cached on its first replay.

No write tool exists yet (the registry still refuses them until permissions and
the HITL approval gate exist, ADR-010). The agent uses this service for
caller-supplied turn idempotency keys (`Agent.arun(idempotency_key=...)`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agentic_platform.cache.backend import KeyValueBackend
from agentic_platform.core.errors import (
    AgentInputError,
    CacheError,
    IdempotencyInProgressError,
    IdempotencyKeyReuseError,
    IdempotencyLeaseLostError,
)
from agentic_platform.core.hashing import stable_hash
from agentic_platform.core.logging import get_logger
from agentic_platform.db.engine import Database
from agentic_platform.db.idempotency import MAX_KEY_LENGTH, IdempotencyRepository, NewClaim
from agentic_platform.db.unit_of_work import UnitOfWork

logger = get_logger(__name__)

DEFAULT_LEASE_SECONDS = 300.0
DEFAULT_TTL_SECONDS = 24 * 3600.0
DEFAULT_FAST_PATH_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class IdempotencyClaim:
    scope: str
    key: str
    request_hash: str
    claim_token: str | None
    replayed: bool
    response: dict[str, Any] | None = None


class IdempotencyService:
    def __init__(
        self,
        database: Database,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        fast_path: KeyValueBackend | None = None,
        fast_path_ttl_seconds: float = DEFAULT_FAST_PATH_TTL_SECONDS,
    ) -> None:
        if lease_seconds <= 0 or ttl_seconds < lease_seconds or fast_path_ttl_seconds <= 0:
            raise ValueError("Need 0 < lease_seconds <= ttl_seconds and a positive cache TTL.")
        self._database = database
        self._lease = lease_seconds
        self._ttl = ttl_seconds
        self._fast = fast_path
        self._fast_ttl = min(fast_path_ttl_seconds, ttl_seconds)

    async def claim(
        self,
        scope: str,
        key: str,
        request: Any,
        *,
        request_id: str | None = None,
        thread_id: str | None = None,
    ) -> IdempotencyClaim:
        _check_identifier("scope", scope)
        _check_identifier("idempotency key", key)
        request_hash = stable_hash(request)
        token = uuid.uuid4().hex
        fresh = NewClaim(
            scope=scope,
            key=key,
            request_hash=request_hash,
            claim_token=token,
            lease_seconds=self._lease,
            ttl_seconds=self._ttl,
            request_id=request_id,
            thread_id=thread_id,
        )
        cached = await self._fast_get(scope, key, request_hash)
        if cached is not None:
            logger.info("idempotency.replayed", extra={"fields": {"scope": scope, "via": "redis"}})
            return IdempotencyClaim(scope, key, request_hash, None, replayed=True, response=cached)

        claim, expires_at = await self._claim_in_database(fresh)
        if claim.replayed and expires_at is not None:
            # The record is committed: safe to warm the fast-path now.
            remaining = (expires_at - datetime.now(UTC)).total_seconds()
            await self._fast_put(claim, min(self._fast_ttl, remaining))
        return claim

    async def _claim_in_database(self, fresh: NewClaim) -> tuple[IdempotencyClaim, datetime | None]:
        scope, key, request_hash = fresh.scope, fresh.key, fresh.request_hash
        new = IdempotencyClaim(scope, key, request_hash, fresh.claim_token, replayed=False)

        async with self._database.unit_of_work() as uow:
            repo = IdempotencyRepository(uow.session)
            if await repo.insert_if_absent(fresh):
                return new, None
            locked = await repo.get_for_update(scope, key)
            if locked is None or locked.expired:  # purged meanwhile, or stale record
                if locked is None:
                    await repo.insert_if_absent(fresh)
                else:
                    await repo.reclaim(fresh)
                return new, None

            record = locked.record
            if record.request_hash != request_hash:
                raise IdempotencyKeyReuseError(f"Key reused with a different request ({scope}).")
            if record.status == "completed":
                logger.info("idempotency.replayed", extra={"fields": {"scope": scope}})
                replay = IdempotencyClaim(
                    scope, key, request_hash, None, replayed=True, response=record.response
                )
                return replay, record.expires_at
            if record.status == "in_progress" and locked.lease_active:
                raise IdempotencyInProgressError(f"Key is being processed ({scope}).")
            # failed, or in progress with an expired lease (the worker died): retry.
            await repo.reclaim(fresh)
            logger.info(
                "idempotency.reclaimed",
                extra={"fields": {"scope": scope, "previous_status": record.status}},
            )
            return new, None

    async def complete(
        self,
        claim: IdempotencyClaim,
        response: dict[str, Any],
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        await self._finish(claim, "completed", response, None, uow)

    async def fail(
        self, claim: IdempotencyClaim, *, error_code: str, uow: UnitOfWork | None = None
    ) -> None:
        """Mark the attempt failed so the same request may be retried with the same key."""
        await self._finish(claim, "failed", None, error_code, uow)

    async def purge_expired(self, limit: int | None = None) -> int:
        """Delete expired records (`limit` bounds one transaction; Phase 6 maintenance)."""
        async with self._database.unit_of_work() as uow:
            return await IdempotencyRepository(uow.session).purge_expired(limit)

    async def _finish(
        self,
        claim: IdempotencyClaim,
        status: str,
        response: dict[str, Any] | None,
        error_code: str | None,
        uow: UnitOfWork | None,
    ) -> None:
        if claim.replayed or claim.claim_token is None:
            raise AgentInputError("A replayed claim cannot be completed or failed again.")

        async def finish(unit: UnitOfWork) -> None:
            held = await IdempotencyRepository(unit.session).finish(
                scope=claim.scope,
                key=claim.key,
                claim_token=claim.claim_token or "",
                status=status,
                response=response,
                error_code=error_code,
            )
            if not held:
                raise IdempotencyLeaseLostError(f"Claim on {claim.scope} was lost before {status}.")

        if uow is not None:
            await finish(uow)  # committed later by the caller: do not cache yet
            return
        async with self._database.unit_of_work() as own:
            await finish(own)
        if status == "completed" and response is not None:
            done = IdempotencyClaim(
                claim.scope, claim.key, claim.request_hash, None, replayed=True, response=response
            )
            await self._fast_put(done, self._fast_ttl)

    # -- Redis fast-path (fails open: PostgreSQL is authoritative) ------------------------

    @staticmethod
    def _fast_key(scope: str, key: str) -> str:
        return f"idem:{scope}:{stable_hash(key)}"

    async def _fast_get(self, scope: str, key: str, request_hash: str) -> dict[str, Any] | None:
        if self._fast is None:
            return None
        try:
            raw = await self._fast.get(self._fast_key(scope, key))
        except CacheError as exc:
            logger.warning("idempotency.fast_path.degraded", extra={"fields": {"code": exc.code}})
            return None
        if raw is None:
            return None
        entry = json.loads(raw)
        if entry.get("request_hash") != request_hash:
            return None  # let PostgreSQL decide (it raises IdempotencyKeyReuseError)
        response: dict[str, Any] = entry["response"]
        return response

    async def _fast_put(self, claim: IdempotencyClaim, ttl_seconds: float) -> None:
        if self._fast is None or claim.response is None or ttl_seconds < 1:
            return
        data = json.dumps(
            {"request_hash": claim.request_hash, "response": claim.response},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            await self._fast.set(
                self._fast_key(claim.scope, claim.key), data, ttl_ms=int(ttl_seconds * 1000)
            )
        except CacheError as exc:
            logger.warning("idempotency.fast_path.degraded", extra={"fields": {"code": exc.code}})


def _check_identifier(name: str, value: str) -> None:
    if not value or len(value) > MAX_KEY_LENGTH:
        raise AgentInputError(
            f"Invalid {name} length {len(value or '')}.",
            public_message=f"The {name} must be 1 to {MAX_KEY_LENGTH} characters.",
        )
