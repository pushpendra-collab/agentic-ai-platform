"""Conversation (thread) identification (pure helpers; no framework imports).

A thread id names one conversation. It keys the checkpointer (state), the
per-thread lock (one turn at a time) and turn idempotency keys. Ids are opaque
to the platform: the agent generates a random one when the caller has none.

Rules: 1-128 characters from `[A-Za-z0-9_.:-]`. The restricted alphabet keeps
ids safe in log lines, Redis keys and URLs.

Phase 5 (ADR-039): the public id is unique per tenant. Storage (checkpoints,
thread lock, turn idempotency) uses the key `"<tenant_id>/<thread_id>"`. `/` is not
allowed in either id, so a caller can never name another tenant's storage key,
and two tenants can use the same public id without sharing anything. Ownership
(which user may use the thread) is recorded in `conversation_thread` and checked
by `services.conversations.ConversationService` before any of these keys is used.
"""

from __future__ import annotations

import re
import uuid

from agentic_platform.core.errors import InvalidThreadIdError

MAX_THREAD_ID_LENGTH = 128
_THREAD_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


def new_thread_id() -> str:
    return uuid.uuid4().hex


def validate_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
        raise InvalidThreadIdError(
            f"Invalid thread id (length {len(thread_id) if isinstance(thread_id, str) else '?'}).",
            public_message=(
                f"The conversation id must be 1-{MAX_THREAD_ID_LENGTH} characters of "
                "letters, digits, '_', '.', ':' or '-'."
            ),
        )
    return thread_id


TENANT_SEPARATOR = "/"


def storage_thread_id(tenant_id: str, thread_id: str) -> str:
    """The checkpointer / lock key of a tenant's thread."""
    return f"{tenant_id}{TENANT_SEPARATOR}{validate_thread_id(thread_id)}"


def tenant_of_storage_key(storage_key: str) -> str:
    """The tenant part of a storage key (the whole key for pre-Phase-5 keys)."""
    return storage_key.split(TENANT_SEPARATOR, 1)[0]


def thread_lock_name(storage_key: str) -> str:
    return f"thread:{storage_key}"

