"""Conversations of the authenticated user (Phase 5 ownership: owner only, no sharing).

Every handler calls the `Agent` / `ConversationService` / `AuthorizedMemory`,
which check `conversation.*` permissions and ownership. Another user's or
tenant's conversation answers 404 exactly like a missing one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from agentic_platform.api.dependencies import PrincipalDep, RuntimeDep, rate_limit, turn_slot
from agentic_platform.api.errors import ERROR_RESPONSES, request_id_of
from agentic_platform.api.routes.agent import TURN_RESPONSES, turn_json
from agentic_platform.api.schemas import (
    ID_PATTERN,
    ConversationListView,
    ConversationRefView,
    ConversationView,
    ForgetView,
    HistoryView,
    MessageRequest,
    ResumeRequest,
    StartConversationRequest,
    TurnResponse,
    TurnView,
)
from agentic_platform.core.errors import ConversationNotFoundError
from agentic_platform.security.permissions import Permission
from agentic_platform.services.rate_limit import LimitKind

router = APIRouter(prefix="/conversations", tags=["conversations"], responses=ERROR_RESPONSES)

ThreadId = Annotated[str, Path(pattern=ID_PATTERN, description="Conversation id.")]
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get(
    "", response_model=ConversationListView, summary="List my conversations.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def list_conversations(
    principal: PrincipalDep, runtime: RuntimeDep, limit: Limit = 50
) -> ConversationListView:
    refs = await runtime.deps.security.conversations.list_mine(principal, limit=limit)
    return ConversationListView(
        conversations=[ConversationRefView(thread_id=r.thread_id) for r in refs]
    )


@router.post(
    "",
    response_model=TurnResponse,
    status_code=201,
    responses=TURN_RESPONSES,
    summary="Start a conversation with its first message (the server picks the id).",
    dependencies=[rate_limit(LimitKind.TURN), turn_slot()],
)
async def start_conversation(
    body: StartConversationRequest, request: Request, principal: PrincipalDep, runtime: RuntimeDep
) -> JSONResponse:
    response = await runtime.agent.arun(
        body.message, principal=principal, request_id=request_id_of(request)
    )
    return turn_json(response, created=True)


@router.get(
    "/{thread_id}", response_model=ConversationView, summary="Conversation metadata.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def get_conversation(
    thread_id: ThreadId, principal: PrincipalDep, runtime: RuntimeDep
) -> ConversationView:
    status = await runtime.agent.athread_status(thread_id, principal=principal)
    if not status.exists:
        raise ConversationNotFoundError("No visible conversation with this id.")
    return ConversationView.of(status)


@router.post(
    "/{thread_id}/messages",
    response_model=TurnResponse,
    responses=TURN_RESPONSES,
    summary="Continue one of my conversations.",
    dependencies=[rate_limit(LimitKind.TURN), turn_slot()],
)
async def continue_conversation(
    thread_id: ThreadId,
    body: MessageRequest,
    request: Request,
    principal: PrincipalDep,
    runtime: RuntimeDep,
) -> JSONResponse:
    # Continue means "existing and mine" (POST /agent/turn may also start one).
    await runtime.deps.security.conversations.require_access(
        principal, thread_id, Permission.CONVERSATION_WRITE
    )
    response = await runtime.agent.arun(
        body.message,
        principal=principal,
        thread_id=thread_id,
        request_id=request_id_of(request),
        idempotency_key=body.idempotency_key,
    )
    return turn_json(response)


@router.post(
    "/{thread_id}/resume",
    response_model=TurnResponse,
    responses=TURN_RESPONSES,
    summary="Resume my paused or interrupted turn (after an approval decision, or with an "
    "answer).",
    dependencies=[rate_limit(LimitKind.TURN), turn_slot()],
)
async def resume_conversation(
    thread_id: ThreadId,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    body: ResumeRequest | None = None,
) -> JSONResponse:
    response = await runtime.agent.aresume(
        thread_id, principal=principal, resume_value=body.answer if body else None
    )
    return turn_json(response)


@router.get(
    "/{thread_id}/history", response_model=HistoryView, summary="Messages of my conversation.",
    dependencies=[rate_limit(LimitKind.READ)],
)
async def conversation_history(
    thread_id: ThreadId, principal: PrincipalDep, runtime: RuntimeDep, limit: Limit = 20
) -> HistoryView:
    turns = await runtime.memory().history(principal, thread_id, limit=limit)
    return HistoryView(
        thread_id=thread_id, turns=[TurnView(role=t.role, content=t.content) for t in turns]
    )


@router.delete(
    "/{thread_id}",
    response_model=ForgetView,
    summary="Forget my conversation (state, summary, working memory, episodes; open "
    "approvals are cancelled).",
    dependencies=[rate_limit(LimitKind.WRITE)],
)
async def forget_conversation(
    thread_id: ThreadId, principal: PrincipalDep, runtime: RuntimeDep
) -> ForgetView:
    counts = await runtime.agent.aforget_conversation(thread_id, principal=principal)
    return ForgetView(thread_id=thread_id, deleted=counts)
