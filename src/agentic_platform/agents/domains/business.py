"""The `business` domain: read-only questions answered from the ERP (Phase 10, ADR-059).

Registered only when an ERP adapter is configured (`ERP_ADAPTER=postgres`). It runs
the planned `business.*` tool calls through the registry exactly like `general` does,
with one extra rule: only READ tools run here, whatever the plan says. Business writes
(orders, invoices, payments, stock, customers) do not exist.

Phase 11 (ADR-060): when the planner described the question as a typed
`BusinessQueryIntent` but planned no tool call, the handler compiles the intent into
exactly one typed call (`tool_call_for`). Arguments then come from typed fields, not
from free-form JSON. A write request is never compiled into anything. Multi-step reads
stay bounded: the plan runs once, in order, within `AGENT_MAX_TOOL_CALLS_PER_TURN`;
there is no open-ended loop.

Phase 12 (ADR-061): the Supervisor routes turns here; this domain owns the mapping of its
`BusinessResult` statuses onto the domain-level `DomainResult.outcome`
(`business_outcome`), so the Supervisor never interprets business statuses itself.

Future domain agents (Sales, Inventory, Purchase, Accounting/GST writes) can each become
their own handler, or a LangGraph subgraph behind one, without changing the top-level
graph. Their keys are reserved in agents/routing.py until they are implemented.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from agentic_platform.agents.domains.base import (
    DomainHandler,
    DomainRequest,
    resolve_tool_name,
    run_planned_tool_calls,
    with_tool_outcome,
)
from agentic_platform.agents.schemas import DomainOutcome, DomainResult, PlannedToolCall
from agentic_platform.business.dates import period_from_text
from agentic_platform.business.grounding import business_results
from agentic_platform.business.results import BusinessStatus, Entity
from agentic_platform.tools.base import ToolKind
from agentic_platform.tools.business import BUSINESS_DOMAIN, tool_call_for
from agentic_platform.tools.registry import ToolRegistry


class BusinessDomainHandler(DomainHandler):
    key = BUSINESS_DOMAIN
    description = (
        "Questions about the company's current business data that the listed business "
        "tools answer: customer outstanding, open/unpaid/overdue invoices, sales for a "
        "day or period (also compared with the same period last year), product and "
        "warehouse stock, low-stock products. Read only: "
        "requests to create, change, send or delete anything are `unsupported`."
    )
    tool_domains: tuple[str, ...] = (BUSINESS_DOMAIN,)

    def __init__(self, tools: ToolRegistry, max_tool_calls: int) -> None:
        self._tools = tools
        self._max_tool_calls = max_tool_calls

    def _plan_is_valid(self, calls: list[PlannedToolCall]) -> bool:
        """True when every planned call names a business tool (prefix optional, see
        `resolve_tool_name`) and its arguments pass the tool's strict input model."""
        specs = self._tools.specs(self.tool_domains)
        for call in calls:
            name = resolve_tool_name(call.tool_name, specs)
            spec = next((s for s in specs if s.name == name), None)
            if spec is None:
                return False
            try:
                spec.input_model.model_validate_json(call.arguments_json or "{}")
            except (ValidationError, ValueError):
                return False
        return True

    async def handle(self, request: DomainRequest) -> DomainResult:
        understanding = request.understanding
        query = understanding.business_query
        # The typed intent wins over a free-form plan that cannot run (an unknown tool or
        # invented arguments such as `filters`): a small local model gets the intent right
        # more often than the JSON. A valid plan is kept as it is; without a compiled call
        # the plan runs unchanged (and fails as before).
        if query is not None and (
            not understanding.tool_calls or not self._plan_is_valid(understanding.tool_calls)
        ):
            compiled = tool_call_for(query)
            if compiled is not None:
                name, arguments = compiled
                call = PlannedToolCall(tool_name=name, arguments_json=arguments)
                understanding = understanding.model_copy(update={"tool_calls": [call]})
                request = request.model_copy(update={"understanding": understanding})
        calls = [self._period_from_words(c, request.user_input) for c in understanding.tool_calls]
        if calls != understanding.tool_calls:
            understanding = understanding.model_copy(update={"tool_calls": calls})
            request = request.model_copy(update={"understanding": understanding})
        kept = self._without_new_registration_counts(calls, request.user_input)
        if not kept and calls:
            return DomainResult(
                domain=self.key,
                status="unsupported",
                outcome="UNSUPPORTED",
                metadata={"not_recorded": "new_registrations"},
            )
        calls = kept
        result = await self._run(request, calls)
        return business_outcome(await self._narrow_shortened_names(request, calls, result))

    def _without_new_registration_counts(
        self, calls: list[PlannedToolCall], user_input: str
    ) -> list[PlannedToolCall]:
        """The calls minus any customer count, when the user asks about *new* customers or
        users ("aaj kitne naye users register hue?"). The ERP records no registration date,
        and customers billed today are not new customers, so that count must not run (live
        2026-11-25: "aaj 3 naye users register hue"). Other reads of the turn still run."""
        if not _NEW_REGISTRATIONS.search(user_input):
            return calls
        specs = self._tools.specs(self.tool_domains)
        count = f"{BUSINESS_DOMAIN}.customer_count"
        return [c for c in calls if resolve_tool_name(c.tool_name, specs) != count]

    async def _run(self, request: DomainRequest, calls: list[PlannedToolCall]) -> DomainResult:
        understanding = request.understanding.model_copy(update={"tool_calls": calls})
        return await run_planned_tool_calls(
            self._tools,
            request.model_copy(update={"understanding": understanding}),
            domain=self.key,
            tool_domains=self.tool_domains,
            max_tool_calls=self._max_tool_calls,
            allowed=lambda spec: spec.kind is ToolKind.READ,
        )

    def _period_from_words(self, call: PlannedToolCall, user_input: str) -> PlannedToolCall:
        """The LLM never computes a date (business/dates.py). When it sent a calendar date
        for sales although the user typed no digit, the user's own period word ("kal",
        "is mahine") replaces it. A customer count gets the user's period word when the
        model left the period out ("aaj kitne customer aaye?" is a count of the day's
        customers, not of all customers)."""
        specs = self._tools.specs(self.tool_domains)
        name = resolve_tool_name(call.tool_name, specs)
        args = _json_object(call.arguments_json)
        if args is None or args.get("period") is not None:
            return call
        if name == f"{BUSINESS_DOMAIN}.daily_sales":
            day = args.get("day")
            if not isinstance(day, str) or day == "today":
                return call
        elif name != f"{BUSINESS_DOMAIN}.customer_count" or _SO_FAR.search(user_input):
            return call
        if re.search(r"\d", user_input):
            return call
        period = period_from_text(user_input)
        if period is None:
            return call
        args = {k: v for k, v in args.items() if k != "day"} | {"period": period.value}
        return call.model_copy(update={"arguments_json": json.dumps(args)})

    async def _narrow_shortened_names(
        self, request: DomainRequest, calls: list[PlannedToolCall], result: DomainResult
    ) -> DomainResult:
        """AMBIGUOUS because the model shortened the user's words ("Godown" for "Main
        Godown")? When exactly one candidate's full name is in the user's text and contains
        the name the model sent, read that one record (by its reference, inside the same
        tenant and scope). A name the model passed in full stays ambiguous ("Amit Kumar":
        Amit Kumar / Amit Kumar Traders), and so does any other case."""
        if len(result.tool_results) != len(calls):
            return result
        text = request.user_input.casefold()
        results = list(result.tool_results)
        extra = 0
        for index, (call, tool_result) in enumerate(zip(calls, results, strict=True)):
            out = (tool_result.output or {}).get("result")
            if not isinstance(out, dict) or out.get("status") != BusinessStatus.AMBIGUOUS:
                continue
            entity = out.get("entity")
            args = _json_object(call.arguments_json)
            if entity not in _NARROWABLE or args is None:
                continue
            given = str(args.get(f"{entity}_name") or "").strip().casefold()
            if not given:
                continue
            picks = [
                c
                for c in out.get("candidates", [])
                if isinstance(c, dict)
                and given in (base := _base_name(str(c.get("display_name", "")))).casefold()
                and len(base) > len(given)
                and base.casefold() in text
            ]
            if len(picks) != 1 or len(calls) + extra >= self._max_tool_calls:
                continue
            narrowed = {k: v for k, v in args.items() if k != f"{entity}_name"}
            narrowed[f"{entity}_ref"] = picks[0]["ref"]
            retry = call.model_copy(update={"arguments_json": json.dumps(narrowed)})
            again = await self._run(request, [retry])
            extra += 1
            if again.tool_results:
                results[index] = again.tool_results[0]
        if not extra:
            return result
        return with_tool_outcome(result.model_copy(update={"tool_results": results}))


_NARROWABLE = frozenset({Entity.CUSTOMER, Entity.PRODUCT, Entity.WAREHOUSE})
# A question about *new* customers or users, or about registering as an event (Hinglish,
# English, Hindi). "Kitne registered customers hain?" (a total) and "New Delhi" do not match.
_WHO = r"(?:customers?|users?|clients?|grahak|log|ग्राहक|यूज़र|यूजर|कस्टमर)"
_NEW_REGISTRATIONS = re.compile(
    rf"\b(?:naye|naya|nayi|new|नए|नया|नई)\s+(?:\w+\s+)?{_WHO}"
    r"|\bregister(?:ed)?\s+(?:hue|hua|hui|huye|kiye|kiya|kare|today|yesterday|this|last)\b"
    r"|\b(?:registrations?|sign[- ]?ups?|signed\s+up|joined)\b"
    r"|रजिस्टर\s+(?:हुए|हुआ|हुई|किए|किया)",
    re.IGNORECASE,
)
# "Aaj tak" / "ab tak" (so far) is a total, not today (customer counts).
_SO_FAR = re.compile(
    r"\b(?:aaj|aj|ab)\s+tak\b|\btill\s+(?:date|today|now)\b|\bso\s+far\b"
    r"|आज\s+तक|अब\s+तक",
    re.IGNORECASE,
)


def _json_object(text: str | None) -> dict[str, Any] | None:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _base_name(display_name: str) -> str:
    """'Main Godown (TEST)' -> 'Main Godown' (a trailing bracket is a label, not a word
    the user types)."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", display_name).strip()


def business_outcome(result: DomainResult) -> DomainResult:
    """Map the business reads' statuses onto the domain-level outcome.

    NOT_FOUND is a truthful answer, so it counts as SUCCESS, like a found record: the
    outcome says whether the domain could answer, not what the answer was. AMBIGUOUS
    needs the user's choice. Without any business result (for example the registry
    refused every call), the tool-level outcome already set by `run_planned_tool_calls`
    stands."""
    statuses = {r.get("status") for r in business_results(result.tool_results)}
    if not statuses:
        return result
    outcome: DomainOutcome
    if BusinessStatus.AMBIGUOUS in statuses:
        outcome = "CLARIFICATION_REQUIRED"
    elif statuses & {BusinessStatus.SUCCESS, BusinessStatus.NOT_FOUND}:
        outcome = "SUCCESS"
    elif statuses & {BusinessStatus.UNAVAILABLE, BusinessStatus.DEPENDENCY_ERROR}:
        outcome = "DEPENDENCY_ERROR"
    elif BusinessStatus.UNAUTHORIZED in statuses:
        outcome = "UNAUTHORIZED"
    elif BusinessStatus.VALIDATION_ERROR in statuses:
        outcome = "VALIDATION_ERROR"
    else:
        outcome = "FAILED"
    return result.model_copy(
        update={
            "outcome": outcome,
            "next_action": "clarify" if outcome == "CLARIFICATION_REQUIRED" else "respond",
        }
    )
