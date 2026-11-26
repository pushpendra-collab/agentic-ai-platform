"""Typed configuration loaded from environment variables and an optional `.env` file.

Each concern has its own settings class with its own env prefix so the groups
stay independent. Use `get_settings()` in application code; construct
`AppSettings(...)` directly in tests.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Secret files (Phase 7, ADR-049): when APP_SECRETS_DIR names a directory, every setting
# can also be read from a file in it named after its environment variable (for example
# /run/secrets/DATABASE_URL or /run/secrets/ANTHROPIC_API_KEY). That is how Docker and
# Kubernetes secrets, Vault Agent and the cloud secret-store CSI drivers deliver secrets.
# Precedence: environment variables, then `.env`, then secret files.
SECRETS_DIR_ENV = "APP_SECRETS_DIR"


def _secrets_dir() -> str | None:
    value = os.environ.get(SECRETS_DIR_ENV, "").strip()
    return value or None


_COMMON: dict[str, Any] = {
    "env_file": ".env",
    "env_file_encoding": "utf-8",
    "extra": "ignore",
    "secrets_dir": _secrets_dir(),
}


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_", **_COMMON)  # type: ignore[typeddict-item]

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"

    @field_validator("level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class ObservabilitySettings(BaseSettings):
    """Phase 13 (ADR-062): observability events, metrics and diagnostics (in-process)."""

    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", **_COMMON)  # type: ignore[typeddict-item]

    # One structured log line per observability event (metrics and traces are kept either way).
    log_events: bool = True
    # Recent requests whose events are kept in memory for diagnostics (0 = none).
    trace_buffer_requests: int = Field(default=200, ge=0, le=10_000)
    # Phase 15 (ADR-064): where events go besides the JSON log lines on stderr. Only `none`
    # exists: a metrics/tracing exporter (Prometheus, OpenTelemetry, a vendor agent) needs
    # the deployment target (PROJECT_STATUS Q3). Any other value is refused at startup
    # instead of being ignored. Exporters plug in through `observability.hub.Exporter`.
    exporter: Literal["none"] = "none"
    # On shutdown, how long exporters may take to flush buffered events.
    flush_timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", **_COMMON)  # type: ignore[typeddict-item]

    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    max_tokens: int = Field(default=16000, gt=0)
    effort: Effort = "medium"
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    anthropic_fallbacks: Literal["default", "off"] = "default"
    # Read from the provider's conventional variable name, not the LLM_ prefix.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    # Phase 7: optional API endpoint override (an egress gateway or proxy in front of
    # the provider). Unset = the SDK default (or ANTHROPIC_BASE_URL). https in production.
    anthropic_base_url: str | None = Field(default=None, max_length=500)
    # LLM_PROVIDER=ollama: a local Ollama server (no credential). `num_ctx` is the context
    # window Ollama allocates per request; a prompt longer than it is silently truncated
    # by Ollama, so it must fit the planner prompt. `think` turns on reasoning models'
    # thinking (qwen3, deepseek-r1): slower, and the thinking text is discarded.
    ollama_base_url: str = Field(default="http://localhost:11434", min_length=1, max_length=500)
    ollama_num_ctx: int = Field(default=16384, ge=2048, le=262_144)
    ollama_think: bool = False
    ollama_keep_alive: str = Field(default="10m", min_length=1, max_length=20)

    @field_validator("anthropic_api_key", "anthropic_base_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


CheckpointerKind = Literal["auto", "postgres", "memory", "none"]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", **_COMMON)  # type: ignore[typeddict-item]

    recursion_limit: int = Field(default=25, ge=5)
    max_tool_calls_per_turn: int = Field(default=5, ge=0)
    max_input_chars: int = Field(default=8000, gt=0)
    max_history_messages: int = Field(default=20, ge=1)
    # auto = postgres when DATABASE_URL is set, otherwise memory (ADR-027).
    checkpointer: CheckpointerKind = "auto"
    # One turn at a time per thread (distributed lock, ADR-031).
    thread_lock_ttl_seconds: float = Field(default=30.0, gt=0)
    thread_lock_wait_seconds: float = Field(default=2.0, ge=0)
    # Phase 12 (ADR-061): deadline for one domain handler run (all its planned tool calls).
    # Each tool has its own timeout; this bounds the domain as a whole.
    domain_timeout_seconds: float = Field(default=120.0, gt=0, le=600)


_PG_DRIVER = "postgresql+asyncpg"


def normalise_postgres_url(url: str) -> str:
    """Accept postgres://, postgresql:// or postgresql+asyncpg:// and return the asyncpg form.

    Any other scheme or driver is rejected: PostgreSQL is the only database (ADR-006).
    """
    scheme, sep, rest = url.strip().partition("://")
    if not sep or not rest:
        raise ValueError("Database URL must look like postgresql://user:password@host:5432/db")
    if scheme.lower() not in ("postgres", "postgresql", _PG_DRIVER):
        raise ValueError(
            f"Unsupported database URL scheme '{scheme}': only PostgreSQL via asyncpg is supported."
        )
    return f"{_PG_DRIVER}://{rest}"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", **_COMMON)  # type: ignore[typeddict-item]

    # Runtime connection (least-privilege application role). Unset = database disabled.
    url: SecretStr | None = None
    # Schema-owner connection used only by Alembic. Falls back to `url`.
    migration_url: SecretStr | None = None
    pool_size: int = Field(default=5, ge=1, le=100)
    max_overflow: int = Field(default=5, ge=0, le=100)
    pool_timeout_seconds: float = Field(default=10.0, gt=0)
    pool_recycle_seconds: int = Field(default=1800, ge=-1)
    pool_pre_ping: bool = True
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    statement_timeout_ms: int = Field(default=15000, ge=0)
    # Phase 8: CLIENT-side limit for every command (asyncpg `command_timeout`): a query
    # that the server accepted but does not finish (statement_timeout 0, lock waits, a
    # slow replica) fails with DATABASE_* instead of waiting forever. Keep it above
    # statement_timeout so the server's limit normally fires first. It does NOT bound a
    # fully hung server: asyncpg then waits for the server to acknowledge the cancel. That
    # case is bounded by the pool (at most pool_size + max_overflow requests wait; others
    # get 503 after pool_timeout), by readiness (a hard deadline, services/database_health)
    # and by the gateway's read timeout (ARCHITECTURE.md § A.12).
    command_timeout_seconds: float = Field(default=20.0, gt=0, le=3600)
    idle_in_transaction_timeout_ms: int = Field(default=60000, ge=0)
    ssl_mode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] | None = None
    application_name: str = "agentic_platform"
    health_timeout_seconds: float = Field(default=3.0, gt=0)
    echo_sql: bool = False

    @field_validator("url", "migration_url", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, str):
            return SecretStr(normalise_postgres_url(value))
        return value

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _blank_ssl_is_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def configured(self) -> bool:
        return self.url is not None

    def runtime_url(self) -> str:
        if self.url is None:
            raise ValueError("DATABASE_URL is not set.")
        return self.url.get_secret_value()

    def migrations_url(self) -> str:
        chosen = self.migration_url or self.url
        if chosen is None:
            raise ValueError("Neither DATABASE_MIGRATION_URL nor DATABASE_URL is set.")
        return chosen.get_secret_value()


def normalise_redis_url(url: str) -> str:
    """Accept redis:// or rediss:// (TLS) only."""
    scheme, sep, rest = url.strip().partition("://")
    if not sep or not rest:
        raise ValueError("Redis URL must look like redis://:password@host:6379/0")
    if scheme.lower() not in ("redis", "rediss"):
        raise ValueError(f"Unsupported Redis URL scheme '{scheme}': use redis:// or rediss://.")
    return f"{scheme.lower()}://{rest}"


class RedisSettings(BaseSettings):
    """Redis holds only ephemeral, rebuildable data (ADR-007, ADR-030)."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", **_COMMON)  # type: ignore[typeddict-item]

    # Unset = Redis disabled: process-local fallbacks are used (see cache/__init__.py).
    url: SecretStr | None = None
    # Every key is "<key_prefix>:<kind>:..." so several apps/environments can share a server.
    key_prefix: str = Field(default="agentic", min_length=1, max_length=50, pattern=r"^[\w.-]+$")
    max_connections: int = Field(default=20, ge=1, le=1000)
    socket_timeout_seconds: float = Field(default=2.0, gt=0)
    connect_timeout_seconds: float = Field(default=2.0, gt=0)
    health_timeout_seconds: float = Field(default=2.0, gt=0)
    cache_default_ttl_seconds: int = Field(default=300, ge=1)
    temporary_state_ttl_seconds: int = Field(default=3600, ge=1)
    idempotency_cache_ttl_seconds: int = Field(default=3600, ge=1)
    client_name: str = "agentic_platform"

    @field_validator("url", mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, str):
            return SecretStr(normalise_redis_url(value))
        return value

    @property
    def configured(self) -> bool:
        return self.url is not None

    def connection_url(self) -> str:
        if self.url is None:
            raise ValueError("REDIS_URL is not set.")
        return self.url.get_secret_value()


MEMORY_ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,128}$"


class MemorySettings(BaseSettings):
    """Agent memory (ADR-033 to ADR-036)."""

    model_config = SettingsConfigDict(env_prefix="MEMORY_", **_COMMON)  # type: ignore[typeddict-item]

    enabled: bool = True
    # Scope used when the caller does not pass one (CLI, scripts). There is no
    # authentication yet: the scope is asserted by the caller, never by the LLM.
    default_tenant_id: str = Field(default="default", pattern=MEMORY_ID_PATTERN)
    default_user_id: str | None = Field(default="local", pattern=MEMORY_ID_PATTERN)

    # Embeddings: "hashing" is local, deterministic and lexical (dev/tests);
    # "voyage" calls Voyage AI (semantic; needs VOYAGE_API_KEY).
    embedding_provider: Literal["hashing", "voyage"] = "hashing"
    embedding_model: str | None = None  # provider default when unset
    embedding_dimensions: int = Field(default=256, ge=8, le=4096)  # hashing only
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    voyage_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("VOYAGE_API_KEY")
    )

    # Recall (what the agent reads before answering).
    recall_k: int = Field(default=5, ge=0, le=50)
    recall_min_score: float = Field(default=0.2, ge=-1.0, le=1.0)
    recall_timeout_seconds: float = Field(default=5.0, gt=0)
    context_max_chars: int = Field(default=4000, ge=200)

    # Writing and retention.
    record_episodes: bool = True
    episodic_retention_days: int = Field(default=90, ge=1)
    working_ttl_seconds: int = Field(default=3600, ge=1)
    max_content_chars: int = Field(default=20000, ge=100)
    chunk_chars: int = Field(default=1200, ge=100)
    chunk_overlap_chars: int = Field(default=150, ge=0)
    # Phase 5: may the agent propose long-term facts? "off" (default, Phase 4
    # behaviour) or "approval": the `memory.remember_fact` tool is offered and every
    # write waits for the user's explicit approval (HITL). Never written silently.
    agent_fact_writes: Literal["off", "approval"] = "off"

    @field_validator("voyage_api_key", "default_user_id", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


AuthProvider = Literal["dev_token", "oidc"]


class AuthSettings(BaseSettings):
    """Identity and permissions (Phase 5, ARCHITECTURE.md § A.9; Phase 6 § A.10).

    No identity provider has been chosen yet (PROJECT_STATUS Q6). Credential sources,
    all behind the `security.Authenticator` interface:

    * signed development tokens (`AUTH_TOKEN_SECRET`), verified by
      `security.authentication.HmacTokenAuthenticator`;
    * a local identity asserted by a trusted local operator (CLI flags, scripts),
      allowed outside production only unless `AUTH_ALLOW_LOCAL_IDENTITY` says otherwise.
      Never accepted over HTTP;
    * Phase 6: an OIDC/OAuth2 identity provider issuing signed JWT access tokens
      (`AUTH_PROVIDER=oidc`, `AUTH_OIDC_*`), verified by `security.oidc.OidcJwtProvider`
      against the provider's published keys (JWKS).
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_", **_COMMON)  # type: ignore[typeddict-item]

    # Which bearer tokens the HTTP API accepts. Unset = `dev_token` when AUTH_TOKEN_SECRET
    # is set outside production; production must name `oidc` (dev tokens are refused there).
    provider: AuthProvider | None = None

    token_secret: SecretStr | None = None
    token_issuer: str = Field(default="agentic-platform-dev", min_length=1, max_length=100)
    # Optional `aud` claim: when set, development tokens carry it and must match it.
    token_audience: str | None = Field(default=None, max_length=200)
    token_max_ttl_seconds: int = Field(default=12 * 3600, ge=60)
    # None = allowed unless APP_ENV=production.
    allow_local_identity: bool | None = None
    local_roles: list[str] = Field(default_factory=lambda: ["member"])
    # Optional JSON object {"role": ["permission", ...]} replacing the built-in role
    # catalogue (security/permissions.py). The real catalogue is PROJECT_STATUS Q8.
    role_catalog_json: str | None = None

    # --- OIDC / JWT identity provider (AUTH_PROVIDER=oidc) --------------------------
    # Expected `iss`. Also the base for discovery (/.well-known/openid-configuration)
    # when AUTH_OIDC_JWKS_URL is unset.
    oidc_issuer: str | None = Field(default=None, max_length=500)
    # Expected `aud` (this API's client id / resource identifier). Required for oidc.
    oidc_audience: str | None = Field(default=None, max_length=500)
    oidc_jwks_url: str | None = Field(default=None, max_length=1000)
    # Asymmetric algorithms only: symmetric (HS*) and "none" are always refused.
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    # Claim names (provider-specific; PROJECT_STATUS Q6).
    oidc_user_claim: str = Field(default="sub", min_length=1, max_length=100)
    oidc_tenant_claim: str = Field(default="tenant_id", min_length=1, max_length=100)
    oidc_roles_claim: str = Field(default="roles", min_length=1, max_length=100)
    # Optional JSON {"<external role or group>": ["<platform role>", ...]}. When set,
    # only mapped values grant roles; otherwise external values that name a role in
    # the catalogue are used as-is. Permissions are never taken from tokens.
    oidc_role_map_json: str | None = None
    oidc_jwks_cache_seconds: int = Field(default=600, ge=30)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0)
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)
    # Phase 7: longest access-token lifetime (exp - iat) accepted from the provider.
    # Unset = whatever the provider issues. Bounds how long a token stays usable after
    # a user is disabled, because JWTs cannot be revoked (ADR-048).
    oidc_max_token_lifetime_seconds: int | None = Field(default=None, ge=60, le=86400)

    @field_validator("token_secret", mode="before")
    @classmethod
    def _secret(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, str) and len(value) < 32:
            raise ValueError("AUTH_TOKEN_SECRET must be at least 32 characters.")
        return value

    @field_validator(
        "role_catalog_json",
        "allow_local_identity",
        "provider",
        "token_audience",
        "oidc_issuer",
        "oidc_audience",
        "oidc_jwks_url",
        "oidc_role_map_json",
        "oidc_max_token_lifetime_seconds",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("oidc_algorithms")
    @classmethod
    def _asymmetric_only(cls, value: list[str]) -> list[str]:
        allowed = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"}
        refused = [a for a in value if a not in allowed]
        if refused or not value:
            raise ValueError(
                f"AUTH_OIDC_ALGORITHMS must be asymmetric JWT algorithms from {sorted(allowed)}."
            )
        return value


class ApprovalSettings(BaseSettings):
    """Human-in-the-loop approvals (Phase 5). No business thresholds live here."""

    model_config = SettingsConfigDict(env_prefix="APPROVAL_", **_COMMON)  # type: ignore[typeddict-item]

    # How long a pending approval stays valid.
    ttl_seconds: int = Field(default=24 * 3600, ge=60)
    # Read tools at or above this risk also need approval; write tools always do (ADR-010).
    min_risk: Literal["low", "medium", "high", "never"] = "high"


class ApiSettings(BaseSettings):
    """HTTP API (Phase 6, ARCHITECTURE.md § A.10)."""

    model_config = SettingsConfigDict(env_prefix="API_", **_COMMON)  # type: ignore[typeddict-item]

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Interactive docs (/docs) and the schema (/openapi.json). None = on unless production.
    docs_enabled: bool | None = None
    # Larger request bodies are refused with 413 before they are read.
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    # Run the maintenance scheduler inside the API process (single-process development
    # only). Off by default: production runs `python -m agentic_platform.maintenance`.
    run_maintenance: bool = False
    # Phase 7: on SIGTERM, how long in-flight requests (agent turns wait for the LLM)
    # may run before the server closes them. Keep it below the orchestrator's grace period.
    graceful_shutdown_seconds: int = Field(default=30, ge=1, le=600)
    # Phase 15 (ADR-064): after that, how long the shutdown may take to let cancelled
    # requests unwind (they record their failure) and to close Redis, PostgreSQL and ERP
    # pools. Each step is bounded; a hung close is abandoned. The orchestrator's grace
    # period must exceed graceful_shutdown_seconds + shutdown_cleanup_seconds.
    shutdown_cleanup_seconds: float = Field(default=10.0, gt=0, le=120)
    # Phase 15 (ADR-064): agent turns (start, continue, resume) running at once in this
    # process. One more is refused at once with 503 SERVER_BUSY (retryable) instead of
    # queueing: each turn holds LLM calls, a database session and memory, so a burst must
    # not open an unbounded number of them. 0 = no limit. A protection bound, NOT a
    # capacity figure: size it from a load test of the real deployment (Q3).
    max_concurrent_turns: int = Field(default=32, ge=0, le=10_000)
    # Phase 8 (ADR-054): trusted reverse proxies. uvicorn applies X-Forwarded-For/-Proto
    # only from these addresses (comma-separated IPs/CIDRs; uvicorn's default is the
    # loopback address). Never "*" in production: any client could then forge its address
    # and scheme. The application does not authorize anything by client address.
    proxy_headers: bool = True
    forwarded_allow_ips: str = Field(default="127.0.0.1", min_length=1, max_length=1000)

    @field_validator("docs_enabled", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class MaintenanceSettings(BaseSettings):
    """Scheduled maintenance jobs (Phase 6). Retention periods are NOT set here: each job
    applies the expiry already recorded on the data (Phase 4/5 policies)."""

    model_config = SettingsConfigDict(env_prefix="MAINTENANCE_", **_COMMON)  # type: ignore[typeddict-item]

    # Rows per batch; every batch is its own transaction.
    batch_size: int = Field(default=500, ge=1, le=10_000)
    # Upper bound on batches per job per run (the rest waits for the next run).
    max_batches: int = Field(default=200, ge=1, le=100_000)
    # Local scheduler interval (`python -m agentic_platform.maintenance --loop`).
    interval_seconds: float = Field(default=3600.0, ge=10)
    # Lease of the per-job lock that keeps two schedulers from running one job at once.
    job_lock_ttl_seconds: float = Field(default=300.0, gt=0)


class RateLimitSettings(BaseSettings):
    """Per-caller request budgets in the API (Phase 8, ADR-053).

    Abuse and cost protection, never authorization: limits apply after authentication,
    keyed by the verified principal (tenant + user from the token, never from headers or
    bodies), and every request still goes through the normal permission checks. Fixed
    windows of `window_seconds`, counted in Redis so every API replica shares them.

    The numbers are provisional technical defaults, not business decisions (PROJECT_STATUS
    Q20): they are configuration so operators can change them without a release. `0`
    switches one class off. Unauthenticated traffic is limited per client address at the
    gateway (deploy/gateway), because before authentication there is no principal.
    """

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", **_COMMON)  # type: ignore[typeddict-item]

    # None = on in production, off elsewhere (tests and local development).
    enabled: bool | None = None
    window_seconds: int = Field(default=60, ge=1, le=3600)
    # Agent turns: start, continue, resume (each may make several LLM calls).
    turns_per_window: int = Field(default=20, ge=0)
    # Approve / reject / execute decisions.
    approval_decisions_per_window: int = Field(default=60, ge=0)
    # Memory writes and deletions, conversation deletion.
    writes_per_window: int = Field(default=60, ge=0)
    # Every other authenticated request (reads).
    reads_per_window: int = Field(default=600, ge=0)
    # Redis unavailable: True = allow the request (logged); False = refuse with 503.
    # Turns need Redis for the thread lock anyway (they fail closed there).
    fail_open: bool = True

    @field_validator("enabled", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ErpSettings(BaseSettings):
    """Business reads from the external ERP (Phase 10, ADR-059). Read only, always.

    Separate from `DatabaseSettings` on purpose: the Agent DB settings (and therefore
    Alembic) can never point at the ERP, and the ERP connection can never be used for
    anything but the read-only business layer.

    * `adapter=none` (default): the business layer is not registered; business questions
      are answered as `unsupported`, exactly as before Phase 10.
    * `adapter=postgres`: the read-only PostgreSQL boundary to the ERP. A capability is
      answered only when the ERP contract maps it VERIFIED and names its reader set
      (InvenLogic: `adapters/invenlogic.py`); otherwise `UNAVAILABLE / RULE_NOT_VERIFIED`.

    The fake TEST-data adapter was removed (2026-10-30, owner decision): the platform has
    no adapter that serves made-up business data.
    """

    # hide_input_in_errors: a malformed ERP_DATABASE_URL(S) must never be echoed (credentials).
    model_config = SettingsConfigDict(env_prefix="ERP_", hide_input_in_errors=True, **_COMMON)  # type: ignore[typeddict-item]

    adapter: Literal["none", "postgres"] = "none"
    # Read-only ERP role only (never the ERP application's role, never the Agent DB).
    database_url: SecretStr | None = None
    # A multi-database ERP (contract `databases`, e.g. InvenLogic's one database per
    # service): a JSON object, contract database key → read-only URL, e.g.
    # {"master_data": "postgresql://...", "sales": "...", "inventory": "..."}.
    # Used instead of ERP_DATABASE_URL; the database names come from the contract.
    database_urls: SecretStr | None = None
    # The database name agreed with the ERP owner; checked before the first read.
    expected_database: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]{1,63}$")
    ssl_mode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] | None = None
    pool_size: int = Field(default=2, ge=1, le=20)
    max_overflow: int = Field(default=2, ge=0, le=20)
    pool_timeout_seconds: float = Field(default=5.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    # Server-side limit for every ERP statement; never unlimited.
    statement_timeout_ms: int = Field(default=8000, ge=100, le=60_000)
    # Service-side deadline for one business read (adapter call), below the tool timeout.
    query_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    # Phase 13 (ADR-062): attempts per business read when the ERP is unreachable
    # (ERP_UNAVAILABLE only; never timeouts or data errors). All attempts share the one
    # `query_timeout_seconds` deadline. 1 = no retry.
    read_max_attempts: int = Field(default=2, ge=1, le=5)
    read_retry_backoff_seconds: float = Field(default=0.1, ge=0, le=5)
    # Upper bound on rows any single read returns to the business layer.
    max_rows: int = Field(default=200, ge=1, le=5000)
    # Time zone that defines the business day ("today"). Unset = a business rule that has
    # not been confirmed: "today" questions answer UNAVAILABLE / RULE_NOT_VERIFIED.
    business_timezone: str | None = None
    # Phase 14 (ADR-063): the reviewed, non-secret ERP access contract (JSON; Q1). Required
    # for adapter=postgres in production; without it the live adapter answers nothing.
    contract_path: str | None = Field(default=None, max_length=1000)

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_url(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, str):
            return SecretStr(normalise_postgres_url(value))
        return value

    @field_validator("database_urls", mode="before")
    @classmethod
    def _normalise_urls(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, dict):
            value = json.dumps(value)
        if not isinstance(value, str):
            return value
        try:
            urls = json.loads(value)
        except json.JSONDecodeError:
            # Never echo the value: it holds credentials.
            raise ValueError("ERP_DATABASE_URLS must be a JSON object of key → URL") from None
        if not isinstance(urls, dict) or not urls or not all(
            isinstance(k, str) and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", k)
            and isinstance(v, str) and v.strip()
            for k, v in urls.items()
        ):
            raise ValueError("ERP_DATABASE_URLS must map plain keys to non-empty URLs")
        return SecretStr(json.dumps({k: normalise_postgres_url(v) for k, v in urls.items()}))

    @field_validator(
        "expected_database", "business_timezone", "ssl_mode", "contract_path", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def database_url_map(self) -> dict[str, SecretStr]:
        """`ERP_DATABASE_URLS` as key → URL (empty when unset)."""
        if self.database_urls is None:
            return {}
        raw = json.loads(self.database_urls.get_secret_value())
        return {key: SecretStr(url) for key, url in raw.items()}


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", **_COMMON)  # type: ignore[typeddict-item]

    env: Literal["development", "test", "production"] = "development"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    maintenance: MaintenanceSettings = Field(default_factory=MaintenanceSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    erp: ErpSettings = Field(default_factory=ErpSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @property
    def docs_enabled(self) -> bool:
        enabled = self.api.docs_enabled
        return self.env != "production" if enabled is None else enabled

    @property
    def rate_limit_enabled(self) -> bool:
        enabled = self.rate_limit.enabled
        return self.env == "production" if enabled is None else enabled

    @property
    def local_identity_allowed(self) -> bool:
        allowed = self.auth.allow_local_identity
        return self.env != "production" if allowed is None else allowed


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Process-wide settings, read once. Call `get_settings.cache_clear()` to reload."""
    return AppSettings()
