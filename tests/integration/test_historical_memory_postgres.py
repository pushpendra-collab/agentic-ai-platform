"""Phase 16: business-analysis references persist in PostgreSQL across sessions (ADR-065).

Real PostgreSQL 16 + pgvector (the throw-away `agentic_test` database, TEST_DATABASE_*),
scripted LLM, and the real ERP read path on the InvenLogic TEST replica (tests/erp_db.py,
TEST_ERP_*; the fake ERP was removed on 2026-10-30): a fresh TEST company with the sales
days the comparison reads. Two separate `Agent` instances with their own dependencies
stand for two sessions; only the Agent DB connects them.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from typing import Any, cast

import pytest
from alembic import command

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.core.config import (
    AgentSettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    ErpSettings,
    LLMSettings,
)
from agentic_platform.db import Database
from agentic_platform.db.migrations import alembic_config
from agentic_platform.memory import MemoryScope
from agentic_platform.memory.adapters.postgres import PostgresMemoryStore
from agentic_platform.services.clock import ClockService
from tests.business_helpers import ROLES, TZ, business_principal, erp_port, need_erp
from tests.conftest import FIXED_NOW, understanding
from tests.erp_db import ErpCompany
from tests.fakes import ScriptedLLM
from tests.memory_contract import make_manager

pytestmark = pytest.mark.postgres

COMPARE = "same_period_last_year"


@pytest.fixture
def app_url() -> str:
    owner = os.environ.get("TEST_DATABASE_URL")
    url = os.environ.get("TEST_DATABASE_APP_URL")
    if not owner or not url:
        pytest.skip("TEST_DATABASE_URL / TEST_DATABASE_APP_URL not set")
    command.upgrade(alembic_config(owner), "head")
    return url


def _company() -> ErpCompany:
    """A fresh TEST company: September 2026 sells ₹1,40,000 (the fake ERP's sales days),
    September 2025 ₹1,12,000 on one day."""
    need_erp()
    company = ErpCompany()
    for day, total, count in (
        (date(2026, 9, 30), "80000.00", 5), (date(2026, 9, 28), "40000.00", 2),
        (date(2026, 9, 2), "20000.00", 1), (date(2025, 9, 5), "112000.00", 7),
    ):  # fmt: skip
        company.sales_day(day, total, count)
    return company


def _agent(app_url: str, *script: object) -> Agent:
    settings = AppSettings(
        env="test",
        database=DatabaseSettings(url=app_url),
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=3),
        auth=AuthSettings(role_catalog_json=json.dumps(ROLES)),
        erp=ErpSettings(adapter="postgres", business_timezone=TZ),
    )
    clock = ClockService(now_utc=lambda: FIXED_NOW)
    deps = build_dependencies(
        settings,
        llm=ScriptedLLM(script=list(script)),
        clock=clock,
        erp_port=cast(ErpReadPort, erp_port()),
    )
    assert deps.business is not None
    return Agent(deps)


def test_comparison_reference_survives_a_new_session_and_stays_in_its_tenant(
    app_url: str,
) -> None:
    tenant = _company().tenant
    asha = business_principal(tenant_id=tenant)
    summary = "Compare last month's sales with the same period last year"
    plan = understanding(intent="sales_comparison", domain="business").model_copy(
        update={
            "summary": summary,
            "business_query": BusinessQueryIntent(
                capability="daily_sales", period="last_month", comparison=COMPARE
            ),
        }
    )

    first = _agent(app_url, plan, "₹1,40,000 vs ₹1,12,000: 25.0% zyada (TEST data).")
    assert first.dependencies.memory is not None
    assert first.dependencies.memory.store_name == "postgres"
    response = first.run("Pichhle mahine ki sales pichhle saal se compare karo",
                         thread_id=f"{tenant}-s1", principal=asha)  # fmt: skip
    assert response.status == "completed"
    asyncio.run(first.aclose())

    async def recall(scope: MemoryScope) -> Any:
        db = Database(DatabaseSettings(url=app_url))  # a new connection pool: a new session
        try:
            manager = make_manager(PostgresMemoryStore(db), recall_min_score=0.0)
            episodes = await manager.episodic.recent(scope)
            context = await manager.recall(scope, thread_id=f"{tenant}-s2", query=summary)
            return episodes, context
        finally:
            await db.dispose()

    episodes, context = asyncio.run(recall(asha.memory_scope))
    (episode,) = episodes
    (analysis,) = episode.metadata["analyses"]
    assert analysis["capability"] == "daily_sales" and analysis["status"] == "SUCCESS"
    assert analysis["period"] == {"start": "2026-09-01", "last_day": "2026-09-30"}
    assert analysis["compared_with"] == {"start": "2025-09-01", "last_day": "2025-09-30"}
    assert analysis["source"]["adapter"] == "postgres" and analysis["source"]["test_data"] is False
    stored = json.dumps(episode.metadata) + episode.content
    assert "140000" not in stored and "112000" not in stored  # figures are never stored

    view = context.to_prompt(10_000)
    assert view is not None
    (recalled,) = [e for e in view["past_interactions"] if e.get("analyses")]
    assert recalled["basis"] == "interaction_summary"

    other_episodes, other_context = asyncio.run(recall(MemoryScope(f"{tenant}-other", "asha")))
    assert other_episodes == [] and other_context.episodes == []  # RLS: another tenant
