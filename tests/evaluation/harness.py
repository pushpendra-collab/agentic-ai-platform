"""Deterministic agent evaluation harness (Phase 13). Test infrastructure only.

An `EvalCase` is a scripted conversation: each turn has the user's text and the scripted
model outputs (planner classification, writer reply). The agent runs for real: real graph,
real Supervisor, real tool registry and permission checks, real business service and
grounding verification, and the real ERP read path (`PostgresErpAdapter` and the
InvenLogic readers) on the InvenLogic TEST replica (tests/erp_db.py; the fake ERP was
removed on 2026-10-30). No API key, no model network call.

The business data is the extended TEST company `ext()` (tests/business_helpers.py), plus
`globex()` for the cross-tenant cases. Its refs and tenant id are new on every run, so
cases name them by placeholder: `ref::rahul` is `ext()["rahul"]`, `ref::tenant` its
company id, `ref::globex` / `ref::globex_tenant` the other company's (`resolve_refs`).
Placeholders are resolved in the turn texts and the scripted model outputs when the case
runs; snapshots never contain refs.

`run_case` returns an `Observation`: the response, the observability events of the last
turn, the ERP reads and the model calls. `check` compares it with the case's structured
expectations (route, tools, ERP access, outcome, error category) and its named safety
invariants. `Observation.snapshot()` is the stable, wording-free record the golden
regression suite compares (tests/evaluation/golden/).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.agents.routing import RoutingHints
from agentic_platform.agents.schemas import AgentResponse, RequestUnderstanding
from agentic_platform.agents.serialization import checkpoint_serializer
from agentic_platform.business.grounding import (
    business_results,
    claims_action,
    sourced_numbers,
    ungrounded_numbers,
)
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.core.config import (
    AgentSettings,
    AppSettings,
    AuthSettings,
    ErpSettings,
    LLMSettings,
    MemorySettings,
)
from agentic_platform.observability import EventName, ObsEvent, Stage, capture_events
from agentic_platform.observability.events import is_stage_end
from agentic_platform.security.identity import Principal
from agentic_platform.services.clock import ClockService
from agentic_platform.tools.base import ToolKind
from tests.business_helpers import ROLES, TZ, ErpFaults, business_principal, erp_port, ext, globex
from tests.conftest import FIXED_NOW, understanding
from tests.fakes import ScriptedLLM

# Principals are built when a case runs (the TEST company is created on first use).
Who = Callable[[], Principal]


def WHO() -> Principal:
    return business_principal(ext().tenant)


def NO_BUSINESS() -> Principal:
    return business_principal(ext().tenant, roles=("member",))


def LIMITED_STOCK() -> Principal:
    return business_principal(ext().tenant, roles=("member", "stock_viewer_limited"))


def resolve_refs() -> dict[str, str]:
    """Placeholder name -> this run's value (`ref::<name>` in case texts and scripts)."""
    return {
        **ext().refs,
        "tenant": ext().tenant,
        "globex": globex()["globex"],
        "globex_tenant": globex().tenant,
    }


# `ref::<name>`: a shape the ref validators accept (BusinessQueryIntent checks refs).
_PLACEHOLDER = re.compile(r"ref::(\w+)")


def resolve(value: Any, refs: dict[str, str] | None = None) -> Any:
    """`value` with every `ref::<name>` replaced (strings and pydantic models)."""
    if isinstance(value, str):
        if _PLACEHOLDER.search(value) is None:
            return value
        refs = resolve_refs() if refs is None else refs
        return _PLACEHOLDER.sub(lambda m: refs[m.group(1)], value)
    if isinstance(value, BaseModel):
        dumped = value.model_dump_json()
        resolved = resolve(dumped, refs)
        return value if resolved == dumped else type(value).model_validate_json(resolved)
    return value


# Strings that must never appear in observability events (user text, names, figures,
# identities, credentials). Checked by the `events_are_safe` invariant, together with
# this run's customer ref and tenant ids (`_run_secrets`).
EVENT_SECRETS = (
    "Rahul",
    "Gupta",
    "45,000",
    "45000",
    "Globex",
    "asha",
    "sk-ant-",
    "Bearer ",
    "hunter2",
)


def _run_secrets() -> tuple[str, ...]:
    """This run's identifiers that must never appear in events (with the fake ERP:
    TEST_CUSTOMER_001 and the tenant "acme")."""
    refs = resolve_refs()
    return (refs["rahul"], refs["tenant"], refs["globex"], refs["globex_tenant"])


def contains_id(blob: str, ident: str) -> bool:
    """`ident` (a numeric ERP id or tenant id) as a whole token, not inside a longer
    number or identifier (1523 must not match 0.15234, 41523, si:1523 or a uuid)."""
    pattern = rf"(?<![0-9A-Za-z.:]){re.escape(ident)}(?![0-9A-Za-z.:])"
    return re.search(pattern, blob) is not None


def settings(
    *,
    domain_timeout: float = 120.0,
    erp: ErpSettings | None = None,
    # Generous: a host stall must not turn a normal read into a timeout (timeout cases set
    # their own small value).
    query_timeout: float = 10.0,
    retry_backoff: float = 0.01,
    memory: bool = True,
) -> AppSettings:
    return AppSettings(
        env="test",
        llm=LLMSettings(provider="anthropic", anthropic_api_key="test-key"),
        agent=AgentSettings(max_tool_calls_per_turn=3, domain_timeout_seconds=domain_timeout),
        auth=AuthSettings(role_catalog_json=json.dumps(ROLES)),
        memory=MemorySettings(enabled=memory),
        erp=erp
        or ErpSettings(
            adapter="postgres",
            business_timezone=TZ,
            query_timeout_seconds=query_timeout,
            read_retry_backoff_seconds=retry_backoff,
        ),
    )


def build_agent(
    *script: Any, checkpointer: Any = None, **options: Any
) -> tuple[Agent, ScriptedLLM]:
    """An agent whose ERP port is an `ErpFaults` around the live adapter on the TEST
    replica. With an explicit `erp=` setting the port is built from that setting instead
    (only for cases that must never connect)."""
    llm = ScriptedLLM(script=[resolve(item) for item in script])
    # ErpFaults defines the port methods at runtime (setattr): structurally a port.
    port = None if options.get("erp") is not None else cast(ErpReadPort, erp_port())
    deps = build_dependencies(
        settings(**options), llm=llm, clock=ClockService(now_utc=lambda: FIXED_NOW),
        erp_port=port,
    )  # fmt: skip
    saver = (
        checkpointer if checkpointer is not None else InMemorySaver(serde=checkpoint_serializer())
    )
    return Agent(deps, checkpointer=saver), llm


def plan(
    intent: str,
    calls: Sequence[tuple[str, dict[str, Any]]] = (),
    *,
    domain: str = "business",
    query: dict[str, Any] | None = None,
    unavailable: Sequence[tuple[str, str]] = (),
) -> RequestUnderstanding:
    """A scripted planner classification (what `understand_request` would return)."""
    base = understanding(
        intent=intent,
        domain=domain,
        tool_calls=[(name, json.dumps(args)) for name, args in calls],
    )
    update: dict[str, Any] = {}
    if query is not None:
        update["business_query"] = BusinessQueryIntent.model_validate(query)
    if unavailable:
        update["routing"] = RoutingHints.model_validate(
            {"unavailable": [{"domain": d, "action": a} for d, a in unavailable]}
        )
    return base.model_copy(update=update)


def adapter_of(agent: Agent) -> ErpFaults | None:
    business = agent.dependencies.business
    port = business._port if business is not None else None
    return port if isinstance(port, ErpFaults) else None


# =============================================================================================
# cases
# =============================================================================================


@dataclass
class EvalTurn:
    text: str
    script: Sequence[Any] = ()
    principal: Who = WHO


@dataclass
class Expect:
    """Structured expectations for the case's last turn (None = not checked)."""

    status: str  # response status, or "rejected" when the call raised
    domain: str | None = None
    tools: Sequence[str] | None = None  # names of tools the domain attempted
    erp_access: bool | None = None  # did the last turn read the ERP
    error_code: str | None = None
    error_category: str | None = None
    llm_calls: int | None = None
    must_contain: Sequence[str] = ()
    must_not_contain: Sequence[str] = ()
    business_statuses: Sequence[str] | None = None


@dataclass
class EvalCase:
    id: str
    category: str
    description: str
    turns: Sequence[EvalTurn]
    expect: Expect
    invariants: Sequence[str] = ()
    options: dict[str, Any] = field(default_factory=dict)
    prepare: Callable[[Agent], None] | None = None
    checkpointer: Callable[[], Any] | None = None


# =============================================================================================
# observation
# =============================================================================================


@dataclass
class Observation:
    case_id: str
    response: AgentResponse | None
    raised: str | None
    events: list[ObsEvent]
    erp_calls: list[str]
    llm_calls: int
    write_tools: frozenset[str]
    all_events: list[ObsEvent]
    # The turn texts as sent (placeholders resolved).
    texts: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return self.response.message if self.response is not None else ""

    @property
    def status(self) -> str:
        return self.response.status if self.response is not None else "rejected"

    def of(self, *names: EventName) -> list[ObsEvent]:
        wanted = set(names)
        return [e for e in self.events if e.name in wanted]

    def terminal(self) -> ObsEvent | None:
        terminal = self.of(
            EventName.TURN_COMPLETED,
            EventName.TURN_FAILED,
            EventName.TURN_INTERRUPTED,
            EventName.TURN_REJECTED,
        )
        return terminal[-1] if terminal else None

    def snapshot(self) -> dict[str, Any]:
        """Wording-free, duration-free record of what happened (golden suite)."""
        decision = self.of(EventName.SUPERVISOR_DECIDED)
        verification = self.of(EventName.VERIFICATION_RESULT)
        terminal = self.terminal()
        stages = [
            f"{e.stage}:{'failed' if e.name.value.endswith('.failed') else 'ok'}"
            for e in self.events
            if is_stage_end(e.name, e.stage)
        ]
        response = self.response
        return {
            "status": self.status,
            "domain": response.domain if response is not None else None,
            "error_code": terminal.error_code if terminal is not None else None,
            "error_category": terminal.error_category if terminal is not None else None,
            "stages": stages,
            "decision": (
                {k: decision[0].attributes.get(k) for k in ("outcome", "reason")}
                | {"domain": decision[0].domain}
                if decision
                else None
            ),
            "tools": [
                [e.tool, e.status, e.error_category]
                for e in self.of(EventName.TOOL_COMPLETED, EventName.TOOL_FAILED)
            ],
            "business": [
                [
                    e.attributes.get("capability"),
                    e.attributes.get("result_status"),
                    e.attributes.get("reason"),
                    e.attempt,
                ]
                for e in self.of(EventName.BUSINESS_READ)
            ],
            "llm_calls": [
                [e.attributes.get("llm_stage"), e.status]
                for e in self.of(EventName.LLM_CALL_COMPLETED, EventName.LLM_CALL_FAILED)
            ],
            "retries": len(self.of(EventName.RETRY_ATTEMPTED)),
            "timeouts": sorted(str(e.attributes.get("scope")) for e in self.of(EventName.TIMEOUT)),
            "verification": (
                {k: verification[0].attributes.get(k) for k in ("grounded", "replaced")}
                if verification
                else None
            ),
            "write_occurred": self.write_occurred(),
            "erp_reads": len(self.erp_calls),
        }

    def write_occurred(self) -> bool:
        ran = {e.tool for e in self.of(EventName.TOOL_COMPLETED) if e.status == "ok"}
        approvals = self.response is not None and self.response.status == "interrupted"
        return bool(ran & self.write_tools) or approvals


def run_case(case: EvalCase) -> Observation:
    script = [item for turn in case.turns for item in turn.script]
    saver = case.checkpointer() if case.checkpointer is not None else None
    agent, llm = build_agent(*script, checkpointer=saver, **case.options)
    if case.prepare is not None:
        case.prepare(agent)
    write_tools = frozenset(
        s.name for s in agent.dependencies.tools.specs() if s.kind is ToolKind.WRITE
    )
    adapter = adapter_of(agent)
    thread_id = f"eval-{case.id}".replace("_", "-")[:60]
    response: AgentResponse | None = None
    raised: str | None = None
    all_events: list[ObsEvent] = []
    events: list[ObsEvent] = []
    erp_before = llm_before = 0
    texts = [resolve(turn.text) for turn in case.turns]
    for turn, text in zip(case.turns, texts, strict=True):
        erp_before = len(adapter.calls) if adapter is not None else 0
        llm_before = len(llm.calls)
        response, raised = None, None
        with capture_events() as captured:
            try:
                response = agent.run(text, principal=turn.principal(), thread_id=thread_id)
            except Exception as exc:
                raised = getattr(exc, "code", type(exc).__name__)
        events = captured.events
        all_events.extend(events)
    return Observation(
        case_id=case.id,
        response=response,
        raised=raised,
        events=events,
        erp_calls=list(adapter.calls[erp_before:]) if adapter is not None else [],
        llm_calls=len(llm.calls) - llm_before,
        write_tools=write_tools,
        all_events=all_events,
        texts=texts,
    )


# =============================================================================================
# invariants
# =============================================================================================

_FIGURE = re.compile(r"₹\s?\d|\d[\d,]*\s?(pcs|lakh)")
_SUCCESS_CLAIMS = ("mil gaya", "retrieved successfully", "here is the data from the erp")


def _business(obs: Observation) -> list[dict[str, Any]]:
    return business_results(obs.response.tool_results) if obs.response is not None else []


def _no_write(case: EvalCase, obs: Observation) -> str | None:
    if obs.write_occurred():
        return "a write tool ran or an approval was opened"
    if obs.response is not None and obs.response.status == "interrupted":
        return "the turn paused for approval"
    return None


def _no_action_claim(case: EvalCase, obs: Observation) -> str | None:
    return "the reply claims an action happened" if claims_action(obs.message) else None


def _figures_grounded(case: EvalCase, obs: Observation) -> str | None:
    """Every figure in a business reply comes from an ERP result or the user's own text."""
    results = _business(obs)
    unsourced = ungrounded_numbers(obs.message, sourced_numbers(results, obs.texts[-1]))
    return f"unsourced figures {unsourced}" if unsourced else None


def _no_figure(case: EvalCase, obs: Observation) -> str | None:
    return "the reply states a figure" if _FIGURE.search(obs.message) else None


def _no_success_claim(case: EvalCase, obs: Observation) -> str | None:
    lowered = obs.message.lower()
    hit = next((c for c in _SUCCESS_CLAIMS if c in lowered), None)
    return f"the reply claims success ({hit!r})" if hit else None


def _no_business_data(case: EvalCase, obs: Observation) -> str | None:
    if any(r.get("status") == "SUCCESS" for r in _business(obs)):
        return "a business result was returned"
    return _no_figure(case, obs)


def _no_cross_tenant_data(case: EvalCase, obs: Observation) -> str | None:
    blob = obs.response.model_dump_json() if obs.response is not None else ""
    for marker in ("Globex", "99999", "99,999"):
        if marker in blob and marker not in obs.texts[-1]:
            return f"another tenant's data in the response ({marker})"
    # The other company's id, and the tenant `ErpFaults` stamps on leaked rows (the fake
    # ERP's TEST_OTHER_TENANT). Its customer ref is not checked on its own: a numeric id
    # can equal an unrelated product or invoice id; its name and figure are checked above.
    for ident in (resolve_refs()["globex_tenant"], "999999999"):
        if contains_id(blob, ident) and not contains_id(obs.texts[-1], ident):
            return f"another tenant's data in the response ({ident})"
    return None


def _single_route(case: EvalCase, obs: Observation) -> str | None:
    decisions = obs.of(EventName.SUPERVISOR_DECIDED)
    runs = obs.of(EventName.DOMAIN_RUN_STARTED)
    if len(decisions) > 1 or len(runs) > 1:
        return f"{len(decisions)} routing decisions / {len(runs)} domain runs in one turn"
    return None


def _events_complete(case: EvalCase, obs: Observation) -> str | None:
    if not obs.events:
        return "no events"
    if len({e.request_id for e in obs.events}) != 1:
        return "events of one turn carry different request ids"
    terminal = [
        e
        for e in obs.events
        if e.name
        in (
            EventName.TURN_COMPLETED,
            EventName.TURN_FAILED,
            EventName.TURN_INTERRUPTED,
            EventName.TURN_REJECTED,
        )
    ]
    if len(terminal) != 1:
        return f"{len(terminal)} terminal turn events"
    if terminal[0].name is not EventName.TURN_REJECTED and not obs.of(EventName.TURN_STARTED):
        return "no turn.started"
    return None


def _events_safe(case: EvalCase, obs: Observation) -> str | None:
    blob = json.dumps([e.model_dump(mode="json") for e in obs.all_events], ensure_ascii=False)
    texts = [t for t in obs.texts if len(t) >= 8]  # whole user messages
    for secret in (*EVENT_SECRETS, *texts):
        if secret and secret in blob:
            return f"event data contains {secret!r}"
    # ERP ids are small integers: look for them in the events' strings only (a leaked ref
    # is a string), never in numeric fields such as durations or character counts.
    strings = "\n".join(_strings([e.model_dump(mode="json") for e in obs.all_events]))
    for ident in _run_secrets():
        if contains_id(strings, ident):
            return f"event data contains the ERP id {ident!r}"
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (str(k), *_strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def _memory_is_not_truth(case: EvalCase, obs: Observation) -> str | None:
    return "the remembered figure won over the ERP" if "50,000" in obs.message else None


def _timeout_never_succeeds(case: EvalCase, obs: Observation) -> str | None:
    if obs.of(EventName.TIMEOUT) and any(r.get("status") == "SUCCESS" for r in _business(obs)):
        return "a read reported SUCCESS after its deadline fired"
    if obs.of(EventName.TIMEOUT) and obs.status == "completed" and _FIGURE.search(obs.message):
        return "a timed-out turn still answered with a figure"
    return None


INVARIANTS: dict[str, Callable[[EvalCase, Observation], str | None]] = {
    "no_write": _no_write,
    "no_action_claim": _no_action_claim,
    "figures_grounded": _figures_grounded,
    "no_figure": _no_figure,
    "no_success_claim": _no_success_claim,
    "no_business_data": _no_business_data,
    "no_cross_tenant_data": _no_cross_tenant_data,
    "single_route": _single_route,
    "events_complete": _events_complete,
    "events_safe": _events_safe,
    "memory_is_not_truth": _memory_is_not_truth,
    "timeout_never_succeeds": _timeout_never_succeeds,
}
# Checked on every case, whatever it lists.
ALWAYS = ("no_write", "single_route", "events_complete", "events_safe", "no_cross_tenant_data")


def check(case: EvalCase, obs: Observation) -> list[str]:
    """Problems found (empty = the case passes)."""
    problems: list[str] = []
    e = case.expect
    if obs.status != e.status:
        problems.append(f"status {obs.status!r} != {e.status!r} (raised={obs.raised})")
    response = obs.response
    if e.domain is not None and (response is None or response.domain != e.domain):
        problems.append(f"domain {response.domain if response else None!r} != {e.domain!r}")
    if e.tools is not None:
        tools = [r.tool_name for r in response.tool_results] if response is not None else []
        if list(tools) != list(e.tools):
            problems.append(f"tools {tools} != {list(e.tools)}")
    if e.erp_access is not None and bool(obs.erp_calls) != e.erp_access:
        problems.append(f"ERP access {obs.erp_calls} (expected {e.erp_access})")
    terminal = obs.terminal()
    code = (
        obs.raised
        if obs.raised is not None
        else (response.errors[0].code if response is not None and response.errors else None)
    )
    if e.error_code is not None and code != e.error_code:
        problems.append(f"error code {code!r} != {e.error_code!r}")
    category = terminal.error_category if terminal is not None else None
    if e.error_category is not None and category != e.error_category:
        problems.append(f"error category {category!r} != {e.error_category!r}")
    if e.llm_calls is not None and obs.llm_calls != e.llm_calls:
        problems.append(f"{obs.llm_calls} model calls != {e.llm_calls}")
    if e.business_statuses is not None:
        statuses = [str(r.get("status")) for r in _business(obs)]
        if statuses != list(e.business_statuses):
            problems.append(f"business statuses {statuses} != {list(e.business_statuses)}")
    for text in e.must_contain:
        if text not in obs.message:
            problems.append(f"reply lacks {text!r}")
    for text in e.must_not_contain:
        if text in obs.message:
            problems.append(f"reply contains {text!r}")
    for name in dict.fromkeys((*ALWAYS, *case.invariants)):
        problem = INVARIANTS[name](case, obs)
        if problem is not None:
            problems.append(f"[{name}] {problem}")
    return problems


__all__ = [
    "INVARIANTS",
    "LIMITED_STOCK",
    "NO_BUSINESS",
    "WHO",
    "EvalCase",
    "EvalTurn",
    "Expect",
    "Observation",
    "Stage",
    "Who",
    "adapter_of",
    "build_agent",
    "check",
    "contains_id",
    "plan",
    "resolve",
    "resolve_refs",
    "run_case",
    "settings",
]
