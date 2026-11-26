"""Phase 15 against real PostgreSQL 16 (+ Redis 7 when TEST_REDIS_URL is set), as the
least-privilege app role (ADR-064):

* a turn cut by the shutdown deadline leaves a FAILED idempotency claim, an unfinished
  conversation and a released thread lock: a retry with the same key is never answered
  as a replayed success;
* no pooled connection stays checked out after API turns, and none survives the shutdown
  (counted on the server, by application name);
* after the shutdown sequence closed them, the pools reconnect by themselves;
* readiness on the real stack: database at the migration head, Redis, ERP not configured.

Same setup as test_api_postgres.py (TEST_DATABASE_URL / TEST_DATABASE_APP_URL).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from agentic_platform.agents import Agent
from agentic_platform.agents.checkpointer import PostgresCheckpointSaver
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.api import create_app, runtime_of
from agentic_platform.core.config import (
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    LLMSettings,
    RedisSettings,
)
from agentic_platform.core.errors import ThreadPendingError
from agentic_platform.db.migrations import alembic_config
from agentic_platform.services.clock import ClockService
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token
from tests.conftest import FIXED_NOW, principal
from tests.fakes import ScriptedLLM
from tests.integration.test_api_postgres import owner_sql, uid
from tests.test_operational_readiness import BlockingLLM, greeting

pytestmark = pytest.mark.postgres


@pytest.fixture
def owner_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def settings_pg(owner_url: str) -> AppSettings:
    app_url = os.environ.get("TEST_DATABASE_APP_URL")
    if not app_url:
        pytest.skip("TEST_DATABASE_APP_URL not set")
    redis_url = os.environ.get("TEST_REDIS_URL")
    return AppSettings(
        env="test",
        llm=LLMSettings(anthropic_api_key="test-key"),
        database=DatabaseSettings(url=app_url),
        redis=RedisSettings(url=redis_url, key_prefix=f"agentic-test-{uuid.uuid4().hex[:12]}")
        if redis_url
        else RedisSettings(),
        auth=AuthSettings(token_secret=TOKEN_SECRET),
    )


def agent_with(settings: AppSettings, llm: ScriptedLLM) -> Agent:
    deps = build_dependencies(settings, llm=llm, clock=ClockService(now_utc=lambda: FIXED_NOW))
    assert deps.database is not None
    return Agent(deps, checkpointer=PostgresCheckpointSaver(deps.database))


def test_a_turn_cut_by_shutdown_is_recorded_as_failed_never_replayed(
    settings_pg: AppSettings, owner_url: str
) -> None:
    llm = BlockingLLM(script=[greeting(), "Hi!"])
    agent = agent_with(settings_pg, llm)
    tenant, thread, key = uid("p15"), uid("t-cut"), uid("key")
    asha = principal(tenant_id=tenant)

    async def claim_of(key: str) -> tuple[Any, ...]:
        database = agent.dependencies.database
        assert database is not None
        async with database.unit_of_work(read_only=True) as uow:
            row = (await uow.session.execute(text(
                "SELECT status, error_code, response FROM idempotency_record "
                "WHERE idempotency_key LIKE :key"), {"key": f"%{key}"})).one()  # fmt: skip
        return tuple(row)

    async def body() -> None:
        try:
            turn = asyncio.create_task(
                agent.arun("Hello", principal=asha, thread_id=thread, idempotency_key=key)
            )
            await asyncio.wait_for(llm.entered.wait(), 20)
            turn.cancel()  # what uvicorn does at API_GRACEFUL_SHUTDOWN_SECONDS
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert await claim_of(key) == ("failed", "INTERNAL_ERROR", None)  # not completed
            status = await agent.athread_status(thread, principal=asha)
            assert status.pending is True
            # Same key again: the failed claim is not replayed as a success, and the lock
            # was released (THREAD_HAS_PENDING_TURN, not THREAD_BUSY).
            with pytest.raises(ThreadPendingError):
                await agent.arun("Hello", principal=asha, thread_id=thread, idempotency_key=key)
        finally:
            await agent.aclose()

    asyncio.run(body())
    # The refused retry re-claimed the failed record and failed it again: still no stored
    # response, so no client can ever receive a replayed "success" for the cut turn.
    rows = owner_sql(
        owner_url,
        "SELECT status, error_code, response FROM idempotency_record "
        "WHERE idempotency_key LIKE :key",
        key=f"%{key}",
    )
    assert [(r[0], r[1], r[2]) for r in rows] == [("failed", "THREAD_HAS_PENDING_TURN", None)]


def test_no_connection_leaks_through_turns_or_shutdown(
    settings_pg: AppSettings, owner_url: str
) -> None:
    name = uid("p15-pool")
    settings = settings_pg.model_copy(
        update={
            "database": DatabaseSettings(
                url=settings_pg.database.runtime_url(), application_name=name
            )
        }
    )
    llm = ScriptedLLM(script=[item for _ in range(3) for item in (greeting(), "Hi!")])
    agent = agent_with(settings, llm)
    tenant = uid("p15")
    count_sql = "SELECT count(*) FROM pg_stat_activity WHERE application_name = :name"
    with api_client(settings, agent) as client:
        headers = bearer(token(agent, tenant, "asha"))
        for _ in range(3):
            response = client.post("/api/v1/agent/turn", json={"message": "Hello"},
                                   headers=headers)  # fmt: skip
            assert response.status_code == 200, response.text
        database = agent.dependencies.database
        assert database is not None
        assert database.pool_status().get("checkedout") == 0  # every session returned
        assert owner_sql(owner_url, count_sql, name=name)[0][0] >= 1  # pooled, idle
    # The lifespan shutdown closed the pool: nothing of this process is left on the server.
    assert owner_sql(owner_url, count_sql, name=name)[0][0] == 0


def test_pools_reconnect_after_the_shutdown_sequence_closed_them(
    settings_pg: AppSettings,
) -> None:
    llm = ScriptedLLM(script=[greeting(), "Hi!", greeting(), "Hi again!"])
    agent = agent_with(settings_pg, llm)
    app = create_app(settings_pg, agent=agent)
    asha = principal(tenant_id=uid("p15"))

    async def body() -> list[str]:
        statuses = [(await agent.arun("Hello", principal=asha)).status]
        report = await runtime_of(app).aclose()  # Redis, Agent DB (no ERP configured)
        expected = ["in_flight_requests", "redis", "database", "observability"]
        if not settings_pg.redis.configured:
            expected.remove("redis")
        assert [s.step for s in report.steps] == expected
        assert report.status == "ok"
        statuses.append((await agent.arun("Hello", principal=asha)).status)
        await agent.aclose()
        return statuses

    assert asyncio.run(body()) == ["completed", "completed"]


def test_readiness_on_the_real_stack(settings_pg: AppSettings) -> None:
    agent = agent_with(settings_pg, ScriptedLLM())
    with api_client(settings_pg, agent) as client:
        ready = client.get("/ready")
    assert ready.status_code == 200, ready.text
    checks = ready.json()["checks"]
    assert checks["database"]["status"] == "ok"  # reachable, schema at head 0005
    expected_redis = "ok" if settings_pg.redis.configured else "not_configured"
    assert checks["redis"]["status"] == expected_redis
    assert checks["erp"] == {"status": "not_configured", "ready": True, "latency_ms": None,
                             "error_code": None}  # fmt: skip
    assert "agentic_app" not in ready.text and "localhost" not in ready.text
