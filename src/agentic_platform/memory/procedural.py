"""Procedural memory: versioned how-to knowledge (prompts, playbooks, instructions).

Two sources, one interface:

* **Built-in** procedures are the versioned prompt files shipped with the code
  (`prompts/templates/<name>.v<N>.md` via `PromptRegistry`). They are read-only
  here and change through code review.
* **Tenant** procedures are stored as versioned `procedural` records. Defining a
  procedure always creates a new version; earlier versions are kept, so the
  behaviour of any version can be reproduced. A tenant version overrides the
  built-in one of the same name.

The agent's own prompts still load from `PromptRegistry` directly; per-tenant
overrides of agent prompts are planned, not wired.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from agentic_platform.core.errors import MemoryPolicyError, PromptNotFoundError
from agentic_platform.memory.long_term import check_name
from agentic_platform.memory.models import (
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
)
from agentic_platform.memory.ports import MemoryStore
from agentic_platform.prompts import PromptRegistry


class Procedure(BaseModel):
    name: str
    version: int
    content: str
    source: Literal["tenant", "builtin"]
    metadata: dict[str, Any] = {}


class ProceduralMemory:
    def __init__(
        self, store: MemoryStore, prompts: PromptRegistry | None, *, max_chars: int
    ) -> None:
        self._store = store
        self._prompts = prompts
        self._max_chars = max_chars

    async def define(
        self,
        scope: MemoryScope,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        """Store a new version of the tenant procedure `name`."""
        check_name("procedure name", name)
        if not content.strip() or len(content) > self._max_chars:
            raise MemoryPolicyError(f"Procedure must be 1-{self._max_chars} characters.")
        tenant = MemoryScope(scope.tenant_id, None)
        version = await self._store.next_version(tenant, MemoryKind.PROCEDURAL.value, name)
        (record,) = await self._store.insert(
            tenant,
            [
                NewMemory(
                    kind=MemoryKind.PROCEDURAL,
                    namespace=name,
                    key=name,
                    version=version,
                    content=content,
                    metadata={**(metadata or {}), "defined_by": scope.user_id},
                    source_type="procedure",
                    shared=True,
                )
            ],
        )
        return record

    async def get(
        self, scope: MemoryScope, name: str, *, version: int | None = None
    ) -> Procedure | None:
        """The tenant version (latest, or `version`), else the built-in prompt."""
        tenant = MemoryScope(scope.tenant_id, None)
        found = await self._store.find(
            tenant,
            MemoryQuery(kinds=(MemoryKind.PROCEDURAL,), namespace=name, version=version, limit=200),
        )
        if found:
            latest = max(found, key=lambda r: r.version)
            return Procedure(
                name=name,
                version=latest.version,
                content=latest.content,
                source="tenant",
                metadata=latest.metadata,
            )
        if self._prompts is None:
            return None
        try:
            template = self._prompts.get(name, version)
        except PromptNotFoundError:
            return None
        return Procedure(
            name=name, version=template.version, content=template.text, source="builtin"
        )

    async def versions(self, scope: MemoryScope, name: str) -> list[MemoryRecord]:
        found = await self._store.find(
            MemoryScope(scope.tenant_id, None),
            MemoryQuery(kinds=(MemoryKind.PROCEDURAL,), namespace=name, limit=200),
        )
        return sorted(found, key=lambda r: r.version)

    async def delete_version(self, scope: MemoryScope, name: str, version: int) -> bool:
        deleted = await self._store.delete(
            MemoryScope(scope.tenant_id, None),
            MemoryQuery(kinds=(MemoryKind.PROCEDURAL,), namespace=name, version=version),
        )
        return deleted > 0
