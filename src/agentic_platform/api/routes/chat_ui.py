"""Local test chat page (development only, 2026-10-30).

    GET  /chat           the browser page (static HTML, no external resources)
    POST /chat/session   a development token for one of the fixed demo personas

Mounted by `create_app` only when `APP_ENV=development` and development tokens are
configured (`AUTH_TOKEN_SECRET`). `/chat/session` additionally answers only clients on
the loopback address, mints tokens only for the fixed personas below (never caller-chosen
roles) and only for a decimal InvenLogic company id. The page then calls the normal
`/api/v1/agent/turn` with that bearer token, so every request goes through the same
authentication, permission, rate-limit and ERP read path as any other client.
Production never mounts this router, and production refuses development tokens anyway.
"""

from __future__ import annotations

import re
from importlib import resources

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from agentic_platform.api.dependencies import RuntimeDep

# Demo personas: user id and platform roles. Business roles must exist in
# AUTH_ROLE_CATALOG_JSON (the launcher scripts/run_chat_demo.ps1 sets them).
PERSONAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "owner": ("demo-owner", ("member", "sales_viewer", "stock_viewer")),
    "sales": ("demo-sales", ("member", "sales_viewer")),
    "stock": ("demo-stock", ("member", "stock_viewer")),
    "member": ("demo-member", ("member",)),
}
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_COMPANY_ID = re.compile(r"^[1-9][0-9]{0,17}$")
SESSION_TTL_SECONDS = 8 * 3600

router = APIRouter(include_in_schema=False)


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: str = Field(pattern=r"^(owner|sales|stock|member)$")
    company_id: str = Field(pattern=_COMPANY_ID.pattern)


class SessionResponse(BaseModel):
    token: str
    user_id: str
    roles: list[str]
    tenant_id: str
    expires_in: int


@router.get("/chat", response_class=HTMLResponse)
async def chat_page() -> HTMLResponse:
    page = resources.files("agentic_platform.api").joinpath("static/chat.html")
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.post("/chat/session", response_model=SessionResponse)
async def chat_session(
    body: SessionRequest, request: Request, runtime: RuntimeDep
) -> SessionResponse:
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="The test chat works on this computer only.")
    tokens = runtime.agent.dependencies.security.tokens
    if tokens is None:
        raise HTTPException(status_code=503, detail="Development tokens are not configured.")
    user_id, roles = PERSONAS[body.persona]
    token = tokens.issue(
        user_id=user_id,
        tenant_id=body.company_id,
        roles=roles,
        ttl_seconds=SESSION_TTL_SECONDS,
    )
    return SessionResponse(
        token=token,
        user_id=user_id,
        roles=list(roles),
        tenant_id=body.company_id,
        expires_in=SESSION_TTL_SECONDS,
    )


__all__ = ["PERSONAS", "router"]
