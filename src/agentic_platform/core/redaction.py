"""Redaction policy for logs, observability events and diagnostics (Phase 13, ADR-062).

Two layers, both deterministic and dependency-free:

1. **Allow-listed metadata.** Observability events and diagnostics carry only identifiers,
   codes, counts, durations and flags. `safe_attributes` keeps a value only when it is a
   number, a boolean or a short identifier-like token (`SAFE_TOKEN`); free text (prompts,
   replies, names, ERP records, tool argument values) is replaced by `REDACTED`. Keys that
   name a secret (`SENSITIVE_KEY`) are dropped whatever their value.
2. **Pattern scrubbing.** Every formatted log line goes through `redact_text`, which masks
   credentials that may have slipped into a message, an exception or a traceback:
   bearer tokens, `Authorization` headers, Anthropic/Voyage-style API keys, JWTs,
   passwords and secrets in `key=value` / JSON form, and URLs with embedded credentials.

Layer 2 is defence in depth; layer 1 is the contract. Neither is a reason to log raw
user input, prompts, model output, memory content or ERP records (core/logging.py).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[redacted]"

# Keys whose values are never kept (matched case-insensitively, anywhere in the key).
SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token(?!s)|api[_-]?key|authori[sz]ation|cookie|credential|"
    r"private[_-]?key|session[_-]?id|dsn|connection[_-]?string)",
    re.IGNORECASE,
)

# A value safe to keep in event metadata: identifier-like, short, no spaces.
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/\-]{0,64}$")

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <token> / Basic <b64>
    (re.compile(r"(?i)\b(authorization)\b(\s*[:=]\s*)\S+(\s+\S+)?"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]{8,}=*"), r"\1 " + REDACTED),
    # Provider API keys (Anthropic sk-ant-..., generic sk-..., Voyage pa-...).
    (re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,}|pa-[A-Za-z0-9_\-]{16,})"),
     REDACTED),
    # JSON Web Tokens (three base64url parts).
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"), REDACTED),
    # Credentials inside URLs: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@"),
     r"\1\2:" + REDACTED + "@"),
    # key=value / key: value / "key": "value" for sensitive keys.
    (re.compile(
        r"(?i)([\"']?\b[\w\-]*(?:password|passwd|secret|token(?!s)|api[_-]?key|private[_-]?key)"
        r"[\w\-]*\b[\"']?\s*[:=]\s*)([\"']?)[^\s\"',;}]+\2"
    ), r"\1\2" + REDACTED + r"\2"),
)  # fmt: skip


def redact_text(text: str) -> str:
    """Mask credentials in free text (log messages, exception text, tracebacks)."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY.search(key))


def safe_scalar(value: Any) -> str | int | float | bool | None:
    """A metadata value as kept in events: numbers, booleans, None and safe tokens only."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and SAFE_TOKEN.fullmatch(value) and redact_text(value) == value:
        return value
    return REDACTED


def safe_attributes(values: Mapping[str, Any] | None) -> dict[str, str | int | float | bool | None]:
    """Event metadata: sensitive keys dropped, every value reduced by `safe_scalar`.

    Lists and dicts are not kept (they could carry records); count them before passing."""
    if not values:
        return {}
    kept: dict[str, str | int | float | bool | None] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not SAFE_TOKEN.fullmatch(key) or is_sensitive_key(key):
            continue
        kept[key] = safe_scalar(value)
    return kept


def redact_structure(value: Any, *, depth: int = 0) -> Any:
    """Scrub a log payload: sensitive keys masked, strings pattern-scrubbed (bounded depth)."""
    if depth > 6:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k)
                else redact_structure(v, depth=depth + 1))
            for k, v in value.items()
        }  # fmt: skip
    if isinstance(value, list | tuple):
        return [redact_structure(v, depth=depth + 1) for v in value]
    return value


__all__ = [
    "REDACTED",
    "SAFE_TOKEN",
    "SENSITIVE_KEY",
    "is_sensitive_key",
    "redact_structure",
    "redact_text",
    "safe_attributes",
    "safe_scalar",
]
