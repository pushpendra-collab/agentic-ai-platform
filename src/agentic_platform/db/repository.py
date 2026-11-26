"""Generic repository base.

Repositories perform data access for one aggregate using SQLAlchemy expressions.
They contain no business rules, never commit, and never accept SQL text from
callers. Domain repositories subclass `Repository[Model]` and add typed query
methods (for example `find_open_by_supplier(...)`) instead of exposing generic
filtering.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import ScalarResult, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from agentic_platform.core.errors import RecordNotFoundError

ModelT = TypeVar("ModelT", bound=DeclarativeBase)

MAX_PAGE_SIZE = 500


class Repository(Generic[ModelT]):
    model: ClassVar[type[Any]]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get(self, primary_key: Any) -> ModelT | None:
        entity: ModelT | None = await self._session.get(self.model, primary_key)
        return entity

    async def get_required(self, primary_key: Any) -> ModelT:
        entity = await self.get(primary_key)
        if entity is None:
            raise RecordNotFoundError(f"{self.model.__name__} {primary_key!r} not found.")
        return entity

    async def list_page(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        """A page of rows in primary-key order. `limit` is capped at MAX_PAGE_SIZE."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        order = list(inspect(self.model).primary_key)
        stmt = select(self.model).order_by(*order).limit(limit).offset(offset)
        result: ScalarResult[ModelT] = await self._session.scalars(stmt)
        return result.all()

    async def count(self) -> int:
        result = await self._session.scalar(select(func.count()).select_from(self.model))
        return int(result or 0)

    async def add(self, entity: ModelT) -> ModelT:
        """Stage a new row and flush so database defaults and constraint errors surface now."""
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self._session.delete(entity)
        await self._session.flush()
