"""AnthropicProvider against a stub client: request shape and error mapping (no network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from agentic_platform.core.config import LLMSettings
from agentic_platform.core.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMTransientError,
)
from agentic_platform.llm.anthropic_provider import AnthropicProvider
from agentic_platform.llm.base import ChatMessage

MESSAGES = [ChatMessage(role="user", content="hi")]


class Out(BaseModel):
    answer: str


def response(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "content": [SimpleNamespace(type="text", text="hello")],
        "stop_reason": "end_turn",
        "stop_details": None,
        "model": "claude-opus-5-5",
        "usage": SimpleNamespace(input_tokens=10, output_tokens=5),
        "parsed_output": None,
        "_request_id": "req_123",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class StubClient:
    def __init__(self, result: Any) -> None:
        self.kwargs: dict[str, Any] = {}
        self.result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._call, parse=self._call))

    async def _call(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def provider(result: Any, **settings: Any) -> tuple[AnthropicProvider, StubClient]:
    client = StubClient(result)
    return AnthropicProvider(LLMSettings(**settings), client=client), client


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=request), body=None)


def test_text_request_shape_and_result() -> None:
    p, client = provider(response())
    result = asyncio.run(p.generate_text(system="sys", messages=MESSAGES, effort="low"))
    assert result.text == "hello"
    assert result.meta.provider_request_id == "req_123"
    assert result.meta.input_tokens == 10
    assert client.kwargs["model"] == "claude-opus-5-5"
    assert client.kwargs["system"] == "sys"
    assert client.kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert client.kwargs["output_config"] == {"effort": "low"}
    assert client.kwargs["max_tokens"] == 16000
    assert client.kwargs["fallbacks"] == "default"
    assert client.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in client.kwargs  # Opus 5.5 thinks adaptively by default


def test_fallbacks_can_be_disabled() -> None:
    p, client = provider(response(), anthropic_fallbacks="off", effort="high")
    asyncio.run(p.generate_text(system="s", messages=MESSAGES))
    assert "fallbacks" not in client.kwargs
    assert "betas" not in client.kwargs
    assert client.kwargs["output_config"] == {"effort": "high"}


def test_structured_output() -> None:
    p, client = provider(response(parsed_output=Out(answer="42")))
    result = asyncio.run(p.generate_structured(system="s", messages=MESSAGES, output_model=Out))
    assert result.output == Out(answer="42")
    assert client.kwargs["output_format"] is Out


def test_structured_output_missing_is_error() -> None:
    p, _ = provider(response(parsed_output=None))
    with pytest.raises(LLMOutputError):
        asyncio.run(p.generate_structured(system="s", messages=MESSAGES, output_model=Out))


def test_refusal() -> None:
    p, _ = provider(
        response(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), content=[])
    )
    with pytest.raises(LLMRefusalError):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_truncation() -> None:
    p, _ = provider(response(stop_reason="max_tokens"))
    with pytest.raises(LLMOutputError):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_empty_text() -> None:
    p, _ = provider(response(content=[SimpleNamespace(type="thinking", thinking="")]))
    with pytest.raises(LLMOutputError):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_missing_credentials_is_auth_error() -> None:
    # Message as raised by anthropic 1.11 when no credential source resolves.
    p, _ = provider(TypeError('"Could not resolve authentication method. Expected one of ..."'))
    with pytest.raises(LLMAuthError, match="ANTHROPIC_API_KEY"):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_unrelated_type_error_is_not_masked() -> None:
    p, _ = provider(TypeError("unexpected keyword argument"))
    with pytest.raises(TypeError):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(anthropic.RateLimitError, 429), LLMTransientError),
        (status_error(anthropic.InternalServerError, 500), LLMTransientError),
        (status_error(anthropic.APIStatusError, 529), LLMTransientError),
        (status_error(anthropic.AuthenticationError, 401), LLMAuthError),
        (status_error(anthropic.PermissionDeniedError, 403), LLMAuthError),
        (status_error(anthropic.BadRequestError, 400), LLMError),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            LLMTransientError,
        ),
    ],
)
def test_error_mapping(error: Exception, expected: type[LLMError]) -> None:
    p, _ = provider(error)
    with pytest.raises(expected) as info:
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))
    assert type(info.value) is expected
