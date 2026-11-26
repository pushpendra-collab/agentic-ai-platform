"""Typed error hierarchy.

Every platform error carries a stable machine-readable `code`, a `public_message`
that is safe to show to users and to the LLM, and a `retryable` flag. Internal
details (stack traces, provider payloads) go to logs only, never into
`public_message` (ARCHITECTURE.md § B.7).
"""

from __future__ import annotations


class AgentPlatformError(Exception):
    code: str = "PLATFORM_ERROR"
    default_public_message: str = "An internal error occurred."
    retryable: bool = False

    def __init__(
        self,
        detail: str | None = None,
        *,
        public_message: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.detail = detail or self.default_public_message
        self.public_message = public_message or self.default_public_message
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.detail)


# --- Configuration --------------------------------------------------------------


class ConfigurationError(AgentPlatformError):
    code = "CONFIGURATION_ERROR"
    default_public_message = "The assistant is not configured correctly."


class FeatureUnavailableError(AgentPlatformError):
    """A capability that is switched off or needs infrastructure this deployment lacks
    (for example memory with MEMORY_ENABLED=false, or the audit log without a database)."""

    code = "FEATURE_UNAVAILABLE"
    default_public_message = "This feature is not available on this server."


# --- Prompts --------------------------------------------------------------------


class PromptError(AgentPlatformError):
    code = "PROMPT_ERROR"


class PromptNotFoundError(PromptError):
    code = "PROMPT_NOT_FOUND"


class PromptRenderError(PromptError):
    code = "PROMPT_RENDER_ERROR"


# --- LLM ------------------------------------------------------------------------


class LLMError(AgentPlatformError):
    code = "LLM_ERROR"
    default_public_message = "The language model could not complete the request."


class LLMTransientError(LLMError):
    """Rate limits, timeouts, overload, connection problems. Safe to retry later."""

    code = "LLM_UNAVAILABLE"
    default_public_message = "The language model is temporarily unavailable. Please try again."
    retryable = True


class LLMAuthError(LLMError):
    code = "LLM_AUTH_ERROR"
    default_public_message = "The assistant is not configured correctly."


class LLMRefusalError(LLMError):
    code = "LLM_REFUSAL"
    default_public_message = "The request could not be completed."


class LLMOutputError(LLMError):
    """The model answered, but the output was unusable (truncated, invalid schema)."""

    code = "LLM_OUTPUT_ERROR"


# --- Tools ----------------------------------------------------------------------


class ToolError(AgentPlatformError):
    code = "TOOL_ERROR"
    default_public_message = "The tool call failed."


class ToolNotFoundError(ToolError):
    code = "TOOL_NOT_FOUND"
    default_public_message = "The requested tool does not exist."


class ToolValidationError(ToolError):
    code = "TOOL_INVALID_INPUT"
    default_public_message = "The tool input was invalid."


class ToolNotAllowedError(ToolError):
    code = "TOOL_NOT_ALLOWED"
    default_public_message = "The tool is not allowed in this context."


class ToolPermissionDeniedError(ToolNotAllowedError):
    """The principal lacks the tool's `required_permission` (Phase 5)."""

    code = "PERMISSION_DENIED"
    default_public_message = "You do not have permission to use this tool."


class ToolExecutionError(ToolError):
    code = "TOOL_EXECUTION_FAILED"


class ToolRegistrationError(ToolError):
    code = "TOOL_REGISTRATION_ERROR"


# --- Database -------------------------------------------------------------------
# Raised only by the db layer (`db/errors.translate_db_error`). Messages never
# contain SQL text or bound parameters.


class DatabaseError(AgentPlatformError):
    code = "DATABASE_ERROR"
    default_public_message = "A data storage error occurred."


class DatabaseNotConfiguredError(DatabaseError):
    code = "DATABASE_NOT_CONFIGURED"
    default_public_message = "Data storage is not configured."


class DatabaseUnavailableError(DatabaseError):
    """Cannot connect, connection lost, pool exhausted, server shutting down."""

    code = "DATABASE_UNAVAILABLE"
    default_public_message = "Data storage is temporarily unavailable. Please try again."
    retryable = True


class DatabaseTimeoutError(DatabaseError):
    code = "DATABASE_TIMEOUT"
    default_public_message = "The data storage operation took too long."
    retryable = True


class DatabaseConcurrencyError(DatabaseError):
    """Serialization failure or deadlock: the whole transaction may be retried."""

    code = "DATABASE_CONCURRENCY_CONFLICT"
    default_public_message = "The data changed while it was being processed. Please try again."
    retryable = True


class DatabaseIntegrityError(DatabaseError):
    code = "DATABASE_INTEGRITY_VIOLATION"
    default_public_message = "The change conflicts with existing data rules."


class DatabaseConflictError(DatabaseIntegrityError):
    """Unique-constraint violation: the record already exists."""

    code = "DATABASE_DUPLICATE"
    default_public_message = "A record with the same identity already exists."


class DatabaseReadOnlyError(DatabaseError):
    code = "DATABASE_READ_ONLY"
    default_public_message = "This operation is not allowed to change data."


class DatabasePermissionError(DatabaseError):
    code = "DATABASE_PERMISSION_DENIED"
    default_public_message = "The data storage operation is not permitted."


class RecordNotFoundError(DatabaseError):
    code = "RECORD_NOT_FOUND"
    default_public_message = "The requested record was not found."


# --- Idempotency (ADR-012) -----------------------------------------------------------


class IdempotencyError(AgentPlatformError):
    code = "IDEMPOTENCY_ERROR"
    default_public_message = "The request could not be processed safely."


class IdempotencyKeyReuseError(IdempotencyError):
    """The same key was sent again with a different request: never execute it."""

    code = "IDEMPOTENCY_KEY_REUSED"
    default_public_message = "This request was already submitted with different details."


class IdempotencyInProgressError(IdempotencyError):
    """Another execution holds the key's lease. Retry after it finishes."""

    code = "IDEMPOTENCY_IN_PROGRESS"
    default_public_message = "This request is already being processed. Please try again shortly."
    retryable = True


class IdempotencyLeaseLostError(IdempotencyError):
    """The claim expired and was taken over before it was completed."""

    code = "IDEMPOTENCY_LEASE_LOST"


# --- Redis: cache, temporary state, locks (ADR-030, ADR-031) ---------------------------
# Raised only by the cache layer (`cache/errors.translate_redis_error`). Messages
# never contain keys or values.


class CacheError(AgentPlatformError):
    code = "CACHE_ERROR"
    default_public_message = "A temporary storage error occurred."


class CacheNotConfiguredError(CacheError):
    code = "CACHE_NOT_CONFIGURED"
    default_public_message = "Temporary storage is not configured."


class CacheUnavailableError(CacheError):
    """Cannot connect, connection lost, pool exhausted, server loading or out of memory."""

    code = "CACHE_UNAVAILABLE"
    default_public_message = "Temporary storage is unavailable. Please try again."
    retryable = True


class CacheTimeoutError(CacheError):
    code = "CACHE_TIMEOUT"
    default_public_message = "Temporary storage took too long to respond."
    retryable = True


class LockError(AgentPlatformError):
    code = "LOCK_ERROR"


class LockNotAcquiredError(LockError):
    """Someone else holds the lock and it was not released within the wait time."""

    code = "RESOURCE_BUSY"
    default_public_message = "This item is busy. Please try again shortly."
    retryable = True


class LockLostError(LockError):
    """The lock expired or was taken over while it was supposed to be held."""

    code = "LOCK_LOST"


# --- Rate limiting (Phase 8, ADR-053) ---------------------------------------------------


class RateLimitedError(AgentPlatformError):
    """The caller used up its request budget for this kind of operation in this window.

    Abuse and cost protection only: it never replaces authorization."""

    code = "RATE_LIMITED"
    default_public_message = "Too many requests. Please wait and try again."
    retryable = True

    def __init__(self, detail: str | None = None, *, retry_after_seconds: int = 1) -> None:
        super().__init__(detail)
        self.retry_after_seconds = max(1, retry_after_seconds)


class ServerBusyError(AgentPlatformError):
    """Phase 15 (ADR-064): this API process already runs API_MAX_CONCURRENT_TURNS agent
    turns. Refused at once (no queueing); another replica or a retry will take it."""

    code = "SERVER_BUSY"
    default_public_message = "The server is busy. Please try again in a moment."
    retryable = True


# --- Memory (ADR-033)------------------------------------------------------------
# (Not named MemoryError: that is a Python built-in.)


class AgentMemoryError(AgentPlatformError):
    code = "MEMORY_ERROR"
    default_public_message = "The assistant's memory could not be used."


class MemoryNotFoundError(AgentMemoryError):
    code = "MEMORY_NOT_FOUND"
    default_public_message = "The requested memory was not found."


class MemoryPolicyError(AgentMemoryError):
    """The write breaks a memory rule (for example business records in semantic memory)."""

    code = "MEMORY_POLICY_VIOLATION"
    default_public_message = "This information cannot be stored in the assistant's memory."


class EmbeddingError(AgentMemoryError):
    code = "EMBEDDING_ERROR"
    default_public_message = "Text could not be prepared for search."


class EmbeddingUnavailableError(EmbeddingError):
    code = "EMBEDDING_UNAVAILABLE"
    default_public_message = "The search service is temporarily unavailable."
    retryable = True


# --- Security: identity, authorization, approvals (Phase 5) --------------------------
# Caller-facing: the agent entry point re-raises these instead of turning them into
# failed responses, so an API layer can map them to 401/403/404. Public messages never
# reveal whether a resource of another tenant or user exists.


class SecurityError(AgentPlatformError):
    code = "SECURITY_ERROR"
    default_public_message = "The request is not allowed."


class UnauthenticatedError(SecurityError):
    code = "AUTHENTICATION_REQUIRED"
    default_public_message = "Sign in to continue."


class InvalidIdentityError(UnauthenticatedError):
    """Malformed, forged or expired credentials. The reason goes to `detail` only."""

    code = "INVALID_CREDENTIALS"
    default_public_message = "The sign-in credentials are not valid."


class IdentityProviderUnavailableError(SecurityError):
    """The identity provider (or its signing keys) cannot be reached. Not the caller's
    fault: nothing is known about the credential yet, so it is neither accepted nor
    reported as invalid."""

    code = "IDENTITY_PROVIDER_UNAVAILABLE"
    default_public_message = "Sign-in is temporarily unavailable. Please try again."
    retryable = True


class MissingTenantError(UnauthenticatedError):
    code = "TENANT_REQUIRED"
    default_public_message = "The sign-in credentials do not name a company (tenant)."


class PermissionDeniedError(SecurityError):
    code = "PERMISSION_DENIED"
    default_public_message = "You do not have permission to do this."


class ResourceNotFoundError(SecurityError):
    """Missing, or not visible to the caller. Both look the same from outside."""

    code = "NOT_FOUND"
    default_public_message = "The requested item was not found."


class CrossTenantAccessError(ResourceNotFoundError):
    """Another tenant's resource. Same public code and message as 'not found'."""


class ConversationNotFoundError(ResourceNotFoundError):
    code = "CONVERSATION_NOT_FOUND"
    default_public_message = "The conversation was not found."


class InvalidResumeContextError(SecurityError):
    """A paused turn was resumed by someone other than the user who started it."""

    code = "RESUME_CONTEXT_INVALID"
    default_public_message = "This conversation cannot be resumed with the current sign-in."


class ApprovalError(SecurityError):
    code = "APPROVAL_ERROR"
    default_public_message = "The approval could not be used."


class ApprovalNotFoundError(ApprovalError):
    code = "APPROVAL_NOT_FOUND"
    default_public_message = "The approval request was not found."


class ApprovalInvalidError(ApprovalError):
    """The approval does not belong to this tenant, user, workflow or operation."""

    code = "APPROVAL_INVALID"
    default_public_message = "The approval does not match this action."


class ApprovalStaleError(ApprovalError):
    """The operation changed after it was approved (different arguments or turn)."""

    code = "APPROVAL_STALE"
    default_public_message = "The action changed after it was approved. Ask for a new approval."


class ApprovalPendingError(ApprovalError):
    code = "APPROVAL_PENDING"
    default_public_message = "This action is still waiting for approval."


class ApprovalExpiredError(ApprovalError):
    code = "APPROVAL_EXPIRED"
    default_public_message = "The approval request expired. Ask again if it is still needed."


class ApprovalRejectedError(ApprovalError):
    code = "APPROVAL_REJECTED"
    default_public_message = "The action was not approved."


class ApprovalCancelledError(ApprovalError):
    code = "APPROVAL_CANCELLED"
    default_public_message = "The approval request was cancelled."


class ApprovalAlreadyDecidedError(ApprovalError):
    code = "APPROVAL_ALREADY_DECIDED"
    default_public_message = "This approval request has already been decided."


# --- Agent ----------------------------------------------------------------------


class AgentInputError(AgentPlatformError):
    code = "INVALID_INPUT"
    default_public_message = "The request was invalid."


class InvalidMemoryScopeError(AgentInputError):
    code = "INVALID_MEMORY_SCOPE"
    default_public_message = "The tenant or user id is not valid."


class InvalidThreadIdError(AgentInputError):
    code = "INVALID_THREAD_ID"
    default_public_message = "The conversation id is not valid."


class ThreadPendingError(AgentInputError):
    """The thread has an unfinished (interrupted or crashed) turn: resume it first."""

    code = "THREAD_HAS_PENDING_TURN"
    default_public_message = (
        "This conversation has an unfinished request. Resume it before sending a new message."
    )


class NothingToResumeError(AgentInputError):
    code = "NOTHING_TO_RESUME"
    default_public_message = "This conversation has nothing to resume."


class ThreadBusyError(AgentPlatformError):
    code = "THREAD_BUSY"
    default_public_message = (
        "This conversation is already processing a request. Please try again shortly."
    )
    retryable = True


class AgentStateError(AgentPlatformError):
    """The graph reached a node with state it cannot work with (a wiring bug)."""

    code = "AGENT_STATE_ERROR"


class DomainRegistrationError(AgentPlatformError):
    code = "DOMAIN_REGISTRATION_ERROR"


# --- Orchestration (Phase 12, ADR-061) ------------------------------------------------


class OrchestrationError(AgentPlatformError):
    """The Supervisor's routing contract was broken (missing/invalid decision, a domain
    that disappeared, a node reached with an outcome it does not handle). The turn ends
    as a controlled failure; nothing is retried or re-routed."""

    code = "ORCHESTRATION_FAILED"
    default_public_message = "I couldn't route your request. Please try again."


class OrchestrationLimitError(OrchestrationError):
    """A routing limit was hit (a second routing decision or domain run in one turn)."""

    code = "ORCHESTRATION_LIMIT_EXCEEDED"


class DomainTimeoutError(AgentPlatformError):
    code = "DOMAIN_TIMEOUT"
    default_public_message = "Handling your request took too long. Please try again."
    retryable = True


# --- Checkpoints (Phase 13, ADR-062) -------------------------------------------------
# Raised by the checkpoint instrumentation (observability/checkpoints.py) when a saver
# fails with something that is not already a platform error (a store outage keeps its
# DatabaseError code). Details never contain checkpoint content.


class CheckpointError(AgentPlatformError):
    """A stored checkpoint could not be read (corrupted, truncated, undecodable)."""

    code = "CHECKPOINT_UNREADABLE"
    default_public_message = "This conversation's saved state could not be loaded."


class CheckpointSerializationError(CheckpointError):
    """The turn's state could not be serialized for the checkpoint store."""

    code = "CHECKPOINT_SERIALIZATION_FAILED"
    default_public_message = "The request could not be saved. Please try again."
