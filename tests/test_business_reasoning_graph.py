"""Phase 11: business query reasoning through the real LangGraph agent (MOCK VERIFIED).

Scripted LLM + the real ERP read path (InvenLogic readers on the TEST replica,
tests/erp_db.py; the extended TEST company). What is proven is orchestration:
tool selection over the six capabilities, intent-compiled calls, bounded multi-step
reads, follow-ups through the checkpointed conversation focus, memory never overriding
business truth, grounding of every figure form, plain NOT_FOUND, no fabricated success
after failures, read-only refusal, safe logs, and the API path. Nothing here talks to the
real company ERP or a real model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.schemas import RequestUnderstanding
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.core.config import (
    AgentSettings,
    AppSettings,
    AuthSettings,
    ErpSettings,
    LLMSettings,
)
from agentic_platform.security import Principal
from agentic_platform.services.clock import ClockService
from agentic_platform.tools.base import ToolKind
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token
from tests.business_helpers import (
    ROLES,
    TODAY,
    TZ,
    ErpFaults,
    business_principal,
    erp_port,
    ext,
    need_erp,
)
from tests.conftest import FIXED_NOW, understanding
from tests.erp_db import ErpCompany, StandardData, standard_company
from tests.fakes import ScriptedLLM

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


def settings(*, max_calls: int = 3, erp: ErpSettings | None = None, **extra: Any) -> AppSettings:
    return AppSettings(
        env="test",
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=max_calls),
        auth=AuthSettings(role_catalog_json=json.dumps(ROLES), **extra),
        erp=erp or ErpSettings(adapter="postgres", business_timezone=TZ),
    )


@pytest.fixture
def make_agent() -> MakeAgent:
    def factory(
        *script: object, port: ErpFaults | None = None, **kw: Any
    ) -> tuple[Agent, ScriptedLLM]:
        llm = ScriptedLLM(script=list(script))
        deps = build_dependencies(
            settings(**kw),
            llm=llm,
            clock=ClockService(now_utc=lambda: FIXED_NOW),
            erp_port=port or erp_port(),
        )
        return Agent(deps, checkpointer=InMemorySaver()), llm

    return factory


def adapter_of(agent: Agent) -> ErpFaults:
    business = agent.dependencies.business
    assert business is not None
    port = business._port
    assert isinstance(port, ErpFaults)
    return port


def plan(
    intent: str,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    query: dict[str, Any] | None = None,
    domain: str = "business",
) -> RequestUnderstanding:
    base = understanding(
        intent=intent,
        domain=domain,
        tool_calls=[(name, json.dumps(args)) for name, args in calls or []],
    )
    if query is None:
        return base
    return base.model_copy(update={"business_query": BusinessQueryIntent.model_validate(query)})


def business_of(response: Any, index: int = 0) -> dict[str, Any]:
    result: dict[str, Any] = response.tool_results[index].output["result"]
    return result


def who(data: StandardData | None = None) -> Principal:
    """A principal of the extended TEST company (or of `data`'s company)."""
    return business_principal((data or ext()).tenant)


def fresh_company() -> StandardData:
    """An independent copy of the extended TEST company, for tests that change ERP rows."""
    need_erp()
    return standard_company(TODAY, extended=True)


_SALES_45K: dict[str, ErpCompany] = {}


def sales_45k_company() -> ErpCompany:
    """A company whose only data is today's sales of 45,000 (3 counter invoices)."""
    if "c" not in _SALES_45K:
        need_erp()
        company = ErpCompany()
        company.sales_day(TODAY, "45000.00", 3)
        _SALES_45K["c"] = company
    return _SALES_45K["c"]


# =============================================================================================
# 15. tool selection across the six capabilities (and payment questions)
# =============================================================================================

SELECTION = [
    # (family, question, tool, args, reply quoting only sourced figures)
    (
        "customer",
        "Rahul Gupta ka outstanding kitna hai?",
        "business.customer_outstanding",
        {"customer_name": "Rahul Gupta"},
        "Rahul Gupta ka outstanding ₹45,000 hai.",
    ),
    (
        "invoice",
        "Kaunse invoices unpaid hain?",
        "business.customer_open_invoices",
        {},
        "4 unpaid invoices, total ₹65,000.",
    ),
    (
        "payment_due",
        "Rahul Gupta ka kitna payment baaki hai?",
        "business.customer_outstanding",
        {"customer_name": "Rahul Gupta"},
        "₹45,000 baaki hai.",
    ),
    (
        "sales",
        "Is mahine ki sales kitni hai?",
        "business.daily_sales",
        {"period": "this_month"},
        "Is mahine ki sales ₹1,25,000 hai.",
    ),
    (
        "product",
        "Warehouse Godown 2 mein iPhone 15 ka stock kitna hai?",
        "business.product_stock",
        {"product_name": "iPhone 15", "warehouse_name": "Godown 2"},
        "Godown 2 mein iPhone 15: 3 pcs.",
    ),
    (
        "inventory",
        "Godown 3 mein kya stock hai?",
        "business.warehouse_stock",
        {"warehouse_name": "Godown 3"},
        "Godown 3: Galaxy S24 6, USB-C Cable 40.",
    ),
    (
        "low_stock",
        "iPhone 15 ka stock low hai kya?",
        "business.low_stock_products",
        {"product_name": "iPhone 15"},
        "Haan, iPhone 15 low hai: 3 pcs, level 5.",
    ),
]


@pytest.mark.parametrize(
    ("family", "question", "tool", "args", "reply"), SELECTION, ids=[s[0] for s in SELECTION]
)
def test_planner_selects_among_the_typed_capabilities(
    make_agent: MakeAgent, family: str, question: str, tool: str, args: dict[str, Any], reply: str
) -> None:
    agent, llm = make_agent(plan(family, [(tool, args)]), reply)
    response = agent.run(question, principal=who())
    assert response.status == "completed" and response.domain == "business"
    assert business_of(response)["status"] == "SUCCESS"
    assert response.message == reply  # every figure is sourced: kept as written
    # the planner saw every business tool the user may run; the writer saw the composition
    for name in (
        "customer_outstanding",
        "customer_open_invoices",
        "daily_sales",
        "product_stock",
        "warehouse_stock",
        "low_stock_products",
    ):
        assert f"business.{name}" in llm.calls[0].system
    writer = llm.calls[1].messages[-1].content
    assert '"business"' in writer and '"figures"' in writer


def test_payment_history_has_no_capability_and_is_unsupported(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "payment_history",
            domain="unsupported",
            query={"entity": "customer", "explanation": "no payment capability"},
        ),
        "Payment history abhi assistant mein available nahi hai.",
    )
    response = agent.run("Rahul ne pichle mahine kitna payment kiya?", principal=who())
    assert response.status == "unsupported" and response.tool_results == []
    assert adapter_of(agent).calls == []


def test_a_typed_intent_without_tool_calls_is_compiled_into_one_call(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "sales_this_week",
            query={
                "capability": "daily_sales",
                "period": "this_week",
                "requested_metric": "total_sales",
            },
        ),
        "Is hafte ki sales ₹2,45,000 hai.",
    )
    response = agent.run("Is hafte ki sales?", principal=who())
    [result] = response.tool_results
    assert result.tool_name == "business.daily_sales"
    data = business_of(response)["data"]
    assert data["total"]["amount"] == "245000.00"
    assert (data["date_range"]["start"], data["date_range"]["last_day"]) == (
        "2026-09-28",
        "2026-10-01",
    )
    assert response.message.startswith("Is hafte ki sales ₹2,45,000")


def test_a_tool_name_without_the_domain_prefix_still_runs(make_agent: MakeAgent) -> None:
    # Local models often drop the prefix ("daily_sales" for "business.daily_sales").
    agent, _ = make_agent(
        plan("sales_this_week", [("daily_sales", {"period": "this_week"})]),
        "Is hafte ki sales ₹2,45,000 hai.",
    )
    response = agent.run("Is hafte ki sales?", principal=who())
    [result] = response.tool_results
    assert (result.tool_name, result.status) == ("business.daily_sales", "ok")
    assert business_of(response)["data"]["total"]["amount"] == "245000.00"


def test_an_invalid_plan_gives_way_to_the_typed_intent(make_agent: MakeAgent) -> None:
    # The model invents an argument (`filters`); the typed intent is right and compiles.
    agent, _ = make_agent(
        plan(
            "customer_overdue_invoices",
            [("business.customer_open_invoices", {"filters": ["overdue"]})],
            query={
                "capability": "customer_open_invoices",
                "operation": "list",
                "entity": "invoice",
                "filters": ["overdue"],
            },
        ),
        "Overdue bills ki list neeche hai.",
    )
    response = agent.run("Sabhi customers ke overdue bills dikhao", principal=who())
    [result] = response.tool_results
    assert (result.tool_name, result.status) == ("business.customer_open_invoices", "ok")
    assert business_of(response)["status"] == "SUCCESS"


def test_a_date_the_model_computed_gives_way_to_the_users_period_word(
    make_agent: MakeAgent,
) -> None:
    # Today is 2026-10-01: "kal" is 2026-09-30 (₹80,000), not the model's 2026-09-28.
    agent, _ = make_agent(
        plan("sales", [("daily_sales", {"day": "2026-09-28"})]),
        "Kal ki sales ₹80,000 thi.",
    )
    response = agent.run("Kal ki sales batao", principal=who())
    data = business_of(response)["data"]
    assert (data["business_date"], data["total"]["amount"]) == ("2026-09-30", "80000.00")


def test_a_date_the_user_typed_is_kept(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("sales", [("business.daily_sales", {"day": "2026-09-28"})]),
        "28 September ki sales ₹40,000 thi.",
    )
    response = agent.run("28 September ki sales batao", principal=who())
    assert business_of(response)["data"]["total"]["amount"] == "40000.00"


def test_a_name_the_model_shortened_is_narrowed_to_the_users_words(make_agent: MakeAgent) -> None:
    # The model sends "Godown" (matches Godown 1, 2, 3); the user wrote "Godown 1".
    agent, _ = make_agent(
        plan("stock", [("business.warehouse_stock", {"warehouse_name": "Godown"})]),
        "Godown 1 ka stock neeche hai.",
    )
    response = agent.run("Godown 1 mein kya stock hai?", principal=who())
    [result] = response.tool_results
    out = result.output["result"]  # type: ignore[index]
    assert out["status"] == "SUCCESS"
    assert out["data"]["warehouse"]["display_name"] == "Godown 1 (TEST)"


def test_a_full_name_that_is_really_ambiguous_stays_ambiguous(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("due", [("business.customer_outstanding", {"customer_name": "Amit Sharma"})]),
        "unused",
    )
    response = agent.run("Amit Sharma ka outstanding kitna hai?", principal=who())
    [result] = response.tool_results
    assert result.output["result"]["status"] == "AMBIGUOUS"  # type: ignore[index]


def test_a_clarification_is_dropped_when_the_typed_read_names_everything(
    make_agent: MakeAgent,
) -> None:
    # The model asks "which warehouse?" although the user named it; the ERP decides.
    asked = plan(
        "warehouse_stock",
        query={
            "capability": "warehouse_stock",
            "operation": "lookup",
            "entity": "warehouse",
            "warehouse_reference": {"name": "Godown 1"},
        },
    ).model_copy(update={"needs_clarification": True, "clarification_question": "Which warehouse?"})
    agent, _ = make_agent(asked, "Godown 1 ka stock neeche hai.")
    response = agent.run("Godown 1 mein kya stock hai?", principal=who())
    [result] = response.tool_results
    assert (result.tool_name, result.status) == ("business.warehouse_stock", "ok")
    assert business_of(response)["status"] == "SUCCESS"
    assert response.status == "completed"


def test_a_clarification_stays_when_the_typed_read_lacks_a_name(make_agent: MakeAgent) -> None:
    asked = plan(
        "warehouse_stock",
        query={"capability": "warehouse_stock", "operation": "lookup", "entity": "warehouse"},
    ).model_copy(update={"needs_clarification": True, "clarification_question": "Which warehouse?"})
    agent, _ = make_agent(asked, "unused")
    response = agent.run("Godown mein kya stock hai?", principal=who())
    assert (response.status, response.message) == ("needs_clarification", "Which warehouse?")
    assert response.tool_results == []


def test_a_user_count_question_is_not_answered_as_a_purchase(make_agent: MakeAgent) -> None:
    # Live (2026-11-25): the planner filed "kitne user hain?" under purchase:read.
    from agentic_platform.agents.routing import RoutingHints
    from agentic_platform.agents.supervisor import READ_NOT_AVAILABLE_MESSAGE

    hints = RoutingHints.model_validate(
        {"unavailable": [{"domain": "purchase", "action": "read"}], "confidence": "low"}
    )
    planned = plan("ask_user_count", domain="unsupported").model_copy(update={"routing": hints})
    agent, _ = make_agent(planned, "unused")
    response = agent.run("Mere database me kitne user hain?", principal=who())
    assert response.status == "unsupported" and response.tool_results == []
    assert response.message == READ_NOT_AVAILABLE_MESSAGE
    assert "purchase" not in response.message
    assert adapter_of(agent).calls == []


@pytest.mark.parametrize(
    "question",
    ["Aaj kitne naye users register hue?", "Is mahine kitne new customers aaye?"],
)
def test_new_registrations_are_not_answered_with_a_customer_count(
    make_agent: MakeAgent, question: str
) -> None:
    # Live (2026-11-25): "aaj 3 naye users register hue" from customers billed today.
    from agentic_platform.business.composition import NOT_RECORDED_MESSAGES

    agent, _ = make_agent(
        plan("new_users", [("business.customer_count", {"period": "today"})]),
        "Aaj 3 naye users register hue.",
    )
    response = agent.run(question, principal=who())
    assert response.status == "unsupported" and response.tool_results == []
    assert response.message == NOT_RECORDED_MESSAGES["new_registrations"]
    assert adapter_of(agent).calls == []


def test_a_new_customers_part_does_not_drop_the_rest_of_the_question(
    make_agent: MakeAgent,
) -> None:
    agent, _ = make_agent(
        plan(
            "sales_and_new",
            [("business.daily_sales", {"period": "today"}), ("business.customer_count", {})],
        ),
        "Aaj ki sales ₹1,25,000 hai.",
    )
    response = agent.run("Aaj ki sales kitni hui aur kitne naye customer aaye?", principal=who())
    assert [r.tool_name for r in response.tool_results] == ["business.daily_sales"]
    assert adapter_of(agent).calls == ["daily_sales"]


def test_a_question_about_what_the_user_meant_is_still_asked(make_agent: MakeAgent) -> None:
    # A customer count compiles without any name, so it must not clear the question.
    asked = plan("customers", query={"capability": "customer_count"}).model_copy(
        update={"needs_clarification": True, "clarification_question": "Total ya aaj ke?"}
    )
    agent, _ = make_agent(asked, "unused")
    response = agent.run("Customers kitne?", principal=who())
    assert (response.status, response.message) == ("needs_clarification", "Total ya aaj ke?")
    assert adapter_of(agent).calls == []


@pytest.mark.parametrize(
    "question", ["Kitne registered customers hain?", "Aaj tak kitne customers hain?"]
)
def test_total_customer_questions_get_the_total(make_agent: MakeAgent, question: str) -> None:
    agent, _ = make_agent(plan("customer_count", [("customer_count", {})]), "unused")
    response = agent.run(question, principal=who())
    data = business_of(response)["data"]
    assert "active_count" in data and "date_range" not in data
    assert adapter_of(agent).calls == ["customer_count"]


def test_a_null_period_still_gets_the_users_period_word(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(plan("customers_today", [("customer_count", {"period": None})]), "x")
    response = agent.run("Aaj kitne customer aaye?", principal=who())
    assert business_of(response)["data"]["date_range"]["start"] == "2026-10-01"


def test_a_date_in_a_no_data_reply_does_not_count_as_the_answer(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan("sales", [("business.daily_sales", {"period": "this_month"})]),
        "October 2026 ki sales ki koi jaankari nahi hai.",
    )
    response = agent.run("Is mahine ki sales kitni hai?", principal=who())
    assert response.message.startswith("Total sales on 2026-10-01: ₹1,25,000.")


def test_todays_customers_get_the_users_period_when_the_model_leaves_it_out(
    make_agent: MakeAgent,
) -> None:
    agent, _ = make_agent(plan("customers_today", [("customer_count", {})]), "unused")
    response = agent.run("Aaj kitne customer aaye?", principal=who())
    [result] = response.tool_results
    assert (result.tool_name, result.status) == ("business.customer_count", "ok")
    data = business_of(response)["data"]
    assert data["date_range"]["start"] == data["date_range"]["last_day"] == "2026-10-01"
    assert adapter_of(agent).calls == ["customers_billed"]


def test_how_many_customers_compiles_from_the_typed_intent(make_agent: MakeAgent) -> None:
    # The model's own count (999) is not the ERP's: the deterministic answer replaces it.
    agent, _ = make_agent(
        plan("customer_count", query={"capability": "customer_count", "operation": "count"}),
        "Aapke 999 customers hain.",
    )
    response = agent.run("Mere paas kitne customers hain?", principal=who())
    data = business_of(response)["data"]
    assert data["active_count"] > 0 and "date_range" not in data
    assert response.status == "completed"
    assert response.message.startswith(f"Active customers: {data['active_count']}.")


def test_a_mislabelled_list_answer_is_replaced_by_the_deterministic_text(
    make_agent: MakeAgent,
) -> None:
    # Live (2026-11-25): "Number of Overdue Invoices: 99" for 99 open bills, fewer overdue.
    agent, _ = make_agent(
        plan(
            "customer_overdue_invoices",
            [("business.customer_open_invoices", {})],
            query={
                "capability": "customer_open_invoices",
                "operation": "list",
                "entity": "invoice",
                "filters": ["overdue"],
            },
        ),
        "Overdue invoices: 3.",
    )
    response = agent.run("Sabhi customers ke overdue bills dikhao", principal=who())
    data = business_of(response)["data"]
    assert data["overdue_count"] is not None
    assert response.message.startswith("Total pending (open invoices):")
    assert f"Open invoices: {data['invoice_count']}." in response.message
    assert f"Overdue invoices: {data['overdue_count']}." in response.message


def test_a_reply_that_denies_data_the_erp_returned_is_replaced(make_agent: MakeAgent) -> None:
    # Live (2026-11-26): the total was read, the model wrote "no information" about it.
    agent, _ = make_agent(
        plan("sales", [("business.daily_sales", {"period": "this_month"})]),
        "मशीन की बर्बादी के बारे में आपके पास कोई जानकारी नहीं है।",
    )
    response = agent.run("Is mahine ki sales kitni hai?", principal=who())
    assert business_of(response)["status"] == "SUCCESS"
    # TODAY is 1 October, so this month to date is one day.
    assert response.message.startswith("Total sales on 2026-10-01: ₹1,25,000.")
    assert "कोई जानकारी" not in response.message


@pytest.mark.parametrize(
    ("question", "reply", "kept"),
    [
        # Live (2026-11-26): the right figure, but "is mahine" transliterated as मशीन.
        ("Is mahine ki sales kitni hai?", "मशीन की बिक्री ₹1,25,000 है।", False),
        ("Is mahine ki sales kitni hai?", "Is mahine ki total sales ₹1,25,000 hai.", True),
        ("इस महीने की बिक्री कितनी है?", "इस महीने की कुल बिक्री ₹1,25,000 है।", True),
    ],
)
def test_a_reply_in_another_script_than_the_question_is_replaced(
    make_agent: MakeAgent, question: str, reply: str, kept: bool
) -> None:
    agent, _ = make_agent(
        plan("sales", [("business.daily_sales", {"period": "this_month"})]), reply
    )
    response = agent.run(question, principal=who())
    if kept:
        assert response.message == reply
    else:
        assert response.message.startswith("Total sales on 2026-10-01: ₹1,25,000.")
        assert "मशीन" not in response.message


def test_the_reply_prompt_asks_for_the_users_script() -> None:
    from agentic_platform.prompts.registry import PromptRegistry

    text = PromptRegistry().render("generate_response.system")
    assert "Roman letters" in text and "never switch to Devanagari" in text


def test_an_invalid_plan_without_a_typed_intent_fails_as_before(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(plan("sales", [("sales_total", {})]), "unused")
    response = agent.run("Sales?", principal=who())
    [result] = response.tool_results
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_FOUND")
    assert adapter_of(agent).calls == []


# =============================================================================================
# 16. multi-step read workflows (typed, deterministic, bounded)
# =============================================================================================


def test_pending_invoices_total_is_resolved_retrieved_filtered_and_summed_in_code(
    make_agent: MakeAgent,
) -> None:
    agent, llm = make_agent(
        plan(
            "pending_total",
            [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})],
            query={
                "capability": "customer_open_invoices",
                "operation": "total",
                "requested_metric": "open_invoice_total",
            },
        ),
        "Rahul Gupta ke pending invoices ka total ₹45,000 hai.",
    )
    response = agent.run("Rahul Gupta ke pending invoices ka total batao.", principal=who())
    assert response.status == "completed"
    assert adapter_of(agent).calls == ["find_customers", "open_invoices"]  # resolve, retrieve
    writer = json.loads(
        llm.calls[1].messages[-1].content.split("<agent_context>")[1].split("</agent_context>")[0]
    )
    view = writer["business"]["results"][0]
    assert view["figures"][0] == {
        "metric": "open_invoice_total",
        "label": "Total pending (open invoices)",
        "kind": "money",
        "value": "45000.00",
        "currency": "INR",
    }
    assert view["steps"] == ["resolve_customer", "read_open_invoices", "aggregate_in_service"]


def test_overdue_part_needs_the_rule_otherwise_rule_not_verified(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "overdue",
            [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})],
            query={"capability": "customer_open_invoices", "requested_metric": "overdue_amount"},
        ),
        "Overdue ₹30,000 hai.",  # the model guesses although the rule is not verified
        port=erp_port(rules=frozenset()),
    )
    response = agent.run("Rahul Gupta ka kitna overdue hai?", principal=who())
    # The draft's "30,000" is a sourced number (an invoice balance) presented as an overdue
    # figure: numeric grounding alone cannot tell, so the deterministic answer replaces it.
    assert response.message != "Overdue ₹30,000 hai."
    assert "Overdue amount is not available" in response.message
    assert "not verified yet" in response.message
    assert "cannot be determined yet" in response.message


def test_two_reads_in_one_turn_and_the_step_limit(make_agent: MakeAgent) -> None:
    calls = [
        ("business.customer_outstanding", {"customer_name": "Rahul Gupta"}),
        ("business.customer_open_invoices", {"customer_name": "Rahul Gupta"}),
        ("business.daily_sales", {"period": "today"}),
        ("business.low_stock_products", {}),
    ]
    agent, _ = make_agent(
        plan("two_reads", calls), "Outstanding ₹45,000; 2 open invoices.", max_calls=2
    )
    response = agent.run("Rahul Gupta ka outstanding aur invoices?", principal=who())
    assert [r.status for r in response.tool_results] == ["ok", "ok", "error", "error"]
    assert response.tool_results[2].error_message.startswith("Skipped: tool-call limit")
    assert response.steps == 6  # + supervise, finalize_turn (Phase 12): no loop
    assert adapter_of(agent).calls.count("open_invoices") == 1


# =============================================================================================
# 14. follow-ups through the checkpointed focus; business truth re-read every turn
# =============================================================================================


def test_follow_up_uses_the_focus_reference_and_reads_fresh_data(make_agent: MakeAgent) -> None:
    rahul = ext()["rahul"]
    agent, llm = make_agent(
        plan(
            "pending_bill", [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})]
        ),
        "Rahul Gupta ka ₹45,000 pending hai.",
        plan(
            "overdue_part",
            [("business.customer_open_invoices", {"customer_ref": rahul})],
            query={
                "capability": "customer_open_invoices",
                "follow_up": True,
                "customer_reference": {"ref": rahul},
                "requested_metric": "overdue_amount",
            },
        ),
        "Usme se ₹30,000 overdue hai.",
    )
    first = agent.run("Rahul Gupta ka pending bill batao.", principal=who(), thread_id="t-follow")
    assert first.message.startswith("Rahul Gupta ka ₹45,000")
    second = agent.run("Usme se kitna overdue hai?", principal=who(), thread_id="t-follow")

    planner_turn2 = llm.calls[2].system
    assert "Conversation focus" in planner_turn2 and rahul in planner_turn2
    focus_block = planner_turn2.split("Conversation focus")[1]
    for figure in ("45000", "45,000", "30000", "15000"):
        assert figure not in focus_block  # references only, never figures
    assert adapter_of(agent).calls.count("open_invoices") == 2  # fresh truth each turn
    assert second.message == "Usme se ₹30,000 overdue hai."
    assert business_of(second)["data"]["overdue_balance"]["amount"] == "30000.00"


def test_follow_up_answer_follows_the_erp_when_the_data_changed(make_agent: MakeAgent) -> None:
    data = fresh_company()  # its rows change below: not the shared data set
    agent, _ = make_agent(
        plan("pending", [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})]),
        "₹45,000 pending hai.",
        plan("again", [("business.customer_open_invoices", {"customer_ref": data["rahul"]})]),
        "Abhi bhi ₹45,000 pending hai.",  # the model repeats the earlier answer
    )
    agent.run("Rahul Gupta ka pending?", principal=who(data), thread_id="t-change")
    # A further 10,000 received against TEST-INV-002: its balance drops 15,000 -> 5,000.
    data.company.pay(data["rahul"], data["inv2"], "10000.00", TODAY)
    second = agent.run("Ab kitna pending hai?", principal=who(data), thread_id="t-change")
    assert "45,000" not in second.message
    assert "₹35,000" in second.message  # 30000 + 5000, summed by the service


def test_clarification_then_choice_by_candidate_reference(make_agent: MakeAgent) -> None:
    verma = ext()["verma"]
    agent, llm = make_agent(
        plan(
            "bill",
            [("business.customer_open_invoices", {"customer_name": "Rahul"})],
            query={"capability": "customer_open_invoices", "customer_reference": {"name": "Rahul"}},
        ),
        "Do Rahul hain: Rahul Gupta (TEST) aur Rahul Verma (TEST). Kaunsa?",
        plan("bill", [("business.customer_open_invoices", {"customer_ref": verma})]),
        "Rahul Verma ka ₹12,000 pending hai.",
    )
    first = agent.run("Rahul ka bill batao.", principal=who(), thread_id="t-amb")
    assert first.status == "needs_clarification"
    assert business_of(first)["status"] == "AMBIGUOUS"
    second = agent.run("Verma wala", principal=who(), thread_id="t-amb")
    assert verma in llm.calls[2].system  # the candidate refs are in the focus
    assert second.status == "completed" and second.message.startswith("Rahul Verma ka ₹12,000")


# =============================================================================================
# 9/10. ambiguity and not found in the reply
# =============================================================================================


def test_ambiguous_reply_with_a_figure_is_replaced_by_a_safe_question(
    make_agent: MakeAgent,
) -> None:
    agent, _ = make_agent(
        plan(
            "bill",
            [("business.customer_outstanding", {"customer_name": "Rahul"})],
            query={"customer_reference": {"name": "Rahul"}},
        ),
        "Rahul ka ₹45,000 hai.",  # picked one candidate and quoted a figure
    )
    response = agent.run("Rahul ka bill batao.", principal=who())
    assert response.status == "needs_clarification"
    assert "45" not in response.message
    assert response.message.startswith("Several customers match 'Rahul'")


@pytest.mark.parametrize(
    "hedge",
    [
        "Maybe Nobody does not exist.",
        "I think there is probably no such customer.",
        "Shayad yeh customer system mein nahi hai.",
    ],
)
def test_not_found_is_never_softened_into_a_guess(make_agent: MakeAgent, hedge: str) -> None:
    agent, _ = make_agent(
        plan(
            "outstanding",
            [("business.customer_outstanding", {"customer_name": "Nobody"})],
            query={"customer_reference": {"name": "Nobody"}},
        ),
        hedge,
    )
    response = agent.run("Nobody ka outstanding?", principal=who())
    assert response.message == "No customer matching 'Nobody' was found. Please check the name."


# =============================================================================================
# 12/13. grounding and memory vs business truth
# =============================================================================================


def _sales_agent(make_agent: MakeAgent, reply: str) -> tuple[Agent, Principal]:
    """An agent over a company whose sales today are 45,000 (and its principal)."""
    company = sales_45k_company()
    agent, _ = make_agent(plan("sales", [("business.daily_sales", {"day": "today"})]), reply)
    return agent, business_principal(company.tenant)


@pytest.mark.parametrize("form", ["45,000", "45k", "forty-five thousand", "₹45,000", "INR 45,000"])
def test_true_figure_in_any_form_is_kept(make_agent: MakeAgent, form: str) -> None:
    reply = f"Aaj ki sales {form} hai."
    agent, principal = _sales_agent(make_agent, reply)
    response = agent.run("Aaj ka total sales?", principal=principal)
    assert response.message == reply


@pytest.mark.parametrize("form", ["50,000", "50k", "fifty thousand", "₹50,000", "INR 50,000"])
def test_memory_figure_in_any_form_is_replaced(
    make_agent: MakeAgent, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    agent, principal = _sales_agent(make_agent, f"Today's sales are {form}.")

    async def recall(*_: object) -> dict[str, Any]:
        return {"facts": [{"content": "Usually today's sales are ₹50,000"}]}

    monkeypatch.setattr(agent, "_recall", recall)
    response = agent.run("Aaj ka total sales batao.", principal=principal)
    assert "50" not in response.message
    assert "₹45,000" in response.message
    assert "TEST data" not in response.message  # real ERP rows are not labelled TEST


def test_memory_says_50000_erp_says_45000_for_a_customer(
    make_agent: MakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, llm = make_agent(
        plan("outstanding", [("business.customer_outstanding", {"customer_name": "Rahul Gupta"})]),
        "Rahul usually has a ₹50,000 bill, so ₹50,000.",
    )

    async def recall(*_: object) -> dict[str, Any]:
        return {"facts": [{"content": "Rahul usually has a ₹50,000 bill."}]}

    monkeypatch.setattr(agent, "_recall", recall)
    response = agent.run("Rahul Gupta ka kitna bill pending hai?", principal=who())
    assert "₹50,000 bill" in llm.calls[1].messages[-1].content  # memory was offered
    assert response.message.startswith("Outstanding for Rahul Gupta (TEST): ₹45,000.")
    assert "50,000" not in response.message


# =============================================================================================
# 17. no business writes
# =============================================================================================


def test_purchase_order_request_is_refused_and_a_false_claim_is_replaced(
    make_agent: MakeAgent,
) -> None:
    agent, _ = make_agent(
        plan(
            "create_purchase_order",
            domain="unsupported",
            query={"capability": "low_stock_products", "write_requested": True},
        ),
        "Theek hai, maine purchase order bana diya hai.",  # a false claim
    )
    response = agent.run("Stock kam hai, automatically purchase order bana do.", principal=who())
    assert response.status == "unsupported" and response.tool_results == []
    assert adapter_of(agent).calls == []
    assert "bana diya" not in response.message
    assert "only read business data" in response.message


def test_write_request_with_an_honest_reply_is_kept(make_agent: MakeAgent) -> None:
    reply = "Main sirf business data padh sakta hoon; purchase order banana available nahi hai."
    agent, _ = make_agent(
        plan("create_purchase_order", domain="unsupported", query={"write_requested": True}),
        reply,
    )
    assert agent.run("PO bana do", principal=who()).message == reply


def test_no_business_write_tool_is_registered(make_agent: MakeAgent) -> None:
    agent, _ = make_agent()
    specs = agent.dependencies.tools.specs(["business"])
    assert len(specs) == 7 and all(s.kind is ToolKind.READ for s in specs)
    names = " ".join(s.name for s in specs)
    for verb in ("create", "update", "settle", "approve", "reorder", "send", "purchase", "pay"):
        assert verb not in names


# =============================================================================================
# 21. failure handling: never a fabricated success
# =============================================================================================


@pytest.mark.parametrize(
    ("failure", "status", "phrase"),
    [
        ("unavailable", "UNAVAILABLE", "could not be retrieved"),
        ("timeout", "UNAVAILABLE", "could not be retrieved"),
        ("hang", "UNAVAILABLE", "could not be retrieved"),
        ("malformed", "DEPENDENCY_ERROR", "could not be read correctly"),
        ("tenant_leak", "DEPENDENCY_ERROR", "could not be read correctly"),
        ("crash", "DEPENDENCY_ERROR", "could not be read correctly"),
        ("rule_not_verified", "UNAVAILABLE", "not available in the assistant yet"),
    ],
)
def test_failures_never_produce_a_figure(
    make_agent: MakeAgent, failure: str, status: str, phrase: str
) -> None:
    # A short deadline only for "hang": a real read (a fresh connection per turn) can take
    # longer than 0.2 s, and the other failures must reach the adapter wrapper.
    timeout = 0.2 if failure == "hang" else 5.0
    erp = ErpSettings(adapter="postgres", business_timezone=TZ, query_timeout_seconds=timeout)
    agent, _ = make_agent(
        plan("sales", [("business.daily_sales", {"period": "this_month"})]),
        "This month's sales were ₹1,25,000.",  # fabricated after a failure
        erp=erp,
    )
    adapter_of(agent).failure = failure  # type: ignore[assignment]
    response = agent.run("Is mahine ki sales?", principal=who())
    assert business_of(response)["status"] == status
    assert "1,25,000" not in response.message and "125000" not in response.message
    assert phrase in response.message


def test_invalid_input_and_unknown_capability_fail_safely(make_agent: MakeAgent) -> None:
    agent, _ = make_agent(
        plan(
            "x",
            [
                ("business.daily_sales", {"period": "next_year"}),
                ("business.customer_payments", {"customer_name": "Rahul Gupta"}),
            ],
        ),
        "Sales ₹99,999.",
    )
    response = agent.run("?", principal=who())
    assert [r.error_code for r in response.tool_results] == ["TOOL_INVALID_INPUT", "TOOL_NOT_FOUND"]
    assert adapter_of(agent).calls == []
    # No business result exists, so the fabricated figure has no source: replaced.
    assert "99" not in response.message
    assert response.message == "The business data could not be retrieved."


# =============================================================================================
# 22. observability: metadata only, request correlation
# =============================================================================================


def test_a_full_business_turn_logs_no_business_data(
    make_agent: MakeAgent, caplog: pytest.LogCaptureFixture
) -> None:
    agent, _ = make_agent(
        plan(
            "overdue",
            [("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})],
            query={
                "capability": "customer_open_invoices",
                "customer_reference": {"name": "Rahul Gupta"},
                "requested_metric": "overdue_amount",
            },
        ),
        "Overdue ₹31,000.",  # ungrounded: also exercises the rejection log
    )
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"):
        agent.run("Rahul Gupta ka overdue?", principal=who(), request_id="req-p11")
    text = " ".join(f"{r.getMessage()} {getattr(r, 'fields', '')}" for r in caplog.records)
    data = ext()
    for secret in (
        "Rahul",
        f"'{data['rahul']}'",  # the customer ref as a logged value (ids are numeric)
        f'"{data["rahul"]}"',
        data["inv1"],  # bill refs ("si:<id>")
        data["inv2"],
        "TEST-INV",
        "30000",
        "31000",
        "45000",
        "INR",
        "test-key",
    ):
        assert secret not in text, secret
    events = {r.getMessage() for r in caplog.records}
    assert {"request.understood", "business.read.completed", "business.response.rejected"} <= events
    understood = next(r for r in caplog.records if r.getMessage() == "request.understood")
    assert understood.fields["business_query"]["metric"] == "overdue_amount"  # type: ignore[attr-defined]


# =============================================================================================
# T. API regression: a business turn over HTTP
# =============================================================================================


def test_business_turn_through_the_http_api() -> None:
    llm = ScriptedLLM(
        script=[
            plan("sales", [("business.daily_sales", {"period": "yesterday"})]),
            "Kal ki sales ₹90,000 thi.",  # wrong: the ERP says 80,000
        ]
    )
    app_settings = settings(token_secret=TOKEN_SECRET)
    deps = build_dependencies(
        app_settings,
        llm=llm,
        clock=ClockService(now_utc=lambda: FIXED_NOW),
        erp_port=erp_port(),
    )
    agent = Agent(deps, checkpointer=InMemorySaver())
    with api_client(app_settings, agent) as client:
        response = client.post(
            "/api/v1/agent/turn",
            json={"message": "Kal ki sales kitni thi?", "thread_id": "t-api-p11"},
            headers=bearer(token(agent, ext().tenant, "asha", ("member", "sales_viewer"))),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["message"].startswith("Total sales on 2026-09-30: ₹80,000.")
    assert "90,000" not in body["message"]
    assert body["tool_calls"][0]["tool_name"] == "business.daily_sales"
    assert "output" not in body  # internal results are not exposed
