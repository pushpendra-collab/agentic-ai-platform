"""Unit of Work: one database transaction per service command.

    async with database.unit_of_work() as uow:
        repo = SomeRepository(uow.session)
        ...                      # commit happens on clean exit

* Clean exit: commit (read-only units roll back; there is nothing to commit).
* Any exception: rollback, then re-raise. SQLAlchemy/driver errors are re-raised
  as `DatabaseError` subclasses; other exceptions propagate unchanged.
* `read_only=True` issues `SET TRANSACTION READ ONLY`, so PostgreSQL itself
  rejects any write attempted inside the unit (`DatabaseReadOnlyError`).
* `tenant_id=...` binds the tenant for row-level security for the whole
  transaction (`db/tenancy.py`, Phase 5). For a read-only unit it is sent in the
  same statement that makes the transaction read-only, so it costs no extra
  round trip; a writing unit pays one statement.

Repositories never commit or roll back; only the unit of work does.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import Text, bindparam, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentic_platform.core.errors import AgentStateError
from agentic_platform.core.logging import get_logger
from agentic_platform.db.errors import translate_db_error
from agentic_platform.db.tenancy import TENANT_SETTING

logger = get_logger(__name__)

_DRIVER_ERRORS: tuple[type[BaseException], ...] = (SQLAlchemyError, OSError, TimeoutError)


class UnitOfWork:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        read_only: bool,
        tenant_id: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._read_only = read_only
        self._tenant_id = tenant_id
        self._session: AsyncSession | None = None

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise AgentStateError("UnitOfWork.session used outside 'async with'.")
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        session = self._sessions()
        try:
            await session.begin()
            await self._prepare(session)
        except _DRIVER_ERRORS as exc:
            await _safe_close(session)
            raise translate_db_error(exc) from exc
        self._session = session
        return self

    async def _prepare(self, session: AsyncSession) -> None:
        tenant = (
            func.set_config(TENANT_SETTING, bindparam("tenant", self._tenant_id, Text), True)
            if self._tenant_id is not None
            else None
        )
        if self._read_only and tenant is not None:
            # Same effect as SET TRANSACTION READ ONLY, plus the RLS tenant: one round trip.
            await session.execute(
                select(func.set_config("transaction_read_only", "on", True), tenant)
            )
        elif self._read_only:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        elif tenant is not None:
            await session.execute(select(tenant))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if exc is None and not self._read_only:
                try:
                    await session.commit()
                except _DRIVER_ERRORS as commit_exc:
                    await _safe_rollback(session)
                    raise translate_db_error(commit_exc) from commit_exc
            else:
                await _safe_rollback(session)
        finally:
            self._session = None
            await _safe_close(session)

        if exc is not None and isinstance(exc, _DRIVER_ERRORS):
            raise translate_db_error(exc) from exc
        # Any other exception propagates unchanged (return None).


async def _safe_rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:  # connection already broken; the original error matters more
        logger.warning("database.rollback.failed", exc_info=True)


async def _safe_close(session: AsyncSession) -> None:
    try:
        await session.close()
    except Exception:
        logger.warning("database.session_close.failed", exc_info=True)
