"""File-based prompt registry.

Templates live in `templates/` as `<name>.v<version>.md` and use `$variable`
placeholders (`string.Template`). Rendering is strict: a missing variable is an
error, never a silently empty string. `get(name)` returns the highest version;
pin a version with `get(name, version=N)` when behaviour must not drift.

Prompts are code: change them through review and add a new version file rather
than editing a version that is already in use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from agentic_platform.core.errors import PromptNotFoundError, PromptRenderError

DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"

_FILENAME = re.compile(r"^(?P<name>[a-z0-9_.]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    text: str

    @property
    def variables(self) -> set[str]:
        return {
            m.group("named") or m.group("braced")
            for m in Template.pattern.finditer(self.text)
            if m.group("named") or m.group("braced")
        }

    def render(self, **variables: object) -> str:
        missing = self.variables - variables.keys()
        if missing:
            raise PromptRenderError(
                f"Prompt {self.name}.v{self.version} missing variables: {sorted(missing)}"
            )
        try:
            return Template(self.text).substitute({k: str(v) for k, v in variables.items()})
        except (KeyError, ValueError) as exc:
            raise PromptRenderError(f"Prompt {self.name}.v{self.version}: {exc}") from exc


class PromptRegistry:
    def __init__(self, templates_dir: Path = DEFAULT_TEMPLATES_DIR) -> None:
        self._templates: dict[str, dict[int, PromptTemplate]] = {}
        for path in sorted(templates_dir.glob("*.md")):
            match = _FILENAME.match(path.name)
            if not match:
                continue
            template = PromptTemplate(
                name=match["name"],
                version=int(match["version"]),
                text=path.read_text(encoding="utf-8").strip(),
            )
            self._templates.setdefault(template.name, {})[template.version] = template

    def names(self) -> list[str]:
        return sorted(self._templates)

    def get(self, name: str, version: int | None = None) -> PromptTemplate:
        versions = self._templates.get(name)
        if not versions:
            raise PromptNotFoundError(f"No prompt named '{name}'.")
        if version is None:
            return versions[max(versions)]
        if version not in versions:
            raise PromptNotFoundError(f"Prompt '{name}' has no version {version}.")
        return versions[version]

    def render(
        self, prompt_name: str, /, *, version: int | None = None, **variables: object
    ) -> str:
        """Render the named prompt. `version` is reserved and cannot be a template variable."""
        return self.get(prompt_name, version).render(**variables)
