"""Stable fingerprints of request payloads (audit `input_hash`, idempotency `request_hash`).

The hash lets us compare and correlate requests without storing their content,
which may contain business data or PII.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def stable_hash(payload: Any) -> str:
    """SHA-256 hex digest of `payload` in canonical JSON (sorted keys, no whitespace).

    Equal payloads hash equally regardless of key order. Pydantic models are
    dumped in JSON mode first; a `str` is hashed as-is.
    """
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
