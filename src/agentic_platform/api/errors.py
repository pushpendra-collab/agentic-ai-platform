"""One error envelope for every API failure, and the platform-error → HTTP mapping.

    {"error": {"code": "...", "message": "...", "retryable": false,
               "request_id": "...", "details": [...]}}

* `code` is the platform's stable machine-readable code (`core/errors.py`).
* `message` is the error's `public_message`: written to be shown to users. Internal
  details (`detail`, SQL, stack traces, provider payloads) go to logs only.
* Another tenant's or user's resource is reported exactly like a missing one
  (404 + the same code and message), as in Phase 5.

* 401: no, malformed, forged or expired credentials (AUTHENTICATION_REQUIRED,
  INVALID_CREDENTIALS, TENANT_REQUIRED);
* 403: authenticated, but not allowed (PERMISSION_DENIED, RESUME_CONTEXT_INVALID);
* 404: missing, or not visible to the caller (NOT_FOUND, CONVERSATION_NOT_FOUND,
  APPROVAL_NOT_FOUND, MEMORY_NOT_FOUND);
* 409: wrong state or conflict (THREAD_HAS_PENDING_TURN, NOTHING_TO_RESUME,
  APPROVAL_PENDING, APPROVAL_ALREADY_DECIDED, APPROVAL_REJECTED, THREAD_BUSY,
  IDEMPOTENCY_KEY_REUSED);
* 410: the approval expired (APPROVAL_EXPIRED);
* 413: body too large (REQUEST_TOO_LARGE);
* 429: the caller's request budget is spent (RATE_LIMITED, with `Retry-After`; ADR-053);
* 422: request validation (VALIDATION_ERROR, INVALID_INPUT, INVALID_THREAD_ID,
  MEMORY_POLICY_VIOLATION);
* 502: the language model failed (LLM_ERROR, LLM_OUTPUT_ERROR, LLM_REFUSAL);
* 503: a dependency is down or a feature is unavailable (DATABASE_UNAVAILABLE,
  CACHE_UNAVAILABLE, IDENTITY_PROVIDER_UNAVAILABLE, FEATURE_UNAVAILABLE);
* 500: anything else (INTERNAL_ERROR).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentic_platform.core.errors import (
    AgentInputError,
    AgentPlatformError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ConfigurationError,
    DatabaseNotConfiguredError,
    FeatureUnavailableError,
    IdempotencyError,
    IdentityProviderUnavailableError,
    InvalidIdentityError,
    LLMError,
    LockNotAcquiredError,
    MemoryNotFoundError,
    MemoryPolicyError,
    NothingToResumeError,
    PermissionDeniedError,
    RateLimitedError,
    ResourceNotFoundError,
    SecurityError,
    ThreadBusyError,
    ThreadPendingError,
    UnauthenticatedError,
)
from agentic_platform.core.logging import get_logger

logger = get_logger(__name__)

INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "An internal error occurred."
VALIDATION_ERROR_CODE = "VALIDATION_ERROR"
REQUEST_TOO_LARGE_CODE = "REQUEST_TOO_LARGE"


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    # Validation problems only: where and what (never the submitted values).
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# (exception type, HTTP status), most specific first.
_STATUS: tuple[tuple[type[AgentPlatformError], int], ...] = (
    (RateLimitedError, 429),
    (IdentityProviderUnavailableError, 503),
    (UnauthenticatedError, 401),
    (ResourceNotFoundError, 404),
    (ApprovalNotFoundError, 404),
    (ApprovalExpiredError, 410),
    (ApprovalError, 409),
    (PermissionDeniedError, 403),
    (SecurityError, 403),
    (ThreadPendingError, 409),
    (NothingToResumeError, 409),
    (AgentInputError, 422),
    (MemoryNotFoundError, 404),
    (MemoryPolicyError, 422),
    (IdempotencyError, 409),
    (ThreadBusyError, 409),
    (LockNotAcquiredError, 409),
    (FeatureUnavailableError, 503),
    (DatabaseNotConfiguredError, 503),
    (ConfigurationError, 500),
)

# Codes of failed agent turns (`AgentResponse.errors`), which arrive as codes, not exceptions.
_STATUS_BY_CODE = {
    "THREAD_BUSY": 409,
    "RESOURCE_BUSY": 409,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "IDEMPOTENCY_IN_PROGRESS": 409,
}


def status_for(exc: AgentPlatformError) -> int:
    for kind, status in _STATUS:
        if isinstance(exc, kind):
            return status
    if isinstance(exc, LLMError):
        return 503 if exc.retryable else 502
    return 503 if exc.retryable else 500


def status_for_code(code: str, retryable: bool) -> int:
    if code in _STATUS_BY_CODE:
        return _STATUS_BY_CODE[code]
    if code.startswith("LLM_"):
        return 503 if retryable else 502
    return 503 if retryable else 500


def request_id_of(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def error_json(
    status: int,
    code: str,
    message: str,
    *,
    request_id: str | None,
    retryable: bool = False,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
            request_id=request_id,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status, content=body.model_dump(exclude_none=True), headers=headers
    )


def error_bytes(code: str, message: str, request_id: str) -> bytes:
    """The envelope as bytes (for the ASGI middleware, outside FastAPI)."""
    body = ErrorBody(code=code, message=message, request_id=request_id)
    return json.dumps({"error": body.model_dump(exclude_none=True)}).encode("utf-8")


def _auth_headers(exc: AgentPlatformError) -> dict[str, str] | None:
    if not isinstance(exc, UnauthenticatedError):
        return None
    if isinstance(exc, InvalidIdentityError):
        return {"WWW-Authenticate": 'Bearer error="invalid_token"'}
    return {"WWW-Authenticate": "Bearer"}


def _retry_headers(exc: AgentPlatformError) -> dict[str, str] | None:
    if isinstance(exc, RateLimitedError):
        return {"Retry-After": str(exc.retry_after_seconds)}
    return {"Retry-After": "2"} if exc.retryable else None


async def platform_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AgentPlatformError)
    status = status_for(exc)
    fields = {"code": exc.code, "status": status, "detail": exc.detail}
    if status >= 500:
        logger.error("http.request.error", extra={"fields": fields})
    else:
        logger.info("http.request.refused", extra={"fields": fields})
    headers = _auth_headers(exc) or _retry_headers(exc)
    return error_json(
        status,
        exc.code,
        exc.public_message,
        request_id=request_id_of(request),
        retryable=exc.retryable,
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "message": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in exc.errors()
    ]
    logger.info("http.request.invalid", extra={"fields": {"problems": len(details)}})
    return error_json(
        422,
        VALIDATION_ERROR_CODE,
        "The request is not valid.",
        request_id=request_id_of(request),
        details=details,
    )


_HTTP_CODES = {
    404: ("NOT_FOUND", "The requested resource was not found."),
    405: ("METHOD_NOT_ALLOWED", "This method is not allowed on this resource."),
    413: (REQUEST_TOO_LARGE_CODE, "The request body is too large."),
}


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, message = _HTTP_CODES.get(
        exc.status_code, (f"HTTP_{exc.status_code}", "The request could not be processed.")
    )
    return error_json(
        exc.status_code,
        code,
        message,
        request_id=request_id_of(request),
        headers=dict(exc.headers) if exc.headers else None,
    )


class RequestTooLarge(HTTPException):
    """Raised while the body is read (FastAPI re-raises HTTPException unchanged)."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AgentPlatformError, platform_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)


# OpenAPI: the envelope for the documented error statuses of protected routes.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, malformed, forged or expired token."},
    403: {"model": ErrorResponse, "description": "Authenticated, but not permitted."},
    404: {"model": ErrorResponse, "description": "Missing, or not visible to the caller."},
    422: {"model": ErrorResponse, "description": "The request is not valid."},
    429: {"model": ErrorResponse, "description": "Rate limit reached; see Retry-After."},
    500: {"model": ErrorResponse, "description": "Internal error (no details are exposed)."},
    503: {"model": ErrorResponse, "description": "A dependency is temporarily unavailable."},
}
