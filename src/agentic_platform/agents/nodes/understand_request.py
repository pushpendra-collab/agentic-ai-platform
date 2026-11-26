"""understand_request: turn the latest user message into a `RequestUnderstanding`."""

from __future__ import annotations

import json
from typing import Any

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node, to_chat_messages
from agentic_platform.agents.routing import describe_unavailable_for_llm
from agentic_platform.agents.schemas import RequestUnderstanding
from agentic_platform.agents.state import AgentState
from agentic_platform.business.focus import BusinessFocus
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.core.logging import get_logger
from agentic_platform.security.context import require_principal
from agentic_platform.tools.business import BUSINESS_DOMAIN, tool_call_for

logger = get_logger(__name__)

DEFAULT_CLARIFICATION = "Could you tell me a bit more about what you need?"


def make_understand_request(deps: AgentDependencies) -> NodeFn:
    @instrumented_node("understand_request")
    async def understand_request(state: AgentState) -> dict[str, Any]:
        # Only tools the authenticated user may run are shown to the model (defence in
        # depth: the registry checks the permission again before anything runs), grouped
        # by the domain whose handler may run them (Phase 10: `general`, `business`).
        principal = require_principal()
        sections: list[str] = []
        for key in deps.domains.keys():  # noqa: SIM118 - DomainRegistry, not a dict
            handler = deps.domains.get(key)
            specs = deps.tools.specs(handler.tool_domains, principal=principal) if handler else []
            lines = "\n".join(spec.describe_for_llm() for spec in specs) or "- (none)"
            sections.append(f"Tools of the `{key}` domain:\n{lines}")
        system = deps.prompts.render(
            "understand_request.system",
            domains=deps.domains.describe_for_llm(),
            tools="\n\n".join(sections) or "- (none)",
        )
        # Phase 12 (ADR-061): the Supervisor's routing rules and the domains that do not
        # exist yet, so a request needing one is named in `routing`, never routed to it.
        system += "\n\n" + deps.prompts.render(
            "supervisor.routing",
            unavailable_domains=describe_unavailable_for_llm(set(deps.domains.keys())),
        )
        # Phase 11: the conversation's business focus (references only, no figures), so
        # a follow-up ("usme se kitna overdue hai?") can name the exact reference.
        focus = BusinessFocus.load(state.get("business_focus")).to_state()
        if focus and deps.domains.get(BUSINESS_DOMAIN) is not None:
            system += "\n\n" + deps.prompts.render(
                "understand_request.focus",
                focus_json=json.dumps(focus, indent=2, ensure_ascii=False),
            )
        history = to_chat_messages(state["messages"], deps.settings.agent.max_history_messages)
        result = await deps.llm.generate_structured(
            system=system,
            messages=history,
            output_model=RequestUnderstanding,
            effort="low",
        )
        understanding = _normalise(result.output)
        fields: dict[str, Any] = {
            "intent": understanding.intent,
            "domain": understanding.domain,
            "needs_clarification": understanding.needs_clarification,
            "planned_tool_calls": len(understanding.tool_calls),
        }
        if understanding.routing is not None:
            fields["routing_unavailable"] = len(understanding.routing.unavailable)
        if understanding.business_query is not None:
            # Metadata only: capability, metric, period token, flags. Never names or refs.
            fields["business_query"] = understanding.business_query.safe_log_fields()
        logger.info("request.understood", extra={"fields": fields})
        return {"understanding": understanding}

    return understand_request


def _names_an_entity(query: BusinessQueryIntent) -> bool:
    """The user named the record the read is about (a godown, customer, product or bill).
    Only then is "which one?" the ERP's to decide; a question about what the user meant
    ("total customers, or those who came today?") stays the planner's."""
    return any(
        ref is not None
        for ref in (query.customer_reference, query.product_reference, query.warehouse_reference)
    ) or bool(query.invoice_number)


def _normalise(understanding: RequestUnderstanding) -> RequestUnderstanding:
    """Repair internally inconsistent model output instead of failing the turn."""
    query = understanding.business_query
    if (
        understanding.needs_clarification
        and understanding.domain.strip().lower() == BUSINESS_DOMAIN
        and query is not None
        and _names_an_entity(query)
        and tool_call_for(query) is not None
    ):
        # The typed read already names everything its tool needs ("Main Godown mein kya
        # stock hai?"). The ERP decides whether the name is ambiguous (AMBIGUOUS with
        # candidates), deterministically, instead of the model asking on a guess.
        understanding = understanding.model_copy(
            update={"needs_clarification": False, "clarification_question": None}
        )
    question = (understanding.clarification_question or "").strip()
    if understanding.needs_clarification and not question:
        return understanding.model_copy(update={"clarification_question": DEFAULT_CLARIFICATION})
    if not understanding.needs_clarification and question:
        return understanding.model_copy(update={"clarification_question": None})
    return understanding
