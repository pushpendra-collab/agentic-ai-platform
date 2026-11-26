"""Durable idempotency records (ADR-012, ADR-029).

One row per (scope, idempotency_key). `scope` names the operation (for example
a tool name); `request_hash` fingerprints the request so a key reused for a
different request is detected. A claim holds a lease (`locked_until`) and a
random `claim_token`; only the holder of the current token can complete or fail
the record, so a worker whose lease expired cannot overwrite a newer claim.

The repository provides primitives only. The claim/replay protocol lives in
`services/idempotency.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Text,
    delete,
    func,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentic_platform.db.base import Base, TimestampMixin

IDEMPOTENCY_STATUSES = ("in_progress", "completed", "failed")
MAX_KEY_LENGTH = 255


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        CheckConstraint("status IN ('in_progress', 'completed', 'failed')", name="status"),
        CheckConstraint(
            f"char_length(idempotency_key) BETWEEN 1 AND {MAX_KEY_LENGTH}", name="key_length"
        ),
        CheckConstraint(f"char_length(scope) BETWEEN 1 AND {MAX_KEY_LENGTH}", name="scope_length"),
        Index("ix_idempotency_record_expires_at", "expires_at"),
    )

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    claim_token: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class LockedRecord:
    """A row locked FOR UPDATE, with time checks evaluated by the database clock."""

    record: IdempotencyRecord
    expired: bool
    lease_active: bool


@dataclass(frozen=True)
class NewClaim:
    """A fresh in-progress claim on (scope, key)."""

    scope: str
    key: str
    request_hash: str
    claim_token: str
    lease_seconds: float
    ttl_seconds: float
    request_id: str | None = None
    thread_id: str | None = None


def _claim_values(claim: NewClaim) -> dict[str, Any]:
    return {
        "request_hash": claim.request_hash,
        "claim_token": claim.claim_token,
        "request_id": claim.request_id,
        "thread_id": claim.thread_id,
        "locked_until": func.now() + timedelta(seconds=claim.lease_seconds),
        "expires_at": func.now() + timedelta(seconds=claim.ttl_seconds),
    }


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(self, claim: NewClaim) -> bool:
        """Insert a new in-progress claim. False if a row for (scope, key) exists.

        A concurrent inserter of the same key blocks on the unique index until the
        first transaction ends, then sees the conflict.
        """
        stmt = (
            insert(IdempotencyRecord)
            .values(
                scope=claim.scope,
                idempotency_key=claim.key,
                status="in_progress",
                **_claim_values(claim),
            )
            .on_conflict_do_nothing(index_elements=["scope", "idempotency_key"])
            .returning(IdempotencyRecord.scope)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def get_for_update(self, scope: str, key: str) -> LockedRecord | None:
        stmt = (
            select(
                IdempotencyRecord,
                (IdempotencyRecord.expires_at <= func.now()).label("expired"),
                (IdempotencyRecord.locked_until > func.now()).label("lease_active"),
            )
            .where(IdempotencyRecord.scope == scope, IdempotencyRecord.idempotency_key == key)
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return LockedRecord(record=row[0], expired=bool(row[1]), lease_active=bool(row[2]))

    async def reclaim(self, claim: NewClaim) -> None:
        """Restart a row as a fresh in-progress claim (expired, failed or abandoned)."""
        await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == claim.scope,
                IdempotencyRecord.idempotency_key == claim.key,
            )
            .values(
                status="in_progress",
                response=None,
                error_code=None,
                updated_at=func.now(),
                **_claim_values(claim),
            )
            .execution_options(synchronize_session=False)
        )

    async def finish(
        self,
        *,
        scope: str,
        key: str,
        claim_token: str,
        status: str,
        response: Mapping[str, Any] | None,
        error_code: str | None,
    ) -> bool:
        """Complete or fail the claim. False if `claim_token` no longer holds it."""
        result = await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
                IdempotencyRecord.claim_token == claim_token,
                IdempotencyRecord.status == "in_progress",
            )
            .values(
                status=status,
                response=dict(response) if response is not None else None,
                error_code=error_code,
                locked_until=func.now(),
                updated_at=func.now(),
            )
            .returning(IdempotencyRecord.scope)
            .execution_options(synchronize_session=False)
        )
        return result.first() is not None

    async def purge_expired(self, limit: int | None = None) -> int:
        """Delete expired records (claims already treat them as absent). With `limit`,
        at most that many, oldest expiry first, skipping rows locked elsewhere."""
        due: Any = IdempotencyRecord.expires_at <= func.now()
        if limit is not None:
            due = tuple_(IdempotencyRecord.scope, IdempotencyRecord.idempotency_key).in_(
                select(IdempotencyRecord.scope, IdempotencyRecord.idempotency_key)
                .where(due)
                .order_by(IdempotencyRecord.expires_at)
                .limit(max(1, limit))
                .with_for_update(skip_locked=True)
            )
        result = await self._session.execute(
            delete(IdempotencyRecord)
            .where(due)
            .returning(IdempotencyRecord.scope)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())
