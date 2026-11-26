"""The principal of the request being processed, bound for its duration.

`Agent` binds the authenticated principal with `acting_as(principal)` around a
turn. Graph nodes, tool handlers and the checkpoint saver read it with
`require_principal()` / `current_principal()` (contextvars, so it follows the
asyncio tasks LangGraph creates and never leaks between concurrent requests).

It is set only from an authenticated `Principal`; graph state and LLM output
can never change it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from agentic_platform.core.errors import UnauthenticatedError
from agentic_platform.core.logging import log_context
from agentic_platform.security.identity import Principal

_current: ContextVar[Principal | None] = ContextVar("principal", default=None)


@contextmanager
def acting_as(principal: Principal) -> Iterator[Principal]:
    token = _current.set(principal)
    try:
        with log_context(**principal.log_fields()):
            yield principal
    finally:
        _current.reset(token)


def current_principal() -> Principal | None:
    return _current.get()


def require_principal() -> Principal:
    principal = _current.get()
    if principal is None:
        raise UnauthenticatedError("No authenticated principal is bound to this request.")
    return principal
