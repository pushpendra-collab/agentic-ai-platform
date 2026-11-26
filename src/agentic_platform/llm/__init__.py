"""Provider-neutral LLM abstraction. Nodes depend on `LLMProvider`, never on an SDK."""

from agentic_platform.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResultMeta,
    StructuredResult,
    TextResult,
)
from agentic_platform.llm.factory import create_llm_provider, register_provider

__all__ = [
    "ChatMessage",
    "LLMProvider",
    "LLMResultMeta",
    "StructuredResult",
    "TextResult",
    "create_llm_provider",
    "register_provider",
]
