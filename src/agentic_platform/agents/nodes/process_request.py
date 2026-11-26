"""process_request: the domain router. Runs the domain the Supervisor chose (Phase 12).

It dispatches on the `SupervisorDecision` in state, never on the planner's label:

* ROUTE: the registered handler for `decision.domain` runs, once
  (`MAX_DOMAIN_RUNS_PER_TURN`), within `AGENT_DOMAIN_TIMEOUT_SECONDS`.
* UNSUPPORTED: no handler runs; a `DomainResult(status="unsupported")` lets the reply say
  so honestly.
* No decision, an invalid one, a second run, or any other outcome: `OrchestrationError`
  (a wiring bug or tampered state). The turn fails in a controlled way.

The handler acts as the authenticated principal bound to the request
(`security.require_principal`), never as an identity named in the request or the
decision. A protected tool call leaves a `pending_action`, which routes to `approval_gate`.
"""

from __future__ import annotations

from typing import Any

from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.agents.domains.base import DomainRequest
from agentic_platform.agents.nodes._common import NodeFn, instrumented_node
from agentic_platform.agents.schemas import DomainResult
from agentic_platform.agents.state import AgentState
from agentic_platform.agents.supervisor import (
    MAX_DOMAIN_RUNS_PER_TURN,
    SupervisorOutcome,
    load_decision,
)
from agentic_platform.core.errors import (
    AgentStateError,
    DomainTimeoutError,
    OrchestrationError,
    OrchestrationLimitError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.observability.deadline import strict_deadline
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import span
from agentic_platform.observability.taxonomy import ErrorCategory
from agentic_platform.security.context import require_principal

logger = get_logger(__name__)


def make_process_request(deps: AgentDependencies) -> NodeFn:
    @instrumented_node("process_request")
    async def process_request(state: AgentState) -> dict[str, Any]:
        understanding = state.get("understanding")
        if understanding is None:
            raise AgentStateError("process_request reached without an understanding.")
        orchestration = dict(state.get("orchestration") or {})
        decision = load_decision(orchestration)
        runs = int(orchestration.get("domain_runs", 0))
        if runs >= MAX_DOMAIN_RUNS_PER_TURN:
            raise OrchestrationLimitError(
                f"Domain run {runs + 1} in one turn (limit {MAX_DOMAIN_RUNS_PER_TURN})."
            )

        if decision.outcome is SupervisorOutcome.UNSUPPORTED:
            logger.info("domain.unsupported", extra={"fields": {"domain": understanding.domain}})
            result = DomainResult(
                domain=understanding.domain, status="unsupported", outcome="UNSUPPORTED"
            )
        elif decision.outcome is SupervisorOutcome.ROUTE and decision.domain is not None:
            handler = deps.domains.get(decision.domain)
            if handler is None:
                raise OrchestrationError(f"Routed domain '{decision.domain}' is not registered.")
            request = DomainRequest(
                request_id=state["request_id"],
                thread_id=state["thread_id"],
                user_input=state["user_input"],
                understanding=understanding,
                principal=require_principal(),
            )
            seconds = deps.settings.agent.domain_timeout_seconds
            with span(
                Stage.DOMAIN,
                started=EventName.DOMAIN_RUN_STARTED,
                completed=EventName.DOMAIN_RUN_COMPLETED,
                failed=EventName.DOMAIN_RUN_FAILED,
                domain=decision.domain,
            ) as run:
                try:
                    # Phase 13 (ADR-062): a strict deadline. A handler that swallows the
                    # cancellation and returns anyway still ends in DOMAIN_TIMEOUT: a
                    # timed-out domain can never produce an apparently successful result.
                    async with strict_deadline(seconds, scope="domain", domain=decision.domain):
                        result = await handler.handle(request)
                except TimeoutError:
                    run.fail(
                        code=DomainTimeoutError.code,
                        category=ErrorCategory.TIMEOUT,
                        status="timeout",
                        retryable=True,
                    )
                    raise DomainTimeoutError(
                        f"Domain '{decision.domain}' exceeded {seconds} s."
                    ) from None
                run.set(
                    outcome=result.outcome,
                    next_action=result.next_action,
                    tool_calls=len(result.tool_results),
                    failed_calls=len(result.errors),
                )
                if result.outcome in ("DEPENDENCY_ERROR", "FAILED"):
                    run.fail(code=result.outcome, category=ErrorCategory.DOMAIN)
        else:
            raise OrchestrationError(
                f"process_request reached with outcome {decision.outcome.value}."
            )
        orchestration["domain_runs"] = runs + 1
        return {
            "domain_result": result,
            "tool_results": result.tool_results,
            "pending_action": result.pending_action,
            "orchestration": orchestration,
        }

    return process_request
