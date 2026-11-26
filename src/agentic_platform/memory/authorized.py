"""Authorized memory: the permission-checked, audited way into memory (ADR-038).

`MemoryManager` (Phase 4) does what it is asked for any `MemoryScope`. This
facade is what callers acting for a user go through:

* the scope is **derived from the authenticated principal**, never passed in,
  so nobody can name another tenant or user;
* every operation needs a permission (security/permissions.py):

  | Operation                                   | Permission                     |
  |---------------------------------------------|--------------------------------|
  | recall, read facts / knowledge / history    | memory.read                    |
  | write or update own facts, private docs     | memory.write                   |
  | delete own facts, forget own memory         | memory.delete                  |
  | shared facts, tenant knowledge, procedures  | memory.shared.write            |
  | delete shared facts / knowledge / procedures| memory.shared.delete           |
  | save a conversation summary                 | memory.write + owns the thread |

* every write and delete is audited (content as a SHA-256 hash only);
* automatic steps of a turn (recall before, episode after) are skipped, not
  failed, when the user lacks the permission, and are not audited per turn.

The agent still has **no unrestricted memory-writing path**: the only agent
route to long-term memory is the `memory.remember_fact` tool, which is off by
default and needs the user's approval for every write (ADR-040).
"""

from __future__ import annotations

from datetime import timedelta

from agentic_platform.core.hashing import stable_hash
from agentic_platform.core.logging import get_logger
from agentic_platform.memory.manager import MemoryManager
from agentic_platform.memory.models import (
    ConversationTurn,
    Episode,
    MemoryContext,
    MemoryRecord,
    ScoredMemory,
)
from agentic_platform.security.audit import ActorType, AuditAction, SecurityAudit
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.identity import Principal
from agentic_platform.security.permissions import Permission
from agentic_platform.services.conversations import ConversationService

logger = get_logger(__name__)


class AuthorizedMemory:
    def __init__(
        self,
        memory: MemoryManager,
        authorizer: Authorizer,
        conversations: ConversationService,
    ) -> None:
        self._memory = memory
        self._authorizer = authorizer
        self._audit: SecurityAudit = authorizer.audit
        self._conversations = conversations

    @property
    def manager(self) -> MemoryManager:
        """The unchecked manager, for trusted platform code (retention jobs, tests)."""
        return self._memory

    # -- automatic turn steps -----------------------------------------------------------

    async def recall(self, principal: Principal, *, thread_id: str, query: str) -> MemoryContext:
        if not principal.has(Permission.MEMORY_READ):
            logger.info("memory.recall.skipped", extra={"fields": {"reason": "no memory.read"}})
            return MemoryContext()
        return await self._memory.recall(principal.memory_scope, thread_id=thread_id, query=query)

    async def record_turn(self, principal: Principal, episode: Episode) -> None:
        if not principal.has(Permission.MEMORY_WRITE):
            logger.info("memory.episode.skipped", extra={"fields": {"reason": "no memory.write"}})
            return
        await self._memory.record_turn(principal.memory_scope, episode)

    # -- long-term facts ----------------------------------------------------------------

    async def remember_fact(
        self,
        principal: Principal,
        namespace: str,
        key: str,
        content: str,
        *,
        shared: bool = False,
        ttl: timedelta | None = None,
        source_type: str = "user",
        actor_type: ActorType = "user",
    ) -> MemoryRecord:
        """Create or update a fact. Shared (tenant-wide) facts need `memory.shared.write`."""
        permission = Permission.MEMORY_SHARED_WRITE if shared else Permission.MEMORY_WRITE
        resource = f"memory:fact:{namespace}/{key}"
        await self._authorizer.require(principal, permission, resource=resource)
        existing = await self._memory.long_term.get(
            principal.memory_scope, namespace, key, shared=shared
        )
        record = await self._memory.long_term.remember(
            principal.memory_scope,
            namespace,
            key,
            content,
            ttl=ttl,
            shared=shared,
            source_type=source_type,
        )
        await self._written(
            principal,
            AuditAction.MEMORY_FACT_UPDATE if existing else AuditAction.MEMORY_FACT_CREATE,
            record,
            content,
            actor_type=actor_type,
            details={"namespace": namespace, "key": key, "shared": shared},
        )
        return record

    async def get_fact(
        self, principal: Principal, namespace: str, key: str, *, shared: bool = False
    ) -> MemoryRecord | None:
        await self._authorizer.require(principal, Permission.MEMORY_READ)
        return await self._memory.long_term.get(
            principal.memory_scope, namespace, key, shared=shared
        )

    async def list_facts(
        self, principal: Principal, *, namespace: str | None = None, limit: int = 50
    ) -> list[MemoryRecord]:
        await self._authorizer.require(principal, Permission.MEMORY_READ)
        return await self._memory.long_term.list_facts(
            principal.memory_scope, namespace=namespace, limit=limit
        )

    async def forget_fact(
        self, principal: Principal, namespace: str, key: str, *, shared: bool = False
    ) -> bool:
        permission = Permission.MEMORY_SHARED_DELETE if shared else Permission.MEMORY_DELETE
        await self._authorizer.require(
            principal, permission, resource=f"memory:fact:{namespace}/{key}"
        )
        deleted = await self._memory.long_term.forget(
            principal.memory_scope, namespace, key, shared=shared
        )
        await self._deleted(
            principal,
            AuditAction.MEMORY_FACT_DELETE,
            1 if deleted else 0,
            details={"namespace": namespace, "key": key, "shared": shared},
        )
        return deleted

    # -- semantic knowledge -------------------------------------------------------------

    async def ingest_document(
        self,
        principal: Principal,
        *,
        source_ref: str,
        text: str,
        source_type: str = "document",
        title: str | None = None,
        shared: bool = True,
    ) -> list[MemoryRecord]:
        """Tenant knowledge (default) needs `memory.shared.write`; a private note,
        `memory.write`."""
        permission = Permission.MEMORY_SHARED_WRITE if shared else Permission.MEMORY_WRITE
        await self._authorizer.require(principal, permission, resource=f"memory:doc:{source_ref}")
        chunks = await self._memory.semantic.ingest(
            principal.memory_scope,
            source_ref=source_ref,
            text=text,
            source_type=source_type,
            title=title,
            shared=shared,
        )
        await self._audit.record_required(
            self._audit.event(
                AuditAction.MEMORY_DOCUMENT_WRITE,
                "ok",
                principal,
                input_hash=stable_hash(text),
                entity_refs=[{"type": "memory_document", "id": source_ref}],
                details={"chunks": len(chunks), "shared": shared, "source_type": source_type},
            )
        )
        return chunks

    async def delete_document(
        self, principal: Principal, source_ref: str, *, shared: bool = True
    ) -> int:
        permission = Permission.MEMORY_SHARED_DELETE if shared else Permission.MEMORY_DELETE
        await self._authorizer.require(principal, permission, resource=f"memory:doc:{source_ref}")
        deleted = await self._memory.semantic.delete_source(
            principal.memory_scope, source_ref, shared=shared
        )
        await self._deleted(
            principal,
            AuditAction.MEMORY_DOCUMENT_DELETE,
            deleted,
            entity_refs=[{"type": "memory_document", "id": source_ref}],
            details={"shared": shared},
        )
        return deleted

    async def search_knowledge(
        self, principal: Principal, query: str, *, k: int = 5
    ) -> list[ScoredMemory]:
        await self._authorizer.require(principal, Permission.MEMORY_READ)
        return await self._memory.semantic.search(principal.memory_scope, query, k=k)

    # -- procedures (tenant-wide) -------------------------------------------------------

    async def define_procedure(
        self, principal: Principal, name: str, content: str
    ) -> MemoryRecord:
        await self._authorizer.require(
            principal, Permission.MEMORY_SHARED_WRITE, resource=f"memory:procedure:{name}"
        )
        record = await self._memory.procedural.define(principal.memory_scope, name, content)
        await self._written(
            principal,
            AuditAction.MEMORY_PROCEDURE_WRITE,
            record,
            content,
            details={"name": name, "version": record.version},
        )
        return record

    async def delete_procedure_version(
        self, principal: Principal, name: str, version: int
    ) -> bool:
        await self._authorizer.require(
            principal, Permission.MEMORY_SHARED_DELETE, resource=f"memory:procedure:{name}"
        )
        deleted = await self._memory.procedural.delete_version(
            principal.memory_scope, name, version
        )
        await self._deleted(
            principal,
            AuditAction.MEMORY_PROCEDURE_DELETE,
            1 if deleted else 0,
            details={"name": name, "version": version},
        )
        return deleted

    # -- conversations ------------------------------------------------------------------

    async def history(
        self, principal: Principal, thread_id: str, *, limit: int = 20
    ) -> list[ConversationTurn]:
        ref = await self._conversations.require_access(
            principal, thread_id, Permission.CONVERSATION_READ
        )
        return await self._memory.conversation.history(ref.storage_key, limit=limit)

    async def save_summary(self, principal: Principal, thread_id: str, text: str) -> MemoryRecord:
        await self._conversations.require_access(principal, thread_id, Permission.MEMORY_WRITE)
        record = await self._memory.conversation.save_summary(
            principal.memory_scope, thread_id, text
        )
        await self._written(
            principal, AuditAction.MEMORY_SUMMARY_WRITE, record, text, details={}
        )
        return record

    async def forget_me(self, principal: Principal) -> int:
        """Delete all of the principal's private durable memory (tenant-shared memory stays)."""
        await self._authorizer.require(principal, Permission.MEMORY_DELETE, resource="memory:user")
        deleted = await self._memory.forget_user(principal.memory_scope)
        await self._deleted(principal, AuditAction.MEMORY_USER_FORGET, deleted, details={})
        return deleted

    # -- helpers ------------------------------------------------------------------------

    async def _written(
        self,
        principal: Principal,
        action: str,
        record: MemoryRecord,
        content: str,
        *,
        actor_type: ActorType = "user",
        details: dict[str, object],
    ) -> None:
        await self._audit.record_required(
            self._audit.event(
                action,
                "ok",
                principal,
                actor_type=actor_type,
                input_hash=stable_hash(content),
                entity_refs=[{"type": "memory", "id": str(record.id)}],
                details={**details, "kind": record.kind.value},
            )
        )

    async def _deleted(
        self,
        principal: Principal,
        action: str,
        count: int,
        *,
        entity_refs: list[dict[str, str]] | None = None,
        details: dict[str, object],
    ) -> None:
        await self._audit.record_required(
            self._audit.event(
                action,
                "ok",
                principal,
                entity_refs=entity_refs or [],
                details={**details, "deleted": count},
            )
        )
