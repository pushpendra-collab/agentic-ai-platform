"""Test doubles. Never imported by application code."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
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

T = TypeVar("T", bound=BaseModel)


@dataclass
class RecordedCall:
    kind: str
    system: str
    messages: list[ChatMessage]
    output_model: type[BaseModel] | None = None
    effort: Effort | None = None


@dataclass
class ScriptedLLM(LLMProvider):
    """Returns pre-scripted outputs in order. An Exception in the script is raised."""

    script: list[Any] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)
    name: str = "scripted"

    def _next(self) -> Any:
        if not self.script:
            raise AssertionError("ScriptedLLM called more times than scripted.")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _meta(self) -> LLMResultMeta:
        return LLMResultMeta(provider=self.name, model="scripted-model", stop_reason="end_turn")

    async def generate_text(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> TextResult:
        self.calls.append(RecordedCall("text", system, list(messages), effort=effort))
        item = self._next()
        assert isinstance(item, str), f"expected scripted str, got {type(item)}"
        return TextResult(text=item, meta=self._meta())

    async def generate_structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        output_model: type[T],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> StructuredResult[T]:
        self.calls.append(
            RecordedCall("structured", system, list(messages), output_model, effort)
        )
        item = self._next()
        assert isinstance(item, output_model), f"expected {output_model}, got {type(item)}"
        return StructuredResult(output=item, meta=self._meta())
