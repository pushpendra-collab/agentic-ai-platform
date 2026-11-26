"""The provider contract every LLM adapter implements.

Adapters translate provider SDK errors into the `LLMError` hierarchy in
`core.errors`, so callers never import a provider SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from agentic_platform.core.config import Effort

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMResultMeta(BaseModel):
    provider: str
    model: str
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_request_id: str | None = None


class TextResult(BaseModel):
    text: str
    meta: LLMResultMeta


class StructuredResult(BaseModel, Generic[T]):
    output: T
    meta: LLMResultMeta


class LLMProvider(ABC):
    """Async LLM interface used by graph nodes."""

    name: str

    @abstractmethod
    async def generate_text(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> TextResult:
        """Return a free-text completion."""

    @abstractmethod
    async def generate_structured(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        output_model: type[T],
        max_tokens: int | None = None,
        effort: Effort | None = None,
    ) -> StructuredResult[T]:
        """Return output validated against `output_model`, or raise `LLMOutputError`."""
