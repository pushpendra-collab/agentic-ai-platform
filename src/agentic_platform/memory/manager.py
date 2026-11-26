"""Memory manager: the single entry point from the agent to memory (ADR-033).

    Agent
      ↓
    MemoryManager            recall / record_turn / forget / purge
      ↓
    Memory types             working · conversation · long_term · semantic · episodic
                             · procedural · checkpoints
      ↓
    Storage adapters         Redis/in-memory (working) · PostgreSQL+pgvector or in-memory
                             (durable + vectors) · LangGraph checkpointer (conversation,
                             checkpoints) · Embedder (hashing / Voyage)

Recall is best-effort. Each section fails independently and is reported in
`MemoryContext.errors`; a memory outage never fails a turn.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from agentic_platform.cache import CacheRuntime, TemporaryStateStore
from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import AgentPlatformError
from agentic_platform.core.logging import get_logger
from agentic_platform.db.engine import Database
from agentic_platform.memory.adapters.checkpoint import CheckpointConversationSource
from agentic_platform.memory.adapters.in_memory import InMemoryMemoryStore
from agentic_platform.memory.adapters.postgres import PostgresMemoryStore
from agentic_platform.memory.checkpoints import CheckpointMemory
from agentic_platform.memory.conversation import ConversationMemory
from agentic_platform.memory.embeddings import create_embedder
from agentic_platform.memory.episodic import EpisodicMemory
from agentic_platform.memory.long_term import LongTermMemory
from agentic_platform.memory.models import (
    Episode,
    MemoryContext,
    MemoryKind,
    MemoryQuery,
    MemoryScope,
)
from agentic_platform.memory.ports import Embedder, MemoryStore, VectorStore
from agentic_platform.memory.procedural import ProceduralMemory
from agentic_platform.memory.semantic import SemanticMemory
from agentic_platform.memory.working import WorkingMemory
from agentic_platform.prompts import PromptRegistry

logger = get_logger(__name__)

_DURABLE_KINDS = tuple(MemoryKind)


class MemoryManager:
    def __init__(
        self,
        *,
        store: MemoryStore,
        vectors: VectorStore,
        embedder: Embedder,
        working_store: TemporaryStateStore,
        prompts: PromptRegistry | None,
        settings: AppSettings,
    ) -> None:
        mem = settings.memory
        self._settings = mem
        self._store = store
        self._embedder = embedder
        self.working = WorkingMemory(working_store, ttl_seconds=mem.working_ttl_seconds)
        self.conversation = ConversationMemory(store)
        self.long_term = LongTermMemory(store, vectors, embedder, max_chars=mem.max_content_chars)
        self.semantic = SemanticMemory(
            store,
            vectors,
            embedder,
            chunk_chars=mem.chunk_chars,
            overlap_chars=mem.chunk_overlap_chars,
        )
        self.episodic = EpisodicMemory(
            store, vectors, embedder, retention_days=mem.episodic_retention_days
        )
        self.procedural = ProceduralMemory(store, prompts, max_chars=mem.max_content_chars)
        self.checkpoints = CheckpointMemory()

    @property
    def store_name(self) -> str:
        return self._store.name

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def default_scope(self) -> MemoryScope:
        return MemoryScope(self._settings.default_tenant_id, self._settings.default_user_id)

    def bind_checkpointer(self, saver: BaseCheckpointSaver[Any] | None) -> None:
        """Conversation history and checkpoint state read from the agent's checkpointer."""
        self.conversation.bind_source(CheckpointConversationSource(saver) if saver else None)
        self.checkpoints.bind(saver)

    # -- agent hooks --------------------------------------------------------------------

    async def recall(self, scope: MemoryScope, *, thread_id: str, query: str) -> MemoryContext:
        """Gather memory relevant to `query` for one turn, within the recall timeout."""
        context = MemoryContext()
        try:
            async with asyncio.timeout(self._settings.recall_timeout_seconds):
                await self._recall_into(context, scope, thread_id, query)
        except TimeoutError:
            context.errors.append("MEMORY_RECALL_TIMEOUT")
            logger.warning("memory.recall.timeout")
        return context

    async def _recall_into(
        self, context: MemoryContext, scope: MemoryScope, thread_id: str, query: str
    ) -> None:
        k, min_score = self._settings.recall_k, self._settings.recall_min_score

        async def section(name: str, coro: Any) -> Any:
            try:
                return await coro
            except AgentPlatformError as exc:
                context.errors.append(exc.code)
                logger.warning(
                    "memory.recall.section_failed",
                    extra={"fields": {"section": name, "code": exc.code}},
                )
            except Exception:
                context.errors.append("MEMORY_ERROR")
                logger.exception(
                    "memory.recall.section_crashed", extra={"fields": {"section": name}}
                )
            return None

        working = await section("working", self.working.get_all(scope, thread_id))
        context.working = working or {}
        summary = await section("conversation", self.conversation.summary(scope, thread_id))
        context.conversation_summary = summary.content if summary else None
        if k == 0:
            return
        embedding = await section("embedding", self._embedder.embed_query(query))
        if embedding is None:
            return
        facts, knowledge, episodes = await asyncio.gather(
            section(
                "long_term",
                self.long_term.search(
                    scope, query, k=k, min_score=min_score, query_embedding=embedding
                ),
            ),
            section(
                "semantic",
                self.semantic.search(
                    scope, query, k=k, min_score=min_score, query_embedding=embedding
                ),
            ),
            section(
                "episodic",
                self.episodic.recall(
                    scope, query, k=k, min_score=min_score, query_embedding=embedding
                ),
            ),
        )
        context.facts = facts or []
        context.knowledge = knowledge or []
        context.episodes = episodes or []

    async def record_turn(self, scope: MemoryScope, episode: Episode) -> None:
        """Remember a finished turn (episodic). Never raises."""
        if not self._settings.record_episodes:
            return
        try:
            await self.episodic.record(scope, episode)
        except Exception:
            logger.warning("memory.episode.record_failed", exc_info=True)

    # -- retention ----------------------------------------------------------------------

    async def forget_thread(
        self, scope: MemoryScope, thread_id: str, *, checkpoint_key: str | None = None
    ) -> dict[str, int]:
        """Delete one conversation: checkpoints, summary, working memory, its episodes.

        `checkpoint_key` is the thread's checkpointer key when it differs from the
        public thread id (Phase 5: `<tenant>/<thread>`, see core/threads.py).
        Callers must have checked ownership (`AuthorizedMemory` / `Agent` do).
        """
        key = checkpoint_key or thread_id
        summaries = await self.conversation.forget(scope, thread_id, checkpoint_key=key)
        episodes = await self.episodic.forget(scope, thread_id=thread_id)
        await self.working.clear(scope, thread_id)
        if self.checkpoints.available:
            await self.checkpoints.delete(key)
        return {"conversation_summary": summaries, "episodic": episodes}

    async def forget_user(self, scope: MemoryScope) -> int:
        """Delete every durable memory owned by the scope's user (right to erasure).

        Tenant-shared memory is kept. Working memory expires on its own (TTL).
        Checkpoints are keyed by thread, not user, so threads must be deleted with
        `forget_thread` (planned: bind threads to users with identity).
        """
        if scope.user_id is None:
            raise ValueError("forget_user needs a user; use tenant tooling for shared memory.")
        return await self._store.delete(scope, MemoryQuery(kinds=_DURABLE_KINDS))

    async def purge_expired(self) -> int:
        """Retention job: delete expired durable memories (all tenants)."""
        purged = await self._store.purge_expired()
        logger.info("memory.purged", extra={"fields": {"count": purged}})
        return purged

    async def purge_expired_batch(self, limit: int) -> dict[str, int]:
        """One bounded retention step (Phase 6 maintenance): at most `limit` expired
        durable memories of all tenants. Returns deleted counts by kind."""
        return await self._store.purge_expired_batch(limit)


def build_memory_manager(
    settings: AppSettings,
    *,
    database: Database | None,
    cache: CacheRuntime,
    prompts: PromptRegistry | None = None,
    embedder: Embedder | None = None,
) -> MemoryManager | None:
    """PostgreSQL+pgvector when a database is configured, otherwise process-local."""
    if not settings.memory.enabled:
        return None
    store: PostgresMemoryStore | InMemoryMemoryStore = (
        PostgresMemoryStore(database) if database is not None else InMemoryMemoryStore()
    )
    return MemoryManager(
        store=store,
        vectors=store,
        embedder=embedder or create_embedder(settings.memory),
        working_store=TemporaryStateStore(
            cache.backend, default_ttl_seconds=settings.memory.working_ttl_seconds
        ),
        prompts=prompts,
        settings=settings,
    )
