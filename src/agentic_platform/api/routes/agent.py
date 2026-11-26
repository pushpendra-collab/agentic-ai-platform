"""Agent turns over HTTP: the existing `Agent` entry point, unchanged.

    POST /api/v1/agent/turn
      → authenticate (bearer) → Principal
      → Agent.arun(message, principal, thread_id, request_id = HTTP request id)
          ownership + conversation.write (ConversationService), thread lock,
          LangGraph, tool permission/approval boundary, memory, audit (Phase 1-5)
      → 200 finished · 202 paused (approval or question) · 409/5xx failed
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agentic_platform.agents.schemas import AgentResponse
from agentic_platform.api.dependencies import PrincipalDep, RuntimeDep, rate_limit, turn_slot
from agentic_platform.api.errors import ERROR_RESPONSES, request_id_of, status_for_code
from agentic_platform.api.schemas import TurnRequest, TurnResponse
from agentic_platform.services.rate_limit import LimitKind

router = APIRouter(prefix="/agent", tags=["agent"])

TURN_RESPONSES: dict[int | str, dict[str, object]] = {
    202: {"model": TurnResponse, "description": "Paused: approval or an answer is required."},
    409: {"model": TurnResponse, "description": "Conversation busy, or the key was reused."},
    **ERROR_RESPONSES,
}


def turn_status(response: AgentResponse, *, created: bool = False) -> int:
    if response.status == "interrupted":
        return 202
    if response.status == "failed":
        error = response.errors[0] if response.errors else None
        return status_for_code(error.code, error.retryable) if error else 500
    return 201 if created else 200


def turn_json(response: AgentResponse, *, created: bool = False) -> JSONResponse:
    status = turn_status(response, created=created)
    retryable = response.status == "failed" and any(e.retryable for e in response.errors)
    return JSONResponse(
        status_code=status,
        content=TurnResponse.of(response).model_dump(mode="json"),
        headers={"Retry-After": "2"} if retryable else None,
    )


@router.post(
    "/turn",
    response_model=TurnResponse,
    responses=TURN_RESPONSES,
    summary="Send a message to the agent (new or existing conversation).",
    dependencies=[rate_limit(LimitKind.TURN), turn_slot()],
)
async def agent_turn(
    body: TurnRequest, request: Request, principal: PrincipalDep, runtime: RuntimeDep
) -> JSONResponse:
    response = await runtime.agent.arun(
        body.message,
        principal=principal,
        thread_id=body.thread_id,
        request_id=request_id_of(request),
        idempotency_key=body.idempotency_key,
    )
    return turn_json(response)
