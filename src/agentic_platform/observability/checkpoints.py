"""Checkpoint observability (Phase 13, ADR-062).

`instrument_checkpointer` wraps the three operations LangGraph uses on every turn
(`aget_tuple` = load, `aput` / `aput_writes` = save) on the saver *instance*, so the saver
keeps its class (and `isinstance` checks keep working) whichever saver is configured:
the in-memory saver, `PostgresCheckpointSaver` or a test double.

* A successful load/save emits `checkpoint.loaded` / `checkpoint.saved` (DEBUG logs,
  latency metric).
* A failure emits `checkpoint.load_failed` / `checkpoint.save_failed` with its category.
  A platform error (for example `DatabaseUnavailableError`) is re-raised unchanged, so
  the turn's public error code stays what it was. Anything else is replaced by a
  platform error without chaining (decoder messages can quote checkpoint bytes):
  `CheckpointError` (CHECKPOINT_UNREADABLE) on load, `CheckpointSerializationError`
  (CHECKPOINT_SERIALIZATION_FAILED) on save.
* A type outside the checkpoint allow-list (agents/serialization.py) is blocked by the
  serializer, which does not import it; the serde event listener turns that into
  `checkpoint.rejected` (CHECKPOINT_TYPE_REJECTED). The allow-list is not changed here.

Event attributes are the operation, whether a checkpoint was found, the number of
writes, and the rejected type's class name. Never checkpoint content or keys.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from langgraph.checkpoint.serde.event_hooks import SerdeEvent, register_serde_event_listener

from agentic_platform.core.errors import (
    AgentPlatformError,
    CheckpointError,
    CheckpointSerializationError,
)
from agentic_platform.observability.events import EventName, Stage
from agentic_platform.observability.hub import emit, new_id
from agentic_platform.observability.taxonomy import ErrorCategory, classify_exception

_MARK = "_agentic_platform_observed"
_listener_lock = threading.Lock()
_listener_installed = False


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _failed(name: EventName, operation: str, started: float, exc: BaseException) -> None:
    emit(
        name,
        stage=Stage.CHECKPOINT,
        span_id=new_id(),
        status="error",
        duration_ms=_ms(started),
        error_category=classify_exception(exc),
        error_code=getattr(exc, "code", None) or type(exc).__name__,
        attributes={"operation": operation},
    )


def instrument_checkpointer(saver: Any) -> Any:
    """Wrap a saver's load/save operations with events (idempotent; None passes through)."""
    if saver is None or getattr(saver, _MARK, False):
        return saver
    load, put, put_writes = saver.aget_tuple, saver.aput, saver.aput_writes

    async def aget_tuple(config: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await load(config)
        except AgentPlatformError as exc:
            _failed(EventName.CHECKPOINT_LOAD_FAILED, "load", started, exc)
            raise
        except Exception as exc:
            error = CheckpointError(f"Checkpoint load failed: {type(exc).__name__}.")
            _failed(EventName.CHECKPOINT_LOAD_FAILED, "load", started, error)
            raise error from None
        emit(EventName.CHECKPOINT_LOADED, stage=Stage.CHECKPOINT, status="ok",
             duration_ms=_ms(started),
             attributes={"operation": "load", "found": result is not None})  # fmt: skip
        return result

    async def aput(config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await put(config, checkpoint, metadata, new_versions)
        except AgentPlatformError as exc:
            _failed(EventName.CHECKPOINT_SAVE_FAILED, "save", started, exc)
            raise
        except Exception as exc:
            error = CheckpointSerializationError(f"Checkpoint save failed: {type(exc).__name__}.")
            _failed(EventName.CHECKPOINT_SAVE_FAILED, "save", started, error)
            raise error from None
        emit(EventName.CHECKPOINT_SAVED, stage=Stage.CHECKPOINT, status="ok",
             duration_ms=_ms(started), attributes={"operation": "save"})  # fmt: skip
        return result

    async def aput_writes(config: Any, writes: Any, task_id: str, task_path: str = "") -> None:
        started = time.perf_counter()
        try:
            await put_writes(config, writes, task_id, task_path)
        except AgentPlatformError as exc:
            _failed(EventName.CHECKPOINT_SAVE_FAILED, "save_writes", started, exc)
            raise
        except Exception as exc:
            error = CheckpointSerializationError(f"Checkpoint write failed: {type(exc).__name__}.")
            _failed(EventName.CHECKPOINT_SAVE_FAILED, "save_writes", started, error)
            raise error from None
        emit(EventName.CHECKPOINT_SAVED, stage=Stage.CHECKPOINT, status="ok",
             duration_ms=_ms(started),
             attributes={"operation": "save_writes", "writes": len(writes)})  # fmt: skip

    saver.aget_tuple = aget_tuple
    saver.aput = aput
    saver.aput_writes = aput_writes
    setattr(saver, _MARK, True)
    install_serde_listener()
    return saver


def _on_serde_event(event: SerdeEvent) -> None:
    if event.get("kind") not in ("msgpack_blocked", "msgpack_method_blocked"):
        return
    emit(
        EventName.CHECKPOINT_REJECTED,
        stage=Stage.CHECKPOINT,
        status="rejected",
        error_category=ErrorCategory.SERIALIZATION,
        error_code="CHECKPOINT_TYPE_REJECTED",
        attributes={"operation": "load", "type_name": str(event.get("name", ""))[:64]},
    )


def install_serde_listener() -> None:
    """Register the process-wide serde listener once."""
    global _listener_installed
    with _listener_lock:
        if not _listener_installed:
            register_serde_event_listener(_on_serde_event)
            _listener_installed = True


__all__ = ["install_serde_listener", "instrument_checkpointer"]
