"""Conversation ownership (ADR-039).

Ownership model:

* Every conversation (thread) belongs to exactly one tenant and one user: the
  user who started it. The public thread id is unique per tenant.
* There is **no sharing**: only the owner can continue, resume, read the status
  of, or forget a conversation. Sharing and administrator access are open
  questions (PROJECT_STATUS Q16); nothing here guesses a rule for them.
* Another user's or another tenant's conversation looks exactly like one that
  does not exist (`ConversationNotFoundError`), and the attempt is audited.

Storage: `conversation_thread` in PostgreSQL (RLS by tenant), or a process-local
store without a database. Checkpoints use the tenant-namespaced storage key.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agentic_platform.core.errors import ConversationNotFoundError
from agentic_platform.core.threads import storage_thread_id, validate_thread_id
from agentic_platform.db.conversations import ConversationRepository, ConversationThread
from agentic_platform.db.engine import Database
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.identity import Principal
from agentic_platform.security.permissions import Permission


@dataclass(frozen=True)
class ConversationRef:
    tenant_id: str
    thread_id: str
    owner_user_id: str

    @property
    def storage_key(self) -> str:
        """Checkpointer / lock key: `<tenant_id>/<thread_id>`."""
        return storage_thread_id(self.tenant_id, self.thread_id)


class ConversationStore(Protocol):
    async def open(self, tenant_id: str, thread_id: str, owner: str) -> ConversationRef | None:
        """Create for `owner` or touch if `owner` owns it; None if someone else does."""
        ...

    async def get(self, tenant_id: str, thread_id: str) -> ConversationRef | None: ...

    async def delete(self, tenant_id: str, thread_id: str) -> bool: ...

    async def list_for_owner(
        self, tenant_id: str, owner: str, limit: int
    ) -> list[ConversationRef]: ...


def _ref(row: ConversationThread) -> ConversationRef:
    return ConversationRef(row.tenant_id, row.thread_id, row.owner_user_id)


class PostgresConversationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def open(self, tenant_id: str, thread_id: str, owner: str) -> ConversationRef | None:
        async with self._database.unit_of_work(tenant_id=tenant_id) as uow:
            row = await ConversationRepository(uow.session).open(tenant_id, thread_id, owner)
            return _ref(row) if row else None

    async def get(self, tenant_id: str, thread_id: str) -> ConversationRef | None:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            row = await ConversationRepository(uow.session).get(tenant_id, thread_id)
            return _ref(row) if row else None

    async def delete(self, tenant_id: str, thread_id: str) -> bool:
        async with self._database.unit_of_work(tenant_id=tenant_id) as uow:
            return await ConversationRepository(uow.session).delete(tenant_id, thread_id)

    async def list_for_owner(self, tenant_id: str, owner: str, limit: int) -> list[ConversationRef]:
        async with self._database.unit_of_work(read_only=True, tenant_id=tenant_id) as uow:
            rows: Sequence[ConversationThread] = await ConversationRepository(
                uow.session
            ).list_for_owner(tenant_id, owner, limit=limit)
            return [_ref(r) for r in rows]


class InMemoryConversationStore:
    """Process-local (development, tests). Same behaviour as the PostgreSQL store."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ConversationRef] = {}
        self._order: dict[tuple[str, str], int] = {}
        self._clock = 0

    async def open(self, tenant_id: str, thread_id: str, owner: str) -> ConversationRef | None:
        key = (tenant_id, thread_id)
        existing = self._rows.get(key)
        if existing is not None and existing.owner_user_id != owner:
            return None
        self._clock += 1
        self._order[key] = self._clock
        ref = existing or ConversationRef(tenant_id, thread_id, owner)
        self._rows[key] = ref
        return ref

    async def get(self, tenant_id: str, thread_id: str) -> ConversationRef | None:
        return self._rows.get((tenant_id, thread_id))

    async def delete(self, tenant_id: str, thread_id: str) -> bool:
        self._order.pop((tenant_id, thread_id), None)
        return self._rows.pop((tenant_id, thread_id), None) is not None

    async def list_for_owner(self, tenant_id: str, owner: str, limit: int) -> list[ConversationRef]:
        mine = [k for k, r in self._rows.items() if k[0] == tenant_id and r.owner_user_id == owner]
        mine.sort(key=lambda k: self._order[k], reverse=True)
        return [self._rows[k] for k in mine[:limit]]


class ConversationService:
    def __init__(self, store: ConversationStore, authorizer: Authorizer) -> None:
        self._store = store
        self._authorizer = authorizer

    async def open_for_turn(self, principal: Principal, thread_id: str) -> ConversationRef:
        """Start or continue the principal's conversation (`conversation.write`)."""
        thread_id = validate_thread_id(thread_id)
        resource = f"conversation:{thread_id}"
        await self._authorizer.require(principal, Permission.CONVERSATION_WRITE, resource=resource)
        ref = await self._store.open(principal.tenant_id, thread_id, principal.user_id)
        if ref is None:
            await self._authorizer.deny(principal, None, reason="not_owner", resource=resource)
            raise ConversationNotFoundError(f"{principal.user_id} does not own {resource}.")
        return ref

    async def require_access(
        self, principal: Principal, thread_id: str, permission: str
    ) -> ConversationRef:
        """An existing conversation of the principal; anything else is 'not found'."""
        thread_id = validate_thread_id(thread_id)
        resource = f"conversation:{thread_id}"
        await self._authorizer.require(principal, permission, resource=resource)
        ref = await self._store.get(principal.tenant_id, thread_id)
        if ref is None:
            raise ConversationNotFoundError(f"No {resource} in the principal's tenant.")
        await self._authorizer.require_owner(
            principal, ref.owner_user_id, resource=resource, error=ConversationNotFoundError
        )
        return ref

    async def find(self, principal: Principal, thread_id: str) -> ConversationRef | None:
        """The principal's conversation, or None (missing and not-yours look the same)."""
        try:
            return await self.require_access(principal, thread_id, Permission.CONVERSATION_READ)
        except ConversationNotFoundError:
            return None

    async def remove(self, ref: ConversationRef) -> bool:
        """Delete the ownership record (after the conversation's data is gone)."""
        return await self._store.delete(ref.tenant_id, ref.thread_id)

    async def list_mine(self, principal: Principal, *, limit: int = 50) -> list[ConversationRef]:
        await self._authorizer.require(principal, Permission.CONVERSATION_READ)
        return await self._store.list_for_owner(principal.tenant_id, principal.user_id, limit)
