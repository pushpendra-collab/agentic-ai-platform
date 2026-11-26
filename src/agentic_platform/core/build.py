"""Build information (Phase 15): which release is running, safe to show to operators.

* `version`: the installed package version (`importlib.metadata`, from pyproject.toml).
* `commit`: `APP_BUILD_COMMIT`, a git commit SHA given to the image build
  (`docker build --build-arg APP_BUILD_COMMIT=$(git rev-parse HEAD)`); `unknown` otherwise.
* `build_time`: `APP_BUILD_TIME` (UTC, ISO 8601), also set at image build; `unknown` otherwise.

Nothing is read from git at run time (the image has no git and no repository). Values
come from the environment only when they have the expected shape (a hex SHA, an ISO
timestamp), so a misconfigured variable can never put arbitrary text, let alone a
secret, into a probe or a log line.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel

PACKAGE = "agentic-platform"
UNKNOWN = "unknown"
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


class BuildInfo(BaseModel):
    version: str
    commit: str
    build_time: str


def _shaped(name: str, pattern: re.Pattern[str]) -> str:
    value = os.environ.get(name, "").strip()
    return value if pattern.fullmatch(value) else UNKNOWN


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    try:
        installed = version(PACKAGE)
    except PackageNotFoundError:  # running from a source tree without installing
        installed = UNKNOWN
    return BuildInfo(
        version=installed,
        commit=_shaped("APP_BUILD_COMMIT", _COMMIT),
        build_time=_shaped("APP_BUILD_TIME", _TIMESTAMP),
    )
