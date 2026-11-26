"""Readiness: may this process receive traffic now? (Phase 6, ADR-045)

Built on the existing health services (ADR-026, ADR-030); it never raises.

| Check          | Ready when                                                       |
|----------------|------------------------------------------------------------------|
| database       | `ok` (reachable, schema at the migration head). Not configured:  |
|                | ready outside production (the database is optional, ADR-026);    |
|                | not ready in production (audit, approvals and RLS need it).      |
| redis          | `ok`, or not configured (process-local fallbacks, one process).  |
| authentication | an HTTP authenticator was configured at startup.                 |
| llm (Phase 7)  | never gates readiness. `ok` when a credential is configured,     |
|                | `not_configured` otherwise. Checked by configuration only: a     |
|                | probe never calls (or bills) the provider. A provider outage is  |
|                | DEGRADED: turns answer 503/502, every other endpoint works.      |
| erp (Phase 15) | never gates readiness, and never connects to the ERP.            |
|                | `not_configured` with ERP_ADAPTER=none (the default); `ok` for   |
|                | an adapter without a gate (test doubles) and for a live          |
|                | adapter whose security gate passed (ADR-063); `degraded` before  |
|                | the gate has run (first business read); `not_configured` when no |
|                | contract capability is VERIFIED; `misconfigured` when the gate   |
|                | refused the connection. Business reads fail closed on their own  |
|                | (UNAVAILABLE); taking the whole API out of rotation for an ERP   |
|                | problem would also stop history, approvals and general questions.|

Liveness (`/health`) depends on nothing; readiness is this report. There is no separate
startup probe: the server listens only after the application's startup has finished
(configuration validated, production policy enforced, runtime built), so the first
successful `/health` means "started" (ARCHITECTURE.md § A.19).

The report holds statuses, latencies, error codes and the build information
(`core/build.py`: version, commit, build time) only: no URLs, host names, server
versions, pool details or credentials (the endpoint is unauthenticated; the gateway does
not expose it publicly).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from agentic_platform.business.service import BusinessReadService
from agentic_platform.cache.redis import RedisConnection
from agentic_platform.core.build import BuildInfo, build_info
from agentic_platform.db.engine import Database
from agentic_platform.services.database_health import DatabaseHealthService
from agentic_platform.services.redis_health import RedisHealthService

CheckStatus = Literal["ok", "degraded", "unavailable", "not_configured", "misconfigured"]


class ComponentCheck(BaseModel):
    status: CheckStatus
    ready: bool
    latency_ms: int | None = None
    error_code: str | None = None


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, ComponentCheck]
    build: BuildInfo | None = None


# Phase 15: (status, error code) of the ERP component per live-adapter gate state.
_ERP_GATE: dict[str, tuple[CheckStatus, str | None]] = {
    "verified": ("ok", None),
    "pending": ("degraded", "ERP_NOT_YET_VERIFIED"),
    "unverified": ("not_configured", "ERP_NO_VERIFIED_CAPABILITY"),
    "refused": ("misconfigured", "ERP_SAFETY_VIOLATION"),
}


def erp_check(business: BusinessReadService | None) -> ComponentCheck:
    """The ERP component (Phase 15). Configuration and gate state only: never connects,
    and always `ready` (see the module docstring)."""
    if business is None:
        return ComponentCheck(status="not_configured", ready=True, error_code=None)
    state = business.gate_state()
    if state is None:  # an adapter without a gate (test doubles)
        return ComponentCheck(status="ok", ready=True)
    status, code = _ERP_GATE[state]
    return ComponentCheck(status=status, ready=True, error_code=code)


class ReadinessService:
    def __init__(
        self,
        *,
        database: Database | None,
        redis: RedisConnection | None,
        production: bool,
        authentication_configured: bool,
        llm_configured: bool = True,
        business: BusinessReadService | None = None,
    ) -> None:
        self._database = DatabaseHealthService(database)
        self._redis = RedisHealthService(redis)
        self._production = production
        self._auth = authentication_configured
        self._llm = llm_configured
        self._business = business

    async def check(self) -> Readiness:
        database, redis = await asyncio.gather(self._database.check(), self._redis.check())
        checks = {
            "database": ComponentCheck(
                status=database.status,
                ready=database.status == "ok"
                or (database.status == "not_configured" and not self._production),
                latency_ms=database.latency_ms,
                error_code=database.error_code,
            ),
            "redis": ComponentCheck(
                status=redis.status,
                ready=redis.status in ("ok", "not_configured"),
                latency_ms=redis.latency_ms,
                error_code=redis.error_code if redis.status != "not_configured" else None,
            ),
            "authentication": ComponentCheck(
                status="ok" if self._auth else "misconfigured",
                ready=self._auth,
                error_code=None if self._auth else "AUTHENTICATION_NOT_CONFIGURED",
            ),
            "llm": ComponentCheck(
                status="ok" if self._llm else "not_configured",
                ready=True,  # degraded, not down: only agent turns need it
                error_code=None if self._llm else "LLM_NOT_CONFIGURED",
            ),
            "erp": erp_check(self._business),
        }
        ready = all(check.ready for check in checks.values())
        return Readiness(
            status="ready" if ready else "not_ready", checks=checks, build=build_info()
        )
