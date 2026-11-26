"""Phase 15 deployment smoke test: build the release image and drive it end to end.

    .venv/Scripts/python deploy/smoke/run_smoke.py               # build + full run
    .venv/Scripts/python deploy/smoke/run_smoke.py --no-build    # reuse agentic-platform:smoke
    .venv/Scripts/python deploy/smoke/run_smoke.py --keep        # leave the stack running

What it proves (each is a named check in the JSON report):

 1. the image: non-root, production default, no secrets, no build/dev tools, build info;
 2. the production configuration gate, through the image: the rendered production template
    passes; development tokens, DEBUG logging and an ERP without a verified contract are
    refused; an incomplete production start exits 2 without starting;
 3. the stack (compose.smoke.yml) starts: migrate job (exit 0) → API + worker; liveness,
    then readiness; the Agent DB is the expected database at the migration head;
 4. authentication boundary: missing, malformed and forged tokens 401; a body naming a
    tenant 422; a valid token resolves the token's tenant;
 5. a general turn and a business turn (ERP boundary without a contract: RULE_NOT_VERIFIED)
    complete; no ERP connection;
 6. observability events in the JSON logs, and no token or secret in any log line;
 7. resource protection: body limit 413, rate limit 429, concurrent-turn cap 503;
 8. graceful shutdown: an in-flight turn finishes during SIGTERM; a turn longer than the
    shutdown deadline is cancelled and is NOT recorded as a success; bounded stop time;
 9. restart: ready again, earlier conversations readable (Agent DB), turns work (Redis);
10. the maintenance worker: one-shot run, SIGTERM stop with exit 0;
11. Agent DB backup → restore into a new database → verification; refusals for a
    non-Agent DB and for a non-empty target; the application reads the restored copy;
12. the ERP candidate container is in the same state before and after (never touched).

It uses its own compose project (`agentic-smoke`) with throw-away PostgreSQL and Redis in
tmpfs: the development stack and every other container are left alone. Timings are local
measurements (Docker Desktop on the developer machine), never capacity figures.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agentic_platform.core.config import AppSettings, AuthSettings  # noqa: E402
from agentic_platform.security import IdentityResolver  # noqa: E402
from agentic_platform.security.authentication import build_token_authenticator  # noqa: E402
from agentic_platform.security.permissions import RoleCatalog  # noqa: E402

COMPOSE = ROOT / "deploy" / "smoke" / "compose.smoke.yml"
TEMPLATE = ROOT / "deploy" / "env" / "production.env.example"
RESULTS = ROOT / "deploy" / "smoke" / "results"
PROJECT = "agentic-smoke"
IMAGE = "agentic-platform:smoke"
ERP_CANDIDATE = "inventoryai-postgres"
API_PORT = int(os.environ.get("SMOKE_API_PORT", "18000"))
BASE = f"http://127.0.0.1:{API_PORT}"
GRACEFUL_SECONDS = 5
TENANT = "smoke"
ROLE_CATALOG = json.dumps(
    {
        "member": [
            "conversation.read",
            "conversation.write",
            "conversation.delete",
            "memory.read",
            "memory.write",
            "memory.delete",
            "tool.execute",
            "approval.request",
        ],
        "sales_viewer": ["business.customer.read", "business.sales.read"],
    }
)
GATE_SECRETS = {
    "DATABASE_URL": "postgresql://agentic_app:Fake-db-P15-secret@db.internal:5432/agentic",
    "REDIS_URL": "rediss://:Fake-redis-P15-secret@redis.internal:6380/0",
    "ANTHROPIC_API_KEY": "sk-ant-api03-FAKE-P15-not-a-key",
}


class Smoke:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.timings: dict[str, float] = {}
        self.notes: dict[str, Any] = {}
        self.secret_values: list[str] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": self.scrub(detail)})
        print(
            f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if not ok else ""), flush=True
        )
        return bool(ok)

    def scrub(self, value: Any) -> Any:
        text = json.dumps(value, default=str)
        for secret in self.secret_values:
            text = text.replace(secret, "***")
        return json.loads(text)

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks)


# --- process helpers ------------------------------------------------------------------------


def run(
    cmd: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 600,
    check: bool = False,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        input=stdin,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}... exit {result.returncode}: {result.stderr}")
    return result


class Stack:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    def compose(
        self, *args: str, timeout: float = 600, check: bool = False, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return run(
            ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args],
            env=self.env,
            timeout=timeout,
            check=check,
            stdin=stdin,
        )

    def container(self, service: str) -> str:
        out = self.compose("ps", "-a", "-q", service, check=True).stdout.strip().splitlines()
        if not out:
            raise RuntimeError(f"no container for {service}")
        return out[0]

    def psql(self, sql: str, database: str = "agentic") -> str:
        result = self.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "agentic_owner",
            "-d",
            database,
            "-c",
            sql,
            check=True,
        )
        return result.stdout.strip()


def container_state(name: str) -> str:
    result = run(["docker", "inspect", "-f", "{{.State.Status}}", name])
    return result.stdout.strip() if result.returncode == 0 else "absent"


def exit_code(container_id: str) -> int:
    return int(
        run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id], check=True
        ).stdout.strip()
    )


def logs(container_id: str) -> str:
    result = run(["docker", "logs", container_id])
    return result.stdout + result.stderr


def json_lines(text: str) -> list[dict[str, Any]]:
    events = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("{"):
            with contextlib.suppress(json.JSONDecodeError):  # uvicorn's own plain-text lines
                events.append(json.loads(line))
    return events


def wait_for(probe: Callable[[], bool], timeout: float, interval: float = 0.2) -> float | None:
    """Seconds until `probe()` is true, or None at the timeout."""
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            if probe():
                return time.perf_counter() - started
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return None


def status_of(path: str) -> bool:
    return httpx.get(BASE + path, timeout=5).status_code == 200


# --- the checks -----------------------------------------------------------------------------


def build_image(s: Smoke) -> None:
    commit = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]) if shutil.which("git") else None
    sha = commit.stdout.strip() if commit is not None and commit.returncode == 0 else ""
    args = [
        "docker",
        "build",
        "-t",
        IMAGE,
        "--build-arg",
        f"APP_BUILD_TIME={datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if sha:
        args += ["--build-arg", f"APP_BUILD_COMMIT={sha}"]
    started = time.perf_counter()
    result = run([*args, str(ROOT)], timeout=1800)
    s.timings["image_build_s"] = round(time.perf_counter() - started, 1)
    s.check("image builds", result.returncode == 0, result.stderr[-2000:])


def image_checks(s: Smoke) -> None:
    info = json.loads(run(["docker", "image", "inspect", IMAGE], check=True).stdout)[0]
    config = info["Config"]
    s.notes["image_size_mb"] = round(info["Size"] / 1_000_000, 1)
    s.check("image runs as uid/gid 10001", config.get("User") == "10001:10001", config.get("User"))
    s.check("image defaults to APP_ENV=production", "APP_ENV=production" in config["Env"])
    s.check(
        "image exposes only port 8000",
        set((config.get("ExposedPorts") or {}).keys()) == {"8000/tcp"},
    )
    s.check("image has a health check", bool(config.get("Healthcheck")))
    env_names = [e.split("=", 1)[0] for e in config["Env"]]
    s.check(
        "no secret variables baked into the image",
        not [n for n in env_names if re.search(r"PASSWORD|SECRET|_KEY|_URL|TOKEN", n)],
        env_names,
    )
    probe = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "id -u; for t in pip pip3 gcc cc make git curl wget; do "
            "command -v $t >/dev/null 2>&1 && echo HAS:$t; done; "
            "python -c 'import pytest' 2>/dev/null && echo HAS:pytest; "
            "python -c 'import mypy' 2>/dev/null && echo HAS:mypy; "
            "ls /app; true",
        ],
        check=True,
    ).stdout
    lines = probe.split()
    s.check("container user is 10001", lines[:1] == ["10001"], lines[:1])
    s.check(
        "no package manager, compiler, VCS, downloader or dev tool in the runtime image",
        not [w for w in lines if w.startswith("HAS:")],
        [w for w in lines if "HAS:" in w],
    )
    s.check("only alembic.ini under /app", lines[1:] == ["alembic.ini"], lines[1:])
    version = run(
        ["docker", "run", "--rm", IMAGE, "python", "-m", "agentic_platform.api", "--version"]
    )
    build = json.loads(version.stdout) if version.returncode == 0 else {}
    s.notes["build"] = build
    s.check(
        "build info is baked in (no git at runtime)",
        version.returncode == 0 and bool(build.get("version")) and bool(build.get("build_time")),
        version.stdout + version.stderr,
    )


def template_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        values[key] = (
            "10.0.0.0/24"
            if key == "API_FORWARDED_ALLOW_IPS"
            else re.sub(r"<[^>]*>", "example", value.strip())
        )
    return values


def config_gate(s: Smoke, workdir: Path) -> None:
    secrets_dir = workdir / "gate-secrets"
    secrets_dir.mkdir()
    for name, value in GATE_SECRETS.items():
        (secrets_dir / name).write_text(value, encoding="utf-8")

    def gate(
        module: str, extra: dict[str, str] | None = None, command: str = "--check-config"
    ) -> subprocess.CompletedProcess[str]:
        env_file = workdir / "gate.env"
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in {**template_env(), **(extra or {})}.items()) + "\n",
            encoding="utf-8",
        )
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--env-file",
            str(env_file),
            "-v",
            f"{secrets_dir}:/run/secrets:ro",
            IMAGE,
            "python",
            "-m",
            module,
        ]
        return run([*args, command] if command else args, timeout=120)

    def codes(result: subprocess.CompletedProcess[str]) -> set[str]:
        try:
            return {i["code"] for i in json.loads(result.stdout)["issues"]}
        except (json.JSONDecodeError, KeyError):
            return set()

    api = gate("agentic_platform.api")
    s.check(
        "production template passes the API gate in the image",
        api.returncode == 0,
        api.stdout + api.stderr,
    )
    worker = gate("agentic_platform.maintenance")
    s.check(
        "production template passes the worker gate in the image",
        worker.returncode == 0,
        worker.stdout + worker.stderr,
    )
    for extra, code in (
        ({"AUTH_TOKEN_SECRET": "x" * 40}, "DEV_TOKEN_SECRET_SET"),
        ({"AUTH_ALLOW_LOCAL_IDENTITY": "true"}, "LOCAL_IDENTITY_ALLOWED"),
        ({"LOG_LEVEL": "DEBUG"}, "LOG_LEVEL_DEBUG"),
        (
            {
                "ERP_ADAPTER": "postgres",
                "ERP_EXPECTED_DATABASE": "erp",
                "ERP_DATABASE_URL": "postgresql://reader:pw@erp.invalid:5432/erp",
            },
            "ERP_CONTRACT_MISSING",
        ),
    ):
        refused = gate("agentic_platform.api", extra)
        s.check(
            f"production gate refuses {code}",
            refused.returncode == 1 and code in codes(refused),
            refused.stdout[-800:],
        )
    started = time.perf_counter()
    start = gate("agentic_platform.api", {"AUTH_TOKEN_SECRET": "y" * 40}, command="")
    s.timings["refused_start_s"] = round(time.perf_counter() - started, 2)
    s.check(
        "an unsafe production start exits 2 before serving",
        start.returncode == 2 and "Production configuration refused" in start.stderr,
        {"exit": start.returncode, "stderr": start.stderr[-500:]},
    )
    leaked = [v for v in GATE_SECRETS.values() if v in api.stdout + api.stderr + start.stderr]
    s.check("gate reports print no secret values", not leaked)


def start_stack(s: Smoke, stack: Stack) -> None:
    started = time.perf_counter()
    up = stack.compose("up", "-d", "--wait", "postgres", "redis", timeout=300)
    s.check("postgres and redis start healthy", up.returncode == 0, up.stderr[-1500:])
    s.timings["infrastructure_ready_s"] = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    up = stack.compose("up", "-d", "api", "maintenance", timeout=300)
    s.check("migrate job, API and worker start", up.returncode == 0, up.stderr[-1500:])
    s.timings["app_containers_started_s"] = round(time.perf_counter() - started, 1)
    s.check(
        "migrate job exits 0",
        exit_code(stack.container("migrate")) == 0,
        logs(stack.container("migrate"))[-1500:],
    )
    live = wait_for(lambda: status_of("/health"), 120)
    ready = wait_for(lambda: status_of("/ready"), 120)
    s.timings["api_live_after_start_s"] = round(live or -1, 2)
    s.timings["api_ready_after_start_s"] = round(ready or -1, 2)
    s.check(
        "API becomes live and ready",
        live is not None and ready is not None,
        logs(stack.container("api"))[-3000:],
    )


def database_identity(s: Smoke, stack: Stack) -> None:
    row = stack.psql(
        "SELECT current_database() || '|' || (SELECT version_num FROM alembic_version)"
    )
    s.check("the API's database is the smoke Agent DB at head 0005", row == "agentic|0005", row)
    tables = stack.psql(
        "SELECT string_agg(relname, ',' ORDER BY relname) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND relkind = 'r'"
    )
    expected = (
        "agent_checkpoint_blobs,agent_checkpoint_writes,agent_checkpoints,alembic_version,"
        "approval_request,audit_log,conversation_thread,idempotency_record,memory_item"
    )
    s.check("Agent DB holds only the platform's tables (no ERP schema)", tables == expected, tables)


def probes(s: Smoke) -> None:
    health = httpx.get(BASE + "/health", timeout=5)
    s.check("GET /health 200", health.status_code == 200 and health.json()["status"] == "ok")
    started = time.perf_counter()
    ready = httpx.get(BASE + "/ready", timeout=10)
    s.timings["ready_probe_ms"] = round((time.perf_counter() - started) * 1000, 1)
    body = ready.json()
    checks = body.get("checks", {})
    s.notes["ready"] = body
    s.check(
        "GET /ready 200 with database and redis ok",
        ready.status_code == 200
        and checks["database"]["status"] == "ok"
        and checks["redis"]["status"] == "ok",
        body,
    )
    s.check(
        "readiness reports the ERP without gating on it",
        checks.get("erp", {}).get("ready") is True,
        checks.get("erp"),
    )
    s.check(
        "readiness carries build info and no secret",
        body.get("build", {}).get("version") and not any(v in ready.text for v in s.secret_values),
        body.get("build"),
    )


def tokens(secret: str) -> Callable[..., str]:
    settings = AppSettings(
        env="development", auth=AuthSettings(token_secret=secret, role_catalog_json=ROLE_CATALOG)
    )
    authenticator = build_token_authenticator(
        settings, IdentityResolver(RoleCatalog.from_json(ROLE_CATALOG))
    )
    assert authenticator is not None

    def issue(user: str, roles: Sequence[str] = ("member",), tenant: str = TENANT) -> str:
        return authenticator.issue(user_id=user, tenant_id=tenant, roles=list(roles))

    return issue


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_boundary(s: Smoke, issue: Callable[..., str]) -> None:
    me = BASE + "/api/v1/me"
    s.check("no token: 401", httpx.get(me, timeout=10).status_code == 401)
    s.check(
        "malformed token: 401",
        httpx.get(me, headers={"Authorization": "Basic abc"}, timeout=10).status_code == 401,
    )
    forged = tokens("forged-secret-" + "z" * 40)("mallory")
    s.check(
        "token signed with another key: 401",
        httpx.get(me, headers=bearer(forged), timeout=10).status_code == 401,
    )
    ok = httpx.get(me, headers=bearer(issue("u-auth")), timeout=10)
    s.check(
        "valid token: 200 with the token's tenant",
        ok.status_code == 200 and ok.json().get("tenant_id") == TENANT,
        ok.text[:300],
    )
    body = {"message": "Hello", "tenant_id": "other-tenant", "roles": ["tenant_admin"]}
    forged_body = httpx.post(
        BASE + "/api/v1/agent/turn", json=body, headers=bearer(issue("u-auth")), timeout=30
    )
    s.check("a body naming a tenant or roles is refused (422)", forged_body.status_code == 422)


def turn(
    token: str,
    message: str,
    *,
    thread_id: str | None = None,
    key: str | None = None,
    timeout: float = 60,
) -> httpx.Response:
    body: dict[str, Any] = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    if key:
        body["idempotency_key"] = key
    return httpx.post(
        BASE + "/api/v1/agent/turn", json=body, headers=bearer(token), timeout=timeout
    )


def turns(s: Smoke, issue: Callable[..., str]) -> dict[str, str]:
    started = time.perf_counter()
    general = turn(issue("u-general"), "Hello")
    s.timings["general_turn_ms"] = round((time.perf_counter() - started) * 1000, 1)
    data = general.json()
    s.check(
        "a safe general turn completes (scripted model)",
        general.status_code == 200
        and data["status"] == "completed"
        and "smoke-test stub" in data["message"],
        general.text[:400],
    )
    business_token = issue("u-business", roles=("member", "sales_viewer"))
    started = time.perf_counter()
    business = turn(business_token, "Rahul Gupta ka outstanding kitna hai?")
    s.timings["business_turn_ms"] = round((time.perf_counter() - started) * 1000, 1)
    bdata = business.json()
    calls = [(c["tool_name"], c["status"]) for c in bdata.get("tool_calls", [])]
    s.check(
        "a safe business turn completes (ERP boundary, no contract: nothing read)",
        business.status_code == 200
        and bdata["status"] == "completed"
        and calls == [("business.customer_outstanding", "ok")],
        business.text[:400],
    )
    # Repeat timings (warm): a few more turns, median reported.
    samples = []
    for i in range(5):
        started = time.perf_counter()
        turn(issue(f"u-warm-{i}"), "Hello")
        samples.append((time.perf_counter() - started) * 1000)
    s.timings["general_turn_warm_median_ms"] = round(sorted(samples)[2], 1)
    return {
        "general": data["thread_id"],
        "business": bdata["thread_id"],
        "business_token": business_token,
    }


def resource_protection(s: Smoke, issue: Callable[..., str]) -> None:
    big = "x" * (2 * 1024 * 1024)
    too_large = httpx.post(
        BASE + "/api/v1/agent/turn",
        content=big,
        headers={**bearer(issue("u-big")), "content-type": "application/json"},
        timeout=30,
    )
    s.check(
        "request body over API_MAX_BODY_BYTES: 413",
        too_large.status_code == 413 and too_large.json()["error"]["code"] == "REQUEST_TOO_LARGE",
    )
    token = issue("u-ratelimit")
    statuses = [
        httpx.get(BASE + "/api/v1/me", headers=bearer(token), timeout=10) for _ in range(62)
    ]
    limited = [r for r in statuses if r.status_code == 429]
    s.check(
        "per-caller rate limit: 429 RATE_LIMITED with Retry-After",
        bool(limited)
        and limited[0].json()["error"]["code"] == "RATE_LIMITED"
        and "retry-after" in limited[0].headers,
    )
    # API_MAX_CONCURRENT_TURNS=2: a third simultaneous turn is refused at once.
    results: list[httpx.Response | None] = [None, None, None]
    barrier = threading.Barrier(3)

    def worker(index: int) -> None:
        token = issue(f"u-concurrent-{index}")
        barrier.wait()
        time.sleep(0.3 * index)  # 0 and 1 take the slots; 2 arrives while both are held
        results[index] = turn(token, "Hello SLOW-4", timeout=60)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = [r.status_code if r is not None else None for r in results]
    busy = results[2]
    s.check(
        "concurrent-turn cap: 2 complete, the third gets 503 SERVER_BUSY",
        codes[:2] == [200, 200]
        and busy is not None
        and busy.status_code == 503
        and busy.json()["error"]["code"] == "SERVER_BUSY",
        codes,
    )


def observability(s: Smoke, stack: Stack, token_samples: Sequence[str]) -> None:
    text = logs(stack.container("api"))
    events = json_lines(text)
    names = {e.get("event") for e in events}
    wanted = {
        "turn.started",
        "turn.completed",
        "planner.completed",
        "supervisor.decision",
        "tool.completed",
        "business.read",
        "llm.call.completed",
        "http.request.finished",
        "auth.succeeded",
        "auth.failed",
        "api.started",
    }
    s.check(
        "observability events are emitted as JSON log lines",
        wanted <= names,
        sorted(wanted - names),
    )
    reads = [e for e in events if e.get("event") == "business.read"]
    s.check(
        "business reads go through the read-only postgres boundary only",
        bool(reads) and all(e.get("adapter") == "postgres" for e in reads),
        [e.get("adapter") for e in reads],
    )
    s.check(
        "no ERP connection attempt in the logs",
        not [e for e in events if str(e.get("event", "")).startswith("erp.")],
    )
    leaked = [v for v in [*s.secret_values, *token_samples] if v and v in text]
    s.check("no token or secret in any API log line", not leaked, len(leaked))


def graceful_shutdown(s: Smoke, stack: Stack, issue: Callable[..., str]) -> None:
    api = stack.container("api")
    token = issue("u-shutdown-a")
    result: dict[str, Any] = {}

    def in_flight() -> None:
        try:
            result["response"] = turn(token, "Hello SLOW-3", timeout=60)
        except httpx.HTTPError as exc:
            result["error"] = type(exc).__name__

    thread = threading.Thread(target=in_flight)
    thread.start()
    time.sleep(1.0)
    started = time.perf_counter()
    run(["docker", "stop", "-t", "30", api], check=True, timeout=120)
    s.timings["api_stop_with_inflight_turn_s"] = round(time.perf_counter() - started, 2)
    thread.join(60)
    response = result.get("response")
    s.check(
        "SIGTERM: the in-flight turn finishes with 200",
        response is not None
        and response.status_code == 200
        and response.json()["status"] == "completed",
        result,
    )
    code = exit_code(api)
    s.notes["api_exit_code_after_sigterm"] = code
    s.check("API exits cleanly on SIGTERM (0, or 143 = re-raised SIGTERM)", code in (0, 143), code)
    shutdown_events = [
        e
        for e in json_lines(logs(api))
        if e.get("event", "").startswith(("shutdown.", "api.stopped"))
    ]
    steps = [e.get("step") for e in shutdown_events if e.get("event") == "shutdown.step"]
    s.notes["shutdown_steps"] = steps
    s.check(
        "shutdown closes resources in order and reports completion",
        steps[:1] == ["in_flight_requests"]
        and "redis" in steps
        and "database" in steps
        and any(
            e.get("event") == "shutdown.completed" and e.get("status") == "ok"
            for e in shutdown_events
        ),
        shutdown_events[-8:],
    )

    restarted = time.perf_counter()
    run(["docker", "start", api], check=True)
    ready = wait_for(lambda: status_of("/ready"), 120)
    s.timings["api_restart_to_ready_s"] = round(
        (time.perf_counter() - restarted) if ready is not None else -1, 2
    )
    s.check("restart: ready again", ready is not None)


def shutdown_deadline(s: Smoke, stack: Stack, issue: Callable[..., str]) -> None:
    api = stack.container("api")
    token = issue("u-shutdown-b")
    first = turn(token, "Hello")
    thread_id = first.json()["thread_id"]
    key = "smoke-deadline-" + secrets.token_hex(4)
    result: dict[str, Any] = {}

    def too_long() -> None:
        try:
            result["response"] = turn(
                token, "Hello SLOW-60", thread_id=thread_id, key=key, timeout=120
            )
        except httpx.HTTPError as exc:
            result["error"] = type(exc).__name__

    thread = threading.Thread(target=too_long)
    thread.start()
    time.sleep(1.5)
    started = time.perf_counter()
    run(["docker", "stop", "-t", "30", api], check=True, timeout=120)
    stop_s = time.perf_counter() - started
    s.timings["api_stop_past_deadline_s"] = round(stop_s, 2)
    thread.join(120)
    response = result.get("response")
    s.check(
        "a turn longer than the shutdown deadline does not succeed",
        response is None or response.status_code != 200,
        result,
    )
    s.check(
        f"stop is bounded by API_GRACEFUL_SHUTDOWN_SECONDS ({GRACEFUL_SECONDS}) + cleanup, "
        "not by the turn (60 s)",
        stop_s < GRACEFUL_SECONDS + 10,
        round(stop_s, 2),
    )
    text = logs(api)
    s.check("the over-deadline task was cancelled", "timeout graceful shutdown exceeded" in text)

    run(["docker", "start", api], check=True)
    s.check(
        "restart after a cut turn: ready", wait_for(lambda: status_of("/ready"), 120) is not None
    )
    view = httpx.get(f"{BASE}/api/v1/conversations/{thread_id}", headers=bearer(token), timeout=10)
    s.check(
        "the cut turn is left unfinished (pending), not completed",
        view.status_code == 200 and view.json()["pending"] is True,
        view.text[:300],
    )
    record = stack.psql(
        "SELECT status || '|' || coalesce(error_code, '') FROM idempotency_record "
        f"WHERE idempotency_key LIKE '%{key}'"
    )
    s.notes["cut_turn_idempotency_record"] = record
    s.check(
        "its idempotency record is not 'completed' (a retry cannot replay a success)",
        record != "" and not record.startswith("completed"),
        record,
    )
    retry = turn(token, "Hello SLOW-60", thread_id=thread_id, key=key)
    s.check(
        "retrying the cut turn is not answered as a replayed success",
        not (retry.status_code == 200 and retry.json().get("replayed")),
        {"status": retry.status_code, "body": retry.text[:300]},
    )
    forget = httpx.delete(
        f"{BASE}/api/v1/conversations/{thread_id}", headers=bearer(token), timeout=30
    )
    s.check("the unfinished conversation can be forgotten", forget.status_code == 200)


def after_restart(s: Smoke, stack: Stack, threads: dict[str, str]) -> None:
    history = httpx.get(
        f"{BASE}/api/v1/conversations/{threads['business']}/history",
        headers=bearer(threads["business_token"]),
        timeout=10,
    )
    s.check(
        "Agent DB usable after restarts: earlier conversation readable",
        history.status_code == 200 and len(history.json().get("messages", [])) >= 2,
        history.text[:300],
    )
    again = turn(
        threads["business_token"],
        "Rahul Gupta ka outstanding kitna hai?",
        thread_id=threads["business"],
    )
    s.check(
        "Redis and checkpoints usable after restarts: a follow-up turn completes",
        again.status_code == 200 and again.json()["status"] == "completed",
        again.text[:300],
    )
    ping = stack.compose(
        "exec", "-T", "redis", "sh", "-c", 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping'
    )
    s.check("Redis answers PING", ping.stdout.strip() == "PONG")


def worker(s: Smoke, stack: Stack) -> None:
    once = stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "maintenance",
        "python",
        "-m",
        "agentic_platform.maintenance",
        timeout=300,
    )
    reports = [json.loads(line) for line in once.stdout.splitlines() if line.startswith("{")]
    s.check(
        "one-shot maintenance run exits 0 with a report per job",
        once.returncode == 0
        and len(reports) >= 2
        and all(r["status"] in ("ok", "skipped") for r in reports),
        {"exit": once.returncode, "reports": reports, "stderr": once.stderr[-500:]},
    )
    container = stack.container("maintenance")
    started = time.perf_counter()
    run(["docker", "stop", "-t", "60", container], check=True, timeout=120)
    s.timings["worker_stop_s"] = round(time.perf_counter() - started, 2)
    text = logs(container)
    s.check(
        "--loop worker: SIGTERM → stop requested → exit 0",
        exit_code(container) == 0 and "maintenance.scheduler.stopped" in text,
        exit_code(container),
    )


def backup_restore(s: Smoke, stack: Stack, app_password: str) -> None:
    owner_env = ["-e", "PGUSER=agentic_owner", "-e", "PGHOST=/var/run/postgresql"]
    refused = stack.compose(
        "exec",
        "-T",
        *owner_env,
        "-e",
        "PGDATABASE=agentic_smoke_scratch",
        "postgres",
        "sh",
        "/backup/agent_db_backup.sh",
        "/tmp/not-agent.dump",
    )
    s.check(
        "backup refuses a database that is not the Agent DB",
        refused.returncode == 3,
        refused.stderr[-300:],
    )
    started = time.perf_counter()
    backup = stack.compose(
        "exec",
        "-T",
        *owner_env,
        "-e",
        "PGDATABASE=agentic",
        "postgres",
        "sh",
        "/backup/agent_db_backup.sh",
        "/tmp/agent.dump",
    )
    s.timings["backup_s"] = round(time.perf_counter() - started, 2)
    s.check(
        "Agent DB backup (pg_dump -Fc + manifest)",
        backup.returncode == 0,
        backup.stdout + backup.stderr,
    )
    started = time.perf_counter()
    restore = stack.compose(
        "exec",
        "-T",
        *owner_env,
        "postgres",
        "sh",
        "/backup/agent_db_restore.sh",
        "/tmp/agent.dump",
        "agentic_restored",
    )
    s.timings["restore_s"] = round(time.perf_counter() - started, 2)
    s.check(
        "restore into a new database",
        restore.returncode == 0,
        restore.stdout[-500:] + restore.stderr[-1500:],
    )
    verify = stack.compose(
        "exec",
        "-T",
        *owner_env,
        "postgres",
        "sh",
        "/backup/agent_db_verify.sh",
        "/tmp/agent.dump.manifest",
        "agentic_restored",
        "--strict",
    )
    s.check(
        "restore verification (revision, tables, RLS, triggers, grants, row counts)",
        verify.returncode == 0 and "VERIFIED" in verify.stdout,
        verify.stdout[-1500:],
    )
    again = stack.compose(
        "exec",
        "-T",
        *owner_env,
        "postgres",
        "sh",
        "/backup/agent_db_restore.sh",
        "/tmp/agent.dump",
        "agentic_restored",
    )
    s.check(
        "restore refuses a non-empty target (never over a live database)",
        again.returncode == 4,
        again.stderr[-300:],
    )
    url = f"postgresql://agentic_app:{app_password}@postgres:5432/agentic_restored"
    health = stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "-e",
        f"DATABASE_URL={url}",
        "api",
        "python",
        "-m",
        "agentic_platform",
        "--db-health",
        timeout=300,
    )
    s.check(
        "the application (runtime role) reads the restored copy at the migration head",
        health.returncode == 0 and '"up_to_date": true' in health.stdout.replace(" ", " "),
        health.stdout[-800:] + health.stderr[-500:],
    )


def resource_usage(s: Smoke, stack: Stack) -> None:
    out = run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}",
            stack.container("api"),
        ]
    ).stdout.strip()
    s.notes["api_memory_after_turns"] = out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Leave the stack running.")
    args = parser.parse_args(argv)
    s = Smoke()
    erp_before = container_state(ERP_CANDIDATE)
    s.notes.update(
        {
            "started": datetime.now(UTC).isoformat(),
            "erp_candidate_before": erp_before,
            "host": f"{platform.system()} {platform.release()}",
            "docker": run(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip(),
        }
    )
    workdir = Path(tempfile.mkdtemp(prefix="agentic-smoke-"))
    passwords = {name: secrets.token_urlsafe(24) for name in ("owner", "app", "redis")}
    token_secret = secrets.token_urlsafe(48)
    s.secret_values = [*passwords.values(), token_secret]
    stack_secrets = workdir / "secrets"
    stack_secrets.mkdir()
    files = {
        "DATABASE_URL": f"postgresql://agentic_app:{passwords['app']}@postgres:5432/agentic",
        "DATABASE_MIGRATION_URL": f"postgresql://agentic_owner:{passwords['owner']}@postgres:5432/agentic",
        "REDIS_URL": f"redis://:{passwords['redis']}@redis:6379/0",
        "AUTH_TOKEN_SECRET": token_secret,
        "ANTHROPIC_API_KEY": "smoke-stub-key-not-real",
    }
    for name, value in files.items():
        (stack_secrets / name).write_text(value, encoding="utf-8")
    env = {
        **os.environ,
        "SMOKE_SECRETS_DIR": str(stack_secrets),
        "SMOKE_PG_OWNER_PASSWORD": passwords["owner"],
        "SMOKE_PG_APP_PASSWORD": passwords["app"],
        "SMOKE_REDIS_PASSWORD": passwords["redis"],
        "SMOKE_GRACEFUL_SECONDS": str(GRACEFUL_SECONDS),
        "SMOKE_API_PORT": str(API_PORT),
    }
    stack = Stack(env)
    issue = tokens(token_secret)
    began = time.perf_counter()
    try:
        stack.compose("down", "--remove-orphans", timeout=300)  # a previous run's leftovers
        if not args.no_build:
            build_image(s)
        image_checks(s)
        config_gate(s, workdir)
        start_stack(s, stack)
        database_identity(s, stack)
        probes(s)
        auth_boundary(s, issue)
        threads = turns(s, issue)
        resource_protection(s, issue)
        observability(s, stack, [threads["business_token"]])
        resource_usage(s, stack)
        graceful_shutdown(s, stack, issue)
        shutdown_deadline(s, stack, issue)
        after_restart(s, stack, threads)
        worker(s, stack)
        backup_restore(s, stack, passwords["app"])
    except Exception as exc:  # report what happened; the stack is still torn down
        s.check("smoke run completed without an exception", False, repr(exc))
    finally:
        s.timings["total_s"] = round(time.perf_counter() - began, 1)
        if not args.keep:
            stack.compose("down", "--remove-orphans", timeout=300)
        shutil.rmtree(workdir, ignore_errors=True)
        erp_after = container_state(ERP_CANDIDATE)
        s.notes["erp_candidate_after"] = erp_after
        s.check(
            "ERP candidate container untouched (same state before and after)",
            erp_after == erp_before,
            {"before": erp_before, "after": erp_after},
        )
        s.notes["finished"] = datetime.now(UTC).isoformat()
    RESULTS.mkdir(parents=True, exist_ok=True)
    report = s.scrub(
        {"passed": s.passed, "checks": s.checks, "timings": s.timings, "notes": s.notes}
    )
    out = RESULTS / "phase15-smoke.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = [c["name"] for c in s.checks if not c["ok"]]
    print(f"\n{len(s.checks) - len(failed)}/{len(s.checks)} checks passed; report: {out}")
    for name in failed:
        print(f"  FAILED: {name}")
    return 0 if s.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
