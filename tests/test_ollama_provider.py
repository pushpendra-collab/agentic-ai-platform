"""OllamaProvider against a mock transport: request shape and error mapping (no network)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from agentic_platform.core.config import LLMSettings
from agentic_platform.core.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMTransientError,
)
from agentic_platform.llm import create_llm_provider
from agentic_platform.llm.base import ChatMessage
from agentic_platform.llm.ollama_provider import OllamaProvider

MESSAGES = [ChatMessage(role="user", content="hi")]


class Out(BaseModel):
    answer: str


def chat_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "qwen3:8b",
        "message": {"role": "assistant", "content": "hello"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    base.update(overrides)
    return base


class Recorder:
    def __init__(self, result: httpx.Response | Exception) -> None:
        self.result = result
        self.payload: dict[str, Any] = {}
        self.path = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.path = request.url.path
        self.payload = json.loads(request.content)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def provider(
    result: httpx.Response | Exception, **settings: Any
) -> tuple[OllamaProvider, Recorder]:
    recorder = Recorder(result)
    client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(recorder)
    )
    llm = LLMSettings(provider="ollama", model="qwen3:8b", **settings)
    return OllamaProvider(llm, client=client), recorder


def ok(**overrides: Any) -> httpx.Response:
    return httpx.Response(200, json=chat_body(**overrides))


def test_text_request_shape_and_result() -> None:
    p, rec = provider(ok())
    result = asyncio.run(p.generate_text(system="sys", messages=MESSAGES, effort="low"))
    assert result.text == "hello"
    assert result.meta.provider == "ollama"
    assert result.meta.model == "qwen3:8b"
    assert result.meta.input_tokens == 10
    assert result.meta.output_tokens == 5
    assert rec.path == "/api/chat"
    assert rec.payload["model"] == "qwen3:8b"
    assert rec.payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert rec.payload["stream"] is False
    assert rec.payload["think"] is False
    assert rec.payload["keep_alive"] == "10m"
    assert rec.payload["options"] == {"num_predict": 16000, "num_ctx": 16384}
    assert "format" not in rec.payload


def test_settings_are_passed_through() -> None:
    p, rec = provider(ok(), ollama_think=True, ollama_num_ctx=8192, ollama_keep_alive="1h")
    asyncio.run(p.generate_text(system="s", messages=MESSAGES, max_tokens=50))
    assert rec.payload["think"] is True
    assert rec.payload["keep_alive"] == "1h"
    assert rec.payload["options"] == {"num_predict": 50, "num_ctx": 8192}


def test_structured_output() -> None:
    p, rec = provider(ok(message={"role": "assistant", "content": '{"answer": "42"}'}))
    result = asyncio.run(p.generate_structured(system="s", messages=MESSAGES, output_model=Out))
    assert result.output == Out(answer="42")
    assert rec.payload["format"] == Out.model_json_schema()


def test_structured_output_not_matching_schema() -> None:
    p, _ = provider(ok(message={"role": "assistant", "content": '{"wrong": 1}'}))
    with pytest.raises(LLMOutputError, match="Out") as info:
        asyncio.run(p.generate_structured(system="s", messages=MESSAGES, output_model=Out))
    assert "wrong" not in str(info.value)  # model output is never quoted


def test_truncation() -> None:
    p, _ = provider(ok(done_reason="length"))
    with pytest.raises(LLMOutputError, match="truncated"):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_empty_text() -> None:
    p, _ = provider(ok(message={"role": "assistant", "content": "  "}))
    with pytest.raises(LLMOutputError):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_malformed_response() -> None:
    p, _ = provider(httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMOutputError, match="Malformed"):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_non_json_body() -> None:
    p, _ = provider(httpx.Response(200, text="not json"))
    with pytest.raises(LLMOutputError, match="Malformed"):
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))


def test_missing_model_names_the_pull_command() -> None:
    p, _ = provider(httpx.Response(404, json={"error": "model 'qwen3:8b' not found"}))
    with pytest.raises(LLMError, match="ollama pull qwen3:8b") as info:
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))
    assert type(info.value) is LLMError


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (httpx.Response(500, json={"error": "boom"}), LLMTransientError),
        (httpx.Response(503, json={"error": "busy"}), LLMTransientError),
        (httpx.Response(429, json={"error": "slow down"}), LLMTransientError),
        (httpx.Response(401, json={"error": "unauthorized"}), LLMAuthError),
        (httpx.Response(400, json={"error": "bad"}), LLMError),
        (httpx.ConnectError("refused"), LLMTransientError),
        (httpx.ReadTimeout("slow"), LLMTransientError),
    ],
)
def test_error_mapping(result: httpx.Response | Exception, expected: type[LLMError]) -> None:
    p, _ = provider(result)
    with pytest.raises(expected) as info:
        asyncio.run(p.generate_text(system="s", messages=MESSAGES))
    assert type(info.value) is expected


def test_provider_factory_ollama() -> None:
    llm = create_llm_provider(LLMSettings(provider="ollama"))
    assert isinstance(llm, OllamaProvider)


def test_env_selects_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
    monkeypatch.setenv("LLM_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("LLM_OLLAMA_THINK", "true")
    s = LLMSettings()
    assert s.provider == "ollama"
    assert s.model == "qwen3:8b"
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert s.ollama_think is True
