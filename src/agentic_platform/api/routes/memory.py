"""Memory over HTTP: only operations `AuthorizedMemory` already supports and authorizes.

Scope is always the caller's principal (own private memory + the tenant's shared
memory). Permissions: reads `memory.read`; private writes/deletes `memory.write` /
`memory.delete`; shared (tenant-wide) facts and knowledge `memory.shared.write` /
`memory.shared.delete`. Every write and delete is audited (content hashed only).

Not exposed: raw queries, other users' or tenants' memory, episodes, working
memory, procedures, embeddings, and anything resembling SQL.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentic_platform.api.dependencies import PrincipalDep, RuntimeDep, rate_limit
from agentic_platform.api.errors import ERROR_RESPONSES
from agentic_platform.api.schemas import (
    NAME_PATTERN,
    DeletedView,
    DocumentWriteRequest,
    DocumentWriteView,
    FactListView,
    FactView,
    FactWriteRequest,
    KnowledgeHit,
    KnowledgeSearchRequest,
    KnowledgeSearchView,
)
from agentic_platform.core.errors import MemoryNotFoundError
from agentic_platform.services.rate_limit import LimitKind

router = APIRouter(prefix="/memory", tags=["memory"], responses=ERROR_RESPONSES)

Name = Annotated[str, Path(pattern=NAME_PATTERN)]
Shared = Annotated[bool, Query(description="The tenant-wide (shared) item instead of mine.")]


@router.get(
    "/facts", response_model=FactListView, summary="My facts and the tenant's shared facts.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def list_facts(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    namespace: Annotated[str | None, Query(pattern=NAME_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FactListView:
    records = await runtime.memory().list_facts(principal, namespace=namespace, limit=limit)
    return FactListView(facts=[FactView.of(r) for r in records])


@router.get(
    "/facts/{namespace}/{key}", response_model=FactView, summary="One fact.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def get_fact(
    namespace: Name, key: Name, principal: PrincipalDep, runtime: RuntimeDep, shared: Shared = False
) -> FactView:
    record = await runtime.memory().get_fact(principal, namespace, key, shared=shared)
    if record is None:
        raise MemoryNotFoundError("No such fact in the caller's scope.")
    return FactView.of(record)


@router.put(
    "/facts/{namespace}/{key}", response_model=FactView, summary="Create or update a fact.",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def put_fact(
    namespace: Name,
    key: Name,
    body: FactWriteRequest,
    principal: PrincipalDep,
    runtime: RuntimeDep,
) -> FactView:
    record = await runtime.memory().remember_fact(
        principal,
        namespace,
        key,
        body.content,
        shared=body.shared,
        ttl=timedelta(seconds=body.ttl_seconds) if body.ttl_seconds else None,
    )
    return FactView.of(record)


@router.delete(
    "/facts/{namespace}/{key}", response_model=DeletedView, summary="Delete a fact.",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def delete_fact(
    namespace: Name, key: Name, principal: PrincipalDep, runtime: RuntimeDep, shared: Shared = False
) -> DeletedView:
    if not await runtime.memory().forget_fact(principal, namespace, key, shared=shared):
        raise MemoryNotFoundError("No such fact in the caller's scope.")
    return DeletedView(deleted=1)


@router.put(
    "/documents/{source_ref}",
    response_model=DocumentWriteView,
    summary="Add or replace knowledge (tenant-wide by default) or a private note.",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def put_document(
    source_ref: Name, body: DocumentWriteRequest, principal: PrincipalDep, runtime: RuntimeDep
) -> DocumentWriteView:
    chunks = await runtime.memory().ingest_document(
        principal,
        source_ref=source_ref,
        text=body.text,
        source_type=body.source_type,
        title=body.title,
        shared=body.shared,
    )
    return DocumentWriteView(source_ref=source_ref, chunks=len(chunks), shared=body.shared)


@router.delete(
    "/documents/{source_ref}", response_model=DeletedView, summary="Delete knowledge or a note.",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def delete_document(
    source_ref: Name, principal: PrincipalDep, runtime: RuntimeDep, shared: Shared = True
) -> DeletedView:
    deleted = await runtime.memory().delete_document(principal, source_ref, shared=shared)
    if deleted == 0:
        raise MemoryNotFoundError("No such document in the caller's scope.")
    return DeletedView(deleted=deleted)


@router.post(
    "/knowledge/search",
    response_model=KnowledgeSearchView,
    summary="Search the tenant's knowledge and my notes (context, not business truth).",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def search_knowledge(
    body: KnowledgeSearchRequest, principal: PrincipalDep, runtime: RuntimeDep
) -> KnowledgeSearchView:
    hits = await runtime.memory().search_knowledge(principal, body.query, k=body.k)
    return KnowledgeSearchView(hits=[KnowledgeHit.of(h) for h in hits])


@router.delete(
    "/me",
    response_model=DeletedView,
    summary="Delete all of my private durable memory (tenant-shared memory stays).",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def forget_me(principal: PrincipalDep, runtime: RuntimeDep) -> DeletedView:
    return DeletedView(deleted=await runtime.memory().forget_me(principal))
