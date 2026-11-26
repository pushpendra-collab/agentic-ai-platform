"""verify_business_response: the ERP result, not the model, decides the figures (Phase 10).

Runs after `generate_response` for turns routed to the `business` domain (Phase 16:
with or without tool results, so a turn that read nothing cannot state a figure), and
(Phase 11) for business write requests. It is deterministic (no LLM call).
The draft reply is **rejected and replaced** by `render_context` (built from the
composed business results alone) when any of these holds:

* a figure in the reply (digits, "45k", "1.25 lakh", "forty-five thousand"...) appears in
  no business result and not in the user's own message, so neither the model nor
  recalled memory can put a figure into a business answer; (Phase 16) a percentage
  must equal a percentage the service computed (or the user wrote);
* the reply hedges ("maybe", "probably", "shayad"...): a business answer is either
  stated from a result or not given, and NOT_FOUND is never softened into a guess;
* the reply claims a business action happened (an order placed, a message sent): the
  business layer is read only, so such a claim is always false;
* (Phase 16) a sales reply speaks of profit, margin or earnings: sales totals are never
  profit;
* (2026-11-26) the ERP answered (SUCCESS with figures or records) but the reply states
  none of them: live, "is mahine ki sales" came back as "no information" although the
  total had been read. A business answer either states the data or is replaced;
* (2026-11-26) the user wrote in Roman letters and the reply is in Devanagari: the local
  model transliterated the user's words and got them wrong ("is mahine" became मशीन,
  "machine"). The figures can be right while the words say something else.

(2026-11-25) The answer to an *overdue* bills question about all customers is always the
deterministic text: a long list with two counts and two totals (open and overdue) that a
model's retelling mixed up live ("overdue: 99" for 99 open invoices of which 84 are
overdue). The chat shows the bills as a table either way. One customer's overdue
figure ("usme se kitna overdue hai?") keeps the model's sourced reply.

Logs record only which checks failed and counts (`business.response.rejected`), never
the reply or its numbers. If any business result is AMBIGUOUS, the turn ends as
`needs_clarification`. Finally the conversation's business focus (references only, no
figures) is updated for follow-up questions.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node
from agentic_platform.agents.state import AgentState
from agentic_platform.business.composition import (
    NOT_RECORDED_MESSAGES,
    BusinessResponseContext,
    compose,
    render_context,
    wants_overdue,
)
from agentic_platform.business.focus import next_focus
from agentic_platform.business.grounding import (
    business_results,
    claims_action,
    claims_profit,
    figures_in,
    hedges,
    numbers_in,
    sourced_numbers,
    sourced_percentages,
    ungrounded_numbers,
    ungrounded_percentages,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit

logger = get_logger(__name__)

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _answer_values(context: BusinessResponseContext) -> set[str]:
    """The numbers a reply to the SUCCESS results could state: their figures (flags
    aside) and the amounts and quantities of their records. Never dates, names or
    document numbers: "November 2026 ki koi jaankari nahi" must not count as an answer."""
    values: set[str] = set()
    for view in context.results:
        if view.status != "SUCCESS":
            continue
        for figure in view.figures:
            if figure.kind != "flag":
                values |= numbers_in(figure.value)
        for record in view.records:
            for text in (record.amount, record.quantity):
                if text:
                    values |= numbers_in(text)
    return values


def make_verify_business_response(deps: AgentDependencies) -> NodeFn:
    @instrumented_node("verify_business_response")
    async def verify_business_response(state: AgentState) -> dict[str, Any]:
        results = business_results(state.get("tool_results", []))
        understanding = state.get("understanding")
        query = understanding.business_query if understanding is not None else None
        context = compose(results, query)
        reply = state.get("final_response") or ""

        problems: dict[str, int] = {}
        unsourced = ungrounded_numbers(reply, sourced_numbers(results, state["user_input"]))
        # Phase 16: a percentage must be one the service computed (or the user wrote).
        unsourced += ungrounded_percentages(
            reply, sourced_percentages(results, state["user_input"])
        )
        if unsourced:
            problems["unsourced_figures"] = len(unsourced)
        if results and hedges(reply):
            problems["hedged"] = 1
        if claims_action(reply):
            problems["action_claim"] = 1
        # Phase 16: a sales total is never profit (no cost data, no verified rule).
        if claims_profit(reply) and any(r.get("capability") == "daily_sales" for r in results):
            problems["profit_claim"] = 1
        # 2026-11-26: a Roman-script question answered in Devanagari (mistransliterated).
        if _DEVANAGARI.search(reply) and not _DEVANAGARI.search(state["user_input"]):
            problems["script_changed"] = 1
        # 2026-11-26: data was read, but the reply states none of it ("koi jaankari nahi").
        answer = _answer_values(context)
        if answer and not (figures_in(reply) & answer):
            problems["answer_figure_missing"] = 1
        # The asked-for figure cannot be given (rule not verified, incomplete list...).
        # Numbers in the draft may be sourced yet stand for something else (an invoice
        # balance presented as "overdue"), so the deterministic answer is used instead.
        if any(view.gaps for view in context.results):
            problems["requested_figure_unavailable"] = 1

        overdue_answer = wants_overdue(query) and any(
            r.get("status") == "SUCCESS"
            and r.get("capability") == "customer_open_invoices"
            and (r.get("data") or {}).get("customer") is None  # all customers
            for r in results
        )
        # 2026-11-25: the domain refused a question about something the ERP does not record
        # (new registrations): a fixed, figure-free reply instead of the model's.
        domain_result = state.get("domain_result")
        not_recorded = (
            NOT_RECORDED_MESSAGES.get(str(domain_result.metadata.get("not_recorded")))
            if domain_result is not None and not results
            else None
        )
        update: dict[str, Any] = {}
        if problems or overdue_answer or not_recorded:
            text = not_recorded or render_context(context)
            if problems:
                logger.warning("business.response.rejected", extra={"fields": problems})
            if "unsourced_figures" in problems:  # Phase 10 event name, kept for dashboards
                logger.warning(
                    "business.response.ungrounded",
                    extra={"fields": {"unsourced_numbers": problems["unsourced_figures"]}},
                )
            last = state["messages"][-1] if state["messages"] else None
            message_id = last.id if isinstance(last, AIMessage) else None
            # Same message id: the add_messages reducer replaces the generated reply.
            update["final_response"] = text
            update["messages"] = [AIMessage(content=text, id=message_id)]
        emit(
            EventName.VERIFICATION_RESULT,
            stage=Stage.VERIFICATION,
            status="ok",
            attributes={
                "grounded": not problems,
                "replaced": bool(problems) or overdue_answer,
                "overdue_answer": overdue_answer,
                "results": len(results),
                **{f"problem_{key}": value for key, value in problems.items()},
            },
        )
        if any(r.get("status") == "AMBIGUOUS" for r in results):
            update["status"] = "needs_clarification"
        if results:
            update["business_focus"] = next_focus(
                state.get("business_focus"), results, query
            ).to_state()
        return update

    return verify_business_response
