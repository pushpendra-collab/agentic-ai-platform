"""Approvals (HITL) over HTTP: the Phase 5 `ApprovalService`, unchanged.

    POST /api/v1/approvals/{id}/approve | reject
      → authenticate approver → ApprovalService.decide
          approver permission, self-approval policy, expiry, compare-and-set, audit
      → requester + "resume": true → Agent.aresume (approval_gate re-validates the
        approval, re-checks the requester's permission, executes at most once)
      → otherwise next_action = "resume_conversation": the requester resumes with
        POST /api/v1/conversations/{thread_id}/resume

Visibility: the requester and users who may decide a request see it; anyone
else (other users without the approver permission, other tenants) gets 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentic_platform.api.dependencies import PrincipalDep, RuntimeDep, rate_limit, turn_slot
from agentic_platform.api.errors import ERROR_RESPONSES
from agentic_platform.api.schemas import (
    ApprovalListView,
    ApprovalView,
    DecisionRequest,
    DecisionView,
    ErrorInfo,
    TurnResponse,
)
from agentic_platform.security.identity import Principal
from agentic_platform.services.rate_limit import LimitKind

router = APIRouter(prefix="/approvals", tags=["approvals"], responses=ERROR_RESPONSES)

ApprovalId = Annotated[str, Path(min_length=1, max_length=64, description="Approval id.")]

_STATE_RESPONSES: dict[int | str, dict[str, object]] = {
    409: {"description": "Already decided, pending, or otherwise not in a usable state."},
    410: {"description": "The approval request expired."},
}


@router.get(
    "", response_model=ApprovalListView, summary="Open approvals I requested or may decide.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def list_approvals(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApprovalListView:
    records = await runtime.approvals.list_open(principal, limit=limit)
    return ApprovalListView(approvals=[ApprovalView.of(r, principal) for r in records])


@router.get(
    "/{approval_id}", response_model=ApprovalView, summary="One approval request.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def get_approval(
    approval_id: ApprovalId, principal: PrincipalDep, runtime: RuntimeDep
) -> ApprovalView:
    return ApprovalView.of(await runtime.approvals.get(principal, approval_id), principal)


async def _decide(
    principal: Principal,
    runtime: RuntimeDep,
    approval_id: str,
    body: DecisionRequest | None,
    *,
    approve: bool,
) -> DecisionView:
    body = body or DecisionRequest()
    decision = await runtime.approvals.decide(
        principal, approval_id, approve=approve, note=body.note, resume=body.resume
    )
    error = decision.resume_error
    return DecisionView(
        approval=ApprovalView.of(decision.record, principal),
        next_action=decision.next_action,
        turn=TurnResponse.of(decision.turn) if decision.turn else None,
        resume_error=ErrorInfo(code=error.code, message=error.public_message) if error else None,
    )


@router.post(
    "/{approval_id}/approve",
    response_model=DecisionView,
    responses=_STATE_RESPONSES,
    summary="Approve a pending request (needs the approver permission).",
    # Phase 15: with "resume": true the decision runs the paused turn, so it holds a slot.
    dependencies=[rate_limit(LimitKind.APPROVAL_DECISION), turn_slot()],
)
async def approve(
    approval_id: ApprovalId,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    body: DecisionRequest | None = None,
) -> DecisionView:
    return await _decide(principal, runtime, approval_id, body, approve=True)


@router.post(
    "/{approval_id}/reject",
    response_model=DecisionView,
    responses=_STATE_RESPONSES,
    summary="Reject a pending request (needs the approver permission).",
    # Phase 15: with "resume": true the decision runs the paused turn, so it holds a slot.
    dependencies=[rate_limit(LimitKind.APPROVAL_DECISION), turn_slot()],
)
async def reject(
    approval_id: ApprovalId,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    body: DecisionRequest | None = None,
) -> DecisionView:
    return await _decide(principal, runtime, approval_id, body, approve=False)


@router.post(
    "/{approval_id}/cancel",
    response_model=ApprovalView,
    responses=_STATE_RESPONSES,
    summary="Withdraw my own pending request.",
    dependencies=[rate_limit(LimitKind.APPROVAL_DECISION)],
)
async def cancel(
    approval_id: ApprovalId, principal: PrincipalDep, runtime: RuntimeDep
) -> ApprovalView:
    return ApprovalView.of(await runtime.approvals.cancel(principal, approval_id), principal)
