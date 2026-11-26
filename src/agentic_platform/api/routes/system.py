"""Identity, audit and probes.

* `GET /api/v1/me`: who the token says I am (resolved on the server).
* `GET /api/v1/audit`: my tenant's audit trail (`audit.read`).
* `GET /health`: liveness. Process up; touches no dependency (cheap, never 5xx
  because a database is down).
* `GET /ready`: readiness. Database, Redis and authentication per
  `ReadinessService`; 503 until ready. Unauthenticated and free of secrets.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from agentic_platform.api.dependencies import PrincipalDep, RuntimeDep, rate_limit
from agentic_platform.api.errors import ERROR_RESPONSES
from agentic_platform.api.schemas import (
    ID_PATTERN,
    AuditEntryView,
    AuditListView,
    HealthView,
    PrincipalView,
)
from agentic_platform.services.rate_limit import LimitKind
from agentic_platform.services.readiness import Readiness

identity_router = APIRouter(tags=["identity"], responses=ERROR_RESPONSES)
audit_router = APIRouter(tags=["audit"], responses=ERROR_RESPONSES)
probe_router = APIRouter(tags=["health"])


@identity_router.get(
    "/me", response_model=PrincipalView, summary="The authenticated caller.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def me(principal: PrincipalDep) -> PrincipalView:
    return PrincipalView.of(principal)


@audit_router.get(
    "/audit", response_model=AuditListView, summary="My tenant's audit trail.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def audit(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    thread_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditListView:
    records = await runtime.audit.list(principal, thread_id=thread_id, limit=limit)
    return AuditListView(entries=[AuditEntryView.of(r) for r in records])


@probe_router.get("/health", response_model=HealthView, summary="Liveness probe.")
async def health() -> HealthView:
    return HealthView(status="ok")


@probe_router.get(
    "/ready",
    response_model=Readiness,
    responses={503: {"model": Readiness, "description": "Not ready."}},
    summary="Readiness probe (database, Redis, authentication).",
)
async def ready(runtime: RuntimeDep) -> JSONResponse:
    readiness = await runtime.readiness.check()
    return JSONResponse(
        status_code=200 if readiness.status == "ready" else 503,
        content=readiness.model_dump(mode="json"),
    )
