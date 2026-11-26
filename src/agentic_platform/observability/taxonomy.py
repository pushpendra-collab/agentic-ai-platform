"""Operational error taxonomy (Phase 13, ADR-062).

One closed set of categories over every failure the platform can report. It does **not**
replace the exception hierarchy in `core/errors.py` (which owns the stable public `code`,
`public_message` and `retryable` of the API contract); it groups those codes for
operators, metrics, retry decisions and diagnostics. The mapping is deterministic:

1. an exact error `code` (`_BY_CODE`): platform codes, tool codes, business reasons;
2. else the exception type, most specific first (`_BY_TYPE`);
3. else `INTERNAL`.

Callers never see a category: `PublicError` and the HTTP error body are unchanged.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum

from agentic_platform.core import errors as e


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    AMBIGUITY = "ambiguity"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    TIMEOUT = "timeout"
    RETRYABLE_DEPENDENCY = "retryable_dependency"
    NON_RETRYABLE_DEPENDENCY = "non_retryable_dependency"
    MODEL = "model"
    TOOL = "tool"
    DOMAIN = "domain"
    CHECKPOINT = "checkpoint"
    SERIALIZATION = "serialization"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


C = ErrorCategory

# Categories where trying the *same read* again later can succeed. Whether the platform
# retries automatically is decided per operation by a RetryPolicy (observability/retry.py).
TRANSIENT_CATEGORIES = frozenset({C.DEPENDENCY_UNAVAILABLE, C.RETRYABLE_DEPENDENCY, C.TIMEOUT})

_BY_CODE: dict[str, ErrorCategory] = {
    # caller input
    "INVALID_INPUT": C.VALIDATION,
    "INVALID_THREAD_ID": C.VALIDATION,
    "INVALID_MEMORY_SCOPE": C.VALIDATION,
    "THREAD_HAS_PENDING_TURN": C.VALIDATION,
    "NOTHING_TO_RESUME": C.VALIDATION,
    "TOOL_INVALID_INPUT": C.VALIDATION,
    "VALIDATION_ERROR": C.VALIDATION,
    "FUTURE_DATE": C.VALIDATION,
    "MIXED_CURRENCY": C.VALIDATION,
    "REQUEST_TOO_LARGE": C.VALIDATION,
    "IDEMPOTENCY_KEY_REUSED": C.VALIDATION,
    # identity
    "AUTHENTICATION_REQUIRED": C.AUTHENTICATION,
    "INVALID_CREDENTIALS": C.AUTHENTICATION,
    "TENANT_REQUIRED": C.AUTHENTICATION,
    "IDENTITY_PROVIDER_UNAVAILABLE": C.DEPENDENCY_UNAVAILABLE,
    # authorization (missing permission, wrong actor, approval refusals)
    "PERMISSION_DENIED": C.AUTHORIZATION,
    "PERMISSION_MISSING": C.AUTHORIZATION,
    "WAREHOUSE_SCOPE_EMPTY": C.AUTHORIZATION,
    "RESUME_CONTEXT_INVALID": C.AUTHORIZATION,
    "TOOL_NOT_ALLOWED": C.AUTHORIZATION,
    "APPROVAL_INVALID": C.AUTHORIZATION,
    "APPROVAL_STALE": C.AUTHORIZATION,
    "APPROVAL_REJECTED": C.AUTHORIZATION,
    "APPROVAL_EXPIRED": C.AUTHORIZATION,
    "APPROVAL_CANCELLED": C.AUTHORIZATION,
    "APPROVAL_PENDING": C.AUTHORIZATION,
    "APPROVAL_ALREADY_DECIDED": C.AUTHORIZATION,
    "APPROVAL_ALREADY_USED": C.AUTHORIZATION,
    "DATABASE_PERMISSION_DENIED": C.AUTHORIZATION,
    # not found / ambiguity (hidden cross-tenant resources look exactly like this)
    "NOT_FOUND": C.NOT_FOUND,
    "CONVERSATION_NOT_FOUND": C.NOT_FOUND,
    "APPROVAL_NOT_FOUND": C.NOT_FOUND,
    "RECORD_NOT_FOUND": C.NOT_FOUND,
    "MEMORY_NOT_FOUND": C.NOT_FOUND,
    "TOOL_NOT_FOUND": C.TOOL,
    "NO_MATCH": C.NOT_FOUND,
    "SEVERAL_MATCHES": C.AMBIGUITY,
    "AMBIGUOUS": C.AMBIGUITY,
    # dependencies
    "DATABASE_UNAVAILABLE": C.DEPENDENCY_UNAVAILABLE,
    "DATABASE_NOT_CONFIGURED": C.CONFIGURATION,
    "CACHE_UNAVAILABLE": C.DEPENDENCY_UNAVAILABLE,
    "CACHE_NOT_CONFIGURED": C.CONFIGURATION,
    "EMBEDDING_UNAVAILABLE": C.DEPENDENCY_UNAVAILABLE,
    "ERP_UNAVAILABLE": C.DEPENDENCY_UNAVAILABLE,
    "ERP_NOT_CONFIGURED": C.DEPENDENCY_UNAVAILABLE,
    "RULE_NOT_VERIFIED": C.NON_RETRYABLE_DEPENDENCY,
    "MALFORMED_RESPONSE": C.NON_RETRYABLE_DEPENDENCY,
    "TENANT_MISMATCH": C.NON_RETRYABLE_DEPENDENCY,
    "ERP_SAFETY_VIOLATION": C.NON_RETRYABLE_DEPENDENCY,
    "DATABASE_CONCURRENCY_CONFLICT": C.RETRYABLE_DEPENDENCY,
    "IDEMPOTENCY_IN_PROGRESS": C.RETRYABLE_DEPENDENCY,
    "RESOURCE_BUSY": C.RETRYABLE_DEPENDENCY,
    "THREAD_BUSY": C.RETRYABLE_DEPENDENCY,
    "RATE_LIMITED": C.RETRYABLE_DEPENDENCY,
    "SERVER_BUSY": C.RETRYABLE_DEPENDENCY,  # Phase 15: concurrent-turn cap
    "LLM_UNAVAILABLE": C.RETRYABLE_DEPENDENCY,
    "DATABASE_INTEGRITY_VIOLATION": C.NON_RETRYABLE_DEPENDENCY,
    "DATABASE_DUPLICATE": C.NON_RETRYABLE_DEPENDENCY,
    "DATABASE_READ_ONLY": C.NON_RETRYABLE_DEPENDENCY,
    "DATABASE_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "CACHE_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "LOCK_LOST": C.NON_RETRYABLE_DEPENDENCY,
    "IDEMPOTENCY_LEASE_LOST": C.NON_RETRYABLE_DEPENDENCY,
    "IDEMPOTENCY_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "LOCK_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "MEMORY_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "EMBEDDING_ERROR": C.NON_RETRYABLE_DEPENDENCY,
    "SECURITY_ERROR": C.AUTHORIZATION,
    "APPROVAL_ERROR": C.AUTHORIZATION,
    # timeouts
    "DATABASE_TIMEOUT": C.TIMEOUT,
    "CACHE_TIMEOUT": C.TIMEOUT,
    "ERP_TIMEOUT": C.TIMEOUT,
    "DOMAIN_TIMEOUT": C.TIMEOUT,
    # model
    "LLM_ERROR": C.MODEL,
    "LLM_AUTH_ERROR": C.CONFIGURATION,
    "LLM_REFUSAL": C.MODEL,
    "LLM_OUTPUT_ERROR": C.MODEL,
    # tools and domains
    "TOOL_ERROR": C.TOOL,
    "TOOL_EXECUTION_FAILED": C.TOOL,
    "TOOL_REGISTRATION_ERROR": C.CONFIGURATION,
    "ORCHESTRATION_FAILED": C.DOMAIN,
    "ORCHESTRATION_LIMIT_EXCEEDED": C.DOMAIN,
    "DOMAIN_REGISTRATION_ERROR": C.CONFIGURATION,
    "AGENT_STATE_ERROR": C.INTERNAL,
    "RECURSION_LIMIT": C.DOMAIN,
    # checkpoints
    "TURN_INCOMPLETE": C.CHECKPOINT,
    "CHECKPOINT_UNREADABLE": C.CHECKPOINT,
    "CHECKPOINT_SERIALIZATION_FAILED": C.SERIALIZATION,
    "CHECKPOINT_TYPE_REJECTED": C.SERIALIZATION,
    # configuration
    "CONFIGURATION_ERROR": C.CONFIGURATION,
    "FEATURE_UNAVAILABLE": C.CONFIGURATION,
    "PROMPT_ERROR": C.CONFIGURATION,
    "PROMPT_NOT_FOUND": C.CONFIGURATION,
    "PROMPT_RENDER_ERROR": C.CONFIGURATION,
    "MEMORY_POLICY_VIOLATION": C.VALIDATION,
    # unexpected
    "INTERNAL_ERROR": C.INTERNAL,
    "PLATFORM_ERROR": C.INTERNAL,
}

# Most specific first: the first matching type wins.
_BY_TYPE: tuple[tuple[type[BaseException], ErrorCategory], ...] = (
    (e.InvalidResumeContextError, C.AUTHORIZATION),
    (e.UnauthenticatedError, C.AUTHENTICATION),
    (e.IdentityProviderUnavailableError, C.DEPENDENCY_UNAVAILABLE),
    (e.ResourceNotFoundError, C.NOT_FOUND),
    (e.PermissionDeniedError, C.AUTHORIZATION),
    (e.ApprovalError, C.AUTHORIZATION),
    (e.SecurityError, C.AUTHORIZATION),
    (e.AgentInputError, C.VALIDATION),
    (e.ToolValidationError, C.VALIDATION),
    (e.ToolNotAllowedError, C.AUTHORIZATION),
    (e.ToolError, C.TOOL),
    (e.LLMTransientError, C.RETRYABLE_DEPENDENCY),
    (e.LLMAuthError, C.CONFIGURATION),
    (e.LLMError, C.MODEL),
    (e.DomainTimeoutError, C.TIMEOUT),
    (e.OrchestrationError, C.DOMAIN),
    (e.CheckpointSerializationError, C.SERIALIZATION),
    (e.CheckpointError, C.CHECKPOINT),
    (e.DatabaseTimeoutError, C.TIMEOUT),
    (e.CacheTimeoutError, C.TIMEOUT),
    (e.DatabaseUnavailableError, C.DEPENDENCY_UNAVAILABLE),
    (e.CacheUnavailableError, C.DEPENDENCY_UNAVAILABLE),
    (e.ConfigurationError, C.CONFIGURATION),
    (e.FeatureUnavailableError, C.CONFIGURATION),
    (e.PromptError, C.CONFIGURATION),
    (TimeoutError, C.TIMEOUT),
    (asyncio.CancelledError, C.TIMEOUT),
)


def classify_code(code: str | None) -> ErrorCategory:
    """Category of a stable error code (platform, tool or business reason)."""
    if not code:
        return C.INTERNAL
    return _BY_CODE.get(code, C.INTERNAL)


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Category of an exception: its `code` first, then its type, else INTERNAL."""
    code = getattr(exc, "code", None)
    if isinstance(exc, e.AgentPlatformError) and isinstance(code, str) and code in _BY_CODE:
        return _BY_CODE[code]
    for kind, category in _BY_TYPE:
        if isinstance(exc, kind):
            return category
    if isinstance(exc, e.AgentPlatformError):
        return C.RETRYABLE_DEPENDENCY if exc.retryable else C.NON_RETRYABLE_DEPENDENCY
    return C.INTERNAL


def classify_business(status: str | None, reason: str | None) -> ErrorCategory | None:
    """Category of a business read result; None for a truthful answer (SUCCESS/NOT_FOUND
    without an error: NOT_FOUND is reported as NOT_FOUND, not as a failure)."""
    if status in (None, "SUCCESS"):
        return None
    if status == "NOT_FOUND":
        return C.NOT_FOUND
    if status == "AMBIGUOUS":
        return C.AMBIGUITY
    if status == "UNAUTHORIZED":
        return C.AUTHORIZATION
    if status == "VALIDATION_ERROR":
        return C.VALIDATION
    if reason and reason in _BY_CODE:
        return _BY_CODE[reason]
    if status == "UNAVAILABLE":
        return C.DEPENDENCY_UNAVAILABLE
    return C.NON_RETRYABLE_DEPENDENCY


def is_transient(category: ErrorCategory) -> bool:
    return category in TRANSIENT_CATEGORIES


__all__ = [
    "TRANSIENT_CATEGORIES",
    "ErrorCategory",
    "classify_business",
    "classify_code",
    "classify_exception",
    "is_transient",
]
