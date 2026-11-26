from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_platform.core.config import AppSettings, LLMSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.llm import create_llm_provider
from agentic_platform.llm.anthropic_provider import AnthropicProvider


def test_defaults() -> None:
    s = AppSettings()
    assert s.llm.provider == "anthropic"
    assert s.llm.model == "claude-opus-5-5"
    assert s.llm.effort == "medium"
    assert s.llm.anthropic_api_key is None
    assert s.agent.recursion_limit == 25
    assert s.logging.format == "json"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("LLM_EFFORT", "low")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS_PER_TURN", "1")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s = AppSettings()
    assert s.llm.model == "claude-sonnet-5-5"
    assert s.llm.effort == "low"
    assert s.agent.max_tool_calls_per_turn == 1
    assert s.logging.level == "DEBUG"
    assert s.llm.anthropic_api_key is not None
    assert s.llm.anthropic_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(s)


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_MAX_TOKENS=1234\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    s = AppSettings()  # conftest chdirs into tmp_path
    assert s.llm.max_tokens == 1234
    assert s.llm.anthropic_api_key is None  # blank means "let the SDK resolve credentials"


def test_invalid_effort_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_EFFORT", "extreme")
    with pytest.raises(ValidationError):
        LLMSettings()


def test_provider_factory_unknown_provider() -> None:
    with pytest.raises(ConfigurationError, match="Unknown LLM_PROVIDER"):
        create_llm_provider(LLMSettings(provider="nope"))


def test_provider_factory_anthropic() -> None:
    provider = create_llm_provider(LLMSettings(anthropic_api_key="test-key"))
    assert isinstance(provider, AnthropicProvider)
