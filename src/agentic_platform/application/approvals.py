"""Approval workflow for API callers (Phase 6): decide, then continue the paused turn.

Composes two Phase 5 entry points and adds no rule of its own:

    ApprovalService.decide(approver)      permission, self-approval policy, expiry,
          │                               compare-and-set, audit (unchanged)
          ▼
    Agent.aresume(thread, requester)      ownership, turn actor, approval re-validation,
                                          requester's permission re-check, execute at
                                          most once, audit (unchanged, approval_gate)

A paused turn can only be resumed by **the user who started it** (Phase 5 rule:
the resuming principal must be the turn's actor). So:

* the requester deciding their own request (where the policy allows self-approval)
  may ask to resume in the same call;
* any other approver only records the decision; the requester then resumes their
  conversation (`POST /api/v1/conversations/{id}/resume`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from agentic_platform.agents.agent import Agent
from agentic_platform.agents.schemas import AgentResponse
from agentic_platform.core.errors import AgentInputError, SecurityError
from agentic_platform.security.identity import Principal
from agentic_platform.services.approvals import ApprovalRecord, ApprovalService

# none: nothing further to do · resume_conversation: the requester should resume
# their conversation now · resumed: the paused turn was resumed in this call.
NextAction = Literal["none", "resume_conversation", "resumed"]


@dataclass(frozen=True)
class ApprovalDecision:
    record: ApprovalRecord
    next_action: NextAction
    turn: AgentResponse | None = None
    # Why the requested resume did not happen (the decision itself was recorded).
    resume_error: AgentInputError | SecurityError | None = None


class ApprovalWorkflow:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    @property
    def approvals(self) -> ApprovalService:
        return self._agent.dependencies.security.approvals

    async def list_open(self, principal: Principal, *, limit: int = 50) -> list[ApprovalRecord]:
        return await self.approvals.list_open(principal, limit=limit)

    async def get(self, principal: Principal, approval_id: str | uuid.UUID) -> ApprovalRecord:
        return await self.approvals.get(principal, approval_id)

    async def cancel(self, principal: Principal, approval_id: str | uuid.UUID) -> ApprovalRecord:
        return await self.approvals.cancel(principal, approval_id)

    async def decide(
        self,
        principal: Principal,
        approval_id: str | uuid.UUID,
        *,
        approve: bool,
        note: str | None = None,
        resume: bool = False,
    ) -> ApprovalDecision:
        record = await self.approvals.decide(principal, approval_id, approve=approve, note=note)
        if record.requester_id != principal.user_id or not resume:
            return ApprovalDecision(record, "resume_conversation")
        try:
            turn = await self._agent.aresume(record.thread_id, principal=principal)
        except (AgentInputError, SecurityError) as exc:
            # The decision stands; the conversation was forgotten, is no longer paused
            # at this approval, or was resumed concurrently. Report, do not undo.
            return ApprovalDecision(record, "none", resume_error=exc)
        return ApprovalDecision(record, "resumed", turn=turn)
