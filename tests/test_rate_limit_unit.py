"""Phase 8: per-caller rate limits (ADR-053) and trusted-proxy settings (ADR-054). No servers.

Rate limits are abuse/cost protection only: they are keyed by the verified principal,
apply after authentication, never replace authorization, and cannot be steered by
client headers or bodies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.api import runtime_of
from agentic_platform.api.__main__ import uvicorn_options
from agentic_platform.cache import InMemoryBackend
from agentic_platform.core.config import (
    ApiSettings,
    AppSettings,
    AuthSettings,
    RateLimitSettings,
)
from agentic_platform.core.errors import CacheUnavailableError, RateLimitedError
from agentic_platform.services.clock import ClockService
from agentic_platform.services.rate_limit import LimitKind, RateLimiter
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, error_of, token
from tests.conftest import principal, understanding
from tests.fakes import ScriptedLLM

LIMITS = RateLimitSettings(
    enabled=True,
    window_seconds=60,
    turns_per_window=2,
    approval_decisions_per_window=5,
    writes_per_window=5,
    reads_per_window=3,
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class DownBackend(InMemoryBackend):
    async def increment(self, key: str, *, ttl_ms: int) -> tuple[int, int]:
        raise CacheUnavailableError("ConnectionError")


# --- the limiter ----------------------------------------------------------------------------


def test_budget_per_kind_then_refused_with_retry_after() -> None:
    limiter = RateLimiter(InMemoryBackend(), LIMITS)
    asha = principal()

    async def scenario() -> None:
        await limiter.check(asha, LimitKind.TURN)
        await limiter.check(asha, LimitKind.TURN)
        with pytest.raises(RateLimitedError) as refused:
            await limiter.check(asha, LimitKind.TURN)
        assert refused.value.code == "RATE_LIMITED" and refused.value.retryable
        assert 1 <= refused.value.retry_after_seconds <= 60
        # Other kinds have their own budget.
        await limiter.check(asha, LimitKind.READ)

    run(scenario())


def test_budget_is_per_tenant_and_user() -> None:
    limiter = RateLimiter(InMemoryBackend(), LIMITS)

    async def scenario() -> None:
        for _ in range(2):
            await limiter.check(principal("acme", "asha"), LimitKind.TURN)
        with pytest.raises(RateLimitedError):
            await limiter.check(principal("acme", "asha"), LimitKind.TURN)
        # Same user id in another tenant, and another user in the same tenant: unaffected.
        await limiter.check(principal("globex", "asha"), LimitKind.TURN)
        await limiter.check(principal("acme", "ravi"), LimitKind.TURN)

    run(scenario())


def test_window_expiry_restores_the_budget() -> None:
    now = [1000.0]
    backend = InMemoryBackend(clock=lambda: now[0])
    limiter = RateLimiter(backend, LIMITS)

    async def scenario() -> None:
        for _ in range(2):
            await limiter.check(principal(), LimitKind.TURN)
        with pytest.raises(RateLimitedError) as refused:
            await limiter.check(principal(), LimitKind.TURN)
        assert refused.value.retry_after_seconds == 60
        now[0] += 61
        await limiter.check(principal(), LimitKind.TURN)

    run(scenario())


def test_zero_switches_a_class_off() -> None:
    limiter = RateLimiter(InMemoryBackend(), LIMITS.model_copy(update={"reads_per_window": 0}))

    async def scenario() -> None:
        for _ in range(50):
            await limiter.check(principal(), LimitKind.READ)

    run(scenario())


def test_keys_hold_no_identifiers() -> None:
    key = RateLimiter.key_for(principal("acme", "asha"), LimitKind.TURN)
    assert key.startswith("ratelimit:turn:") and "acme" not in key and "asha" not in key
    assert key != RateLimiter.key_for(principal("acme", "ravi"), LimitKind.TURN)


def test_backend_outage_fails_open_by_default_and_closed_on_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    open_limiter = RateLimiter(DownBackend(), LIMITS)
    run(open_limiter.check(principal(), LimitKind.TURN))
    assert "ratelimit.unavailable" in caplog.text

    closed = RateLimiter(DownBackend(), LIMITS.model_copy(update={"fail_open": False}))
    with pytest.raises(CacheUnavailableError):
        run(closed.check(principal(), LimitKind.TURN))


def test_enabled_by_default_in_production_only() -> None:
    assert AppSettings(env="production").rate_limit_enabled
    assert not AppSettings(env="development").rate_limit_enabled
    assert not AppSettings(env="test").rate_limit_enabled
    assert AppSettings(env="test", rate_limit=RateLimitSettings(enabled=True)).rate_limit_enabled
    off = AppSettings(env="production", rate_limit=RateLimitSettings(enabled=False))
    assert not off.rate_limit_enabled


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_TURNS_PER_WINDOW", "7")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30")
    monkeypatch.setenv("RATE_LIMIT_FAIL_OPEN", "false")
    limits = RateLimitSettings()
    assert (limits.enabled, limits.turns_per_window, limits.window_seconds) == (True, 7, 30)
    assert limits.fail_open is False
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "")
    assert RateLimitSettings().enabled is None


# --- over HTTP ------------------------------------------------------------------------------


class Harness:
    def __init__(self, settings: AppSettings, clock: ClockService, turns: int) -> None:
        script: list[object] = []
        for _ in range(turns):
            script += [understanding(intent="greeting"), "Hello."]
        self.settings = settings
        self.llm = ScriptedLLM(script=script)
        deps = build_dependencies(settings, llm=self.llm, clock=clock)
        self.agent = Agent(deps, checkpointer=InMemorySaver())

    def headers(self, tenant: str = "acme", user: str = "asha", *roles: str) -> dict[str, str]:
        return bearer(token(self.agent, tenant, user, roles or ("member",)))


@pytest.fixture
def limited(settings: AppSettings, clock: ClockService) -> Iterator[Any]:
    def make(turns: int = 0, limits: RateLimitSettings = LIMITS) -> Harness:
        configured = settings.model_copy(
            update={"auth": AuthSettings(token_secret=TOKEN_SECRET), "rate_limit": limits}
        )
        return Harness(configured, clock, turns)

    yield make


def test_turns_over_budget_answer_429_without_calling_the_agent(limited: Any) -> None:
    h = limited(turns=2)
    with api_client(h.settings, h.agent) as client:
        for _ in range(2):
            ok = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers())
            assert ok.status_code == 200, ok.text
        refused = client.post("/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers())
        assert refused.status_code == 429
        error = error_of(refused)
        assert error["code"] == "RATE_LIMITED" and error["retryable"] is True
        assert 1 <= int(refused.headers["retry-after"]) <= 60
        # The refused turn never reached the agent (no LLM call was consumed or made).
        assert h.llm.script == [] and len(h.llm.calls) == 4
        # Continuing and starting conversations share the turn budget.
        start = client.post("/api/v1/conversations", json={"message": "hi"}, headers=h.headers())
        assert start.status_code == 429
        # Another user of the same tenant has their own budget (the LLM script is empty,
        # so the turn fails, but not with 429).
        other = client.post(
            "/api/v1/agent/turn", json={"message": "hi"}, headers=h.headers("acme", "ravi")
        )
        assert other.status_code != 429


def test_reads_are_limited_separately_from_turns(limited: Any) -> None:
    h = limited()
    with api_client(h.settings, h.agent) as client:
        for _ in range(3):
            assert client.get("/api/v1/me", headers=h.headers()).status_code == 200
        refused = client.get("/api/v1/conversations", headers=h.headers())
        assert refused.status_code == 429
        assert error_of(refused)["code"] == "RATE_LIMITED"


def test_authentication_comes_first_and_is_not_counted(limited: Any) -> None:
    h = limited()
    with api_client(h.settings, h.agent) as client:
        for _ in range(10):
            missing = client.get("/api/v1/me")
            assert missing.status_code == 401
            forged = client.get("/api/v1/me", headers=bearer("not-a-token"))
            assert forged.status_code == 401
        # Refused requests used nothing from the caller's budget.
        for _ in range(3):
            assert client.get("/api/v1/me", headers=h.headers()).status_code == 200


def test_client_headers_cannot_choose_or_reset_the_bucket(limited: Any) -> None:
    h = limited()
    spoofed = [
        {
            "X-Forwarded-For": f"10.0.0.{i}",
            "X-Real-IP": f"10.0.1.{i}",
            "X-Tenant-ID": f"t{i}",
            "X-User-ID": f"u{i}",
            "X-Request-ID": f"r{i}",
        }
        for i in range(4)
    ]
    with api_client(h.settings, h.agent) as client:
        statuses = [
            client.get("/api/v1/me", headers={**h.headers(), **extra}).status_code
            for extra in spoofed
        ]
        assert statuses == [200, 200, 200, 429]
        # Query parameters cannot name a tenant or user either.
        refused = client.get("/api/v1/me?tenant_id=other&user_id=x", headers=h.headers())
        assert refused.status_code == 429


def test_rate_limit_never_grants_permissions(limited: Any) -> None:
    h = limited()
    with api_client(h.settings, h.agent) as client:
        # A member under budget is still refused the audit trail (needs audit.read).
        denied = client.get("/api/v1/audit", headers=h.headers())
        assert denied.status_code == 403
        assert error_of(denied)["code"] == "PERMISSION_DENIED"


def test_probes_are_not_rate_limited(limited: Any) -> None:
    h = limited()
    with api_client(h.settings, h.agent) as client:
        for _ in range(20):
            assert client.get("/health").status_code == 200


def test_runtime_has_no_limiter_when_disabled(settings: AppSettings, clock: ClockService) -> None:
    configured = settings.model_copy(update={"auth": AuthSettings(token_secret=TOKEN_SECRET)})
    h = Harness(configured, clock, turns=0)
    with api_client(h.settings, h.agent) as client:
        assert runtime_of(client.app).rate_limiter is None  # type: ignore[arg-type]
        for _ in range(20):
            assert client.get("/api/v1/me", headers=h.headers()).status_code == 200


# --- trusted proxies ------------------------------------------------------------------------


def test_uvicorn_trusts_forwarded_headers_from_configured_proxies_only() -> None:
    default = uvicorn_options(AppSettings())
    assert default["proxy_headers"] is True
    assert default["forwarded_allow_ips"] == "127.0.0.1"
    gateway = AppSettings(api=ApiSettings(forwarded_allow_ips="10.0.0.5,10.0.1.0/24"))
    assert uvicorn_options(gateway)["forwarded_allow_ips"] == "10.0.0.5,10.0.1.0/24"
    options = uvicorn_options(AppSettings(), host="0.0.0.0", port=9000)
    assert (options["host"], options["port"], options["server_header"]) == ("0.0.0.0", 9000, False)
