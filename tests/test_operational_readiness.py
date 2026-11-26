"""Phase 15: production hardening and operational readiness (ADR-064). No servers.

* build information: baked in, validated, never git at run time;
* configuration boundary: ERP defaults to none, unknown exporters refused, new policy
  checks, entry points;
* liveness / readiness: ERP never gates readiness and never makes liveness fail;
* resource protection: concurrent-turn cap (SERVER_BUSY), default headers, body limit;
* graceful shutdown: bounded sequence in order, hung steps abandoned, resources closed,
  a cancelled turn is never a success;
* observability export boundary: a failing or hanging exporter cannot fail a turn or
  block a shutdown;
* migration discipline and secret hygiene.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.schemas import RequestUnderstanding
from agentic_platform.api import __main__ as api_main
from agentic_platform.api import create_app
from agentic_platform.business.service import BusinessReadService
from agentic_platform.cache.redis import RedisConnection
from agentic_platform.core import build as build_module
from agentic_platform.core.config import (
    ApiSettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    ErpSettings,
    LLMSettings,
    ObservabilitySettings,
    RedisSettings,
    get_settings,
)
from agentic_platform.core.errors import ServerBusyError, ThreadPendingError
from agentic_platform.core.logging import JsonFormatter
from agentic_platform.core.production import check_configuration
from agentic_platform.db.engine import Database
from agentic_platform.db.migrations import head_revision
from agentic_platform.observability import EventName, capture_events
from agentic_platform.observability.hub import HUB
from agentic_platform.observability.taxonomy import ErrorCategory, classify_code
from agentic_platform.security import IdentityResolver
from agentic_platform.services.clock import ClockService
from agentic_platform.services.lifecycle import (
    MIN_STEP_SECONDS,
    InFlight,
    ShutdownStep,
    TurnSlots,
    in_daemon_thread,
    run_shutdown,
)
from agentic_platform.services.readiness import ReadinessService, erp_check
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token
from tests.conftest import understanding
from tests.fakes import ScriptedLLM
from tests.test_production_unit import production

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agentic_platform"
CLOSED = "127.0.0.1:1"  # nothing listens here: connections are refused
DB_PASSWORD = "P15-db-PASSWORD-never-logged"
REDIS_PASSWORD = "P15-redis-PASSWORD-never-logged"
LLM_KEY = "sk-ant-api03-P15-KEY-never-logged"


def greeting() -> RequestUnderstanding:
    return understanding(intent="greeting", tool_calls=[])


def principal(user: str = "asha") -> Any:
    return IdentityResolver().resolve(
        user_id=user, tenant_id="acme", roles=["member"], auth_method="token"
    )


@pytest.fixture
def capture_logs() -> Iterator[io.StringIO]:
    """The JSON lines the platform logger writes (the production formatter)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("agentic_platform")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def events_in(stream: io.StringIO, name: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == name]


# =============================================================================================
# Build information
# =============================================================================================


def test_build_info_comes_from_the_package_and_validated_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_module.build_info.cache_clear()
    monkeypatch.setenv("APP_BUILD_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("APP_BUILD_TIME", "2026-10-05T12:00:00Z")
    try:
        info = build_module.build_info()
        assert info.version == "0.1.0"  # pyproject.toml, via importlib.metadata
        assert info.commit == "0123456789abcdef0123456789abcdef01234567"
        assert info.build_time == "2026-10-05T12:00:00Z"
    finally:
        build_module.build_info.cache_clear()


@pytest.mark.parametrize(
    "commit", ["", "HEAD", "abc", "deadbeef; rm -rf /", "postgresql://u:pw@h/d", "G" * 40]
)
def test_malformed_build_values_never_reach_probes_or_logs(
    monkeypatch: pytest.MonkeyPatch, commit: str
) -> None:
    build_module.build_info.cache_clear()
    monkeypatch.setenv("APP_BUILD_COMMIT", commit)
    monkeypatch.setenv("APP_BUILD_TIME", "yesterday")
    try:
        info = build_module.build_info()
        assert (info.commit, info.build_time) == ("unknown", "unknown")
    finally:
        build_module.build_info.cache_clear()


def test_version_flag_needs_no_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OBSERVABILITY_EXPORTER", "invalid")  # would refuse a start
    get_settings.cache_clear()
    try:
        assert api_main.main(["--version"]) == 0
    finally:
        get_settings.cache_clear()
    assert set(json.loads(capsys.readouterr().out)) == {"version", "commit", "build_time"}


def test_build_info_is_not_read_from_git_at_run_time() -> None:
    source = (SRC / "core" / "build.py").read_text(encoding="utf-8")
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    code = " ".join(lines)
    assert "subprocess" not in code and "import git" not in code and ".git" not in code


# =============================================================================================
# Configuration boundary
# =============================================================================================


def test_erp_adapter_defaults_to_none_everywhere() -> None:
    assert ErpSettings().adapter == "none"
    assert AppSettings().erp.adapter == "none"
    template = (ROOT / "deploy" / "env" / "production.env.example").read_text(encoding="utf-8")
    assert re.search(r"^ERP_ADAPTER=none$", template, re.MULTILINE)
    assert not re.search(r"^ERP_DATABASE_URL=", template, re.MULTILINE)
    assert "ERP_" not in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ERP_ADAPTER: ${ERP_ADAPTER:-none}" in compose
    assert "ERP_DATABASE_URL" not in compose


def test_the_erp_contract_gate_stays_enforced_in_production() -> None:
    erp = ErpSettings(
        adapter="postgres",
        database_url="postgresql://reader:pw@erp.internal:5432/erp",
        expected_database="erp",
        ssl_mode="verify-full",
    )
    report = check_configuration(production(erp=erp), role="api", environ={})
    assert "ERP_CONTRACT_MISSING" in {i.code for i in report.errors}
    # The fake TEST-data adapter was removed (2026-10-30): it cannot even be configured.
    with pytest.raises(ValueError, match="adapter"):
        ErpSettings(adapter="fake")  # type: ignore[arg-type]


def test_an_unknown_observability_exporter_is_refused_not_ignored() -> None:
    assert ObservabilitySettings().exporter == "none"
    with pytest.raises(ValidationError, match="exporter"):
        ObservabilitySettings(exporter="otlp")


def test_api_entry_point_refuses_an_invalid_setting_before_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OBSERVABILITY_EXPORTER", "otlp")
    get_settings.cache_clear()
    try:
        assert api_main.main([]) == 2  # never reaches uvicorn
    finally:
        get_settings.cache_clear()
    assert "Configuration error" in capsys.readouterr().err


def test_unbounded_turn_concurrency_is_a_production_warning() -> None:
    report = check_configuration(
        production(api=ApiSettings(max_concurrent_turns=0)), role="api", environ={}
    )
    assert "TURN_CONCURRENCY_UNBOUNDED" in {i.code for i in report.warnings}
    assert check_configuration(production(), role="api", environ={}).issues == []


def test_server_busy_is_a_retryable_dependency_error() -> None:
    error = ServerBusyError()
    assert error.retryable and error.code == "SERVER_BUSY"
    assert classify_code("SERVER_BUSY") is ErrorCategory.RETRYABLE_DEPENDENCY
    assert "busy" in error.public_message.lower() and "turn slots" not in error.public_message


# =============================================================================================
# Liveness and readiness
# =============================================================================================


@dataclass
class GatedPort:
    """A stand-in live adapter: only what readiness and shutdown look at."""

    state: str
    name: str = "postgres"
    test_data: bool = False
    closed: int = 0

    def gate_state(self) -> str:
        return self.state

    async def aclose(self) -> None:
        self.closed += 1


def service(port: Any) -> BusinessReadService:
    return BusinessReadService(port, settings=ErpSettings())


@pytest.mark.parametrize(
    ("state", "status", "code"),
    [
        ("verified", "ok", None),
        ("pending", "degraded", "ERP_NOT_YET_VERIFIED"),
        ("unverified", "not_configured", "ERP_NO_VERIFIED_CAPABILITY"),
        ("refused", "misconfigured", "ERP_SAFETY_VIOLATION"),
    ],
)
def test_the_erp_gate_state_is_reported_but_never_gates_readiness(
    state: str, status: str, code: str | None
) -> None:
    check = erp_check(service(GatedPort(state)))
    assert (check.status, check.error_code, check.ready) == (status, code, True)
    readiness = asyncio.run(
        ReadinessService(
            database=None,
            redis=None,
            production=False,
            authentication_configured=True,
            business=service(GatedPort(state)),
        ).check()
    )
    assert readiness.status == "ready"


def test_erp_disabled_is_not_configured_and_ready() -> None:
    assert erp_check(None).model_dump() == {
        "status": "not_configured",
        "ready": True,
        "latency_ms": None,
        "error_code": None,
    }


def test_liveness_ignores_dependencies_and_the_erp(
    settings: AppSettings, clock: ClockService
) -> None:
    """Agent DB unreachable, Redis unreachable, ERP disabled: /health stays 200 (the
    process is alive), /ready answers 503 and names the failing dependencies."""
    unreachable = settings.model_copy(
        update={
            "database": DatabaseSettings(
                url=f"postgresql://agentic_app:{DB_PASSWORD}@{CLOSED}/agentic",
                connect_timeout_seconds=1,
                health_timeout_seconds=1,
            ),
            "redis": RedisSettings(
                url=f"redis://:{REDIS_PASSWORD}@{CLOSED}/0",
                connect_timeout_seconds=0.5,
                health_timeout_seconds=1,
            ),
            "auth": AuthSettings(token_secret=TOKEN_SECRET),
        }
    )
    deps = build_dependencies(unreachable, llm=ScriptedLLM(), clock=clock)
    agent = Agent(deps, checkpointer=InMemorySaver())
    with api_client(unreachable, agent) as client:
        health = client.get("/health")
        assert (health.status_code, health.json()) == (200, {"status": "ok"})
        ready = client.get("/ready")
    assert ready.status_code == 503
    body = ready.json()
    assert body["checks"]["database"]["status"] == "unavailable"
    assert body["checks"]["redis"]["status"] == "unavailable"
    assert body["checks"]["erp"] == {
        "status": "not_configured",
        "ready": True,
        "latency_ms": None,
        "error_code": None,
    }
    assert set(body["build"]) == {"version", "commit", "build_time"}
    for secret in (DB_PASSWORD, REDIS_PASSWORD, TOKEN_SECRET, "127.0.0.1"):
        assert secret not in ready.text


# =============================================================================================
# Resource protection
# =============================================================================================


def test_turn_slots_refuse_beyond_the_limit_and_zero_means_unlimited() -> None:
    slots = TurnSlots(2)
    slots.acquire()
    slots.acquire()
    with pytest.raises(ServerBusyError):
        slots.acquire()
    assert slots.active == 2
    slots.release()
    slots.acquire()  # a released slot is usable again
    unlimited = TurnSlots(0)
    for _ in range(1000):
        unlimited.acquire()
    with pytest.raises(ValueError):
        TurnSlots(-1)


@dataclass
class BlockingLLM(ScriptedLLM):
    """Waits for `release` before planning: keeps a turn in flight."""

    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.entered.set()
        await self.release.wait()
        return await super().generate_structured(**kwargs)


def test_concurrent_turn_cap_answers_503_server_busy(
    settings: AppSettings, clock: ClockService
) -> None:
    capped = settings.model_copy(
        update={
            "auth": AuthSettings(token_secret=TOKEN_SECRET),
            "api": ApiSettings(max_concurrent_turns=1),
        }
    )

    async def body() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        llm = BlockingLLM(script=[greeting(), "Hi!", greeting(), "Hi again!"])
        agent = Agent(
            build_dependencies(capped, llm=llm, clock=clock), checkpointer=InMemorySaver()
        )
        app = create_app(capped, agent=agent)
        headers = bearer(token(agent, "acme", "asha"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            first = asyncio.create_task(
                client.post("/api/v1/agent/turn", json={"message": "Hello"}, headers=headers)
            )
            await asyncio.wait_for(llm.entered.wait(), 10)
            busy = await client.post(
                "/api/v1/agent/turn",
                json={"message": "Hello"},
                headers=bearer(token(agent, "acme", "ravi")),
            )
            anonymous = await client.post("/api/v1/agent/turn", json={"message": "Hello"})
            llm.release.set()
            done = await asyncio.wait_for(first, 10)
            after = await client.post(
                "/api/v1/agent/turn", json={"message": "Hello"}, headers=headers
            )
        return done, busy, anonymous, after

    done, busy, anonymous, after = asyncio.run(body())
    assert busy.status_code == 503
    assert busy.json()["error"]["code"] == "SERVER_BUSY"
    assert busy.json()["error"]["retryable"] is True and busy.headers["retry-after"]
    # Authentication runs first: an anonymous request never holds (or waits for) a slot.
    assert anonymous.status_code == 401
    assert done.status_code == 200 and after.status_code == 200  # the slot was released


def test_every_response_carries_safe_default_headers(
    harness_settings: AppSettings, clock: ClockService
) -> None:
    agent = Agent(
        build_dependencies(harness_settings, llm=ScriptedLLM(), clock=clock),
        checkpointer=InMemorySaver(),
    )
    with api_client(harness_settings, agent) as client:
        responses = [
            client.get("/health"),
            client.get("/ready"),
            client.get("/api/v1/me"),  # 401
            client.post(
                "/api/v1/agent/turn",
                content=b"x" * 2_000_000,
                headers={"content-type": "application/json"},
            ),  # 413 (declared)
            client.post(
                "/api/v1/agent/turn",
                content=iter([b"x" * 700_000] * 3),
                headers={
                    **bearer(token(agent, "acme", "asha")),
                    "content-type": "application/json",
                },
            ),  # 413 (chunked)
        ]
    assert [r.status_code for r in responses] == [200, 200, 401, 413, 413]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers.get_list("cache-control") == ["no-store"]  # once
    assert responses[4].json()["error"]["code"] == "REQUEST_TOO_LARGE"


@pytest.fixture
def harness_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"auth": AuthSettings(token_secret=TOKEN_SECRET)})


# =============================================================================================
# Graceful shutdown
# =============================================================================================


def test_shutdown_runs_steps_in_order_and_isolates_failures() -> None:
    ran: list[str] = []

    def step(name: str, *, fail: bool = False) -> ShutdownStep:
        async def action() -> None:
            ran.append(name)
            if fail:
                raise RuntimeError(f"close failed with password={DB_PASSWORD}")

        return ShutdownStep(name, action)

    report = asyncio.run(
        run_shutdown([step("a"), step("b", fail=True), step("c")], budget_seconds=5)
    )
    assert ran == ["a", "b", "c"]  # the failure did not stop the sequence
    assert report.status == "degraded"
    assert [(s.step, s.status, s.error_code) for s in report.steps] == [
        ("a", "ok", None),
        ("b", "error", "RuntimeError"),
        ("c", "ok", None),
    ]
    assert DB_PASSWORD not in report.model_dump_json()  # codes only, never messages


def test_a_hung_step_is_abandoned_and_later_steps_still_run() -> None:
    closed: list[str] = []

    async def hang() -> None:
        await asyncio.sleep(3600)

    async def close() -> None:
        closed.append("redis")

    started = time.perf_counter()
    report = asyncio.run(
        run_shutdown(
            [ShutdownStep("database", hang), ShutdownStep("redis", close)], budget_seconds=0.5
        )
    )
    elapsed = time.perf_counter() - started
    assert closed == ["redis"]  # still attempted after the budget was spent
    assert [s.status for s in report.steps] == ["timeout", "ok"]
    assert report.steps[0].error_code == "SHUTDOWN_STEP_TIMEOUT"
    assert elapsed < 0.5 + 2 * MIN_STEP_SECONDS + 1


def test_a_step_cap_keeps_a_share_of_the_budget_for_later_steps() -> None:
    async def hang() -> None:
        await asyncio.sleep(3600)

    async def quick() -> None:
        await asyncio.sleep(0.6)  # longer than MIN_STEP_SECONDS: needs the kept share

    report = asyncio.run(
        run_shutdown(
            [ShutdownStep("in_flight_requests", hang, max_seconds=1.0), ShutdownStep("db", quick)],
            budget_seconds=3,
        )
    )
    assert [s.status for s in report.steps] == ["timeout", "ok"]


def test_in_flight_counter_waits_until_requests_have_unwound() -> None:
    in_flight = InFlight()

    async def body() -> float:
        in_flight.enter()

        async def finish() -> None:
            await asyncio.sleep(0.2)
            in_flight.exit()

        task = asyncio.create_task(finish())
        started = time.perf_counter()
        await asyncio.wait_for(in_flight.wait_idle(), 5)
        await task
        return time.perf_counter() - started

    assert asyncio.run(body()) >= 0.15
    assert in_flight.count == 0
    in_flight.exit()  # never negative
    assert in_flight.count == 0


def shutdown_settings(base: AppSettings, cleanup: float = 5.0) -> AppSettings:
    return base.model_copy(
        update={
            "auth": AuthSettings(token_secret=TOKEN_SECRET),
            "api": ApiSettings(shutdown_cleanup_seconds=cleanup),
            "database": DatabaseSettings(
                url=f"postgresql://agentic_app:{DB_PASSWORD}@{CLOSED}/agentic",
                connect_timeout_seconds=1,
                health_timeout_seconds=1,
            ),
            "redis": RedisSettings(
                url=f"redis://:{REDIS_PASSWORD}@{CLOSED}/0",
                connect_timeout_seconds=0.5,
                health_timeout_seconds=1,
            ),
            "erp": ErpSettings(
                adapter="postgres",
                expected_database="erp_test",
                database_url=f"postgresql://reader:{DB_PASSWORD}@{CLOSED}/erp_test",
            ),
        }
    )


def database_name(database: Database) -> str:
    return database.settings.runtime_url().rsplit("/", 1)[-1]


def test_app_shutdown_closes_redis_then_databases_then_flushes(
    settings: AppSettings,
    clock: ClockService,
    monkeypatch: pytest.MonkeyPatch,
    capture_logs: io.StringIO,
) -> None:
    closed: list[str] = []

    async def database_dispose(self: Database) -> None:
        closed.append(database_name(self))

    async def redis_dispose(self: RedisConnection) -> None:
        closed.append("redis")

    monkeypatch.setattr(Database, "dispose", database_dispose)
    monkeypatch.setattr(RedisConnection, "dispose", redis_dispose)
    flushed: list[float] = []
    monkeypatch.setattr(HUB, "flush", lambda timeout: flushed.append(timeout))
    configured = shutdown_settings(settings)
    agent = Agent(
        build_dependencies(configured, llm=ScriptedLLM(), clock=clock), checkpointer=InMemorySaver()
    )
    with api_client(configured, agent):
        assert closed == []
    assert closed == ["redis", "agentic", "erp_test"]  # Redis, Agent DB, ERP read-only pool
    assert flushed == [configured.observability.flush_timeout_seconds]
    steps = [e["step"] for e in events_in(capture_logs, "shutdown.step")]
    assert steps == ["in_flight_requests", "redis", "database", "erp", "observability"]
    [completed] = events_in(capture_logs, "shutdown.completed")
    assert completed["status"] == "ok"
    [stopped] = events_in(capture_logs, "api.stopped")
    assert stopped["shutdown"] == "ok"
    text = capture_logs.getvalue()
    for secret in (DB_PASSWORD, REDIS_PASSWORD, TOKEN_SECRET):
        assert secret not in text
    [started] = events_in(capture_logs, "api.started")
    assert {"version", "commit", "build_time"} <= set(started)
    [deps_line] = events_in(capture_logs, "api.startup.dependencies")
    assert (deps_line["database"], deps_line["redis"], deps_line["erp"]) == (
        "unavailable",
        "unavailable",
        "not_configured",
    )


def test_app_shutdown_is_bounded_when_a_pool_close_hangs(
    settings: AppSettings,
    clock: ClockService,
    monkeypatch: pytest.MonkeyPatch,
    capture_logs: io.StringIO,
) -> None:
    closed: list[str] = []

    async def hung_dispose(self: Database) -> None:
        await asyncio.sleep(3600)  # a database that never acknowledges

    async def redis_dispose(self: RedisConnection) -> None:
        closed.append("redis")

    monkeypatch.setattr(Database, "dispose", hung_dispose)
    monkeypatch.setattr(RedisConnection, "dispose", redis_dispose)
    configured = shutdown_settings(settings, cleanup=1.0)
    agent = Agent(
        build_dependencies(configured, llm=ScriptedLLM(), clock=clock), checkpointer=InMemorySaver()
    )
    client_cm = api_client(configured, agent)
    client_cm.__enter__()
    started = time.perf_counter()
    client_cm.__exit__(None, None, None)
    elapsed = time.perf_counter() - started
    assert closed == ["redis"]
    assert elapsed < 1.0 + 4 * MIN_STEP_SECONDS + 2  # budget + per-step floors, not an hour
    statuses = {e["step"]: e["status"] for e in events_in(capture_logs, "shutdown.step")}
    assert statuses["database"] == "timeout" and statuses["erp"] == "timeout"
    assert statuses["redis"] == "ok" and statuses["observability"] == "ok"
    [completed] = events_in(capture_logs, "shutdown.completed")
    assert completed["status"] == "degraded"


def test_agent_aclose_also_closes_the_erp_pool(clock: ClockService) -> None:
    port = GatedPort("pending")
    deps = build_dependencies(
        AppSettings(env="test", llm=LLMSettings(anthropic_api_key="k")),
        llm=ScriptedLLM(),
        clock=clock,
    )
    deps = type(deps)(**{**deps.__dict__, "business": service(port)})
    assert [name for name, _ in deps.closers()] == ["erp"]  # nothing else configured
    asyncio.run(Agent(deps).aclose())
    assert port.closed == 1


def test_a_turn_cancelled_by_shutdown_is_never_a_success(
    settings: AppSettings, clock: ClockService
) -> None:
    """uvicorn cancels requests still running at the graceful deadline. The turn must end
    as unfinished: the cancellation propagates, no `turn.completed`, the thread lock is
    released, and the conversation shows a pending turn instead of an answer."""
    llm = BlockingLLM(script=[greeting(), "Hi!"])
    agent = Agent(build_dependencies(settings, llm=llm, clock=clock), checkpointer=InMemorySaver())
    asha = principal()

    async def body() -> tuple[Any, list[str]]:
        with capture_events() as events:
            task = asyncio.create_task(agent.arun("Hello", principal=asha, thread_id="t-cut"))
            await asyncio.wait_for(llm.entered.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        status = await agent.athread_status("t-cut", principal=asha)
        with pytest.raises(ThreadPendingError):  # not THREAD_BUSY: the lock was released
            await agent.arun("Hello again", principal=asha, thread_id="t-cut")
        return status, events.names()

    status, names = asyncio.run(body())
    assert status.pending is True
    assert EventName.TURN_COMPLETED.value not in names
    assert llm.calls == [] and len(llm.script) == 2  # cut while planning: no reply written


# =============================================================================================
# Observability export boundary
# =============================================================================================


class FailingExporter:
    def __init__(self) -> None:
        self.seen = 0

    def handle(self, event: Any) -> None:
        self.seen += 1
        raise ConnectionError("collector down")

    def flush(self, timeout_seconds: float) -> None:
        raise ConnectionError("collector down")


def test_an_exporter_outage_never_fails_a_turn_or_a_flush(make_agent: Any) -> None:
    exporter = FailingExporter()
    HUB.add_exporter(exporter)
    try:
        agent, _ = make_agent(greeting(), "Hi!")
        response = agent.run("Hello")
        HUB.flush(1.0)  # swallowed
    finally:
        HUB.remove_exporter(exporter)
    assert response.status == "completed"
    assert exporter.seen > 5  # it did receive the turn's events


def test_a_hanging_exporter_flush_cannot_block_shutdown() -> None:
    class SlowExporter:
        def handle(self, event: Any) -> None:
            pass

        def flush(self, timeout_seconds: float) -> None:
            time.sleep(1.5)  # ignores its timeout

    exporter = SlowExporter()
    HUB.add_exporter(exporter)
    try:
        started = time.perf_counter()
        report = asyncio.run(
            run_shutdown(
                [ShutdownStep("observability", lambda: in_daemon_thread(lambda: HUB.flush(0.1)))],
                budget_seconds=0.2,
            )
        )
        elapsed = time.perf_counter() - started  # asyncio.run did not join the thread
    finally:
        HUB.remove_exporter(exporter)
    assert report.steps[0].status == "timeout"
    assert elapsed < 1.2


# =============================================================================================
# Migration discipline
# =============================================================================================


def test_migration_head_is_unchanged_and_nothing_migrates_at_startup() -> None:
    assert head_revision() == "0005"
    versions = sorted(p.name for p in (SRC / "db" / "migrations" / "versions").glob("*.py"))
    assert len(versions) == 5 and versions[-1].startswith("20261002_0005")
    runtime_code = [p for p in SRC.rglob("*.py") if "migrations" not in p.parts]
    for path in runtime_code:
        text = path.read_text(encoding="utf-8")
        assert "command.upgrade" not in text and "alembic.command" not in text, path
        assert "create_all(" not in text, path


# =============================================================================================
# Deployment artifacts (static)
# =============================================================================================


def test_image_is_pinned_locked_and_without_installers() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ARG PYTHON_IMAGE=python:3\.11-slim-bookworm@sha256:[0-9a-f]{64}", dockerfile)
    assert "--constraint constraints.txt" in dockerfile
    assert "pip uninstall --yes pip setuptools wheel" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile and "USER 10001:10001" in dockerfile
    assert "ARG APP_BUILD_COMMIT=unknown" in dockerfile


def test_constraints_pin_every_runtime_dependency_exactly() -> None:
    pinned = {}
    for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            assert version, line
            pinned[name.lower().replace("_", "-")] = version
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = pyproject.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    names = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)', runtime, re.MULTILINE)
    assert len(names) >= 10
    for name in [*names, "pyjwt", "cryptography"]:
        assert name.lower().replace("_", "-") in pinned, name
    for dev_tool in ("pytest", "ruff", "mypy"):
        assert dev_tool not in pinned


def test_app_containers_are_hardened_in_every_compose_file() -> None:
    for path in (ROOT / "docker-compose.yml", ROOT / "deploy" / "smoke" / "compose.smoke.yml"):
        text = path.read_text(encoding="utf-8")
        assert "read_only: true" in text, path
        assert 'cap_drop: ["ALL"]' in text, path
        assert "no-new-privileges:true" in text, path
        assert "init: true" in text, path


def test_smoke_stack_is_isolated_from_other_databases() -> None:
    text = (ROOT / "deploy" / "smoke" / "compose.smoke.yml").read_text(encoding="utf-8")
    assert "internal: true" in text
    assert "container_name" not in text  # never clashes with agentic-postgres/-redis
    assert "inventoryai" not in text
    # The only ERP setting is a placeholder on a reserved host that never resolves
    # (RFC 2606 `.invalid`); without a contract the adapter never connects.
    erp_urls = re.findall(r"ERP_DATABASE_URLS?:\s*(\S+)", text)
    assert erp_urls == ["postgresql://reader:x@erp.invalid:5432/erp"]
    assert "5432:" not in text and "6379:" not in text  # PostgreSQL/Redis not published
    assert '"127.0.0.1:${SMOKE_API_PORT:-18000}:8000"' in text


def test_backup_scripts_refuse_foreign_databases_and_live_targets() -> None:
    backup_dir = ROOT / "deploy" / "backup"
    backup = (backup_dir / "agent_db_backup.sh").read_text(encoding="utf-8")
    restore = (backup_dir / "agent_db_restore.sh").read_text(encoding="utf-8")
    verify = (backup_dir / "agent_db_verify.sh").read_text(encoding="utf-8")
    assert "alembic_version" in backup and "exit 3" in backup  # not the Agent DB: refused
    assert "never overwrite a backup" in backup
    assert "already holds" in restore and "exit 4" in restore  # non-empty target: refused
    for script in (backup, restore, verify):
        assert "\r" not in script  # LF only (.gitattributes)
        assert "set -eu" in script
        for forbidden in ("--clean", "DROP DATABASE", "dropdb", "TRUNCATE"):
            assert forbidden not in script
        assert "PGPASSWORD=" not in script  # credentials come from the environment
