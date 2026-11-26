"""Phase 8: deployment package checks that need no servers.

* The production template (`deploy/env/production.env.example`), rendered with fake
  secrets delivered as files in APP_SECRETS_DIR, passes the real `--check-config` gate of
  both entry points; leaving a secret out fails it.
* The migration guard refuses databases this application does not own.
* The gateway, image and ignore files keep their security properties (TLS, headers,
  limits, no secrets, non-root).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_platform.db.migrations import ForeignDatabaseError, refuse_foreign_database

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEMPLATE = ROOT / "deploy" / "env" / "production.env.example"
GATEWAY = ROOT / "deploy" / "gateway"

FAKE_SECRETS = {
    "DATABASE_URL": "postgresql://agentic_app:Fake-db-P8-secret@db.internal:5432/agentic",
    "REDIS_URL": "rediss://:Fake-redis-P8-secret@redis.internal:6380/0",
    "ANTHROPIC_API_KEY": "sk-ant-api03-FAKE-P8-not-a-key",
}
PLACEHOLDER_VALUES = {
    "API_FORWARDED_ALLOW_IPS": "10.0.0.0/24",
}


def template_env() -> dict[str, str]:
    """Uncommented KEY=VALUE lines, with <placeholders> replaced by harmless values."""
    values: dict[str, str] = {}
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = PLACEHOLDER_VALUES.get(
            key.strip(), re.sub(r"<[^>]*>", "example", value.strip())
        )
    return values


def check_config(module: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("AUTH_", "DATABASE_", "REDIS_", "API_", "APP_", "LLM_",
                                  "ANTHROPIC_", "MAINTENANCE_", "MEMORY_", "RATE_LIMIT_",
                                  "APPROVAL_", "LOG_"))}  # fmt: skip
    clean.update({"PYTHONPATH": str(SRC), **env})
    return subprocess.run(
        [sys.executable, "-m", module, "--check-config"],
        capture_output=True,
        text=True,
        env=clean,
        timeout=120,
        cwd=ROOT / "deploy",  # no .env here
    )


# --- production configuration gate ---------------------------------------------------------


def test_template_holds_no_secrets() -> None:
    env = template_env()
    for name in ("DATABASE_URL", "DATABASE_MIGRATION_URL", "REDIS_URL", "ANTHROPIC_API_KEY",
                 "AUTH_TOKEN_SECRET", "VOYAGE_API_KEY"):  # fmt: skip
        assert name not in env, f"{name} must come from the secret manager"
    assert env["APP_ENV"] == "production"
    assert env["AUTH_PROVIDER"] == "oidc"
    assert env["RATE_LIMIT_ENABLED"] == "true"


def test_rendered_production_template_passes_the_gate(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in FAKE_SECRETS.items():
        (secrets / name).write_text(value, encoding="utf-8")
    env = {**template_env(), "APP_SECRETS_DIR": str(secrets)}

    api = check_config("agentic_platform.api", env)
    assert api.returncode == 0, api.stdout + api.stderr
    report = json.loads(api.stdout)
    assert [i["severity"] for i in report["issues"]] == ["warning"]
    # The template keeps lexical embeddings until Q14 is answered.
    assert {i["code"] for i in report["issues"]} == {"LEXICAL_EMBEDDINGS"}

    worker = check_config("agentic_platform.maintenance", env)
    assert worker.returncode == 0, worker.stdout + worker.stderr

    for text in (api.stdout, api.stderr, worker.stdout, worker.stderr):
        for secret in FAKE_SECRETS.values():
            assert secret not in text


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"drop": "ANTHROPIC_API_KEY"}, "LLM_CREDENTIAL_MISSING"),
        ({"drop": "REDIS_URL"}, "REDIS_REQUIRED"),
        ({"drop": "DATABASE_URL"}, "DATABASE_REQUIRED"),
        ({"set": ("AUTH_PROVIDER", "dev_token")}, "AUTH_PROVIDER_NOT_OIDC"),
        ({"set": ("AUTH_TOKEN_SECRET", "x" * 40)}, "DEV_TOKEN_SECRET_SET"),
        ({"set": ("AUTH_OIDC_ISSUER", "http://idp.example")}, "OIDC_NOT_HTTPS"),
        ({"set": ("API_FORWARDED_ALLOW_IPS", "*")}, "FORWARDED_ALLOW_ALL"),
        ({"set": ("REDIS_URL", "rediss://:agentic_redis_dev@r:6380/0")}, "DEVELOPMENT_PASSWORD"),
    ],
)
def test_gate_refuses_unsafe_variants_of_the_template(
    tmp_path: Path, change: dict[str, object], code: str
) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in FAKE_SECRETS.items():
        if name != change.get("drop"):
            (secrets / name).write_text(value, encoding="utf-8")
    env = {**template_env(), "APP_SECRETS_DIR": str(secrets)}
    if "set" in change:
        key, value = change["set"]  # type: ignore[misc]
        env[str(key)] = str(value)
    result = check_config("agentic_platform.api", env)
    assert result.returncode == 1
    assert code in {i["code"] for i in json.loads(result.stdout)["issues"]}


# --- database separation (ERP / system of record) ------------------------------------------


def test_guard_allows_our_database_and_empty_databases() -> None:
    refuse_foreign_database(True, ["public.approval_request"], None)
    refuse_foreign_database(False, [], None)


def test_guard_refuses_a_non_empty_database_without_our_version_table() -> None:
    with pytest.raises(ForeignDatabaseError, match=r"public\.stock_item"):
        refuse_foreign_database(False, ["public.stock_item"], None)
    with pytest.raises(ForeignDatabaseError):
        refuse_foreign_database(False, ["erp.invoice"], "yes")  # only "1" overrides
    refuse_foreign_database(False, ["public.stock_item"], "1")  # deliberate adoption


def test_application_has_one_database_setting_pair_only() -> None:
    """The application knows the Agent DB only: there is no setting for any other
    database, so it cannot connect to (or migrate) a system of record by itself."""
    from agentic_platform.core.config import DatabaseSettings

    urls = {name for name in DatabaseSettings.model_fields if "url" in name}
    assert urls == {"url", "migration_url"}


# --- gateway -------------------------------------------------------------------------------


def gateway_config() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in (
            GATEWAY / "templates" / "default.conf.template",
            GATEWAY / "snippets" / "proxy.conf",
            GATEWAY / "snippets" / "security_headers.conf",
        )
    )


def test_gateway_terminates_tls_and_redirects_http() -> None:
    conf = gateway_config()
    assert "listen 8443 ssl;" in conf
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in conf
    assert "return 308 https://" in conf
    assert "Strict-Transport-Security" in conf
    assert "/etc/nginx/certs/tls.key" in conf
    assert "server_tokens off;" in conf


def test_gateway_replaces_forwarding_headers_and_keeps_auth_in_the_api() -> None:
    conf = gateway_config()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf  # replace, never append
    assert "$proxy_add_x_forwarded_for" not in conf
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in conf
    # No authentication or identity decisions at the gateway.
    for directive in ("auth_request", "auth_basic", "auth_jwt", "X-Tenant", "X-User"):
        assert directive not in conf
    # The log format never contains headers, query strings or bodies.
    log_format = conf.split("log_format gateway_json", 1)[1].split(";", 1)[0]
    for unsafe in ("$http_authorization", "$request_uri", "$args", "$query_string",
                   "$request_body", "$request\""):  # fmt: skip
        assert unsafe not in log_format


def test_gateway_limits_size_rate_and_connections() -> None:
    conf = gateway_config()
    assert "client_max_body_size 1m;" in conf  # = API_MAX_BODY_BYTES default (1 MiB)
    assert "limit_req_zone $binary_remote_addr zone=per_ip" in conf
    assert "zone=turns_per_ip" in conf and "limit_conn conn_per_ip" in conf
    assert "limit_req_status 429;" in conf
    assert '"code":"RATE_LIMITED"' in conf  # same envelope and code as the API
    assert "proxy_read_timeout ${GATEWAY_TURN_TIMEOUT_SECONDS}s;" in conf


def test_gateway_re_resolves_the_api_address() -> None:
    """A replaced API container gets a new address; the gateway must follow it."""
    conf = gateway_config()
    # ...and never marks its only upstream down after one failed connect.
    assert "server ${GATEWAY_UPSTREAM} resolve max_fails=0;" in conf
    assert "resolver ${GATEWAY_RESOLVER}" in conf and "zone agentic_api" in conf
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "nginx-unprivileged:1.27.5-alpine" in compose  # resolve needs nginx >= 1.27.3


def test_gateway_variables_are_all_supplied_by_compose() -> None:
    template = (GATEWAY / "templates" / "default.conf.template").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{(GATEWAY_[A-Z_]+)\}", template))
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    missing = {name for name in used if f"{name}:" not in compose}
    assert not missing, missing


# --- image and repository hygiene ----------------------------------------------------------


def test_image_runs_as_non_root_with_production_default_and_no_secrets() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "APP_ENV=production" in dockerfile
    for secret in ("ANTHROPIC_API_KEY", "DATABASE_URL", "REDIS_URL", "AUTH_TOKEN_SECRET",
                   "PASSWORD"):  # fmt: skip
        assert secret not in dockerfile


def test_secrets_and_certificates_stay_out_of_git_and_images() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {".env", ".env.*", "deploy/gateway/certs/", "*.key", "*.pem"} <= set(gitignore)
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {".env", ".env.*", "deploy", "tests"} <= set(dockerignore)
