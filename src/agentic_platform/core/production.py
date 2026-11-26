"""Production configuration policy (Phase 7, ADR-047).

`check_configuration(settings, role=...)` lists what is wrong with a configuration
for `APP_ENV=production`. The process entry points (`python -m agentic_platform.api`,
`python -m agentic_platform.maintenance`) refuse to start in production while any
**error** is reported, and log every **warning**. `--check-config` prints the same
report without starting (a deployment-pipeline gate).

The checks only read settings and environment variable *names*: they never connect
to anything and never print a value, so the report is safe to log.

| Severity | Meaning                                                                   |
|----------|---------------------------------------------------------------------------|
| error    | unsafe or non-functional in production; the process does not start         |
| warning  | allowed, but weaker than the documented production setup                   |

Dependencies by role (ARCHITECTURE.md § A.11):

| Dependency | api                         | worker                        |
|------------|-----------------------------|-------------------------------|
| PostgreSQL | REQUIRED                    | REQUIRED                      |
| Redis      | REQUIRED (more than one     | REQUIRED (job locks shared    |
|            | process: thread locks)      | with every other worker)      |
| OIDC IdP   | REQUIRED (`AUTH_PROVIDER=oidc`) | not used                  |
| LLM        | REQUIRED credential; outage = DEGRADED (turns 503, rest works) | not used |
|            | (Anthropic only: Ollama needs none)                            |          |
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import ConfigurationError

Role = Literal["api", "worker"]
Severity = Literal["error", "warning"]

# Passwords of the local docker-compose stack (.env.example). Never valid in production.
DEVELOPMENT_PASSWORDS = frozenset({"agentic_owner_dev", "agentic_app_dev", "agentic_redis_dev"})

# Environment variables through which the Anthropic SDK can find a credential besides
# ANTHROPIC_API_KEY (the SDK reads them itself; see LLMSettings).
_SDK_CREDENTIAL_ENV = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
_WORKLOAD_IDENTITY_ENV = (
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
)
_STRONG_DB_TLS = ("require", "verify-ca", "verify-full")
# An Ollama server on the same host may use plain http (nothing leaves the machine).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigIssue(BaseModel):
    severity: Severity
    code: str
    setting: str
    message: str


class ConfigReport(BaseModel):
    env: str
    role: Role
    issues: list[ConfigIssue]

    @property
    def errors(self) -> list[ConfigIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ConfigIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def llm_credential_source(
    settings: AppSettings, environ: Mapping[str, str] | None = None
) -> str | None:
    """Which credential the Anthropic SDK will use, by name only (None = none found)."""
    env = os.environ if environ is None else environ
    if settings.llm.anthropic_api_key is not None:
        return "ANTHROPIC_API_KEY"
    for name in _SDK_CREDENTIAL_ENV:
        if env.get(name, "").strip():
            return name
    identity_token = env.get("ANTHROPIC_IDENTITY_TOKEN_FILE", "").strip() or env.get(
        "ANTHROPIC_IDENTITY_TOKEN", ""
    ).strip()
    if identity_token and all(env.get(n, "").strip() for n in _WORKLOAD_IDENTITY_ENV):
        return "workload_identity_federation"
    return None


def check_configuration(
    settings: AppSettings, *, role: Role, environ: Mapping[str, str] | None = None
) -> ConfigReport:
    """Production policy for one process role. Outside production: no issues."""
    issues: list[ConfigIssue] = []
    if settings.env != "production":
        return ConfigReport(env=settings.env, role=role, issues=issues)

    def error(code: str, setting: str, message: str) -> None:
        issues.append(ConfigIssue(severity="error", code=code, setting=setting, message=message))

    def warning(code: str, setting: str, message: str) -> None:
        issues.append(
            ConfigIssue(severity="warning", code=code, setting=setting, message=message)
        )

    auth, db, redis = settings.auth, settings.database, settings.redis

    # --- identity: no development authentication of any kind -------------------------
    if auth.token_secret is not None:
        error("DEV_TOKEN_SECRET_SET", "AUTH_TOKEN_SECRET",
              "Development token secret must not be configured in production.")  # fmt: skip
    if auth.allow_local_identity:
        error("LOCAL_IDENTITY_ALLOWED", "AUTH_ALLOW_LOCAL_IDENTITY",
              "The local development identity must stay disabled in production.")  # fmt: skip
    if role == "api":
        if auth.provider != "oidc":
            error("AUTH_PROVIDER_NOT_OIDC", "AUTH_PROVIDER",
                  "Production must authenticate with AUTH_PROVIDER=oidc.")  # fmt: skip
        if auth.oidc_issuer is None or auth.oidc_audience is None:
            error("OIDC_INCOMPLETE", "AUTH_OIDC_ISSUER/AUTH_OIDC_AUDIENCE",
                  "OIDC needs an issuer and an audience.")  # fmt: skip
        for name, url in (("AUTH_OIDC_ISSUER", auth.oidc_issuer),
                          ("AUTH_OIDC_JWKS_URL", auth.oidc_jwks_url)):  # fmt: skip
            if url is not None and not url.startswith("https://"):
                error("OIDC_NOT_HTTPS", name, "OIDC URLs must use https in production.")
        if auth.oidc_max_token_lifetime_seconds is None:
            warning("OIDC_TOKEN_LIFETIME_UNBOUNDED", "AUTH_OIDC_MAX_TOKEN_LIFETIME_SECONDS",
                    "Access-token lifetime is not bounded; a token outlives a disabled "
                    "user until it expires (ADR-048).")  # fmt: skip
        if auth.oidc_leeway_seconds > 60:
            warning("OIDC_LEEWAY_HIGH", "AUTH_OIDC_LEEWAY_SECONDS",
                    "Clock-skew leeway above 60 s extends every token's life.")  # fmt: skip

    # --- PostgreSQL: required, least privilege, encrypted ------------------------------
    if db.url is None:
        error("DATABASE_REQUIRED", "DATABASE_URL",
              "PostgreSQL is required in production (audit, approvals, RLS).")  # fmt: skip
    else:
        runtime_user = _user_of(db.url.get_secret_value())
        if db.migration_url is not None:
            if role == "api":
                warning("MIGRATION_URL_IN_RUNTIME", "DATABASE_MIGRATION_URL",
                        "The schema-owner connection is only needed by the migration job; "
                        "do not give it to the API.")  # fmt: skip
            if runtime_user and runtime_user == _user_of(db.migration_url.get_secret_value()):
                error("RUNTIME_ROLE_IS_OWNER", "DATABASE_URL",
                      "The runtime must not connect as the schema owner (bypasses RLS "
                      "and can run DDL).")  # fmt: skip
        if db.ssl_mode not in _STRONG_DB_TLS:
            warning("DATABASE_TLS_WEAK", "DATABASE_SSL_MODE",
                    "Use DATABASE_SSL_MODE=verify-full for a managed database.")  # fmt: skip
        if db.echo_sql:
            error("DATABASE_ECHO_SQL", "DATABASE_ECHO_SQL",
                  "SQL echo logs statement parameters (data) and must be off.")  # fmt: skip
        # Phase 15 (ADR-064): an unbounded statement can hold a pooled connection forever;
        # enough of them exhaust the pool and every replica stops serving.
        if db.statement_timeout_ms == 0:
            error("DATABASE_STATEMENT_TIMEOUT_DISABLED", "DATABASE_STATEMENT_TIMEOUT_MS",
                  "Statements must have a server-side time limit in production.")  # fmt: skip
    for name, secret in (("DATABASE_URL", db.url), ("DATABASE_MIGRATION_URL", db.migration_url),
                         ("REDIS_URL", redis.url)):  # fmt: skip
        if secret is not None and _password_of(secret.get_secret_value()) in DEVELOPMENT_PASSWORDS:
            error("DEVELOPMENT_PASSWORD", name,
                  "Uses a development password from .env.example.")  # fmt: skip

    # --- Redis: required (cross-process locks), protected ------------------------------
    if redis.url is None:
        error("REDIS_REQUIRED", "REDIS_URL",
              "Redis is required in production: the API and the maintenance worker are "
              "separate processes and share thread and job locks (ADR-031).")  # fmt: skip
    else:
        url = redis.url.get_secret_value()
        if not _password_of(url) and not _user_of(url):
            error("REDIS_NO_AUTH", "REDIS_URL", "Redis must require authentication.")
        if not url.startswith("rediss://"):
            warning("REDIS_TLS_OFF", "REDIS_URL",
                    "Use rediss:// (TLS) unless Redis is only reachable on a private "
                    "network.")  # fmt: skip

    # --- LLM ---------------------------------------------------------------------------
    # Credentials are Anthropic's; other providers manage their own (Ollama needs none).
    provider = settings.llm.provider.lower()
    if role == "api" and provider == "anthropic":
        if llm_credential_source(settings, environ) is None:
            error("LLM_CREDENTIAL_MISSING", "ANTHROPIC_API_KEY",
                  "No LLM credential: set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN, or "
                  "workload identity federation).")  # fmt: skip
        base = settings.llm.anthropic_base_url
        if base is not None and not base.startswith("https://"):
            error("LLM_BASE_URL_NOT_HTTPS", "LLM_ANTHROPIC_BASE_URL",
                  "The LLM endpoint must use https in production.")  # fmt: skip
    if role == "api" and provider == "ollama":
        ollama = urlsplit(settings.llm.ollama_base_url)
        if ollama.scheme != "https" and ollama.hostname not in _LOOPBACK_HOSTS:
            warning("OLLAMA_NOT_HTTPS", "LLM_OLLAMA_BASE_URL",
                    "Prompts reach a remote Ollama server unencrypted: use https or a "
                    "server on this host.")  # fmt: skip

    # --- API process -------------------------------------------------------------------
    if role == "api":
        if settings.api.run_maintenance:
            error("IN_PROCESS_MAINTENANCE", "API_RUN_MAINTENANCE",
                  "Run maintenance as its own worker or scheduled job in production.")  # fmt: skip
        if settings.api.docs_enabled:
            warning("API_DOCS_ENABLED", "API_DOCS_ENABLED",
                    "Interactive API docs are public (unauthenticated) when enabled.")  # fmt: skip
        # Phase 8 (ADR-053, ADR-054).
        if not settings.rate_limit_enabled:
            warning("RATE_LIMIT_DISABLED", "RATE_LIMIT_ENABLED",
                    "Per-caller rate limits are off; agent turns (LLM cost) are then "
                    "bounded only by the gateway.")  # fmt: skip
        elif settings.rate_limit.turns_per_window == 0:
            warning("TURN_RATE_LIMIT_DISABLED", "RATE_LIMIT_TURNS_PER_WINDOW",
                    "Agent turns (LLM cost) have no per-caller limit.")  # fmt: skip
        if settings.api.proxy_headers and "*" in settings.api.forwarded_allow_ips:
            error("FORWARDED_ALLOW_ALL", "API_FORWARDED_ALLOW_IPS",
                  "Trusting X-Forwarded-* from every address lets any client forge its "
                  "address and scheme; list the gateway's addresses.")  # fmt: skip
        # Phase 15 (ADR-064).
        if settings.api.max_concurrent_turns == 0:
            warning("TURN_CONCURRENCY_UNBOUNDED", "API_MAX_CONCURRENT_TURNS",
                    "Concurrent agent turns per process are unbounded; a burst can open "
                    "unlimited LLM calls and database sessions.")  # fmt: skip
    # --- ERP business reads (Phase 10, ADR-059): read only, never fake, never the Agent DB
    erp = settings.erp
    if erp.adapter == "postgres":
        erp_urls = [
            (f"ERP_DATABASE_URLS[{key}]", url.get_secret_value())
            for key, url in erp.database_url_map().items()
        ]
        if erp.database_url is not None:
            erp_urls.append(("ERP_DATABASE_URL", erp.database_url.get_secret_value()))
        if not erp_urls:
            error("ERP_URL_MISSING", "ERP_DATABASE_URL",
                  "ERP_ADAPTER=postgres needs the read-only ERP role's URL "
                  "(ERP_DATABASE_URL, or ERP_DATABASE_URLS for a multi-database ERP).")  # fmt: skip
        for variable, erp_url in erp_urls:
            for name, secret in (("DATABASE_URL", db.url),
                                 ("DATABASE_MIGRATION_URL", db.migration_url)):  # fmt: skip
                if secret is not None and _same_endpoint(erp_url, secret.get_secret_value()):
                    error("ERP_URL_IS_AGENT_DB", variable,
                          f"The ERP URL names the same database as {name}.")  # fmt: skip
            if _password_of(erp_url) in DEVELOPMENT_PASSWORDS:
                error("DEVELOPMENT_PASSWORD", variable,
                      "Uses a development password from .env.example.")  # fmt: skip
        if erp.expected_database is None and erp.database_urls is None:
            error("ERP_EXPECTED_DATABASE_MISSING", "ERP_EXPECTED_DATABASE",
                  "Name the ERP database agreed with the ERP owner.")  # fmt: skip
        if erp.ssl_mode not in _STRONG_DB_TLS:
            warning("ERP_TLS_WEAK", "ERP_SSL_MODE",
                    "Use ERP_SSL_MODE=verify-full for the ERP connection.")  # fmt: skip
        _check_erp_contract(settings, error, warning)

    # Phase 15 (ADR-064): debug mode is refused, not merely discouraged. DEBUG adds
    # free-text lines (library internals, every checkpoint operation, uvicorn protocol
    # events) that the allow-listed event fields do not cover and that pattern-based
    # redaction may not catch; production logs are retained and shipped. Diagnose with
    # the request id, `--trace` and the observability events instead (runbook § 18).
    if settings.logging.level == "DEBUG":
        error("LOG_LEVEL_DEBUG", "LOG_LEVEL",
              "DEBUG logging (debug mode) is not allowed in production; use INFO.")  # fmt: skip
    if settings.memory.enabled and settings.memory.embedding_provider == "hashing":
        warning("LEXICAL_EMBEDDINGS", "MEMORY_EMBEDDING_PROVIDER",
                "The hashing embedder is lexical; semantic recall needs a real embedding "
                "provider (Q14).")  # fmt: skip

    return ConfigReport(env=settings.env, role=role, issues=issues)


# Records one configuration issue: (code, setting, message).
_Report = Callable[[str, str, str], None]


def _check_erp_contract(settings: AppSettings, error: _Report, warning: _Report) -> None:
    """Phase 14 (ADR-063): the live ERP adapter needs a complete, VERIFIED contract (Q1)."""
    from agentic_platform.business.erp_contract import (  # business imports core, not back
        ContractError,
        Verification,
        load_contract,
        missing_inputs,
    )

    erp = settings.erp
    if erp.contract_path is None:
        error("ERP_CONTRACT_MISSING", "ERP_CONTRACT_PATH",
              "ERP_ADAPTER=postgres needs the ERP access contract confirmed by the ERP "
              "owner (Q1).")  # fmt: skip
        return
    try:
        contract = load_contract(erp.contract_path)
    except ContractError as exc:
        error("ERP_CONTRACT_INVALID", "ERP_CONTRACT_PATH", str(exc))
        return
    if contract.status is not Verification.VERIFIED:
        missing = ", ".join(m.item for m in missing_inputs(contract)) or "owner sign-off"
        error("ERP_CONTRACT_NOT_VERIFIED", "ERP_CONTRACT_PATH",
              f"The ERP contract is not VERIFIED (missing Q1 items: {missing}).")  # fmt: skip
    if contract.database is not None and contract.database.name != erp.expected_database:
        error("ERP_CONTRACT_DATABASE_MISMATCH", "ERP_EXPECTED_DATABASE",
              "ERP_EXPECTED_DATABASE differs from the contract's database.")  # fmt: skip
    if contract.databases and set(erp.database_url_map()) != set(contract.databases):
        error("ERP_CONTRACT_DATABASES_MISMATCH", "ERP_DATABASE_URLS",
              "ERP_DATABASE_URLS must name exactly the contract's databases "
              f"({', '.join(sorted(contract.databases))}).")  # fmt: skip
    if contract.time is not None and contract.time.business_timezone != erp.business_timezone:
        error("ERP_TIMEZONE_MISMATCH", "ERP_BUSINESS_TIMEZONE",
              "ERP_BUSINESS_TIMEZONE must equal the contract's time zone.")  # fmt: skip
    if not any(contract.capability_verified(name) for name in contract.capabilities):
        warning("ERP_NO_VERIFIED_CAPABILITY", "ERP_CONTRACT_PATH",
                "No capability mapping is VERIFIED: every business question answers "
                "RULE_NOT_VERIFIED.")  # fmt: skip


def enforce_production_policy(
    settings: AppSettings,
    *,
    role: Role,
    environ: Mapping[str, str] | None = None,
) -> ConfigReport:
    """Raise `ConfigurationError` listing the codes of every error; return the report
    (whose warnings the caller logs) otherwise."""
    report = check_configuration(settings, role=role, environ=environ)
    if report.errors:
        codes = ", ".join(f"{i.code} ({i.setting})" for i in report.errors)
        raise ConfigurationError(f"Production configuration refused: {codes}.")
    return report


def _parts(url: str) -> tuple[str | None, str | None]:
    try:
        parts = urlsplit(url)
        return parts.username, parts.password
    except ValueError:
        return None, None


def _same_endpoint(url_a: str, url_b: str) -> bool:
    def key(url: str) -> tuple[str, int | None, str]:
        try:
            parts = urlsplit(url)
            return (parts.hostname or "").lower(), parts.port or 5432, parts.path.lstrip("/")
        except ValueError:
            return url, None, ""

    return key(url_a) == key(url_b)


def _user_of(url: str) -> str | None:
    return _parts(url)[0] or None


def _password_of(url: str) -> str | None:
    return _parts(url)[1]
