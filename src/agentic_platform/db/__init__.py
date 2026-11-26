"""PostgreSQL access layer (SQLAlchemy 2 async + asyncpg).

    Service ──opens──► UnitOfWork (one transaction) ──provides session──► Repository ──► PostgreSQL

* `Database` owns the engine and connection pool (one per process).
* `UnitOfWork` is the transaction boundary: commit on success, rollback on error,
  and translation of driver errors into `core.errors.DatabaseError` subclasses.
* `Repository` subclasses do data access only and never commit.
* Services hold business rules and decide transaction scope.

Nothing here is reachable by the LLM except through registered tools (ADR-004).
"""

from agentic_platform.db.base import Base, TimestampMixin
from agentic_platform.db.engine import Database
from agentic_platform.db.repository import Repository
from agentic_platform.db.unit_of_work import UnitOfWork

__all__ = ["Base", "Database", "Repository", "TimestampMixin", "UnitOfWork"]
