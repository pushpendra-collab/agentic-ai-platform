"""Conversation (thread) identification: see `core/threads.py` for the rules.

Re-exported here for the agent layer, plus the LangGraph config helper.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agentic_platform.core.threads import (
    MAX_THREAD_ID_LENGTH,
    TENANT_SEPARATOR,
    new_thread_id,
    storage_thread_id,
    tenant_of_storage_key,
    thread_lock_name,
    validate_thread_id,
)

__all__ = [
    "MAX_THREAD_ID_LENGTH",
    "TENANT_SEPARATOR",
    "new_thread_id",
    "storage_thread_id",
    "tenant_of_storage_key",
    "thread_config",
    "thread_lock_name",
    "validate_thread_id",
]


def thread_config(storage_key: str, recursion_limit: int) -> RunnableConfig:
    return {"configurable": {"thread_id": storage_key}, "recursion_limit": recursion_limit}
