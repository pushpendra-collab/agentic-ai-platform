"""Phase 7: LLM failure handling through the REAL Anthropic SDK and HTTP stack.

`AnthropicProvider` talks to a local stub server (`LLM_ANTHROPIC_BASE_URL`), so the
SDK's own request building, response parsing, error classes, timeouts and retries
run unmodified. Each provider failure must become a controlled platform error:
the right code and HTTP status, no API key, prompt or provider message in the
error, the logs or the client response, and no stack trace for the client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import BaseModel

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, AuthSettings, LLMSettings
from agentic_platform.core.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMTransientError,
)
from agentic_platform.llm.anthropic_provider import AnthropicProvider
from agentic_platform.llm.base import ChatMessage
from tests.api_helpers import TOKEN_SECRET, api_client, bearer, token

API_KEY = "sk-ant-api03-PHASE7-NOT-A-REAL-KEY-0123456789"
PROMPT_SECRET = "PROMPT-CANARY-7f3a"  # appears in the prompt and in echoed provider text


def _message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 5},
    }


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


class StubAnthropic:
    """Minimal Messages API stand-in. `mode` selects the behaviour of every POST."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.text = "Hello from the stub."
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output quiet
                pass

            def do_POST(self) -> None:  # http.server API
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items()}
                stub.requests.append({"path": self.path, "headers": headers, "body": body})
                status, payload, headers = stub.respond()
                if status == 0:  # hang (timeout test)
                    time.sleep(3)
                    return
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("request-id", "req_stub_123")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def respond(self) -> tuple[int, Any, dict[str, str]]:
        mode = self.mode
        if mode == "ok":
            return 200, _message(self.text), {}
        if mode == "auth":
            return 401, _error("authentication_error", "invalid x-api-key"), {}
        if mode == "permission":
            return 403, _error("permission_error", "key lacks access"), {}
        if mode == "rate_limit":
            return 429, _error("rate_limit_error", "slow down"), {"retry-after": "0"}
        if mode == "overloaded":
            return 529, _error("overloaded_error", "overloaded"), {}
        if mode == "server":
            return 500, _error("api_error", "internal"), {}
        if mode == "bad_request":
            # Providers can echo request content in error messages.
            return 400, _error("invalid_request_error", f"bad field near {PROMPT_SECRET}"), {}
        if mode == "not_json":
            return 200, b"<html>gateway error</html>", {}
        if mode == "wrong_shape":
            return 200, {"unexpected": True}, {}
        if mode == "refusal":
            return 200, _message("", stop_reason="refusal"), {}
        if mode == "truncated":
            return 200, _message("partial", stop_reason="max_tokens"), {}
        if mode == "bad_structured":
            return 200, _message(f'{{"wrong": "{PROMPT_SECRET}"}}'), {}
        if mode == "hang":
            return 0, b"", {}
        raise AssertionError(mode)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub() -> Iterator[StubAnthropic]:
    server = StubAnthropic()
    try:
        yield server
    finally:
        server.close()


def llm_settings(base_url: str, **overrides: Any) -> LLMSettings:
    values: dict[str, Any] = {
        "anthropic_api_key": API_KEY,
        "anthropic_base_url": base_url,
        "max_retries": 0,
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return LLMSettings(**values)


def provider(base_url: str, **overrides: Any) -> AnthropicProvider:
    return AnthropicProvider(llm_settings(base_url, **overrides))


class Answer(BaseModel):
    answer: str


async def _text(p: AnthropicProvider) -> Any:
    return await p.generate_text(
        system="system", messages=[ChatMessage(role="user", content=f"hi {PROMPT_SECRET}")]
    )


async def _structured(p: AnthropicProvider) -> Any:
    return await p.generate_structured(
        system="system",
        messages=[ChatMessage(role="user", content=f"hi {PROMPT_SECRET}")],
        output_model=Answer,
    )


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# --- provider level ---------------------------------------------------------------------------


def test_success_sends_the_configured_request(stub: StubAnthropic) -> None:
    result = asyncio.run(_text(provider(stub.url)))
    assert result.text == "Hello from the stub."
    assert result.meta.provider_request_id == "req_stub_123"
    sent = stub.requests[0]
    assert sent["path"].startswith("/v1/messages")
    assert sent["headers"]["x-api-key"] == API_KEY  # the key goes to the provider only
    assert sent["body"]["model"] == "claude-opus-5-5"
    assert sent["body"]["output_config"] == {"effort": "medium"}
    assert sent["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent["headers"]["anthropic-beta"]


@pytest.mark.parametrize(
    ("mode", "expected", "retryable"),
    [
        ("auth", LLMAuthError, False),
        ("permission", LLMAuthError, False),
        ("rate_limit", LLMTransientError, True),
        ("overloaded", LLMTransientError, True),
        ("server", LLMTransientError, True),
        ("bad_request", LLMError, False),
        ("not_json", LLMOutputError, False),
        ("wrong_shape", LLMOutputError, False),
        ("refusal", LLMError, False),
        ("truncated", LLMOutputError, False),
    ],
)
def test_provider_failures_become_controlled_errors(
    stub: StubAnthropic,
    caplog: pytest.LogCaptureFixture,
    mode: str,
    expected: type[LLMError],
    retryable: bool,
) -> None:
    stub.mode = mode
    caplog.set_level(logging.DEBUG, logger="agentic_platform")
    with pytest.raises(expected) as info:
        asyncio.run(_text(provider(stub.url)))
    assert info.value.retryable is retryable
    for text in (info.value.detail, info.value.public_message, caplog.text):
        assert API_KEY not in text
        assert PROMPT_SECRET not in text
    assert "Traceback" not in info.value.public_message


def test_structured_output_parse_failure_does_not_leak_model_output(
    stub: StubAnthropic, caplog: pytest.LogCaptureFixture
) -> None:
    stub.mode = "bad_structured"
    caplog.set_level(logging.DEBUG, logger="agentic_platform")
    with pytest.raises(LLMOutputError) as info:
        asyncio.run(_structured(provider(stub.url)))
    assert PROMPT_SECRET not in info.value.detail
    assert PROMPT_SECRET not in caplog.text


def test_timeout_and_unreachable_provider_are_transient(stub: StubAnthropic) -> None:
    stub.mode = "hang"
    started = time.perf_counter()
    with pytest.raises(LLMTransientError, match="APITimeoutError"):
        asyncio.run(_text(provider(stub.url, timeout_seconds=0.5)))
    assert time.perf_counter() - started < 3  # the timeout, not the server, ended it
    with pytest.raises(LLMTransientError, match="APIConnectionError"):
        asyncio.run(_text(provider(f"http://127.0.0.1:{_closed_port()}")))


def test_sdk_retries_transient_errors_within_the_budget(stub: StubAnthropic) -> None:
    stub.mode = "rate_limit"
    with pytest.raises(LLMTransientError):
        asyncio.run(_text(provider(stub.url, max_retries=2)))
    assert len(stub.requests) == 3  # first try + LLM_MAX_RETRIES


def test_failure_log_carries_the_provider_request_id(
    stub: StubAnthropic, caplog: pytest.LogCaptureFixture
) -> None:
    stub.mode = "server"
    caplog.set_level(logging.WARNING, logger="agentic_platform")
    with pytest.raises(LLMTransientError):
        asyncio.run(_text(provider(stub.url)))
    record = next(r for r in caplog.records if r.getMessage() == "llm.call.failed")
    fields = record.fields  # type: ignore[attr-defined]
    assert fields["status_code"] == 500
    assert fields["provider_request_id"] == "req_stub_123"
    assert fields["code"] == "LLM_UNAVAILABLE"


# --- through the HTTP API ----------------------------------------------------------------------


def _api_settings(stub: StubAnthropic) -> AppSettings:
    return AppSettings(
        env="test",
        llm=llm_settings(stub.url),
        auth=AuthSettings(token_secret=TOKEN_SECRET),
    )


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        ("auth", 502, "LLM_AUTH_ERROR"),
        ("rate_limit", 503, "LLM_UNAVAILABLE"),
        ("server", 503, "LLM_UNAVAILABLE"),
        ("not_json", 502, "LLM_OUTPUT_ERROR"),
        ("bad_request", 502, "LLM_ERROR"),
    ],
)
def test_api_turns_return_controlled_errors(
    stub: StubAnthropic,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    mode: str,
    status: int,
    code: str,
) -> None:
    stub.mode = mode
    settings = _api_settings(stub)
    agent = Agent(build_dependencies(settings))
    caplog.set_level(logging.DEBUG, logger="agentic_platform")
    with api_client(settings, agent) as client:
        bearer_token = token(agent, "acme", "asha")
        response = client.post(
            "/api/v1/agent/turn",
            headers=bearer(bearer_token),
            json={"message": f"What time is it? {PROMPT_SECRET}"},
        )
        assert response.status_code == status, response.text
        body = response.json()
        assert body["error"]["code"] == code
        assert body["request_id"] == response.headers["x-request-id"]
        if status == 503:
            assert response.headers.get("retry-after")
        # Nothing internal reaches the client.
        for secret in (API_KEY, PROMPT_SECRET, "Traceback", "anthropic", "req_stub_123"):
            assert secret not in response.text
        # The API is degraded, not down: readiness and other endpoints still work.
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/me", headers=bearer(bearer_token)).status_code == 200
    logs = caplog.text + capsys.readouterr().err
    assert API_KEY not in logs and PROMPT_SECRET not in logs
    assert bearer_token not in logs


def test_api_error_status_for_unknown_errors_is_not_masked(stub: StubAnthropic) -> None:
    """An invalid key is a configuration problem (502, not retryable), not an outage."""
    stub.mode = "auth"
    settings = _api_settings(stub)
    agent = Agent(build_dependencies(settings))
    with api_client(settings, agent) as client:
        response = client.post(
            "/api/v1/agent/turn",
            headers=bearer(token(agent, "acme", "asha")),
            json={"message": "hello"},
        )
    error = response.json()["error"]
    assert (response.status_code, error["retryable"]) == (502, False)
    assert error["message"] == "The assistant is not configured correctly."
