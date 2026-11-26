"""Structured logging with request-scoped context.

`log_context(request_id=..., thread_id=...)` binds fields that every log record
emitted inside the block carries automatically (contextvars, so it is safe
across asyncio tasks). Pass event-specific fields with `extra={"fields": {...}}`.

Never log raw user input, prompts or model output at INFO level; log sizes and
identifiers instead.

Phase 13 (ADR-062): both formatters pass every line through the redaction policy
(`core/redaction.py`): sensitive keys are masked and credentials that slipped into a
message, a field or a traceback (bearer tokens, API keys, JWTs, passwords, URLs with
credentials) are scrubbed before the line is written.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from agentic_platform.core.config import LoggingSettings
from agentic_platform.core.redaction import redact_structure, redact_text

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})  # noqa: B039

_ROOT_LOGGER = "agentic_platform"


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def current_log_context() -> dict[str, Any]:
    return dict(_context.get())


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.context = current_log_context()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "context", {}))
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(redact_structure(payload), default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {**getattr(record, "context", {}), **getattr(record, "fields", {})}
        suffix = " ".join(f"{k}={v}" for k, v in extras.items())
        line = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        if suffix:
            line = f"{line} | {suffix}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return redact_text(line)


def configure_logging(settings: LoggingSettings) -> None:
    """Configure the package logger. Idempotent; safe to call more than once."""
    logger = logging.getLogger(_ROOT_LOGGER)
    logger.setLevel(settings.level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(JsonFormatter() if settings.format == "json" else TextFormatter())
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    if not name.startswith(_ROOT_LOGGER):
        name = f"{_ROOT_LOGGER}.{name}"
    return logging.getLogger(name)
