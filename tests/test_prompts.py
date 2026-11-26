from __future__ import annotations

from pathlib import Path

import pytest

from agentic_platform.core.errors import PromptNotFoundError, PromptRenderError
from agentic_platform.prompts import PromptRegistry


def test_bundled_prompts_load() -> None:
    registry = PromptRegistry()
    assert {
        "understand_request.system",
        "generate_response.system",
        "generate_response.context",
    } <= set(registry.names())


def test_bundled_prompts_declare_expected_variables() -> None:
    registry = PromptRegistry()
    assert registry.get("understand_request.system").variables == {"domains", "tools"}
    assert registry.get("generate_response.system").variables == set()
    assert registry.get("generate_response.context").variables == {"user_input", "context_json"}


def test_latest_version_wins_and_pinning(tmp_path: Path) -> None:
    (tmp_path / "greet.v1.md").write_text("Hello $name", encoding="utf-8")
    (tmp_path / "greet.v2.md").write_text("Hi $name", encoding="utf-8")
    (tmp_path / "README.txt").write_text("ignored", encoding="utf-8")
    registry = PromptRegistry(tmp_path)
    assert registry.render("greet", name="Asha") == "Hi Asha"
    assert registry.render("greet", version=1, name="Asha") == "Hello Asha"


def test_missing_variable_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "greet.v1.md").write_text("Hello $name", encoding="utf-8")
    with pytest.raises(PromptRenderError):
        PromptRegistry(tmp_path).render("greet")


def test_values_are_not_reinterpreted(tmp_path: Path) -> None:
    (tmp_path / "echo.v1.md").write_text("[$text]", encoding="utf-8")
    assert PromptRegistry(tmp_path).render("echo", text="costs $5 ${x}") == "[costs $5 ${x}]"


def test_unknown_prompt_and_version(tmp_path: Path) -> None:
    (tmp_path / "greet.v1.md").write_text("Hello", encoding="utf-8")
    registry = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFoundError):
        registry.get("missing")
    with pytest.raises(PromptNotFoundError):
        registry.get("greet", version=9)
