"""Ollama (local models) adapter for `LLMProvider`.

Talks to Ollama's native `/api/chat` endpoint over `httpx` (no SDK). Structured output
passes the Pydantic model's JSON schema as `format`, which Ollama enforces with a
grammar, and the reply is validated against the model again here.

`effort` has no Ollama equivalent and is ignored; `LLM_OLLAMA_THINK` controls thinking
instead. Connection errors are retried `LLM_MAX_RETRIES` times by the transport.

Failures become the `LLMError` hierarchy with a controlled `detail` (exception type,
HTTP status), never the prompt or the model output, as in the Anthropic adapter. Each
failure is logged once as `llm.call.failed`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from agentic_platform.core.config import Effort, LLMSettings
from agentic_platform.core.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMTransientError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResultMeta,
    StructuredResult,
    TextResult,
)

T = TypeVar("T", bound=BaseModel)

logger = get_logger(__name__)

_CHAT_PATH = "/api/chat"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: LLMSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        if client is None:
            client = httpx.AsyncClient(
                base_url=settings.ollama_base_url.rstrip("/"),
                timeout=settings.timeout_seconds,
                transport=httpx.AsyncHTTPTransport(retries=settings.max_retries),
            )
        self._client = client

    # -- public API ---------------------------------------------------------------

    async def generate_text(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> TextResult:
        body = await self._chat(self._request_body(system, messages, max_tokens))
        text = _content(body).strip()
        if not text:
            raise LLMOutputError("Model returned no text content.")
        return TextResult(text=text, meta=self._meta(body))

    async def generate_structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        output_model: type[T],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> StructuredResult[T]:
        payload = self._request_body(system, messages, max_tokens)
        payload["format"] = output_model.model_json_schema()
        body = await self._chat(payload)
        try:
            parsed = output_model.model_validate_json(_content(body))
        except ValidationError:
            # The validation message quotes the model output: only the schema name is kept.
            raise LLMOutputError(
                f"Model output did not match schema {output_model.__name__}."
            ) from None
        return StructuredResult(output=parsed, meta=self._meta(body))

    # -- internals ----------------------------------------------------------------

    def _request_body(
        self, system: str, messages: Sequence[ChatMessage], max_tokens: int | None
    ) -> dict[str, Any]:
        return {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system},
                *(m.model_dump() for m in messages),
            ],
            "stream": False,
            "think": self._settings.ollama_think,
            "keep_alive": self._settings.ollama_keep_alive,
            "options": {
                "num_predict": max_tokens or self._settings.max_tokens,
                "num_ctx": self._settings.ollama_num_ctx,
            },
        }

    async def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(_CHAT_PATH, json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            error = self._translate(exc)
            if error is None:
                raise
            logger.warning(
                "llm.call.failed",
                extra={
                    "fields": {
                        "provider": self.name,
                        "model": self._settings.model,
                        "code": error.code,
                        "retryable": error.retryable,
                        "error_type": type(exc).__name__,
                        "status_code": _status(exc),
                    }
                },
            )
            # Not chained: the original message can quote request or response content.
            raise error from None
        if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
            raise LLMOutputError("Malformed provider response.")
        if body.get("done_reason") == "length":
            raise LLMOutputError("Model output was truncated at max_tokens.")
        return body

    def _translate(self, exc: Exception) -> LLMError | None:
        """httpx exception → platform error with a controlled detail (no response body)."""
        if isinstance(exc, httpx.TimeoutException):
            return LLMTransientError(f"Ollama timed out: {type(exc).__name__}")
        if isinstance(exc, httpx.TransportError):
            return LLMTransientError(
                f"Ollama connection problem: {type(exc).__name__} "
                f"(is Ollama running at {self._settings.ollama_base_url}?)"
            )
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403):
                return LLMAuthError(f"Ollama auth failed: {code}")
            if code == 404:
                return LLMError(
                    f"Ollama model '{self._settings.model}' not found: "
                    f"run `ollama pull {self._settings.model}`."
                )
            if code >= 500 or code in (408, 429):
                return LLMTransientError(f"Ollama server error {code}.")
            return LLMError(f"Ollama request rejected ({code}).")
        if isinstance(exc, ValueError):
            # A body that is not JSON (JSONDecodeError).
            return LLMOutputError(f"Malformed provider response: {type(exc).__name__}.")
        return None

    def _meta(self, body: dict[str, Any]) -> LLMResultMeta:
        meta = LLMResultMeta(
            provider=self.name,
            model=str(body.get("model") or self._settings.model),
            stop_reason=body.get("done_reason"),
            input_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
        )
        logger.info(
            "llm.call.completed",
            extra={"fields": meta.model_dump(exclude_none=True)},
        )
        return meta


def _content(body: dict[str, Any]) -> str:
    content = body["message"].get("content")
    if not isinstance(content, str):
        raise LLMOutputError("Malformed provider response.")
    return content


def _status(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


__all__ = ["OllamaProvider"]
