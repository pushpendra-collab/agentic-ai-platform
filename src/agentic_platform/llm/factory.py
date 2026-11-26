"""Provider selection by configuration (`LLM_PROVIDER`).

New providers register a constructor here; nothing else in the codebase changes.
"""

from __future__ import annotations

from collections.abc import Callable

from agentic_platform.core.config import LLMSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.llm.base import LLMProvider

ProviderFactory = Callable[[LLMSettings], LLMProvider]


def _anthropic(settings: LLMSettings) -> LLMProvider:
    from agentic_platform.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(settings)


def _ollama(settings: LLMSettings) -> LLMProvider:
    from agentic_platform.llm.ollama_provider import OllamaProvider

    return OllamaProvider(settings)


_PROVIDERS: dict[str, ProviderFactory] = {"anthropic": _anthropic, "ollama": _ollama}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDERS[name.lower()] = factory


def create_llm_provider(settings: LLMSettings) -> LLMProvider:
    factory = _PROVIDERS.get(settings.provider.lower())
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ConfigurationError(
            f"Unknown LLM_PROVIDER '{settings.provider}'. Known providers: {known}."
        )
    return factory(settings)
