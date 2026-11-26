"""Phase 7: production configuration policy, secret files, entry-point refusals, LLM
readiness, OIDC token-lifetime bound and graceful maintenance stop. No servers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import (
    ApiSettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    LLMSettings,
    LoggingSettings,
    MemorySettings,
    RateLimitSettings,
    RedisSettings,
    get_settings,
)
from agentic_platform.core.errors import ConfigurationError, InvalidIdentityError
from agentic_platform.core.production import (
    check_configuration,
    enforce_production_policy,
    llm_credential_source,
)
from agentic_platform.maintenance import BatchResult, MaintenanceRunner, MaintenanceScheduler
from agentic_platform.security import IdentityResolver, build_token_authenticator
from agentic_platform.services.readiness import ReadinessService
from tests.api_helpers import TOKEN_SECRET, api_client
from tests.fakes import ScriptedLLM

SRC = Path(__file__).resolve().parents[1] / "src"

DB_PASSWORD = "S3cret-db-PASSWORD-for-tests"
REDIS_PASSWORD = "S3cret-redis-PASSWORD-for-tests"
LLM_KEY = "sk-ant-api03-S3CRET-KEY-for-tests"
OIDC: dict[str, Any] = {"provider": "oidc", "oidc_issuer": "https://i", "oidc_audience": "a"}


def production(**overrides: Any) -> AppSettings:
    """A configuration that passes the production policy without errors."""
    values: dict[str, Any] = {
        "env": "production",
        "llm": LLMSettings(anthropic_api_key=LLM_KEY),
        "database": DatabaseSettings(
            url=f"postgresql://agentic_app:{DB_PASSWORD}@db.internal:5432/agentic",
            ssl_mode="verify-full",
        ),
        "redis": RedisSettings(url=f"rediss://:{REDIS_PASSWORD}@redis.internal:6380/0"),
        "auth": AuthSettings(
            provider="oidc",
            oidc_issuer="https://login.example.com/realms/acme",
            oidc_audience="agentic-api",
            oidc_max_token_lifetime_seconds=900,
        ),
        "memory": MemorySettings(enabled=False),
    }
    values.update(overrides)
    return AppSettings(**values)


def codes(settings: AppSettings, role: str = "api", **kw: Any) -> set[str]:
    report = check_configuration(settings, role=role, environ={}, **kw)  # type: ignore[arg-type]
    return {i.code for i in report.issues}


# --- the policy ---------------------------------------------------------------------------------


def test_a_proper_production_configuration_passes() -> None:
    report = check_configuration(production(), role="api", environ={})
    assert report.errors == [] and report.warnings == []
    assert check_configuration(production(), role="worker", environ={}).issues == []


def test_policy_applies_to_production_only() -> None:
    development = AppSettings(env="development", auth=AuthSettings(token_secret=TOKEN_SECRET))
    assert check_configuration(development, role="api", environ={}).issues == []


def _db(**values: Any) -> DatabaseSettings:
    """Database settings from `values` only. The parameter lists below are built at
    collection time, before conftest isolates the environment, so a developer's .env
    (DATABASE_URL, DATABASE_MIGRATION_URL) would otherwise leak into them."""
    return DatabaseSettings(_env_file=None, **values)  # type: ignore[call-arg]


def _redis(**values: Any) -> RedisSettings:
    """Redis settings from `values` only (see `_db`)."""
    return RedisSettings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"auth": AuthSettings(token_secret=TOKEN_SECRET, **OIDC)}, "DEV_TOKEN_SECRET_SET"),
        ({"auth": AuthSettings(allow_local_identity=True, **OIDC)}, "LOCAL_IDENTITY_ALLOWED"),
        ({"auth": AuthSettings(provider="dev_token")}, "AUTH_PROVIDER_NOT_OIDC"),
        ({"auth": AuthSettings(provider="oidc", oidc_issuer="https://i")}, "OIDC_INCOMPLETE"),
        ({"auth": AuthSettings(provider="oidc", oidc_issuer="http://i", oidc_audience="a")},
         "OIDC_NOT_HTTPS"),
        ({"database": _db()}, "DATABASE_REQUIRED"),
        ({"database": _db(url="postgresql://u:p@h/d", echo_sql=True,
                                       ssl_mode="verify-full")}, "DATABASE_ECHO_SQL"),
        ({"database": _db(url="postgresql://owner:p@h/d",
                                       migration_url="postgresql://owner:q@h/d",
                                       ssl_mode="verify-full")}, "RUNTIME_ROLE_IS_OWNER"),
        ({"database": _db(
            url="postgresql://agentic_app:agentic_app_dev@h/d", ssl_mode="verify-full")},
         "DEVELOPMENT_PASSWORD"),
        ({"redis": _redis()}, "REDIS_REQUIRED"),
        ({"redis": _redis(url="rediss://redis.internal:6380/0")}, "REDIS_NO_AUTH"),
        ({"redis": _redis(url="redis://:agentic_redis_dev@h:6379/0")},
         "DEVELOPMENT_PASSWORD"),
        # provider set explicitly: these are built at collection time, before conftest
        # isolates the environment, so a local .env could otherwise pick the provider.
        ({"llm": LLMSettings(provider="anthropic")}, "LLM_CREDENTIAL_MISSING"),
        ({"llm": LLMSettings(provider="anthropic", anthropic_api_key="k",
                             anthropic_base_url="http://proxy")},
         "LLM_BASE_URL_NOT_HTTPS"),
        ({"api": ApiSettings(run_maintenance=True)}, "IN_PROCESS_MAINTENANCE"),
        # Phase 8 (ADR-054): trusting every address lets clients forge X-Forwarded-*.
        ({"api": ApiSettings(forwarded_allow_ips="*")}, "FORWARDED_ALLOW_ALL"),
        ({"api": ApiSettings(forwarded_allow_ips="10.0.0.5, *")}, "FORWARDED_ALLOW_ALL"),
        # Phase 15 (ADR-064): debug mode is refused (was a warning: still flagged, stricter).
        ({"logging": LoggingSettings(level="DEBUG")}, "LOG_LEVEL_DEBUG"),
        ({"database": _db(url="postgresql://u:p@h/d", ssl_mode="verify-full",
                                       statement_timeout_ms=0)},
         "DATABASE_STATEMENT_TIMEOUT_DISABLED"),
    ],
)  # fmt: skip
def test_unsafe_production_settings_are_errors(overrides: dict[str, Any], code: str) -> None:
    settings = production(**overrides)
    report = check_configuration(settings, role="api", environ={})
    assert code in {i.code for i in report.errors}
    with pytest.raises(ConfigurationError, match=code):
        enforce_production_policy(settings, role="api", environ={})


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"database": _db(url="postgresql://u:p@h/d")}, "DATABASE_TLS_WEAK"),
        ({"redis": _redis(url="redis://:pw@h:6379/0")}, "REDIS_TLS_OFF"),
        ({"api": ApiSettings(docs_enabled=True)}, "API_DOCS_ENABLED"),
        # LOG_LEVEL_DEBUG was a warning until Phase 15; it is now an error (see the list
        # of unsafe settings above).
        ({"memory": MemorySettings(enabled=True)}, "LEXICAL_EMBEDDINGS"),
        ({"auth": AuthSettings(provider="oidc", oidc_issuer="https://i", oidc_audience="a")},
         "OIDC_TOKEN_LIFETIME_UNBOUNDED"),
        ({"database": _db(url="postgresql://app:p@h/d",
                                       migration_url="postgresql://owner:q@h/d",
                                       ssl_mode="verify-full")}, "MIGRATION_URL_IN_RUNTIME"),
        # Phase 8 (ADR-053).
        ({"rate_limit": RateLimitSettings(enabled=False)}, "RATE_LIMIT_DISABLED"),
        ({"rate_limit": RateLimitSettings(turns_per_window=0)}, "TURN_RATE_LIMIT_DISABLED"),
    ],
)  # fmt: skip
def test_weaker_production_settings_are_warnings(overrides: dict[str, Any], code: str) -> None:
    report = enforce_production_policy(production(**overrides), role="api", environ={})
    assert code in {i.code for i in report.warnings}


def test_worker_role_needs_no_llm_or_identity_provider() -> None:
    worker = production(llm=LLMSettings(), auth=AuthSettings())
    assert codes(worker, role="worker") == set()
    assert {"LLM_CREDENTIAL_MISSING", "AUTH_PROVIDER_NOT_OIDC"} <= codes(worker, role="api")
    # ...but it needs the database and Redis just like the API.
    assert {"DATABASE_REQUIRED", "REDIS_REQUIRED"} <= codes(
        production(database=DatabaseSettings(), redis=RedisSettings()), role="worker"
    )


def test_ollama_needs_no_anthropic_credential() -> None:
    local = production(llm=LLMSettings(provider="ollama", model="qwen3:8b"))
    report = check_configuration(local, role="api", environ={})
    assert report.errors == [] and report.warnings == []
    # The Anthropic endpoint override is not used by Ollama, so it is not checked either.
    proxied = production(
        llm=LLMSettings(provider="ollama", anthropic_base_url="http://proxy")
    )
    assert "LLM_BASE_URL_NOT_HTTPS" not in codes(proxied)


@pytest.mark.parametrize(
    ("base_url", "warned"),
    [
        ("http://localhost:11434", False),
        ("http://127.0.0.1:11434", False),
        ("http://[::1]:11434", False),
        ("https://ollama.internal", False),
        ("http://ollama.internal:11434", True),
    ],
)
def test_remote_ollama_over_http_is_a_warning(base_url: str, warned: bool) -> None:
    settings = production(llm=LLMSettings(provider="ollama", ollama_base_url=base_url))
    report = enforce_production_policy(settings, role="api", environ={})
    assert ("OLLAMA_NOT_HTTPS" in {i.code for i in report.warnings}) is warned
    assert report.errors == []


def test_llm_credential_sources() -> None:
    bare = AppSettings(llm=LLMSettings())
    assert llm_credential_source(bare, {}) is None
    assert llm_credential_source(bare, {"ANTHROPIC_AUTH_TOKEN": "t"}) == "ANTHROPIC_AUTH_TOKEN"
    wif = {
        "ANTHROPIC_FEDERATION_RULE_ID": "r",
        "ANTHROPIC_ORGANIZATION_ID": "o",
        "ANTHROPIC_SERVICE_ACCOUNT_ID": "s",
        "ANTHROPIC_IDENTITY_TOKEN_FILE": "/var/run/token",
    }
    assert llm_credential_source(bare, wif) == "workload_identity_federation"
    assert llm_credential_source(bare, {**wif, "ANTHROPIC_ORGANIZATION_ID": ""}) is None
    keyed = AppSettings(llm=LLMSettings(anthropic_api_key="k"))
    assert llm_credential_source(keyed, {}) == "ANTHROPIC_API_KEY"


def test_the_report_never_contains_secret_values() -> None:
    owner = f"postgresql://owner:{DB_PASSWORD}@h/d"
    settings = production(
        database=DatabaseSettings(url=owner, migration_url=owner, echo_sql=True),
        redis=RedisSettings(url=f"redis://:{REDIS_PASSWORD}@h:6379/0"),
        auth=AuthSettings(token_secret=TOKEN_SECRET, provider="dev_token"),
    )
    report = check_configuration(settings, role="api", environ={})
    assert len(report.errors) >= 4
    text = report.model_dump_json()
    for secret in (DB_PASSWORD, REDIS_PASSWORD, TOKEN_SECRET, LLM_KEY):
        assert secret not in text
    with pytest.raises(ConfigurationError) as info:
        enforce_production_policy(settings, role="api", environ={})
    assert DB_PASSWORD not in info.value.detail


# --- no development authentication in production ------------------------------------------------


def test_development_tokens_are_refused_by_every_entry_point_in_production() -> None:
    dev = AppSettings(env="development", auth=AuthSettings(token_secret=TOKEN_SECRET))
    issued = build_token_authenticator(dev, IdentityResolver())
    assert issued is not None
    token = issued.issue(user_id="asha", tenant_id="acme", roles=["member"])
    prod = dev.model_copy(update={"env": "production"})
    # The authenticator the CLI and scripts use does not exist in production.
    assert build_token_authenticator(prod, IdentityResolver()) is None
    deps = build_dependencies(prod, llm=ScriptedLLM())
    assert deps.security.tokens is None
    assert prod.local_identity_allowed is False
    assert token  # a valid development token, refused nowhere else because nothing accepts it


def _run_module(module: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("AUTH_", "DATABASE_", "REDIS_", "API_", "APP_", "LLM_",
                                  "ANTHROPIC_", "MAINTENANCE_", "MEMORY_",
                                  "RATE_LIMIT_"))}  # fmt: skip
    clean.update({"PYTHONPATH": str(SRC), **env})
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        env=clean,
        timeout=120,
    )


def test_api_entry_point_refuses_unsafe_production_and_reports(tmp_path: Path) -> None:
    env = {
        "APP_ENV": "production",
        "AUTH_TOKEN_SECRET": TOKEN_SECRET,
        "AUTH_PROVIDER": "dev_token",
        "DATABASE_URL": f"postgresql://agentic_app:{DB_PASSWORD}@db:5432/agentic",
    }
    started = _run_module("agentic_platform.api", "--openapi", env=env)
    assert started.returncode == 2
    assert "Production configuration refused" in started.stderr
    assert "DEV_TOKEN_SECRET_SET" in started.stderr and "REDIS_REQUIRED" in started.stderr
    report = _run_module("agentic_platform.api", "--check-config", env=env)
    assert report.returncode == 1
    body = json.loads(report.stdout)
    assert {"DEV_TOKEN_SECRET_SET", "AUTH_PROVIDER_NOT_OIDC", "LLM_CREDENTIAL_MISSING"} <= {
        i["code"] for i in body["issues"]
    }
    for text in (started.stdout, started.stderr, report.stdout, report.stderr):
        assert TOKEN_SECRET not in text and DB_PASSWORD not in text


def test_worker_entry_point_refuses_unsafe_production() -> None:
    result = _run_module(
        "agentic_platform.maintenance", "--list", env={"APP_ENV": "production"}
    )
    assert result.returncode == 2
    assert "DATABASE_REQUIRED" in result.stderr and "REDIS_REQUIRED" in result.stderr


def test_development_entry_point_check_config_is_clean() -> None:
    result = _run_module("agentic_platform.api", "--check-config", env={"APP_ENV": "development"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["issues"] == []


# --- secret files ---------------------------------------------------------------------------------


def test_settings_are_read_from_secret_files(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "DATABASE_URL").write_text(f"postgresql://agentic_app:{DB_PASSWORD}@db/agentic")
    (secrets / "ANTHROPIC_API_KEY").write_text(LLM_KEY)
    (secrets / "AUTH_OIDC_AUDIENCE").write_text("agentic-api")
    code = (
        "from agentic_platform.core.config import AppSettings;"
        "s = AppSettings();"
        "print(s.database.url.get_secret_value().endswith('@db/agentic'),"
        " s.llm.anthropic_api_key.get_secret_value() == " + repr(LLM_KEY) + ","
        " s.auth.oidc_audience, s.redis.url)"
    )
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DATABASE_", "ANTHROPIC_", "AUTH_", "REDIS_"))}  # fmt: skip
    env.update({"PYTHONPATH": str(SRC), "APP_SECRETS_DIR": str(secrets)})
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=tmp_path, timeout=60,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True", "agentic-api", "None"]
    # An environment variable still wins over a secret file.
    env["AUTH_OIDC_AUDIENCE"] = "from-env"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=tmp_path, timeout=60,
    )  # fmt: skip
    assert result.stdout.split()[2] == "from-env"


def test_secret_files_are_off_by_default() -> None:
    assert get_settings.__wrapped__().model_config.get("secrets_dir") is None  # type: ignore[attr-defined]


# --- readiness: the LLM is degraded, never gating ------------------------------------------------


def test_readiness_reports_llm_configuration_without_gating() -> None:
    async def check(llm: bool) -> Any:
        return await ReadinessService(
            database=None,
            redis=None,
            production=False,
            authentication_configured=True,
            llm_configured=llm,
        ).check()

    ok, missing = asyncio.run(check(True)), asyncio.run(check(False))
    assert (ok.status, ok.checks["llm"].status) == ("ready", "ok")
    assert (missing.status, missing.checks["llm"].status) == ("ready", "not_configured")
    assert missing.checks["llm"].error_code == "LLM_NOT_CONFIGURED"


def test_ready_endpoint_shows_llm_state(clock: Any) -> None:
    without_key = AppSettings(env="test", auth=AuthSettings(token_secret=TOKEN_SECRET))
    agent = Agent(build_dependencies(without_key, llm=ScriptedLLM(), clock=clock))
    with api_client(without_key, agent) as client:
        body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["checks"]["llm"] == {
        "status": "not_configured",
        "ready": True,
        "latency_ms": None,
        "error_code": "LLM_NOT_CONFIGURED",
    }


# --- OIDC: maximum token lifetime ----------------------------------------------------------------


def test_oidc_max_token_lifetime() -> None:
    from tests.test_identity_provider_unit import FakeIdp, authenticator

    idp = FakeIdp()
    now = int(time.time())
    long_lived = idp.token(iat=now, exp=now + 86400)
    bounded = authenticator(idp, oidc_max_token_lifetime_seconds=3600)
    with pytest.raises(InvalidIdentityError, match="lifetime"):
        asyncio.run(bounded.authenticate(long_lived))
    assert asyncio.run(bounded.authenticate(idp.token(iat=now, exp=now + 600))).tenant_id
    assert asyncio.run(authenticator(idp).authenticate(long_lived)).tenant_id  # unset = off


# --- graceful maintenance stop -------------------------------------------------------------------


class SlowJob:
    name = "test.slow"
    description = "test job"
    policy = "test"

    def __init__(self, on_batch: Any = None) -> None:
        self.batches = 0
        self.on_batch = on_batch

    async def run_batch(self, limit: int) -> BatchResult:
        self.batches += 1
        if self.on_batch is not None:
            self.on_batch(self.batches)
        return BatchResult(processed=limit, affected=limit)  # always "more to do"


def test_stop_request_finishes_the_current_batch_then_stops() -> None:
    runner: MaintenanceRunner

    def stop_after_second(batch: int) -> None:
        if batch == 2:
            runner.request_stop()  # arrives while batch 2 is running (SIGTERM)

    first, second = SlowJob(stop_after_second), SlowJob()
    second.name = "test.second"
    runner = MaintenanceRunner([first, second])
    reports = asyncio.run(runner.run_all())
    assert first.batches == 2  # batch 2 finished; no batch 3
    assert [r.job for r in reports] == ["test.slow"]  # the second job did not start
    assert (reports[0].status, reports[0].complete) == ("ok", False)  # next run continues
    assert second.batches == 0


def test_scheduler_request_stop_ends_the_loop() -> None:
    runner = MaintenanceRunner([SlowJob()])
    scheduler = MaintenanceScheduler(runner, interval_seconds=3600)

    async def body() -> None:
        task = scheduler.start()
        await asyncio.sleep(0.05)
        scheduler.request_stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(body())
    assert runner.stopping and scheduler.runs >= 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals (containers run Linux)")
def test_worker_exits_cleanly_on_sigterm() -> None:  # pragma: no cover - Linux CI only
    import signal

    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "REDIS_"))}
    env.update({"PYTHONPATH": str(SRC), "APP_ENV": "development"})
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentic_platform.maintenance", "--loop"], env=env
    )
    time.sleep(3)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=30) == 0
