"""Read-only probes used by the database health service.

These are fixed, parameter-free statements; no caller input reaches the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ServerInfo:
    server_version: str
    database: str
    in_recovery: bool


class DatabaseHealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def server_info(self) -> ServerInfo:
        row = (
            await self._session.execute(
                text(
                    "SELECT current_setting('server_version'), current_database(), "
                    "pg_is_in_recovery()"
                )
            )
        ).one()
        return ServerInfo(server_version=row[0], database=row[1], in_recovery=bool(row[2]))

    async def migration_revision(self) -> str | None:
        """Current Alembic revision, or None if migrations have never run."""
        exists = await self._session.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if exists is None:
            return None
        revision: str | None = await self._session.scalar(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        return revision
