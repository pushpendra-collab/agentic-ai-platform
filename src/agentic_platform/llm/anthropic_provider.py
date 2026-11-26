"""Anthropic (Claude) adapter for `LLMProvider`.

Uses the official `anthropic` SDK. Structured output uses `messages.parse` with a
Pydantic `output_format`. Requests go through the beta namespace only to opt
into the server-side refusal fallback (`fallbacks="default"`), which can be
turned off with `LLM_ANTHROPIC_FALLBACKS=off`.

Failures (Phase 7) become the `LLMError` hierarchy with a controlled `detail`: the
exception type, HTTP status and provider request id only. Never the API key, the
prompt, the model output (an SDK parse error can quote it) or a provider stack
trace. Each failure is logged once as `llm.call.failed`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from agentic_platform.core.config import Effort, LLMSettings
from agentic_platform.core.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
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

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: LLMSettings, client: Any | None = None) -> None:
        self._settings = settings
        if client is None:
            api_key = (
                settings.anthropic_api_key.get_secret_value()
                if settings.anthropic_api_key
                else None
            )
            # With api_key=None the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN /
            # an `ant auth login` profile on its own.
            client = anthropic.AsyncAnthropic(
                api_key=api_key,
                base_url=settings.anthropic_base_url,
                timeout=settings.timeout_seconds,
                max_retries=settings.max_retries,
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
        response = await self._call(
            self._client.beta.messages.create,
            self._request_kwargs(system, messages, max_tokens, effort),
        )
        self._check_stop_reason(response)
        try:
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
        except (AttributeError, TypeError) as exc:  # a response without the Messages shape
            raise LLMOutputError("Malformed provider response.") from exc
        if not text:
            raise LLMOutputError("Model returned no text content.")
        return TextResult(text=text, meta=self._meta(response))

    async def generate_structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        output_model: type[T],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> StructuredResult[T]:
        kwargs = self._request_kwargs(system, messages, max_tokens, effort)
        kwargs["output_format"] = output_model
        response = await self._call(self._client.beta.messages.parse, kwargs)
        self._check_stop_reason(response)
        parsed = response.parsed_output
        if not isinstance(parsed, output_model):
            raise LLMOutputError(
                f"Model output did not match schema {output_model.__name__}."
            )
        return StructuredResult(output=parsed, meta=self._meta(response))

    # -- internals ----------------------------------------------------------------

    def _request_kwargs(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        max_tokens: int | None,
        effort: Effort | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": max_tokens or self._settings.max_tokens,
            "system": system,
            "messages": [m.model_dump() for m in messages],
            "output_config": {"effort": effort or self._settings.effort},
        }
        if self._settings.anthropic_fallbacks == "default":
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def _call(self, method: Any, kwargs: dict[str, Any]) -> Any:
        try:
            return await method(**kwargs)
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
                        "status_code": getattr(exc, "status_code", None),
                        "provider_request_id": getattr(exc, "request_id", None),
                    }
                },
            )
            # Not chained: the original message can quote model output or request content.
            raise error from None

    @staticmethod
    def _translate(exc: Exception) -> LLMError | None:
        """SDK exception → platform error with a controlled detail (no provider payload)."""
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return LLMAuthError(f"Anthropic auth failed: {exc.status_code}")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMTransientError("Anthropic rate limit exceeded.")
        if isinstance(exc, anthropic.APITimeoutError | anthropic.APIConnectionError):
            return LLMTransientError(f"Anthropic connection problem: {type(exc).__name__}")
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code >= 500 or exc.status_code in (408, 409, 529):
                return LLMTransientError(f"Anthropic server error {exc.status_code}.")
            # The status and the provider's error type are enough to diagnose; the
            # provider message can echo request content, so it is not kept.
            return LLMError(
                f"Anthropic request rejected ({exc.status_code}, {_error_type(exc)})."
            )
        if isinstance(exc, anthropic.AnthropicError):
            # Includes SDK-side parse/validation failures of the structured output,
            # whose message can quote the model output: only the type is kept.
            return LLMOutputError(f"Anthropic SDK error: {type(exc).__name__}.")
        if isinstance(exc, ValueError):
            # A body that is not JSON (JSONDecodeError) or structured output that does not
            # validate (pydantic ValidationError, whose message quotes the model output).
            # Translated without the message and without chaining it into the log.
            return LLMOutputError(f"Malformed provider response: {type(exc).__name__}.")
        if isinstance(exc, TypeError) and "authentication method" in str(exc):
            # The SDK raises a bare TypeError at request time when no credential resolves.
            return LLMAuthError(
                "No Anthropic credentials found: set ANTHROPIC_API_KEY (or "
                "ANTHROPIC_AUTH_TOKEN, or run `ant auth login`)."
            )
        return None

    @staticmethod
    def _check_stop_reason(response: Any) -> None:
        if not hasattr(response, "stop_reason"):
            raise LLMOutputError("Malformed provider response.")
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusalError(f"Model refused the request (category={category}).")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("Model output was truncated at max_tokens.")

    def _meta(self, response: Any) -> LLMResultMeta:
        usage = getattr(response, "usage", None)
        meta = LLMResultMeta(
            provider=self.name,
            model=getattr(response, "model", self._settings.model),
            stop_reason=response.stop_reason,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            provider_request_id=getattr(response, "_request_id", None),
        )
        logger.info(
            "llm.call.completed",
            extra={"fields": meta.model_dump(exclude_none=True)},
        )
        return meta


def _error_type(exc: Any) -> str:
    """The provider's machine-readable error type (`invalid_request_error`, ...)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return str(error["type"])[:64]
    return "unknown"
