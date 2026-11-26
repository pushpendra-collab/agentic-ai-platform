"""Translate SQLAlchemy / asyncpg exceptions into the platform `DatabaseError` hierarchy.

Classification uses the PostgreSQL SQLSTATE code where the driver provides one.
The resulting `detail` (logged, never shown to users) contains the exception class,
SQLSTATE and constraint name only: never SQL text or bound parameters, which may
hold business data.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import exc as sa_exc

from agentic_platform.core.errors import (
    DatabaseConcurrencyError,
    DatabaseConflictError,
    DatabaseError,
    DatabaseIntegrityError,
    DatabasePermissionError,
    DatabaseReadOnlyError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
)

_UNAVAILABLE_SQLSTATES = {"53300", "57P01", "57P02", "57P03"}  # too many conns, shutdown


def sqlstate_of(exc: BaseException) -> str | None:
    """SQLSTATE from a SQLAlchemy-wrapped or raw driver exception, if any."""
    candidates: list[BaseException | None] = [exc]
    if isinstance(exc, sa_exc.DBAPIError):
        candidates.append(exc.orig)
    for candidate in list(candidates):
        if candidate is not None:
            candidates.append(candidate.__cause__)
    for candidate in candidates:
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(code, str) and code:
            return code
    return None


def _describe(exc: BaseException, sqlstate: str | None) -> str:
    orig = exc.orig if isinstance(exc, sa_exc.DBAPIError) else exc
    parts = [type(orig).__name__ if orig is not None else type(exc).__name__]
    if sqlstate:
        parts.append(f"sqlstate={sqlstate}")
    for source in (orig, getattr(orig, "__cause__", None)):
        constraint = getattr(source, "constraint_name", None)
        if constraint:
            parts.append(f"constraint={constraint}")
            break
    return " ".join(parts)


def translate_db_error(exc: BaseException) -> DatabaseError:
    """Return the platform error for `exc`. Already-translated errors pass through."""
    if isinstance(exc, DatabaseError):
        return exc

    sqlstate = sqlstate_of(exc)
    detail = _describe(exc, sqlstate)

    if isinstance(exc, sa_exc.TimeoutError):  # connection pool exhausted
        return DatabaseUnavailableError(f"Connection pool timeout: {detail}")

    if sqlstate:
        if sqlstate.startswith("08") or sqlstate in _UNAVAILABLE_SQLSTATES:
            return DatabaseUnavailableError(detail)
        if sqlstate.startswith("28") or sqlstate == "3D000":
            # Bad credentials / unknown database: a configuration problem, not transient.
            return DatabaseUnavailableError(f"Misconfigured connection: {detail}", retryable=False)
        if sqlstate == "57014":
            return DatabaseTimeoutError(f"Statement cancelled (timeout): {detail}")
        if sqlstate in ("40001", "40P01"):
            return DatabaseConcurrencyError(detail)
        if sqlstate == "23505":
            return DatabaseConflictError(detail)
        if sqlstate.startswith("23"):
            return DatabaseIntegrityError(detail)
        if sqlstate == "25006":
            return DatabaseReadOnlyError(detail)
        if sqlstate == "42501":
            return DatabasePermissionError(detail)

    if isinstance(exc, sa_exc.DBAPIError) and exc.connection_invalidated:
        return DatabaseUnavailableError(f"Connection invalidated: {detail}")
    if isinstance(exc, sa_exc.InterfaceError | sa_exc.OperationalError):
        return DatabaseUnavailableError(detail)
    if isinstance(exc, OSError | asyncio.TimeoutError | TimeoutError):
        # Connection refused / DNS failure / connect timeout before a session existed.
        return DatabaseUnavailableError(detail)
    return DatabaseError(detail)
