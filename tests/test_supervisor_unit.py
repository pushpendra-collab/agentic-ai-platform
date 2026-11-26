"""Phase 12: the Supervisor's decision logic and contracts (UNIT VERIFIED, no graph, no ERP).

Covers: domain catalogue, typed SupervisorDecision, routing rules (general, business,
typed-capability reroute, reserved/unknown/not-connected domains, partial and ambiguous
multi-domain requests, planner clarification), explicit routing limits, the
no-identity/no-SQL contract of every routing model, the fixed replies (no figures, no
action claims), the domain-level outcome mapping and the Supervisor's import boundary.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_platform.agents import routing
from agentic_platform.agents.domains.base import DomainRegistry, with_tool_outcome
from agentic_platform.agents.domains.business import BusinessDomainHandler, business_outcome
from agentic_platform.agents.domains.general import GeneralDomainHandler
from agentic_platform.agents.routing import (
    IMPLEMENTED_DOMAINS,
    RESERVED_DOMAIN_LABELS,
    RESERVED_DOMAINS,
    RoutingHints,
    UnavailablePart,
    describe_unavailable_for_llm,
)
from agentic_platform.agents.schemas import DomainResult, RequestUnderstanding
from agentic_platform.agents.supervisor import (
    MAX_DOMAIN_RUNS_PER_TURN,
    MAX_ROUTING_DECISIONS_PER_TURN,
    READ_NOT_AVAILABLE_MESSAGE,
    READ_PARTIAL_NOTICE,
    Reason,
    Supervisor,
    SupervisorDecision,
    SupervisorOutcome,
    fixed_reply,
    load_decision,
    not_available_reply,
    partial_notice,
)
from agentic_platform.business.grounding import claims_action, hedges, numbers_in
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.core.errors import (
    DomainRegistrationError,
    OrchestrationError,
    OrchestrationLimitError,
)
from agentic_platform.tools.base import ToolResult
from agentic_platform.tools.registry import ToolRegistry
from tests.conftest import understanding

BOTH = ["general", "business"]
SRC = Path(__file__).resolve().parents[1] / "src" / "agentic_platform"

Out = SupervisorOutcome


def plan(
    domain: str = "general",
    *,
    query: dict[str, Any] | None = None,
    unavailable: list[tuple[str, str]] | None = None,
    confidence: str | None = None,
    **kw: Any,
) -> RequestUnderstanding:
    base = understanding(domain=domain, **kw)
    update: dict[str, Any] = {}
    if query is not None:
        update["business_query"] = BusinessQueryIntent.model_validate(query)
    if unavailable is not None or confidence is not None:
        update["routing"] = RoutingHints.model_validate({
            "unavailable": [{"domain": d, "action": a} for d, a in unavailable or []],
            **({"confidence": confidence} if confidence else {}),
        })  # fmt: skip
    return base.model_copy(update=update)


def decide(
    u: RequestUnderstanding, active: list[str] | None = None, **kw: Any
) -> SupervisorDecision:
    return Supervisor(lambda: BOTH if active is None else active).decide(u, **kw)


# =============================================================================================
# domain catalogue
# =============================================================================================


def test_catalogue_matches_the_implemented_handlers() -> None:
    assert set(IMPLEMENTED_DOMAINS) == {GeneralDomainHandler.key, BusinessDomainHandler.key}
    assert not set(IMPLEMENTED_DOMAINS) & set(RESERVED_DOMAINS)
    assert set(RESERVED_DOMAINS) == set(RESERVED_DOMAIN_LABELS) == {
        "purchase", "sales", "inventory", "accounting", "gst", "knowledge", "notification",
    }  # fmt: skip
    assert "unsupported" not in RESERVED_DOMAINS + IMPLEMENTED_DOMAINS


@pytest.mark.parametrize("key", RESERVED_DOMAINS)
def test_a_reserved_domain_cannot_be_activated_by_registering_a_handler(key: str) -> None:
    stub = GeneralDomainHandler(ToolRegistry(), max_tool_calls=1)
    stub.key = key
    with pytest.raises(DomainRegistrationError):
        DomainRegistry().register(stub)


def test_unavailable_domains_are_described_for_the_planner() -> None:
    text = describe_unavailable_for_llm({"general", "business"})
    for key in RESERVED_DOMAINS:
        assert f"`{key}`" in text
    assert "`business`" not in text  # connected: not listed as unavailable
    assert "`business`" in describe_unavailable_for_llm({"general"})


# =============================================================================================
# routing rules
# =============================================================================================


def test_general_and_business_requests_are_routed_to_their_domain() -> None:
    general = decide(plan("general"))
    assert (general.outcome, general.domain, general.reason) == (
        Out.ROUTE,
        "general",
        Reason.CLASSIFIED,
    )
    assert general.requested_action == "answer" and not general.clarification_required
    business = decide(plan("business", query={"capability": "customer_outstanding"}))
    assert (business.outcome, business.domain, business.requested_action) == (
        Out.ROUTE,
        "business",
        "read",
    )


@pytest.mark.parametrize("label", ["general", "sales", "inventory"])
def test_a_typed_business_read_capability_wins_over_the_label(label: str) -> None:
    decision = decide(plan(label, query={"capability": "daily_sales", "period": "today"}))
    assert (decision.outcome, decision.domain) == (Out.ROUTE, "business")
    assert decision.reason is Reason.TYPED_BUSINESS_CAPABILITY
    assert decision.routing_metadata.rerouted_from == label


def test_no_reroute_without_business_a_write_or_an_explicit_unsupported() -> None:
    read = {"capability": "daily_sales"}
    assert decide(plan("general", query=read), active=["general"]).domain == "general"
    write = decide(
        plan("general", query={"capability": "low_stock_products", "write_requested": True})
    )
    assert write.domain == "general" and write.routing_metadata.rerouted_from is None
    assert decide(plan("unsupported", query=read)).outcome is Out.UNSUPPORTED


@pytest.mark.parametrize("key", RESERVED_DOMAINS)
def test_a_reserved_domain_gets_a_fixed_reply_and_runs_nothing(key: str) -> None:
    decision = decide(plan(key))
    assert decision.outcome is Out.UNSUPPORTED_CAPABILITY and decision.domain is None
    assert decision.reason is Reason.RESERVED_DOMAIN and decision.fixed_reply
    assert not decision.runs_domain
    assert [p.domain for p in decision.unavailable] == [key]
    assert RESERVED_DOMAIN_LABELS[key] in fixed_reply(decision)


@pytest.mark.parametrize(
    "label", ["hack", "admin", "DROP TABLE x", "business; select 1", "", "x" * 80]
)
def test_an_unknown_domain_is_rejected(label: str) -> None:
    decision = decide(plan(label))
    assert (
        decision.outcome is Out.UNSUPPORTED_CAPABILITY and decision.reason is Reason.INVALID_DOMAIN
    )
    assert decision.domain is None and not decision.runs_domain
    assert decision.requested_domain == (label if routing.is_domain_key(label) else "invalid")


def test_business_without_an_erp_is_not_connected() -> None:
    decision = decide(plan("business"), active=["general"])
    assert (
        decision.reason is Reason.DOMAIN_NOT_CONNECTED
        and decision.outcome is Out.UNSUPPORTED_CAPABILITY
    )
    assert "not connected" in fixed_reply(decision)


def test_unsupported_with_and_without_named_parts() -> None:
    plain = decide(plan("unsupported"))
    assert (plain.outcome, plain.reason) == (Out.UNSUPPORTED, Reason.OUT_OF_SCOPE)
    assert plain.runs_domain and not plain.fixed_reply  # explained by the writer, nothing runs
    named = decide(plan("unsupported", unavailable=[("notification", "send")]))
    assert (named.outcome, named.reason) == (Out.UNSUPPORTED_CAPABILITY, Reason.NOT_AVAILABLE)
    assert named.requested_action == "send"
    assert "WhatsApp" in fixed_reply(named)


def test_a_read_with_a_write_part_is_a_partial_route() -> None:
    decision = decide(plan("business", query={"capability": "low_stock_products"},
                           unavailable=[("purchase", "write")]))  # fmt: skip
    assert (decision.outcome, decision.domain, decision.reason) == (
        Out.ROUTE, "business", Reason.PARTIAL_ROUTE,
    )  # fmt: skip
    assert decision.requested_action == "mixed" and not decision.fixed_reply
    notice = partial_notice(decision)
    assert notice is not None and "purchase orders" in notice


def test_a_read_with_a_send_part_asks_first() -> None:
    decision = decide(plan("business", query={"capability": "warehouse_stock"},
                           unavailable=[("notification", "send")]))  # fmt: skip
    assert decision.outcome is Out.CLARIFY and decision.reason is Reason.SEND_NOT_AVAILABLE
    assert decision.domain is None and decision.clarification_required and decision.fixed_reply
    assert "?" in fixed_reply(decision)


def test_the_planners_clarification_passes_through() -> None:
    decision = decide(plan("business", needs_clarification=True, clarification_question="Kaun?"))
    assert decision.outcome is Out.CLARIFY and decision.reason is Reason.PLANNER_CLARIFICATION
    assert decision.domain == "business" and not decision.fixed_reply
    assert decide(plan("purchase", needs_clarification=True,
                       clarification_question="?")).domain is None  # fmt: skip


def test_previous_domain_is_recorded_but_never_decides() -> None:
    switched = decide(plan("general"), previous_domain="business")
    assert switched.domain == "general"  # stale business context does not pull it back
    assert switched.routing_metadata.previous_domain == "business"
    assert switched.routing_metadata.topic_changed is True
    query = {"capability": "customer_open_invoices", "follow_up": True}
    follow = decide(plan("business", query=query), previous_domain="business")
    assert follow.routing_metadata.follow_up and not follow.routing_metadata.topic_changed
    # A tampered previous domain is not even recorded.
    assert (
        decide(plan("general"), previous_domain="x; drop").routing_metadata.previous_domain is None
    )


def test_confidence_comes_from_the_hints_or_the_query() -> None:
    assert decide(plan("general", confidence="low")).confidence == "low"
    assert (
        decide(
            plan("business", query={"capability": "daily_sales", "confidence": "medium"})
        ).confidence
        == "medium"
    )
    assert decide(plan("general")).confidence is None


def test_the_registry_is_read_at_decision_time() -> None:
    active: list[str] = ["general"]
    sup = Supervisor(lambda: active)
    assert sup.decide(plan("asking")).outcome is Out.UNSUPPORTED_CAPABILITY
    active.append("asking")  # registered later (as tests/test_resume_unit.py does)
    assert sup.decide(plan("asking")).domain == "asking"


# =============================================================================================
# limits and state contract
# =============================================================================================


def test_routing_limits_are_explicit() -> None:
    assert MAX_ROUTING_DECISIONS_PER_TURN == 1 and MAX_DOMAIN_RUNS_PER_TURN == 1
    with pytest.raises(OrchestrationLimitError) as caught:
        decide(plan("general"), decisions_so_far=1)
    assert caught.value.code == "ORCHESTRATION_LIMIT_EXCEEDED"


def test_load_decision_rejects_missing_or_invalid_state() -> None:
    good = decide(plan("general")).to_state()
    assert load_decision({"decision": good}).domain == "general"
    for bad in (None, {}, {"decision": "ROUTE"}, {"decision": {**good, "domain": None}},
                {"decision": {**good, "outcome": "EXECUTE"}},
                {"decision": {**good, "tenant_id": "globex"}}):  # fmt: skip
        with pytest.raises(OrchestrationError):
            load_decision(bad)  # type: ignore[arg-type]


FORBIDDEN = {"tenant_id": "globex", "user_id": "root", "permissions": ["*"], "sql": "select 1",
             "table": "invoices", "tool_arguments": {"x": 1}, "principal": "admin"}  # fmt: skip


@pytest.mark.parametrize("field", sorted(FORBIDDEN))
def test_no_routing_model_accepts_identity_permissions_sql_or_tool_arguments(field: str) -> None:
    decision = decide(plan("general")).to_state()
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate({**decision, field: FORBIDDEN[field]})
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate(
            {
                **decision,
                "routing_metadata": {**decision["routing_metadata"], field: FORBIDDEN[field]},
            }
        )
    with pytest.raises(ValidationError):
        RoutingHints.model_validate({"unavailable": [], field: FORBIDDEN[field]})
    with pytest.raises(ValidationError):
        UnavailablePart.model_validate(
            {"domain": "purchase", "action": "write", field: FORBIDDEN[field]}
        )
    with pytest.raises(ValidationError):
        RequestUnderstanding.model_validate(
            {
                **understanding().model_dump(),
                "routing": {"unavailable": [], field: FORBIDDEN[field]},
            }
        )


def test_decision_invariants() -> None:
    base = decide(plan("general")).to_state()
    for broken in (
        {"outcome": "ROUTE", "domain": None},
        {"outcome": "UNSUPPORTED_CAPABILITY", "domain": "general"},
        {"outcome": "CLARIFY", "clarification_required": False},
        {"clarification_required": True},
        {"domain": "general; drop"},
        {"requested_domain": "Robert'); DROP"},
        {"reason": "because"},
    ):
        with pytest.raises(ValidationError):
            SupervisorDecision.model_validate({**base, **broken})


def test_routing_hints_are_bounded_and_closed() -> None:
    with pytest.raises(ValidationError):
        UnavailablePart.model_validate({"domain": "business", "action": "write"})  # not reserved
    with pytest.raises(ValidationError):
        UnavailablePart.model_validate({"domain": "purchase", "action": "execute"})
    many = [{"domain": d, "action": a} for d in RESERVED_DOMAINS for a in ("write", "send")]
    with pytest.raises(ValidationError):
        RoutingHints.model_validate({"unavailable": many})
    twice = RoutingHints.model_validate({"unavailable": [{"domain": "gst", "action": "write"}] * 3})
    assert len(twice.unavailable) == 1


def test_decision_state_is_plain_json() -> None:
    import json

    partial = plan(
        "business", query={"capability": "low_stock_products"}, unavailable=[("purchase", "write")]
    )
    decision = decide(partial, previous_domain="general")
    state = decision.to_state()
    assert json.loads(json.dumps(state)) == state
    assert SupervisorDecision.model_validate(state) == decision


# =============================================================================================
# fixed replies: deterministic, no figures, no false action claims, no hedging
# =============================================================================================


def all_fixed_texts() -> list[str]:
    texts: list[str] = []
    for key in RESERVED_DOMAINS:
        for action in ("write", "send", "read", "other"):
            parts = [UnavailablePart.model_validate({"domain": key, "action": action})]
            texts.append(not_available_reply(parts))
            d = decide(plan("business", query={"capability": "daily_sales"},
                            unavailable=[(key, action)]))  # fmt: skip
            texts.append(fixed_reply(d) if d.fixed_reply else partial_notice(d) or "")
    both = [("purchase", "write"), ("notification", "send")]
    for label in ("hack", "purchase", "unsupported"):
        texts.append(fixed_reply(decide(plan(label, unavailable=both))))
    texts.append(fixed_reply(decide(plan("business"), active=["general"])))
    return texts


def test_fixed_replies_never_claim_an_action_hedge_or_carry_a_figure() -> None:
    texts = all_fixed_texts()
    assert len(texts) > 50
    for text in texts:
        assert text and not claims_action(text), text
        assert not hedges(text), text
        assert not numbers_in(text), text


def test_partial_notice_is_only_for_routed_turns_with_missing_parts() -> None:
    assert partial_notice(decide(plan("general"))) is None
    assert partial_notice(decide(plan("purchase"))) is None
    two = decide(plan("general", unavailable=[("purchase", "write"), ("gst", "write")]))
    assert partial_notice(two) == (
        "Note: part of your request was not done, because it is not available in this "
        "assistant yet: purchase orders and other purchase actions; GST returns and GST entries."
    )


# 2026-11-25: a question that only *reads* something no tool covers ("mere database me
# kitne user hain?") was filed by the planner under `purchase` and answered "not available:
# purchase orders and other purchase actions". A read part is never named by its domain.


@pytest.mark.parametrize("key", RESERVED_DOMAINS)
def test_a_read_nobody_answers_is_not_called_a_purchase_or_other_action(key: str) -> None:
    decision = decide(plan("unsupported", unavailable=[(key, "read")]))
    assert decision.outcome is Out.UNSUPPORTED_CAPABILITY and not decision.runs_domain
    text = fixed_reply(decision)
    assert text == READ_NOT_AVAILABLE_MESSAGE
    assert RESERVED_DOMAIN_LABELS[key] not in text and "purchase" not in text
    assert not claims_action(text) and not hedges(text) and not numbers_in(text)


def test_a_write_or_send_part_keeps_its_label_next_to_a_read_part() -> None:
    parts = [("purchase", "read"), ("purchase", "write")]
    text = fixed_reply(decide(plan("unsupported", unavailable=parts)))
    assert text == (
        "This isn't available in the assistant yet: purchase orders and other purchase "
        "actions. Nothing was carried out. Part of the information you asked for isn't "
        "available yet either."
    )
    # Live: "kitne user hain?" was also filed under knowledge ("answers from company
    # documents"); a read part is never named, whatever domain it was filed under.
    knowledge = fixed_reply(decide(plan("unsupported", unavailable=[("knowledge", "read")])))
    assert knowledge == READ_NOT_AVAILABLE_MESSAGE


def test_a_routed_answer_with_an_unanswerable_read_part_gets_the_read_notice() -> None:
    decision = decide(
        plan("business", query={"capability": "daily_sales"}, unavailable=[("purchase", "read")])
    )
    assert decision.outcome is Out.ROUTE
    assert partial_notice(decision) == READ_PARTIAL_NOTICE


# =============================================================================================
# domain-level outcome (the Supervisor reads this, never business statuses)
# =============================================================================================


def tr(
    name: str, status: str = "ok", code: str | None = None, business: str | None = None
) -> ToolResult:
    output = {"result": {"capability": "x", "status": business}} if business else None
    return ToolResult.model_validate(
        {"tool_name": name, "status": status, "error_code": code, "output": output}
    )


def outcome(*results: ToolResult) -> DomainResult:
    return with_tool_outcome(
        DomainResult(domain="d", status="completed", tool_results=list(results))
    )


def test_tool_level_outcome() -> None:
    assert outcome().outcome == "SUCCESS"
    assert outcome(tr("a"), tr("b", "error", "TOOL_INVALID_INPUT")).outcome == "SUCCESS"
    assert (
        outcome(
            tr("a", "error", "PERMISSION_DENIED"), tr("b", "error", "TOOL_INVALID_INPUT")
        ).outcome
        == "UNAUTHORIZED"
    )
    assert (
        outcome(tr("a", "error", "TOOL_NOT_FOUND"), tr("b", "error", "TOOL_NOT_ALLOWED")).outcome
        == "VALIDATION_ERROR"
    )
    assert outcome(tr("a", "error", "TOOL_EXECUTION_FAILED")).outcome == "DEPENDENCY_ERROR"
    assert outcome(tr("a", "error", "SOMETHING_ELSE")).outcome == "FAILED"
    result = outcome(tr("a"), tr("b", "error", "TOOL_NOT_ALLOWED"))
    assert result.errors == ["TOOL_NOT_ALLOWED"]
    assert result.metadata == {"tool_calls": 2, "failed_calls": 1}
    assert result.next_action == "respond"


@pytest.mark.parametrize(
    ("statuses", "expected", "next_action"),
    [
        (["SUCCESS"], "SUCCESS", "respond"),
        (["NOT_FOUND"], "SUCCESS", "respond"),  # a truthful answer, not a failure
        (["SUCCESS", "AMBIGUOUS"], "CLARIFICATION_REQUIRED", "clarify"),
        (["UNAUTHORIZED"], "UNAUTHORIZED", "respond"),
        (["UNAVAILABLE"], "DEPENDENCY_ERROR", "respond"),
        (["DEPENDENCY_ERROR", "UNAUTHORIZED"], "DEPENDENCY_ERROR", "respond"),
        (["VALIDATION_ERROR"], "VALIDATION_ERROR", "respond"),
    ],
)
def test_business_statuses_map_onto_domain_outcomes(
    statuses: list[str], expected: str, next_action: str
) -> None:
    results = [tr(f"business.r{i}", business=s) for i, s in enumerate(statuses)]
    mapped = business_outcome(outcome(*results))
    assert (mapped.outcome, mapped.next_action) == (expected, next_action)
    # Only codes and counts leave the domain: no business payload in the domain fields.
    assert set(mapped.metadata) == {"tool_calls", "failed_calls"}


def test_business_outcome_without_business_results_keeps_the_tool_outcome() -> None:
    denied = outcome(tr("business.customer_outstanding", "error", "PERMISSION_DENIED"))
    assert business_outcome(denied).outcome == "UNAUTHORIZED"


# =============================================================================================
# boundary: the Supervisor cannot reach data, tools, SQL, the LLM or identity
# =============================================================================================

FORBIDDEN_IMPORTS = (
    "agentic_platform.db",
    "agentic_platform.business.adapters",
    "agentic_platform.business.service",
    "agentic_platform.business.ports",
    "agentic_platform.tools",
    "agentic_platform.llm",
    "agentic_platform.security",
    "agentic_platform.memory",
    "agentic_platform.agents.dependencies",
    "sqlalchemy",
    "asyncpg",
    "anthropic",
)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", ["agents/supervisor.py", "agents/routing.py"])
def test_supervisor_modules_import_no_data_tool_llm_or_identity_layer(module: str) -> None:
    for name in imports_of(SRC / module):
        assert not name.startswith(FORBIDDEN_IMPORTS), name


def test_supervisor_gets_only_the_domain_names() -> None:
    params = list(inspect.signature(Supervisor.__init__).parameters)
    assert params == ["self", "active_domains"]
    public = {n for n in dir(Supervisor) if not n.startswith("_")}
    assert public == {"decide"}
    source = (SRC / "agents" / "nodes" / "supervise.py").read_text(encoding="utf-8")
    assert "Supervisor(deps.domains.keys)" in source
    for name in ("deps.tools", "deps.business", "deps.database", "deps.llm", "deps.memory",
                 "require_principal", "execute"):  # fmt: skip
        assert name not in source, name


def test_supervisor_module_has_no_sql_or_write_vocabulary() -> None:
    text = (SRC / "agents" / "supervisor.py").read_text(encoding="utf-8").lower()
    for word in ("select ", "insert ", "update ", "delete from", "execute(", "text("):
        assert word not in text, word
