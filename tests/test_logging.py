from __future__ import annotations

import json
import logging

from agentic_platform.core.logging import JsonFormatter, _ContextFilter, log_context


def _record(**fields: object) -> logging.LogRecord:
    record = logging.LogRecord("agentic_platform.t", logging.INFO, __file__, 1, "evt", None, None)
    if fields:
        record.fields = fields
    _ContextFilter().filter(record)
    return record


def test_json_formatter_includes_context_and_fields() -> None:
    with log_context(request_id="r1", thread_id="t1"), log_context(node="n"):
        record = _record(duration_ms=5)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "evt"
    assert payload["level"] == "INFO"
    assert (payload["request_id"], payload["thread_id"], payload["node"]) == ("r1", "t1", "n")
    assert payload["duration_ms"] == 5


def test_context_is_restored_after_block() -> None:
    with log_context(request_id="r1"):
        pass
    payload = json.loads(JsonFormatter().format(_record()))
    assert "request_id" not in payload
