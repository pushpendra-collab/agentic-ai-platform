"""Request dependencies: the runtime, and the authenticated principal.

HTTP authentication (ADR-043):

    Authorization: Bearer <token>
        → ApiRuntime.authenticator (development tokens or the OIDC provider)
        → Principal (tenant, user, roles; permissions resolved on the server)

* Only the `Authorization` header is read: never a query parameter, cookie or body
  field, and never the local development identity (no header means 401).
* The principal is resolved once per request (FastAPI caches the dependency).
* Tokens are never logged or audited; a refused credential is audited by the
  authenticator with a reason code only. A request with no credential at all is
  refused without an audit row (unauthenticated probes must not fill the
  append-only audit log).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentic_platform.api.runtime import ApiRuntime
from agentic_platform.core.errors import (
    InvalidIdentityError,
    SecurityError,
    UnauthenticatedError,
)
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit
from agentic_platform.observability.taxonomy import classify_exception
from agentic_platform.security.audit import AuditAction
from agentic_platform.security.identity import Principal
from agentic_platform.services.rate_limit import LimitKind

_BEARER = re.compile(r"^Bearer +([A-Za-z0-9\-._~+/]+=*)$", re.IGNORECASE)
_MAX_HEADER_CHARS = 8192

# Documents the scheme in OpenAPI; the header itself is parsed below.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Access token: an OIDC/JWT token from the identity provider, or (outside "
    "production) a development token.",
)


def get_runtime(request: Request) -> ApiRuntime:
    runtime: ApiRuntime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[ApiRuntime, Depends(get_runtime)]


async def get_principal(
    request: Request,
    runtime: RuntimeDep,
    _: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    try:
        principal = await _authenticate(request, runtime)
    except SecurityError as exc:
        # Phase 13: code and category only, never the credential or the reason detail.
        emit(EventName.AUTH_FAILED, stage=Stage.SECURITY, status="rejected",
             error_category=classify_exception(exc), error_code=exc.code,
             attributes={"method": "http"})  # fmt: skip
        raise
    emit(EventName.AUTH_SUCCEEDED, stage=Stage.SECURITY, status="ok",
         attributes={"method": principal.auth_method})  # fmt: skip
    return principal


async def _authenticate(request: Request, runtime: ApiRuntime) -> Principal:
    header = request.headers.get("authorization")
    if header is None or not header.strip():
        raise UnauthenticatedError("No Authorization header.")
    match = _BEARER.fullmatch(header.strip()) if len(header) <= _MAX_HEADER_CHARS else None
    if match is None:
        error = InvalidIdentityError("Authorization header is not a bearer token.")
        audit = runtime.deps.security.audit
        await audit.record(
            audit.event(
                AuditAction.AUTH_FAILED,
                "denied",
                None,
                error_code=error.code,
                details={"reason": error.detail, "method": "http"},
            )
        )
        raise error
    return await runtime.authenticator.authenticate(match.group(1))


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def rate_limit(kind: LimitKind) -> Any:
    """Route dependency: count the request against the caller's budget (ADR-053).

    Runs after authentication (the key is the verified principal) and before the
    handler. It never grants anything: the handler's authorization is unchanged."""

    async def check_rate_limit(principal: PrincipalDep, runtime: RuntimeDep) -> None:
        if runtime.rate_limiter is not None:
            await runtime.rate_limiter.check(principal, kind)

    return Depends(check_rate_limit)


async def _hold_turn_slot(_: PrincipalDep, runtime: RuntimeDep) -> AsyncIterator[None]:
    runtime.turn_slots.acquire()  # SERVER_BUSY (503, retryable) when all are taken
    try:
        yield
    finally:
        runtime.turn_slots.release()


def turn_slot() -> Any:
    """Route dependency for agent turns (Phase 15, ADR-064): hold one of the process's
    API_MAX_CONCURRENT_TURNS slots for the whole turn. Listed after `rate_limit`, and it
    needs the principal, so unauthenticated or rate-limited requests never hold one."""
    return Depends(_hold_turn_slot)
