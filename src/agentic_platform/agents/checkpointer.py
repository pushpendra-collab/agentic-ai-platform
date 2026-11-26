"""Durable LangGraph checkpointing on PostgreSQL via asyncpg (ADR-009, ADR-027).

LangGraph's official PostgreSQL saver needs psycopg 3, which this platform
does not use (ADR-022; its DLLs are blocked on developer machines). This saver
implements the same `BaseCheckpointSaver` contract on the existing data layer:
every operation runs in a `UnitOfWork` (reads are read-only transactions), and
SQL is built by `db/checkpoints.CheckpointRepository` from typed arguments.

Only the async API is implemented. The agent always uses `ainvoke`; the sync
methods raise `NotImplementedError` with a clear message.

`create_checkpointer` picks the saver from `AGENT_CHECKPOINTER`.

Tenant isolation (Phase 5, ADR-041): every transaction first binds a tenant for
row-level security. It is the authenticated principal's tenant when one is bound
to the request (`security.acting_as`), so a checkpoint key of another tenant is
invisible and cannot be written even if application code computed it wrongly.
Without a principal (maintenance, tests) the tenant is taken from the key itself.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.base import SerializerProtocol

from agentic_platform.agents.serialization import checkpoint_serializer
from agentic_platform.core.config import CheckpointerKind
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.core.threads import tenant_of_storage_key
from agentic_platform.db.checkpoints import (
    CheckpointBlobRow,
    CheckpointQuery,
    CheckpointRepository,
    CheckpointRow,
)
from agentic_platform.db.engine import Database
from agentic_platform.security.context import current_principal

_EMPTY = "empty"


class PostgresCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(self, database: Database, *, serde: SerializerProtocol | None = None) -> None:
        # Phase 12 (ADR-061): the explicit type allow-list unless a caller passes its own.
        super().__init__(serde=serde or checkpoint_serializer())
        self._database = database

    # -- reads -------------------------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        async with self._database.unit_of_work(read_only=True, tenant_id=_tenant(thread_id)) as uow:
            repo = CheckpointRepository(uow.session)
            row = await repo.get_checkpoint(thread_id, checkpoint_ns, get_checkpoint_id(config))
            if row is None:
                return None
            return await self._to_tuple(repo, row)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        configurable = config["configurable"] if config else {}
        query = CheckpointQuery(
            thread_id=str(configurable["thread_id"]) if "thread_id" in configurable else None,
            checkpoint_ns=configurable.get("checkpoint_ns"),
            checkpoint_id=configurable.get("checkpoint_id"),
            before_checkpoint_id=get_checkpoint_id(before) if before else None,
            metadata_contains=filter,
            limit=limit,
        )
        # Materialise inside one read-only transaction: a consumer that stops early
        # must not leave a transaction (and pooled connection) open.
        async with self._database.unit_of_work(
            read_only=True, tenant_id=_tenant(query.thread_id)
        ) as uow:
            repo = CheckpointRepository(uow.session)
            rows = await repo.list_checkpoints(query)
            tuples = [await self._to_tuple(repo, row) for row in rows]
        for item in tuples:
            yield item

    # -- writes ------------------------------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        body: dict[str, Any] = dict(checkpoint)
        values: dict[str, Any] = body.pop("channel_values")
        checkpoint_type, checkpoint_bytes = self.serde.dumps_typed(body)

        blobs = []
        for channel, version in new_versions.items():
            if channel in values:
                value_type, value = self.serde.dumps_typed(values[channel])
            else:
                value_type, value = _EMPTY, None
            blobs.append(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "channel": channel,
                    "version": str(version),
                    "value_type": value_type,
                    "value": value,
                }
            )

        async with self._database.unit_of_work(tenant_id=_tenant(thread_id)) as uow:
            repo = CheckpointRepository(uow.session)
            await repo.insert_blobs(blobs)
            await repo.upsert_checkpoint(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint["id"],
                    "parent_checkpoint_id": configurable.get("checkpoint_id"),
                    "checkpoint_type": checkpoint_type,
                    "checkpoint": checkpoint_bytes,
                    "metadata": _metadata_json(config, metadata),
                }
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        base = {
            "thread_id": str(configurable["thread_id"]),
            "checkpoint_ns": configurable.get("checkpoint_ns", ""),
            "checkpoint_id": configurable["checkpoint_id"],
            "task_id": task_id,
            "task_path": task_path,
        }
        regular: list[dict[str, Any]] = []
        special: list[dict[str, Any]] = []
        for idx, (channel, value) in enumerate(writes):
            value_type, data = self.serde.dumps_typed(value)
            row = {
                **base,
                "idx": WRITES_IDX_MAP.get(channel, idx),
                "channel": channel,
                "value_type": value_type,
                "value": data,
            }
            (special if channel in WRITES_IDX_MAP else regular).append(row)

        async with self._database.unit_of_work(tenant_id=_tenant(base["thread_id"])) as uow:
            repo = CheckpointRepository(uow.session)
            await repo.save_writes(regular, overwrite=False)
            await repo.save_writes(special, overwrite=True)

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._database.unit_of_work(tenant_id=_tenant(str(thread_id))) as uow:
            await CheckpointRepository(uow.session).delete_thread(str(thread_id))

    def get_next_version(self, current: str | None, channel: None) -> str:
        """Same scheme as LangGraph's savers: zero-padded counter plus random suffix."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    # -- sync API: not supported --------------------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise _sync_unsupported()

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise _sync_unsupported()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise _sync_unsupported()

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise _sync_unsupported()

    def delete_thread(self, thread_id: str) -> None:
        raise _sync_unsupported()

    # -- helpers -----------------------------------------------------------------------

    async def _to_tuple(self, repo: CheckpointRepository, row: CheckpointRow) -> CheckpointTuple:
        body: dict[str, Any] = self.serde.loads_typed((row.checkpoint_type, row.checkpoint))
        versions = {str(k): str(v) for k, v in body.get("channel_versions", {}).items()}
        blobs = await repo.get_blobs(row.thread_id, row.checkpoint_ns, versions)
        writes = await repo.get_writes(row.thread_id, row.checkpoint_ns, row.checkpoint_id)
        checkpoint: Checkpoint = {**body, "channel_values": self._load_blobs(blobs)}  # type: ignore[typeddict-item]
        return CheckpointTuple(
            config=_config(row.thread_id, row.checkpoint_ns, row.checkpoint_id),
            checkpoint=checkpoint,
            metadata=row.metadata_,  # type: ignore[arg-type]
            parent_config=(
                _config(row.thread_id, row.checkpoint_ns, row.parent_checkpoint_id)
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=[
                (w.task_id, w.channel, self.serde.loads_typed((w.value_type, w.value)))
                for w in writes
            ],
        )

    def _load_blobs(self, blobs: Sequence[CheckpointBlobRow]) -> dict[str, Any]:
        return {
            blob.channel: self.serde.loads_typed((blob.value_type, blob.value))
            for blob in blobs
            if blob.value_type != _EMPTY and blob.value is not None
        }


def _tenant(thread_id: str | None) -> str | None:
    """The RLS tenant for a checkpoint transaction: the request's principal, else the key's."""
    principal = current_principal()
    if principal is not None:
        return principal.tenant_id
    return tenant_of_storage_key(thread_id) if thread_id is not None else None


def _config(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
        }
    }


def _metadata_json(config: RunnableConfig, metadata: CheckpointMetadata) -> dict[str, Any]:
    """JSON-safe metadata. PostgreSQL JSONB rejects the NUL character, so it is removed."""
    raw = json.dumps(get_serializable_checkpoint_metadata(config, metadata))
    loaded: dict[str, Any] = json.loads(raw.replace("\\u0000", ""))
    return loaded


def _sync_unsupported() -> NotImplementedError:
    return NotImplementedError(
        "PostgresCheckpointSaver is async-only; use ainvoke/astream/aget_state."
    )


def create_checkpointer(
    kind: CheckpointerKind, database: Database | None
) -> BaseCheckpointSaver[Any] | None:
    """`none`: stateless turns. `memory`: process-local. `postgres`: durable (needs
    DATABASE_URL and migrations). `auto`: postgres when a database is configured,
    otherwise memory."""
    if kind == "none":
        return None
    # Phase 12 (ADR-061): explicit checkpoint type allow-list (agents/serialization.py).
    if kind == "memory" or (kind == "auto" and database is None):
        return InMemorySaver(serde=checkpoint_serializer())
    if database is None:
        raise ConfigurationError("AGENT_CHECKPOINTER=postgres requires DATABASE_URL.")
    return PostgresCheckpointSaver(database)


__all__ = ["PostgresCheckpointSaver", "create_checkpointer"]
