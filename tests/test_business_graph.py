"""Phase 10: the business domain inside the real LangGraph agent (scripted LLM, real ERP
read path on the InvenLogic TEST replica, tests/erp_db.py; the fake ERP was removed on
2026-10-30).

The figures are rows of a TEST company on the replica, read by the real adapter and
readers; nothing here talks to the real ERP or a real model. What is proven is the
wiring and the guarantees: routing,
authorization, tool execution, deterministic results, and that the reply cannot carry a
figure that the ERP result does not contain (not from the model, not from memory).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.core.config import AgentSettings, AppSettings, ErpSettings, LLMSettings
from agentic_platform.security import Principal
from agentic_platform.services.clock import ClockService
from tests.business_helpers import ROLES, TZ, ErpFaults, business_principal, erp_port, ext
from tests.conftest import FIXED_NOW, understanding
from tests.fakes import ScriptedLLM

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


def settings(erp: ErpSettings | None = None) -> AppSettings:
    from agentic_platform.core.config import AuthSettings

    return AppSettings(
        env="test",
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=2),
        auth=AuthSettings(role_catalog_json=json.dumps(ROLES)),
        erp=erp or ErpSettings(adapter="postgres", business_timezone=TZ),
    )


@pytest.fixture
def make_business_agent() -> MakeAgent:
    def factory(*script: object, erp: ErpSettings | None = None) -> tuple[Agent, ScriptedLLM]:
        llm = ScriptedLLM(script=list(script))
        # The default ERP is the real adapter on the TEST replica (never built from the
        # settings here: pooled connections do not survive the per-run event loops). A
        # test that passes its own `erp` settings gets what the settings build.
        port: ErpReadPort | None = None
        if erp is None:
            ext()  # the extended TEST company exists before the first read
            port = erp_port()
        deps = build_dependencies(
            settings(erp), llm=llm, clock=ClockService(now_utc=lambda: FIXED_NOW), erp_port=port
        )
        return Agent(deps), llm

    return factory


def plan(intent: str, tool: str, args: dict[str, Any]) -> object:
    return understanding(intent=intent, domain="business", tool_calls=[(tool, json.dumps(args))])


def adapter_of(agent: Agent) -> ErpFaults:
    business = agent.dependencies.business
    assert business is not None
    port = business._port  # test access to the fault-injecting wrapper
    assert isinstance(port, ErpFaults)
    return port


def user(roles: tuple[str, ...] | None = None) -> Principal:
    """A user of the extended TEST company (the old tests' tenant "acme")."""
    if roles is None:
        return business_principal(ext().tenant)
    return business_principal(ext().tenant, roles=roles)


REFERENCE_QUERIES = [
    ("Aaj ka total sales batao.", "daily_sales", "business.daily_sales", {"day": "today"},
     "Aaj ki total sales 1,25,000 INR hai (7 documents). Yeh TEST data hai.", "125000.00"),
    ("Rahul Gupta ka pending bill kitna hai?", "customer_open_invoices",
     "business.customer_open_invoices", {"customer_name": "Rahul Gupta"},
     "Rahul Gupta ke 2 open bills hain, total 45,000 INR (TEST data).", "45000.00"),
    ("Rahul Gupta ka outstanding kitna hai?", "customer_outstanding",
     "business.customer_outstanding", {"customer_name": "Rahul Gupta"},
     "Rahul Gupta ka outstanding 45,000.00 INR hai (TEST data).", "45000.00"),
    ("iPhone 15 ka stock kitna hai?", "product_stock", "business.product_stock",
     {"product_name": "iPhone 15"}, "iPhone 15 ka stock 15 pcs hai: Godown 1 mein 12, "
     "Godown 2 mein 3 (TEST data).", "15"),
    ("Godown 2 mein stock kitna hai?", "warehouse_stock", "business.warehouse_stock",
     {"warehouse_name": "Godown 2"}, "Godown 2 mein iPhone 15: 3 pcs, Galaxy S24: 0 pcs "
     "(TEST data).", "3"),
    ("Low-stock products kaunse hain?", "low_stock_products", "business.low_stock_products",
     {}, "Low stock: iPhone 15 (3, level 5) aur Galaxy S24 (0, level 2), Godown 2 mein "
     "(TEST data).", "5"),
]  # fmt: skip


@pytest.mark.parametrize(("question", "intent", "tool", "args", "reply", "figure"),
                         REFERENCE_QUERIES, ids=[q[1] for q in REFERENCE_QUERIES])  # fmt: skip
def test_reference_queries_against_the_erp(
    make_business_agent: MakeAgent, question: str, intent: str, tool: str,
    args: dict[str, Any], reply: str, figure: str,
) -> None:  # fmt: skip
    agent, llm = make_business_agent(plan(intent, tool, args), reply)
    response = agent.run(question, principal=user())

    assert response.status == "completed"
    assert (response.intent, response.domain) == (intent, "business")
    [result] = response.tool_results
    assert result.tool_name == tool and result.status == "ok" and result.output is not None
    business = result.output["result"]
    assert business["status"] == "SUCCESS" and business["source"]["test_data"] is False
    assert figure in json.dumps(business["data"])
    assert response.message == reply  # every figure in it is sourced: kept as written
    assert response.steps == 6  # understand, supervise, process, generate, verify, finalize
    # the planner saw the business tools; the writer saw the ERP result
    assert tool in llm.calls[0].system
    assert figure in llm.calls[1].messages[-1].content


def test_memory_cannot_override_the_erp_figure(
    make_business_agent: MakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory says 50,000; the ERP says 45,000; a reply quoting memory is replaced."""
    agent, llm = make_business_agent(
        plan("customer_outstanding", "business.customer_outstanding",
             {"customer_name": "Rahul Gupta"}),
        "Rahul Gupta owes ₹50,000.",  # the model followed memory instead of the ERP
    )  # fmt: skip

    async def recall(*_: object) -> dict[str, Any]:
        return {"facts": [{"content": "Rahul owes ₹50,000"}]}

    monkeypatch.setattr(agent, "_recall", recall)
    response = agent.run("Rahul Gupta ka outstanding kitna hai?", principal=user())

    assert "Rahul owes ₹50,000" in llm.calls[1].messages[-1].content  # memory was present
    assert "50,000" not in response.message and "50000" not in response.message
    assert "45000" in response.message.replace(",", "")
    # The replacement is rendered from the ERP result, whose source is not TEST data.
    assert "TEST data" not in response.message
    assert response.status == "completed"


def test_an_invented_figure_is_replaced_even_without_memory(make_business_agent: MakeAgent) -> None:
    agent, _ = make_business_agent(
        plan("product_stock", "business.product_stock", {"product_name": "iPhone 15"}),
        "iPhone 15: 40 pcs in stock.",
    )
    response = agent.run("iPhone 15 ka stock kitna hai?", principal=user())
    assert "40" not in response.message
    assert "15" in response.message and "12" in response.message


def test_ambiguous_customer_asks_for_clarification(make_business_agent: MakeAgent) -> None:
    agent, _ = make_business_agent(
        plan("customer_outstanding", "business.customer_outstanding",
             {"customer_name": "Amit Sharma"}),
        "Kaunse Amit Sharma: Amit Sharma (TEST) ya Amit Sharma Traders (TEST)?",
    )  # fmt: skip
    response = agent.run("Amit Sharma ka outstanding?", principal=user())
    assert response.status == "needs_clarification"
    assert response.tool_results[0].output["result"]["status"] == "AMBIGUOUS"  # type: ignore[index]


def test_unavailable_erp_yields_no_figure(make_business_agent: MakeAgent) -> None:
    agent, _ = make_business_agent(
        plan("customer_outstanding", "business.customer_outstanding",
             {"customer_name": "Rahul Gupta"}),
        "Rahul Gupta ka outstanding shayad 45,000 hoga.",  # a guess
    )  # fmt: skip
    adapter_of(agent).failure = "unavailable"
    response = agent.run("Rahul Gupta ka outstanding?", principal=user())
    assert response.tool_results[0].output["result"]["status"] == "UNAVAILABLE"  # type: ignore[index]
    assert "45" not in response.message
    assert "could not be retrieved" in response.message


def test_purchase_order_request_is_unsupported_and_writes_nothing(
    make_business_agent: MakeAgent,
) -> None:
    agent, _ = make_business_agent(
        understanding(intent="create_purchase_order", domain="unsupported"),
        "Purchase orders banana abhi assistant mein available nahi hai.",
    )
    response = agent.run("Stock kam hai, purchase order bana do.", principal=user())
    assert response.status == "unsupported"
    assert response.tool_results == []
    assert adapter_of(agent).calls == []
    assert not any("purchase" in s.name for s in agent.dependencies.tools.specs())


def test_a_planned_write_tool_does_not_exist(make_business_agent: MakeAgent) -> None:
    agent, _ = make_business_agent(
        plan("create_purchase_order", "business.create_purchase_order", {"product": "x"}),
        "Not possible.",
    )
    response = agent.run("PO bana do", principal=user())
    assert response.tool_results[0].error_code == "TOOL_NOT_FOUND"
    assert adapter_of(agent).calls == []


def test_business_tool_cannot_be_reached_through_the_general_domain(
    make_business_agent: MakeAgent,
) -> None:
    agent, _ = make_business_agent(
        understanding(intent="x", domain="general",
                      tool_calls=[("business.daily_sales", '{"day": "today"}')]),
        "ok",
    )  # fmt: skip
    response = agent.run("sales?", principal=user())
    assert response.tool_results[0].error_code == "TOOL_NOT_ALLOWED"
    assert adapter_of(agent).calls == []


def test_planner_only_sees_tools_the_user_may_run(make_business_agent: MakeAgent) -> None:
    agent, llm = make_business_agent(understanding(intent="greeting"), "Hi")
    agent.run("hello", principal=user(roles=("member", "sales_viewer")))
    system = llm.calls[0].system
    assert "Tools of the `business` domain" in system
    assert "business.daily_sales" in system
    assert "business.product_stock" not in system  # no inventory permission


def test_without_an_erp_adapter_there_is_no_business_domain(make_business_agent: MakeAgent) -> None:
    agent, llm = make_business_agent(
        understanding(intent="customer_outstanding", domain="business"), "Not available yet.",
        erp=ErpSettings(adapter="none"),
    )  # fmt: skip
    response = agent.run("Rahul ka outstanding?", principal=user())
    assert response.status == "unsupported"
    assert agent.dependencies.business is None
    assert "business." not in llm.calls[0].system


def test_postgres_adapter_answers_rule_not_verified_without_connecting(
    make_business_agent: MakeAgent,
) -> None:
    # Port 1 on localhost: nothing listens; any connection attempt would fail differently.
    erp = ErpSettings(adapter="postgres", database_url="postgresql://ro:x@127.0.0.1:1/erp",
                      expected_database="erp", business_timezone=TZ)  # fmt: skip
    agent, _ = make_business_agent(
        plan("customer_outstanding", "business.customer_outstanding",
             {"customer_name": "Rahul Gupta"}),
        "Yeh information abhi available nahi hai.",
        erp=erp,
    )  # fmt: skip
    response = agent.run("Rahul ka outstanding?", principal=user())
    result = response.tool_results[0].output["result"]  # type: ignore[index]
    assert (result["status"], result["reason"]) == ("UNAVAILABLE", "RULE_NOT_VERIFIED")
    assert response.message == "Yeh information abhi available nahi hai."
