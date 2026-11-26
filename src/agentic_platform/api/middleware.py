"""Request context: request id, correlation, body limit, last-resort error handling.

Correlation strategy (ADR-045):

* The server **always generates** the request id (`uuid4().hex`). It is returned in
  the `X-Request-ID` response header and in every error body, bound as the log
  context's `request_id`, and passed to the agent as the turn's `request_id`. The
  turn carries it through LangGraph state, nodes, tool calls, approval requests
  (`approval_request.request_id`) and audit rows.
* A client-supplied `X-Request-ID` is **never reused** as the request id: approvals
  are bound to (thread, request id), so a client choosing ids could make two turns
  share one. A well-formed client value is logged as `client_request_id` only.
* Resuming a paused turn keeps the **turn's original** request id (approval binding);
  the resume call's own HTTP request id is in the response header and logs.

Logging: one `http.request.finished` line per request with method, route path,
status and duration. Never headers (tokens), query strings or bodies.

A pure ASGI middleware (no BaseHTTPMiddleware), so the log context it binds is
visible to the whole request, including the agent turn.

Phase 15 (ADR-064):

* every request is counted in `ApiRuntime.in_flight` until it has finished (or unwound
  after a cancellation), so the shutdown closes pools only after that;
* every response carries `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`
  unless the route set its own: responses hold tenant data, and the API must be safe
  even behind an ingress that adds nothing. The TLS gateway sets the full header set and
  hides these two from the upstream so they are not sent twice (deploy/gateway).
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentic_platform.api.errors import (
    INTERNAL_ERROR_CODE,
    INTERNAL_ERROR_MESSAGE,
    REQUEST_TOO_LARGE_CODE,
    RequestTooLarge,
    error_bytes,
)
from agentic_platform.core.logging import get_logger, log_context
from agentic_platform.services.lifecycle import InFlight

logger = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"
# Phase 15: safe defaults on every response (a route's own value wins).
DEFAULT_HEADERS = ((b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"))
_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


class RequestContextMiddleware:
    def __init__(
        self, app: ASGIApp, *, max_body_bytes: int, in_flight: InFlight | None = None
    ) -> None:
        self.app = app
        self._max_body = max_body_bytes
        self._in_flight = in_flight or InFlight()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        context: dict[str, Any] = {
            "request_id": request_id,
            "http_method": scope.get("method"),
            "http_path": scope.get("path"),
        }
        client_id = headers.get(REQUEST_ID_HEADER, "")
        if _CLIENT_REQUEST_ID.fullmatch(client_id):
            context["client_request_id"] = client_id

        state = {"status": 500, "started": False}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["started"] = True
                state["status"] = message["status"]
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [h for h in DEFAULT_HEADERS if h[0] not in present]
                message["headers"] = [*headers, (b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body:
                    raise RequestTooLarge()
            return message

        started = time.perf_counter()
        self._in_flight.enter()
        with log_context(**context):
            try:
                declared = headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self._max_body:
                    await self._reply(send_with_id, 413, REQUEST_TOO_LARGE_CODE,
                                      "The request body is too large.", request_id)  # fmt: skip
                else:
                    await self.app(scope, limited_receive, send_with_id)
            except Exception:
                logger.exception("http.request.crashed")
                if not state["started"]:
                    await self._reply(
                        send_with_id, 500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE, request_id
                    )
            finally:
                self._in_flight.exit()
                logger.info(
                    "http.request.finished",
                    extra={
                        "fields": {
                            "status": state["status"],
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        }
                    },
                )

    @staticmethod
    async def _reply(send: Send, status: int, code: str, message: str, request_id: str) -> None:
        body = error_bytes(code, message, request_id)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
