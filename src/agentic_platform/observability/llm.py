"""LLM call observability (Phase 13, ADR-062).

`ObservedLLM` wraps any `LLMProvider` and emits one event per call: `llm.call.completed`
(provider, model, stop reason, latency, token counts the provider reported) or
`llm.call.failed` (error category and code). The stage that made the call (planner,
response) comes from the trace context. Prompts, messages and outputs are never part of
an event. It adds no call and no retry: provider retries stay inside the SDK
(`LLM_MAX_RETRIES`, observability/retry.py).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from agentic_platform.core.config import Effort
from agentic_platform.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResultMeta,
    StructuredResult,
    TextResult,
)
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import current_trace, emit, new_id
from agentic_platform.observability.taxonomy import classify_exception

T = TypeVar("T", bound=BaseModel)


class ObservedLLM(LLMProvider):
    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    def __getattr__(self, item: str) -> Any:
        # Test doubles expose extra attributes (for example ScriptedLLM.calls).
        return getattr(self._inner, item)

    async def generate_text(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> TextResult:
        started = time.perf_counter()
        try:
            result = await self._inner.generate_text(
                system=system, messages=messages, max_tokens=max_tokens, effort=effort
            )
        except Exception as exc:
            self._failed(started, exc, "text")
            raise
        self._completed(started, result.meta, "text")
        return result

    async def generate_structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        output_model: type[T],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> StructuredResult[T]:
        started = time.perf_counter()
        try:
            result = await self._inner.generate_structured(
                system=system,
                messages=messages,
                output_model=output_model,
                max_tokens=max_tokens,
                effort=effort,
            )
        except Exception as exc:
            self._failed(started, exc, "structured")
            raise
        self._completed(started, result.meta, "structured")
        return result

    # -- events ----------------------------------------------------------------------

    def _base(self, started: float, mode: str) -> dict[str, Any]:
        stage = current_trace().stage
        return {
            "stage": Stage.LLM,
            "span_id": new_id(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "attributes": {
                "provider": self.name,
                "mode": mode,
                "llm_stage": stage.value if stage is not None else None,
            },
        }

    def _completed(self, started: float, meta: LLMResultMeta, mode: str) -> None:
        fields = self._base(started, mode)
        fields["attributes"].update(
            model=meta.model,
            stop_reason=meta.stop_reason,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
        )
        emit(EventName.LLM_CALL_COMPLETED, status="ok", **fields)

    def _failed(self, started: float, exc: BaseException, mode: str) -> None:
        fields = self._base(started, mode)
        emit(
            EventName.LLM_CALL_FAILED,
            status="error",
            error_category=classify_exception(exc),
            error_code=getattr(exc, "code", None) or type(exc).__name__,
            retryable=getattr(exc, "retryable", None),
            **fields,
        )


__all__ = ["ObservedLLM"]
