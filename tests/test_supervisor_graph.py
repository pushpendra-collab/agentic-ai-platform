"""Phase 12: the Supervisor inside the real LangGraph agent (MOCK VERIFIED).

Scripted LLM + the real ERP read path on the InvenLogic TEST replica (tests/erp_db.py; the
fake ERP was removed on 2026-10-30) + in-memory checkpoints. Proves orchestration only:
routing of the brief's general, business, unsupported and multi-domain examples, one
domain run per single-domain request, follow-ups and topic changes, the Supervisor never
changing a domain's answer, authorization and tenant isolation after routing, failure
handling (invalid route, LLM failure, domain timeout, domain crash, routing limits,
tampered state), checkpoint safety with an explicit type allow-list, and safe logs.
Nothing here talks to the production ERP or a real model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.domains.base import DomainHandler, DomainRequest
from agentic_platform.agents.nodes import make_process_request, make_supervise
from agentic_platform.agents.routing import RoutingHints
from agentic_platform.agents.schemas import DomainResult, RequestUnderstanding
from agentic_platform.agents.serialization import checkpoint_serializer
from agentic_platform.agents.state import STATE_VERSION, initial_turn_state
from agentic_platform.agents.supervisor import Supervisor
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.core.config import (
    AgentSettings,
    AppSettings,
    AuthSettings,
    ErpSettings,
    LLMSettings,
)
from agentic_platform.core.errors import LLMOutputError
from agentic_platform.security import Principal
from agentic_platform.security.context import acting_as
from agentic_platform.services.clock import ClockService
from tests.business_helpers import ROLES, TZ, ErpFaults, business_principal, erp_port, ext, globex
from tests.conftest import FIXED_NOW, understanding
from tests.fakes import ScriptedLLM

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


def who() -> Principal:
    """A user of the extended TEST company (the data the removed fake seeded for agent
    tests: adds Rahul Verma and Godown 3) with every business permission."""
    return business_principal(ext().tenant)


def mentions(text: str, ref: str) -> bool:
    """Whether the numeric ERP ref occurs in `text` as a whole token (refs are short ids,
    so a plain substring test would also hit durations and counts)."""
    return re.search(rf"(?<![\w.]){re.escape(ref)}(?![\w.])", text) is not None


def settings(*, domain_timeout: float = 120.0, erp: ErpSettings | None = None) -> AppSettings:
    return AppSettings(
        env="test",
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=3, domain_timeout_seconds=domain_timeout),
        auth=AuthSettings(role_catalog_json=json.dumps(ROLES)),
        erp=erp or ErpSettings(adapter="postgres", business_timezone=TZ),
    )


@pytest.fixture
def make_agent() -> MakeAgent:
    def factory(
        *script: object, checkpointer: Any = None, port: ErpFaults | None = None, **kw: Any
    ) -> tuple[Agent, ScriptedLLM]:
        """`port`: the ERP port (default: the real adapter on the TEST replica, unless the
        settings say ERP_ADAPTER=none). Never built from the settings: pooled connections
        do not survive the per-call event loops of `agent.run`."""
        llm = ScriptedLLM(script=list(script))
        app_settings = settings(**kw)
        if port is None and app_settings.erp.adapter != "none":
            port = erp_port()
        deps = build_dependencies(
            app_settings, llm=llm, clock=ClockService(now_utc=lambda: FIXED_NOW), erp_port=port
        )
        return Agent(deps, checkpointer=checkpointer or InMemorySaver()), llm

    return factory


def plan(
    intent: str,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    *,
    domain: str = "business",
    query: dict[str, Any] | None = None,
    unavailable: list[tuple[str, str]] | None = None,
) -> RequestUnderstanding:
    base = understanding(
        intent=intent,
        domain=domain,
        tool_calls=[(name, json.dumps(args)) for name, args in calls or []],
    )
    update: dict[str, Any] = {}
    if query is not None:
        update["business_query"] = BusinessQueryIntent.model_validate(query)
    if unavailable is not None:
        update["routing"] = RoutingHints.model_validate(
            {"unavailable": [{"domain": d, "action": a} for d, a in unavailable]}
        )
    return base.model_copy(update=update)


def adapter_of(agent: Agent) -> ErpFaults:
    business = agent.dependencies.business
    assert business is not None
    port = business._port
    assert isinstance(port, ErpFaults)
    return port


def final_state(agent: Agent, thread_id: str, tenant: str | None = None) -> dict[str, Any]:
    tenant = tenant or ext().tenant
    snapshot = asyncio.run(
        agent.graph.aget_state({"configurable": {"thread_id": f"{tenant}/{thread_id}"}})
    )
    return dict(snapshot.values)


def decision_of(agent: Agent, thread_id: str, tenant: str | None = None) -> dict[str, Any]:
    orchestration = final_state(agent, thread_id, tenant)["orchestration"]
    assert isinstance(orchestration, dict)
    return orchestration


class CountingHandler(DomainHandler):
    """Wraps a registered handler and counts its runs (single-domain execution checks)."""

    def __init__(self, inner: DomainHandler) -> None:
        self.inner, self.key, self.description = inner, inner.key, inner.description
        self.tool_domains = inner.tool_domains
        self.runs = 0

    async def handle(self, request: DomainRequest) -> DomainResult:
        self.runs += 1
        return await self.inner.handle(request)


def count_runs(agent: Agent) -> dict[str, CountingHandler]:
    registry = agent.dependencies.domains
    wrapped = {key: CountingHandler(registry.get(key)) for key in registry.keys()}  # type: ignore[arg-type]  # noqa: SIM118
    registry._handlers.update(wrapped)
    return wrapped


# =============================================================================================
# topology: one routing decision, no way back to the Supervisor
# =============================================================================================


def test_topology_has_one_supervisor_and_no_loop(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    graph = agent.graph.get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert {s for s, t in edges if t == "supervise"} == {"understand_request"}
    assert {s for s, t in edges if t == "understand_request"} == {"__start__"}
    assert all(s != t for s, t in edges)  # no node routes to itself
    assert {s for s, t in edges if t == "__end__"} == {"finalize_turn", "handle_error"}
    # The Supervisor reaches a domain only through the router (process_request).
    assert {t for s, t in edges if s == "supervise"} == {
        "process_request",
        "generate_response",
        "finalize_turn",
        "handle_error",
    }


# =============================================================================================
# business routing (brief § 7): delegated, never answered by the Supervisor
# =============================================================================================


def business_examples() -> list[tuple[str, RequestUnderstanding, str, str]]:
    """Built at test time: the stock example uses the replica's refs (they differ per run)."""
    return [
        (
            "Rahul Gupta ka pending bill batao.",
            plan(
                "pending_bill",
                [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})],
            ),
            "Rahul Gupta ke 2 open invoices hain, total ₹45,000 (TEST data).",
            "open_invoices",
        ),
        (
            "Aaj ki sales kitni hai?",
            plan("daily_sales", query={"capability": "daily_sales", "period": "today"}),
            "Aaj ki sales ₹1,25,000 hai (TEST data).",
            "sales",
        ),
        (
            "Warehouse 2 mein product iPhone 15 ka stock kitna hai?",
            plan(
                "product_stock",
                [
                    (
                        "business.product_stock",
                        {"product_ref": ext()["iphone"], "warehouse_ref": ext()["godown2"]},
                    )
                ],
            ),
            "Godown 2 mein 3 pcs hain (TEST data).",
            "stock",
        ),
    ]


@pytest.mark.parametrize(
    "example", [0, 1, 2], ids=["pending_bill", "sales_today", "warehouse_stock"]
)
def test_business_examples_are_delegated_to_the_business_domain(
    make_agent: MakeAgent, example: int
) -> None:
    question, planned, reply, read = business_examples()[example]
    agent, llm = make_agent(planned, reply)
    runs = count_runs(agent)
    response = agent.run(question, principal=who(), thread_id="t-b")
    assert response.status == "completed" and response.domain == "business"
    assert response.message == reply  # sourced figures: the domain's answer is kept
    assert [r.status for r in response.tool_results] == ["ok"]
    assert any(read in call for call in adapter_of(agent).calls)
    assert (runs["business"].runs, runs["general"].runs) == (1, 0)
    orchestration = decision_of(agent, "t-b")
    assert orchestration["decision"]["outcome"] == "ROUTE"
    assert orchestration["decision"]["domain"] == "business"
    assert orchestration["domain_runs"] == 1 and orchestration["domain_outcome"] == "SUCCESS"
    assert final_state(agent, "t-b")["last_domain"] == "business"
    assert len(llm.calls) == 2  # planner + writer: the Supervisor makes no model call


def test_a_typed_capability_labelled_general_is_routed_to_business(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("sales", domain="general", query={"capability": "daily_sales", "period": "today"}),
        "Aaj ki sales ₹1,25,000 hai (TEST data).",
    )
    response = agent.run("Aaj ki sales kitni hai?", principal=who(), thread_id="t-r")
    assert response.domain == "business" and response.tool_results[0].status == "ok"
    assert decision_of(agent, "t-r")["decision"]["routing_metadata"]["rerouted_from"] == "general"


# =============================================================================================
# general routing (brief § 8): ordinary questions never touch business
# =============================================================================================


@pytest.mark.parametrize(
    "question",
    [
        "LangGraph kya hai?",
        "Python mein list aur tuple mein difference kya hai?",
        "Explain agentic AI.",
    ],
)
def test_general_questions_route_to_general(make_agent: MakeAgent, question: str) -> None:
    agent, _ = make_agent(understanding(intent="explain", domain="general"), "An answer.")
    runs = count_runs(agent)
    response = agent.run(question, principal=who(), thread_id="t-g")
    assert (response.status, response.domain, response.message) == (
        "completed",
        "general",
        "An answer.",
    )
    assert (runs["general"].runs, runs["business"].runs) == (1, 0)
    assert adapter_of(agent).calls == []
    assert response.steps == 5  # understand, supervise, process, generate, finalize
    assert decision_of(agent, "t-g")["decision"]["requested_action"] == "answer"


def test_a_general_route_cannot_run_business_tools(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "x",
            [("business.customer_outstanding", {"customer_name": "Rahul Gupta"})],
            domain="general",
        ),
        "ok",
    )
    response = agent.run("hi", principal=who())
    assert response.tool_results[0].error_code == "TOOL_NOT_ALLOWED"
    assert adapter_of(agent).calls == []  # no accidental second-domain execution


# =============================================================================================
# unsupported (brief § 9) and multi-domain requests (§ 10, § 20)
# =============================================================================================


def test_whatsapp_reminder_is_a_fixed_unsupported_reply(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        plan("send_reminder", domain="unsupported", unavailable=[("notification", "send")]),
        "Reminder bhej diya!",  # never requested: the writer is not called
    )
    runs = count_runs(agent)
    response = agent.run(
        "WhatsApp par Rahul ko payment reminder bhejo.", principal=who(), thread_id="t-w"
    )
    assert response.status == "unsupported"
    assert "WhatsApp" in response.message and "Nothing was carried out" in response.message
    assert "bhej diya" not in response.message
    assert len(llm.calls) == 1 and response.tool_results == []
    assert all(h.runs == 0 for h in runs.values()) and adapter_of(agent).calls == []
    assert response.steps == 3  # understand, supervise, finalize
    assert decision_of(agent, "t-w")["decision"]["reason"] == "not_available"
    assert "last_domain" not in final_state(agent, "t-w")  # nothing was routed


@pytest.mark.parametrize(
    "planned",
    [
        plan("create_po", domain="purchase"),
        plan("create_po", domain="unsupported", unavailable=[("purchase", "write")]),
        # Planned calls of an unsupported request are never run.
        plan("create_po", [("business.low_stock_products", {})], domain="purchase"),
    ],
)
def test_automatic_purchase_order_never_executes(
    make_agent: MakeAgent, planned: RequestUnderstanding
) -> None:
    agent, _ = make_agent(planned, "PO bana diya.")
    response = agent.run("Purchase order automatically bana do.", principal=who())
    assert response.status == "unsupported" and response.tool_results == []
    assert "purchase orders" in response.message and "bana diya" not in response.message
    assert adapter_of(agent).calls == []


def test_read_plus_purchase_order_runs_the_read_and_reports_the_rest(make_agent: MakeAgent) -> None:
    reply = "Low stock: iPhone 15 (3 pcs) aur Galaxy S24 (0 pcs). Yeh TEST data hai."
    agent, _ = make_agent(
        plan(
            "low_stock_and_po",
            [("business.low_stock_products", {})],
            query={"capability": "low_stock_products"},
            unavailable=[("purchase", "write")],
        ),
        reply,
    )
    response = agent.run(
        "Stock kam hai aur supplier ko purchase order bhi bana do.",
        principal=who(),
        thread_id="t-m",
    )
    assert response.status == "completed" and response.domain == "business"
    assert response.message.startswith(reply)  # the domain's answer, unchanged
    assert response.message.endswith(
        "not available in this assistant yet: purchase orders and other purchase actions."
    )
    assert adapter_of(agent).calls == ["low_stock"]  # the read ran; nothing else did
    decision = decision_of(agent, "t-m")["decision"]
    assert (decision["reason"], decision["requested_action"]) == ("partial_route", "mixed")


def test_a_false_claim_in_a_partial_business_reply_is_still_replaced(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "low_stock_and_po",
            [("business.low_stock_products", {})],
            unavailable=[("purchase", "write")],
        ),
        "Low stock 2 items hain, aur maine purchase order bana diya.",
    )
    message = agent.run("Stock kam hai, PO bhi bana do.", principal=who()).message
    assert "bana diya" not in message  # verify_business_response replaced the draft
    assert "Note: part of your request was not done" in message


def test_a_false_claim_in_a_partial_general_reply_is_replaced(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("explain_and_po", domain="general", unavailable=[("purchase", "write")]),
        "Agentic AI is ... Also, the purchase order has been created.",
    )
    response = agent.run("Explain agentic AI and create a PO.", principal=who())
    assert "has been created" not in response.message
    assert response.message.startswith("This isn't available in the assistant yet")


def test_inventory_report_to_supplier_asks_first(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        plan(
            "report_and_send",
            [("business.warehouse_stock", {"warehouse_name": "Godown 2"})],
            unavailable=[("notification", "send")],
        ),
        "never used",
    )
    response = agent.run("Inventory ka report bana ke supplier ko bhejo.", principal=who())
    assert response.status == "needs_clarification"
    assert response.message.endswith("without anything being delivered?")
    assert response.tool_results == [] and adapter_of(agent).calls == []
    assert len(llm.calls) == 1


# =============================================================================================
# the Supervisor never overrides domain truth (brief § 13) or lets memory decide (§ 14)
# =============================================================================================


def test_not_found_stays_not_found(make_agent: MakeAgent) -> None:
    reply = "Is naam ka koi customer nahi mila."
    agent, _ = make_agent(
        plan("outstanding", [("business.customer_outstanding", {"customer_name": "Zed Nobody"})]),
        reply,
    )
    response = agent.run("Zed Nobody ka pending bill?", principal=who(), thread_id="t-nf")
    assert response.tool_results[0].output["result"]["status"] == "NOT_FOUND"  # type: ignore[index]
    assert response.message == reply  # not softened into a guess, not replaced
    assert decision_of(agent, "t-nf")["domain_outcome"] == "SUCCESS"


def test_rule_not_verified_is_not_turned_into_a_figure(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "overdue",
            [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})],
            query={"capability": "customer_open_invoices", "requested_metric": "overdue_amount"},
        ),
        "Overdue ₹99,999 hai.",
        # The ERP has no confirmed mapping for the read (an adapter without a verified reader).
        port=erp_port(failure="rule_not_verified"),
    )
    response = agent.run("Rahul Gupta ka overdue?", principal=who(), thread_id="t-rv")
    result = response.tool_results[0].output["result"]  # type: ignore[index]
    assert (result["status"], result["reason"]) == ("UNAVAILABLE", "RULE_NOT_VERIFIED")
    assert "99,999" not in response.message and "99999" not in response.message
    assert decision_of(agent, "t-rv")["domain_outcome"] == "DEPENDENCY_ERROR"


def test_memory_cannot_override_the_business_figure_through_the_supervisor(
    make_agent: MakeAgent,
) -> None:
    agent, _ = make_agent(
        plan(
            "outstanding",
            [("business.customer_outstanding", {"customer_name": "Rahul Gupta"})],
            unavailable=[("notification", "write")],
        ),
        "Rahul Gupta ka outstanding ₹50,000 hai.",
    )
    response = agent.run("Rahul Gupta ka outstanding? Aur reminder entry bana do.", principal=who())
    assert "50,000" not in response.message and "45,000" in response.message
    assert "Note: part of your request was not done" in response.message


# =============================================================================================
# follow-ups and topic changes (brief § 19)
# =============================================================================================


def test_follow_up_stays_in_business_and_a_new_topic_switches(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        plan(
            "pending_bill", [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})]
        ),
        "Rahul Gupta ke 2 open invoices hain, total ₹45,000.",
        plan(
            "overdue",
            query={
                "capability": "customer_open_invoices",
                "follow_up": True,
                "customer_reference": {"ref": ext()["rahul"]},
                "requested_metric": "overdue_amount",
            },
        ),
        "Overdue ₹30,000 hai (TEST-INV-001).",
        understanding(intent="explain_langgraph", domain="general"),
        "LangGraph ek agent framework hai.",
    )
    first = agent.run("Rahul Gupta ka pending bill batao.", principal=who(), thread_id="t-f")
    second = agent.run("Uska overdue kitna hai?", principal=who(), thread_id="t-f")
    assert (first.domain, second.domain) == ("business", "business")
    assert "30,000" in second.message
    meta = decision_of(agent, "t-f")["decision"]["routing_metadata"]
    assert meta["follow_up"] and not meta["topic_changed"] and meta["previous_domain"] == "business"
    reads = len(adapter_of(agent).calls)

    third = agent.run("Waise LangGraph kya hota hai?", principal=who(), thread_id="t-f")
    assert third.domain == "general" and third.message == "LangGraph ek agent framework hai."
    assert len(adapter_of(agent).calls) == reads  # stale business context did not run a read
    meta = decision_of(agent, "t-f")["decision"]["routing_metadata"]
    assert meta["topic_changed"] and meta["previous_domain"] == "business"
    state = final_state(agent, "t-f")
    assert state["last_domain"] == "general"
    assert state["business_focus"]  # kept (references only) for a later "uska"
    # The planner saw the business focus, but the Supervisor routed by its classification.
    assert mentions(llm.calls[4].system, ext()["rahul"])


def test_per_turn_orchestration_is_reset(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(), "a", understanding(), "b")
    agent.run("one", principal=who(), thread_id="t-reset")
    agent.run("two", principal=who(), thread_id="t-reset")
    orchestration = decision_of(agent, "t-reset")
    assert orchestration["decisions"] == 1 and orchestration["domain_runs"] == 1
    assert orchestration["decision"]["routing_metadata"]["decision_number"] == 1


# =============================================================================================
# authorization and isolation still decided after routing (brief § 15, § 23)
# =============================================================================================


def test_routing_to_business_does_not_authorize(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("outstanding", [("business.customer_outstanding", {"customer_name": "Rahul Gupta"})]),
        "Aapko yeh dekhne ki permission nahi hai.",
    )
    nobody = business_principal(ext().tenant, roles=("member",))  # no business permission
    response = agent.run("Rahul Gupta ka outstanding?", principal=nobody, thread_id="t-auth")
    assert response.domain == "business"  # routed ...
    assert response.tool_results[0].error_code == "PERMISSION_DENIED"  # ... but not allowed
    assert adapter_of(agent).calls == []
    assert decision_of(agent, "t-auth")["domain_outcome"] == "UNAUTHORIZED"


def test_cross_tenant_request_stays_not_found(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "outstanding",
            [("business.customer_outstanding", {"customer_ref": globex()["globex"]})],
        ),
        "Customer nahi mila.",
    )
    # globex's only customer (with a 99,999 bill) exists in another InvenLogic company.
    response = agent.run(f"{globex()['globex']} ka outstanding?", principal=who(), thread_id="t-x")
    assert response.domain == "business"
    assert response.tool_results[0].output["result"]["status"] == "NOT_FOUND"  # type: ignore[index]
    assert "99,999" not in response.message and "99999" not in response.message


def test_out_of_scope_warehouse_stays_not_found(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("stock", [("business.warehouse_stock", {"warehouse_name": "Godown 2"})]),
        "Warehouse nahi mila.",
    )
    limited = business_principal(ext().tenant, roles=("member", "stock_viewer_limited"))
    response = agent.run("Godown 2 ka stock?", principal=limited)
    assert response.tool_results[0].output["result"]["status"] in ("NOT_FOUND", "UNAUTHORIZED")  # type: ignore[index]
    assert "3 pcs" not in response.message


def test_a_write_tool_cannot_be_reached_through_any_route(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("po", [("business.create_purchase_order", {"product_name": "x"})]), "No."
    )
    response = agent.run("PO bana do", principal=who())
    assert response.tool_results[0].error_code == "TOOL_NOT_FOUND"
    assert adapter_of(agent).calls == []


# =============================================================================================
# failures terminate deterministically (brief § 28)
# =============================================================================================


def test_llm_routing_failure_fails_the_turn(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(LLMOutputError("structured output did not validate"))
    response = agent.run("hello", principal=who())
    assert response.status == "failed" and response.errors[0].code == "LLM_OUTPUT_ERROR"


def test_invalid_domain_is_rejected_without_running_anything(make_agent: MakeAgent) -> None:
    agent, llm = make_agent(
        understanding(domain="admin_console", tool_calls=[("system.current_time", "{}")])
    )
    runs = count_runs(agent)
    response = agent.run("open admin", principal=who(), thread_id="t-inv")
    assert response.status == "unsupported" and response.tool_results == []
    assert response.message == "I can't handle this request here, so nothing was carried out."
    assert all(h.runs == 0 for h in runs.values()) and len(llm.calls) == 1
    assert decision_of(agent, "t-inv")["decision"]["reason"] == "invalid_domain"


def test_an_invalid_label_is_never_echoed_to_the_caller(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(domain="'; DROP TABLE invoices; --"))
    response = agent.run("x", principal=who())
    assert response.status == "unsupported" and response.domain == "invalid"
    assert "DROP" not in response.model_dump_json()


def test_business_without_erp_is_reported_not_connected(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(plan("outstanding"), "unused", erp=ErpSettings(adapter="none"))
    response = agent.run("Rahul ka outstanding?", principal=who())
    assert response.status == "unsupported" and "not connected" in response.message


class SlowHandler(DomainHandler):
    key, description = "slow", "test"

    async def handle(self, request: DomainRequest) -> DomainResult:
        await asyncio.sleep(5)
        raise AssertionError("not reached")


class CrashingHandler(DomainHandler):
    key, description = "crashing", "test"

    async def handle(self, request: DomainRequest) -> DomainResult:
        raise RuntimeError("secret internals")


def test_domain_timeout_is_a_retryable_failure(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(domain="slow"), domain_timeout=0.05)
    agent.dependencies.domains.register(SlowHandler())
    response = agent.run("slow please", principal=who())
    assert response.status == "failed"
    (error,) = response.errors
    assert (error.code, error.retryable) == ("DOMAIN_TIMEOUT", True)


def test_domain_crash_is_contained(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(understanding(domain="crashing"))
    agent.dependencies.domains.register(CrashingHandler())
    response = agent.run("crash", principal=who())
    assert response.status == "failed" and response.errors[0].code == "INTERNAL_ERROR"
    assert "secret" not in response.model_dump_json()


def node_state(**extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = dict(initial_turn_state(request_id="r", thread_id="t", user_input="x"))
    state.update(understanding=understanding(), **extra)
    return state


def test_a_second_routing_decision_in_one_turn_is_refused(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    supervise = make_supervise(agent.dependencies)
    update = asyncio.run(supervise(node_state(orchestration={"decisions": 1})))  # type: ignore[arg-type]
    assert update["errors"][0].code == "ORCHESTRATION_LIMIT_EXCEEDED"


def test_a_second_domain_run_in_one_turn_is_refused(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    decision = Supervisor(lambda: ["general"]).decide(understanding()).to_state()
    process = make_process_request(agent.dependencies)
    with acting_as(who()):
        update = asyncio.run(
            process(
                node_state(  # type: ignore[arg-type]
                    orchestration={"decision": decision, "decisions": 1, "domain_runs": 1}
                )
            )
        )
    assert update["errors"][0].code == "ORCHESTRATION_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "orchestration",
    [
        None,  # process_request without the Supervisor
        {"decision": {"outcome": "ROUTE", "domain": "purchase"}},  # tampered / incomplete
    ],
)
def test_domain_router_refuses_without_a_valid_decision(
    make_agent: MakeAgent, orchestration: Any
) -> None:
    agent, _ = make_agent()
    process = make_process_request(agent.dependencies)
    with acting_as(who()):
        update = asyncio.run(process(node_state(orchestration=orchestration)))  # type: ignore[arg-type]
    assert update["errors"][0].code == "ORCHESTRATION_FAILED"
    assert adapter_of(agent).calls == []


def test_a_routed_domain_that_is_not_registered_fails_closed(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    forged = Supervisor(lambda: ["general", "notification_x"]).decide(
        understanding(domain="notification_x")
    )
    process = make_process_request(agent.dependencies)
    with acting_as(who()):
        update = asyncio.run(
            process(
                node_state(  # type: ignore[arg-type]
                    orchestration={"decision": forged.to_state(), "decisions": 1, "domain_runs": 0}
                )
            )
        )
    assert update["errors"][0].code == "ORCHESTRATION_FAILED"


# =============================================================================================
# checkpoint safety (brief § 18): explicit allow-list, nothing blocked, plain-JSON orchestration
# =============================================================================================


def test_turns_checkpoint_with_the_strict_allow_list(make_agent: MakeAgent) -> None:
    events: list[dict[str, Any]] = []
    unregister = register_serde_event_listener(lambda e: events.append(dict(e)))
    try:
        agent, _ = make_agent(
            plan(
                "pending_bill",
                [("business.customer_open_invoices", {"customer_name": "Rahul"})],
                query={
                    "capability": "customer_open_invoices",
                    "period": "this_month",
                    "requested_metric": "open_invoice_total",
                    "filters": ["overdue"],
                },
            ),
            "Kaunse Rahul?",
            plan(
                "overdue",
                query={
                    "capability": "customer_open_invoices",
                    "follow_up": True,
                    "customer_reference": {"ref": ext()["rahul"]},
                },
                unavailable=[("purchase", "write")],
            ),
            "Overdue ₹30,000 hai.",
            understanding(domain="purchase"),
            LLMOutputError("bad"),
            plan("stock", [("business.low_stock_products", {})]),
            "2 items low hain (TEST data).",
            checkpointer=InMemorySaver(serde=checkpoint_serializer()),
        )
        statuses = [
            agent.run(text, principal=who(), thread_id="t-ser").status
            for text in ("Rahul ka bill?", "Rahul Gupta wala; PO bhi", "PO bana do", "x", "low?")
        ]
    finally:
        unregister()
    assert statuses == ["needs_clarification", "completed", "unsupported", "failed", "completed"]
    assert not [e for e in events if e.get("kind", "").startswith("msgpack")], events

    state = final_state(agent, "t-ser")
    assert state["state_version"] == STATE_VERSION == 5
    assert isinstance(state["understanding"], RequestUnderstanding)  # revived, not blocked
    assert json.loads(json.dumps(state["orchestration"])) == state["orchestration"]
    assert isinstance(state["last_domain"], str)


def test_an_unlisted_type_in_a_checkpoint_is_blocked() -> None:
    from pydantic import BaseModel

    class Smuggled(BaseModel):
        payload: str = "x"

    serde = checkpoint_serializer()
    events: list[dict[str, Any]] = []
    unregister = register_serde_event_listener(lambda e: events.append(dict(e)))
    try:
        revived = serde.loads_typed(serde.dumps_typed({"value": Smuggled()}))
        allowed = serde.loads_typed(serde.dumps_typed({"value": understanding()}))
    finally:
        unregister()
    assert not isinstance(revived["value"], Smuggled)
    assert any(e.get("kind") == "msgpack_blocked" and e.get("name") == "Smuggled" for e in events)
    assert isinstance(allowed["value"], RequestUnderstanding)


def test_the_default_checkpointers_use_the_allow_list() -> None:
    from agentic_platform.agents.checkpointer import PostgresCheckpointSaver, create_checkpointer
    from agentic_platform.core.config import DatabaseSettings
    from agentic_platform.db.engine import Database

    # The engine connects lazily: nothing is contacted here (port 1 on localhost).
    database = Database(DatabaseSettings(url="postgresql://u:p@127.0.0.1:1/x"))
    savers = [
        create_checkpointer("memory", None),
        create_checkpointer("postgres", database),
        PostgresCheckpointSaver(database),  # also when built directly (integration tests)
    ]
    for saver in savers:
        assert saver is not None and isinstance(saver.serde, JsonPlusSerializer)
        allowed = saver.serde._allowed_msgpack_modules
        assert isinstance(allowed, set)  # not True ("allow everything with a warning")
        assert ("agentic_platform.agents.schemas", "RequestUnderstanding") in allowed


# =============================================================================================
# observability (brief § 27): codes and counts, never names, figures or user text
# =============================================================================================


def test_supervisor_logs_are_safe(make_agent: MakeAgent, caplog: pytest.LogCaptureFixture) -> None:
    agent, _ = make_agent(
        plan(
            "low_stock_and_po",
            [("business.customer_outstanding", {"customer_name": "Rahul Gupta"})],
            unavailable=[("purchase", "write")],
        ),
        "Rahul Gupta ka outstanding ₹45,000 hai.",
    )
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"):
        agent.run("Rahul Gupta ka outstanding batao aur PO bana do", principal=who())
    # Phase 13: the observability logger also emits `supervisor.started/completed/decision`
    # events; this test is about the Supervisor's own log lines, so it skips that logger.
    # The observability lines get the same secret check below.
    supervisor_records = [
        r
        for r in caplog.records
        if r.getMessage().startswith("supervisor.") and r.name != "agentic_platform.observability"
    ]
    observability_text = " ".join(
        f"{r.getMessage()} {getattr(r, 'fields', '')}"
        for r in caplog.records
        if r.name == "agentic_platform.observability"
    )
    assert "supervisor.decision" in observability_text
    for secret in ("Rahul", "Gupta", "45,000", "45000", "outstanding batao", ext().tenant,
                   "asha"):  # fmt: skip
        assert secret not in observability_text, secret
    assert not mentions(observability_text, ext()["rahul"])
    assert {r.getMessage() for r in supervisor_records} == {
        "supervisor.decided",
        "supervisor.turn.completed",
    }
    text = " ".join(f"{r.getMessage()} {getattr(r, 'fields', '')}" for r in supervisor_records)
    for secret in ("Rahul", "Gupta", "45", "outstanding batao", ext().tenant, "asha"):
        assert secret not in text, secret
    assert not mentions(text, ext()["rahul"])
    completed = next(r for r in supervisor_records if r.getMessage() == "supervisor.turn.completed")
    fields = completed.fields  # type: ignore[attr-defined]
    assert fields["outcome"] == "ROUTE" and fields["notice_appended"] is True
    assert fields["domain_outcome"] == "SUCCESS"
