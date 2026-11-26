"""FastAPI application factory (Phase 6, ADR-043 to ADR-045).

    HTTP request
      ↓ RequestContextMiddleware   request id, log context, body limit, last-resort 500
      ↓ router (/api/v1, /health, /ready)
      ↓ get_principal              Authorization: Bearer → Authenticator → Principal
      ↓ handler                    request/response mapping only, no business rules
      ↓ Agent / services           ownership, permissions (Authorizer), approvals, memory
      ↓ repositories → PostgreSQL (RLS) / Redis

`create_app()` builds everything at once (one `Agent`, one authenticator), so a
misconfiguration (for example no usable authentication) stops the process at
startup instead of failing requests. Connections are opened lazily and closed on
shutdown.

Phase 15 (ADR-064): startup logs `api.started` with the build information and one
`api.startup.dependencies` line (the readiness report: database at the migration head,
Redis, ERP gate), without blocking or failing the start: readiness keeps gating
traffic. Shutdown runs the bounded sequence of `ApiRuntime.aclose` (services/lifecycle.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentic_platform.agents.agent import Agent
from agentic_platform.api.errors import install_error_handlers
from agentic_platform.api.middleware import RequestContextMiddleware
from agentic_platform.api.routes import api_v1, probes
from agentic_platform.api.runtime import ApiRuntime, build_runtime
from agentic_platform.core.build import build_info
from agentic_platform.core.config import AppSettings, get_settings
from agentic_platform.core.logging import get_logger
from agentic_platform.security.authentication import Authenticator

logger = get_logger(__name__)

API_VERSION = "1.0.0"
DESCRIPTION = """
HTTP API of the agentic platform (platform infrastructure only: no business agents).

**Authentication.** Every `/api/v1` route needs `Authorization: Bearer <token>`: an
access token from the configured OIDC identity provider or, outside production only,
a development token. Tenant, user and roles come only from the token; request bodies
cannot name them. Permissions are resolved on the server.

**Errors** use one envelope: `{"error": {"code", "message", "retryable", "request_id"}}`.
Another user's or tenant's resource answers 404, like a missing one.

**Agent turns** answer 200 when finished, 202 when paused for approval or an answer
(`approval_required` / `input_request`), and 409 or 5xx when the turn failed.

Every response carries `X-Request-ID`, the correlation id used in logs and the audit trail.
"""


def create_app(
    settings: AppSettings | None = None,
    *,
    agent: Agent | None = None,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    runtime = build_runtime(settings, agent=agent, authenticator=authenticator)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime.scheduler is not None:
            runtime.scheduler.start()
            logger.info("api.maintenance_scheduler.started")
        build = build_info()
        logger.info("api.started", extra={"fields": {"env": settings.env, **build.model_dump()}})
        await _log_startup_dependencies(runtime)
        try:
            yield
        finally:
            report = await runtime.aclose()
            logger.info("api.stopped", extra={"fields": {"shutdown": report.status}})

    docs = settings.docs_enabled
    app = FastAPI(
        title="Agentic Platform API",
        version=API_VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.runtime = runtime
    app.add_middleware(
        RequestContextMiddleware,
        max_body_bytes=settings.api.max_body_bytes,
        in_flight=runtime.in_flight,
    )
    install_error_handlers(app)
    app.include_router(probes)
    app.include_router(api_v1)
    # The local test chat page: development only, and only with development tokens.
    if settings.env == "development" and runtime.agent.dependencies.security.tokens is not None:
        from agentic_platform.api.routes import chat_ui

        app.include_router(chat_ui.router)
        logger.info("api.chat_ui.mounted", extra={"fields": {"path": "/chat"}})
    return app


async def _log_startup_dependencies(runtime: ApiRuntime) -> None:
    """One readiness report at startup, for the operator (statuses and codes only). The
    checks are individually bounded (DATABASE_/REDIS_HEALTH_TIMEOUT_SECONDS)."""
    try:
        report = await runtime.readiness.check()
    except Exception:  # never stops the start: /ready keeps reporting
        logger.warning("api.startup.dependencies_unchecked", exc_info=True)
        return
    checks = {name: check.status for name, check in report.checks.items()}
    level = logger.info if report.status == "ready" else logger.warning
    level("api.startup.dependencies", extra={"fields": {"status": report.status, **checks}})


def runtime_of(app: FastAPI) -> ApiRuntime:
    runtime: ApiRuntime = app.state.runtime
    return runtime
