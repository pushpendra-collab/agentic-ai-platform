"""generate_response: produce the user-facing reply and the final turn status."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node, to_chat_messages
from agentic_platform.agents.state import AgentState
from agentic_platform.business.composition import compose
from agentic_platform.business.grounding import business_results, is_business_tool
from agentic_platform.core.errors import AgentStateError
from agentic_platform.llm.base import ChatMessage


def make_generate_response(deps: AgentDependencies) -> NodeFn:
    @instrumented_node("generate_response")
    async def generate_response(state: AgentState) -> dict[str, Any]:
        understanding = state.get("understanding")
        if understanding is None:
            raise AgentStateError("generate_response reached without an understanding.")

        # Clarification needs no second model call: the question is already written.
        if understanding.needs_clarification:
            question = understanding.clarification_question or ""
            return {
                "final_response": question,
                "status": "needs_clarification",
                "messages": [AIMessage(content=question)],
            }

        domain_result = state.get("domain_result")
        tool_results = state.get("tool_results", [])
        context: dict[str, Any] = {
            "understanding": {
                "intent": understanding.intent,
                "domain": understanding.domain,
                "summary": understanding.summary,
            },
            "processing": {
                "status": domain_result.status if domain_result else "not_run",
                "domain": domain_result.domain if domain_result else None,
            },
            "tool_results": [
                r.model_dump(exclude={"duration_ms"}, exclude_none=True) for r in tool_results
            ],
        }
        # Phase 11 (ADR-060): business results reach the writer as one composed,
        # deterministic context (figures, records, date range, source, warnings,
        # clarification), not as raw tool payloads.
        query = understanding.business_query
        business = business_results(tool_results)
        if business or (query is not None and query.write_requested):
            context["business"] = compose(business, query).to_prompt()
            context["tool_results"] = [
                r.model_dump(
                    exclude={"duration_ms", "output"} if is_business_tool(r) else {"duration_ms"},
                    exclude_none=True,
                )
                for r in tool_results
            ]
        # Phase 12: parts of the request that need a domain that does not exist yet. They
        # were not carried out; finalize_turn appends a fixed notice saying so.
        routing = understanding.routing
        if routing is not None and routing.unavailable:
            context["not_carried_out"] = [p.model_dump() for p in routing.unavailable]
        if memory := state.get("memory_context"):
            context["memory"] = memory
        history = to_chat_messages(state["messages"], deps.settings.agent.max_history_messages)
        if not history or history[-1].role != "user":
            raise AgentStateError("Conversation does not end with a user message.")
        history[-1] = ChatMessage(
            role="user",
            content=deps.prompts.render(
                "generate_response.context",
                user_input=state["user_input"],
                context_json=json.dumps(context, indent=2, ensure_ascii=False),
            ),
        )

        result = await deps.llm.generate_text(
            system=deps.prompts.render("generate_response.system"),
            messages=history,
        )
        unsupported = domain_result is not None and domain_result.status == "unsupported"
        status = "unsupported" if unsupported else "completed"
        return {
            "final_response": result.text,
            "status": status,
            "messages": [AIMessage(content=result.text)],
        }

    return generate_response
