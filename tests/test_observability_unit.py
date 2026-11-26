"""Phase 13 unit tests: redaction, events, error taxonomy, retry, deadlines, metrics,
checkpoint instrumentation and diagnostics. No graph, no network, no model."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ValidationError

from agentic_platform.agents.serialization import checkpoint_serializer
from agentic_platform.core import errors as e
from agentic_platform.core.logging import JsonFormatter, TextFormatter, _ContextFilter
from agentic_platform.core.redaction import REDACTED, redact_text, safe_attributes
from agentic_platform.observability import (
    ErrorCategory,
    EventName,
    ObsEvent,
    Stage,
    capture_events,
    classify_business,
    classify_code,
    classify_exception,
    emit,
    span,
)
from agentic_platform.observability.checkpoints import instrument_checkpointer
from agentic_platform.observability.deadline import strict_deadline
from agentic_platform.observability.diagnostics import TurnDiagnostics, events_from_log_lines
from agentic_platform.observability.hub import LoggingSink, MetricsSink, bind_trace
from agentic_platform.observability.metrics import MAX_SERIES, METRICS, MetricsRegistry
from agentic_platform.observability.retry import NO_RETRY, RetryPolicy, erp_read_policy, retry_async

SECRETS = {
    "bearer": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhc2hhIn0.c2lnbmF0dXJlMTIz",
    "api_key": "using key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
    "password": "password=hunter2-very-secret",
    "url": "postgresql://agentic_owner:topsecretpw@db:5432/agentic",
    "json": '{"api_key": "abc123def456ghi", "token": "zzz-token-value"}',
    "refresh": "refresh_token=rt-1234567890abcdef",
}
SECRET_VALUES = (
    "eyJzdWIiOiJhc2hhIn0",
    "sk-ant-api03",
    "hunter2",
    "topsecretpw",
    "abc123def456ghi",
    "zzz-token-value",
    "rt-1234567890abcdef",
)


# =============================================================================================
# redaction (brief § 6)
# =============================================================================================


@pytest.mark.parametrize("text", SECRETS.values(), ids=SECRETS.keys())
def test_redact_text_masks_credentials(text: str) -> None:
    scrubbed = redact_text(text)
    assert REDACTED in scrubbed
    assert not any(value in scrubbed for value in SECRET_VALUES), scrubbed


def _record(message: str, *, exc: BaseException | None = None, **fields: Any) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    record = logging.LogRecord(
        "agentic_platform.t", logging.ERROR, __file__, 1, message, None, exc_info
    )
    record.fields = fields
    _ContextFilter().filter(record)
    return record


@pytest.mark.parametrize("formatter", [JsonFormatter(), TextFormatter()], ids=["json", "text"])
def test_log_lines_never_carry_secrets(formatter: logging.Formatter) -> None:
    try:
        raise RuntimeError(f"connect failed: {SECRETS['url']} {SECRETS['bearer']}")
    except RuntimeError as exc:
        caught = exc
    line = formatter.format(
        _record(
            f"event {SECRETS['api_key']}",
            exc=caught,
            authorization="Bearer abcdefghijklmnop",
            password="hunter2-very-secret",
            detail=SECRETS["refresh"],
            nested={"api_key": "abc123def456ghi", "ok": 1},
        )
    )
    assert not any(value in line for value in SECRET_VALUES), line
    assert "abcdefghijklmnop" not in line
    assert "connect failed" in line  # the useful part survives


def test_safe_attributes_keep_tokens_drop_text_and_sensitive_keys() -> None:
    kept = safe_attributes(
        {
            "tool": "business.customer_outstanding",
            "count": 3,
            "ok": True,
            "ratio": 0.5,
            "note": "Rahul Gupta owes 45,000",
            "api_key": "x",
            "user_token": "abc",
            "records": [1, 2],
            "bad key": "x",
        }
    )
    assert kept == {
        "tool": "business.customer_outstanding",
        "count": 3,
        "ok": True,
        "ratio": 0.5,
        "note": REDACTED,
        "records": REDACTED,
    }


def test_events_refuse_free_text_and_unknown_fields() -> None:
    event = ObsEvent(
        name=EventName.TOOL_COMPLETED,
        tool="Rahul Gupta's invoice",
        attributes={"prompt": "Rahul Gupta ka bill?", "password": "x", "count": 2},
    )
    assert event.tool == REDACTED
    assert event.attributes == {"prompt": REDACTED, "count": 2}
    assert event.kind == "observability"
    with pytest.raises(ValidationError):
        ObsEvent(name=EventName.TOOL_COMPLETED, arguments={"customer_name": "Rahul"})  # type: ignore[call-arg]


def test_observability_events_are_not_audit_events() -> None:
    from agentic_platform.services.audit import AuditEvent

    assert not issubclass(ObsEvent, AuditEvent) and not issubclass(AuditEvent, ObsEvent)
    assert set(ObsEvent.model_fields).isdisjoint({"tenant_id", "actor_id", "input_hash"})
    source = inspect.getsource(__import__("agentic_platform.observability.hub").observability.hub)
    assert "audit" not in source.lower().replace("audit events", "")  # never writes audit


# =============================================================================================
# error taxonomy (brief § 7)
# =============================================================================================


def _platform_error_classes() -> list[type[e.AgentPlatformError]]:
    return [
        cls
        for cls in vars(e).values()
        if inspect.isclass(cls) and issubclass(cls, e.AgentPlatformError)
    ]


INTERNAL_BY_DESIGN = {"PLATFORM_ERROR", "AGENT_STATE_ERROR"}


def test_every_platform_error_code_has_a_category() -> None:
    for cls in _platform_error_classes():
        category = classify_code(cls.code)
        if cls.code in INTERNAL_BY_DESIGN:
            assert category is ErrorCategory.INTERNAL
        else:
            assert category is not ErrorCategory.INTERNAL, cls.__name__


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (e.AgentInputError(), ErrorCategory.VALIDATION),
        (e.ToolValidationError(), ErrorCategory.VALIDATION),
        (e.InvalidIdentityError(), ErrorCategory.AUTHENTICATION),
        (e.UnauthenticatedError(), ErrorCategory.AUTHENTICATION),
        (e.PermissionDeniedError(), ErrorCategory.AUTHORIZATION),
        (e.ToolPermissionDeniedError(), ErrorCategory.AUTHORIZATION),
        (e.InvalidResumeContextError(), ErrorCategory.AUTHORIZATION),
        (e.CrossTenantAccessError(), ErrorCategory.NOT_FOUND),
        (e.ConversationNotFoundError(), ErrorCategory.NOT_FOUND),
        (e.DatabaseUnavailableError(), ErrorCategory.DEPENDENCY_UNAVAILABLE),
        (e.CacheUnavailableError(), ErrorCategory.DEPENDENCY_UNAVAILABLE),
        (e.DatabaseTimeoutError(), ErrorCategory.TIMEOUT),
        (e.DomainTimeoutError(), ErrorCategory.TIMEOUT),
        (TimeoutError(), ErrorCategory.TIMEOUT),
        (e.LLMTransientError(), ErrorCategory.RETRYABLE_DEPENDENCY),
        (e.DatabaseConcurrencyError(), ErrorCategory.RETRYABLE_DEPENDENCY),
        (e.DatabaseIntegrityError(), ErrorCategory.NON_RETRYABLE_DEPENDENCY),
        (e.LLMOutputError(), ErrorCategory.MODEL),
        (e.LLMRefusalError(), ErrorCategory.MODEL),
        (e.LLMAuthError(), ErrorCategory.CONFIGURATION),
        (e.ToolExecutionError(), ErrorCategory.TOOL),
        (e.OrchestrationError(), ErrorCategory.DOMAIN),
        (e.OrchestrationLimitError(), ErrorCategory.DOMAIN),
        (e.CheckpointError(), ErrorCategory.CHECKPOINT),
        (e.CheckpointSerializationError(), ErrorCategory.SERIALIZATION),
        (e.ConfigurationError(), ErrorCategory.CONFIGURATION),
        (RuntimeError("boom"), ErrorCategory.INTERNAL),
        (ValueError("x"), ErrorCategory.INTERNAL),
    ],
)
def test_exceptions_classify_deterministically(exc: BaseException, category: ErrorCategory) -> None:
    assert classify_exception(exc) is category
    assert classify_exception(exc) is classify_exception(exc)  # same input, same answer


@pytest.mark.parametrize(
    ("status", "reason", "category"),
    [
        ("SUCCESS", None, None),
        ("NOT_FOUND", "NO_MATCH", ErrorCategory.NOT_FOUND),
        ("AMBIGUOUS", "SEVERAL_MATCHES", ErrorCategory.AMBIGUITY),
        ("UNAVAILABLE", "ERP_UNAVAILABLE", ErrorCategory.DEPENDENCY_UNAVAILABLE),
        ("UNAVAILABLE", "ERP_TIMEOUT", ErrorCategory.TIMEOUT),
        ("UNAVAILABLE", "RULE_NOT_VERIFIED", ErrorCategory.NON_RETRYABLE_DEPENDENCY),
        ("UNAUTHORIZED", "PERMISSION_MISSING", ErrorCategory.AUTHORIZATION),
        ("VALIDATION_ERROR", "FUTURE_DATE", ErrorCategory.VALIDATION),
        ("DEPENDENCY_ERROR", "TENANT_MISMATCH", ErrorCategory.NON_RETRYABLE_DEPENDENCY),
        ("DEPENDENCY_ERROR", "MALFORMED_RESPONSE", ErrorCategory.NON_RETRYABLE_DEPENDENCY),
    ],
)
def test_business_results_classify(
    status: str, reason: str | None, category: ErrorCategory | None
) -> None:
    assert classify_business(status, reason) == category


def test_public_error_contract_is_unchanged() -> None:
    from agentic_platform.agents.schemas import PublicError

    assert set(PublicError.model_fields) == {"code", "message", "retryable"}


# =============================================================================================
# retry policy (brief § 8)
# =============================================================================================


class Flaky:
    def __init__(self, failures: list[BaseException], result: str = "ok") -> None:
        self.failures, self.result, self.calls = list(failures), result, 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


def _retry(call: Flaky, policy: RetryPolicy, **kw: Any) -> tuple[str | BaseException, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def scenario() -> str:
        return await retry_async("test_op", call, policy, sleep=sleep, **kw)

    try:
        return asyncio.run(scenario()), slept
    except Exception as exc:
        return exc, slept


POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=0.1,
    retry_on=frozenset({ErrorCategory.DEPENDENCY_UNAVAILABLE}),
)


def test_retry_success_on_first_attempt() -> None:
    call = Flaky([])
    with capture_events() as events:
        result, slept = _retry(call, POLICY)
    assert (result, call.calls, slept) == ("ok", 1, [])
    assert events.of(EventName.RETRY_ATTEMPTED) == []


def test_retry_transient_failure_then_success() -> None:
    call = Flaky([e.DatabaseUnavailableError("down")])
    with capture_events() as events:
        result, slept = _retry(call, POLICY)
    assert (result, call.calls, slept) == ("ok", 2, [0.1])
    (retry,) = events.of(EventName.RETRY_ATTEMPTED)
    assert retry.attempt == 2 and retry.error_category == "dependency_unavailable"
    assert retry.attributes["operation"] == "test_op"


def test_repeated_transient_failure_exhausts_the_budget() -> None:
    call = Flaky([e.DatabaseUnavailableError("down")] * 5)
    with capture_events() as events:
        result, slept = _retry(call, POLICY)
    assert isinstance(result, e.DatabaseUnavailableError)
    assert call.calls == 3 and slept == [0.1, 0.2]  # deterministic exponential backoff
    assert len(events.of(EventName.RETRY_ATTEMPTED)) == 2
    (exhausted,) = events.of(EventName.RETRY_EXHAUSTED)
    assert exhausted.attempt == 3


def test_non_retryable_failure_is_not_retried() -> None:
    call = Flaky([e.DatabaseIntegrityError("dup")])
    result, slept = _retry(call, POLICY)
    assert isinstance(result, e.DatabaseIntegrityError) and call.calls == 1 and slept == []


def test_timeouts_are_not_retried_by_the_erp_policy() -> None:
    call = Flaky([TimeoutError()])
    result, _ = _retry(call, erp_read_policy(3, 0.0))
    assert isinstance(result, TimeoutError) and call.calls == 1


def test_no_retry_beyond_the_deadline() -> None:
    call = Flaky([e.DatabaseUnavailableError("down")] * 3)
    with capture_events() as events:
        result, slept = _retry(call, POLICY, deadline=100.05, clock=lambda: 100.0)
    assert isinstance(result, e.DatabaseUnavailableError) and call.calls == 1 and slept == []
    (exhausted,) = events.of(EventName.RETRY_EXHAUSTED)
    assert exhausted.attributes["reason"] == "deadline"


def test_cancellation_is_never_retried() -> None:
    call = Flaky([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retry_async("x", call, POLICY))
    assert call.calls == 1


def test_default_policy_never_retries() -> None:
    call = Flaky([e.DatabaseUnavailableError("down")])
    result, _ = _retry(call, NO_RETRY)
    assert isinstance(result, e.DatabaseUnavailableError) and call.calls == 1


def test_retry_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(multiplier=0.5)


# =============================================================================================
# strict deadlines (brief § 9)
# =============================================================================================


async def _swallowing() -> str:
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(10)  # ignores the cancellation
    return "late success"


def test_plain_wait_for_lets_a_swallowed_cancellation_succeed() -> None:
    """The defect strict_deadline exists for (Python 3.11 `asyncio.wait_for`)."""

    async def scenario() -> str:
        return await asyncio.wait_for(_swallowing(), timeout=0.01)

    assert asyncio.run(scenario()) == "late success"


def test_strict_deadline_fails_even_if_the_work_swallows_cancellation() -> None:
    async def scenario() -> str:
        async with strict_deadline(0.01, scope="test"):
            return await _swallowing()

    with capture_events() as events, pytest.raises(TimeoutError):
        asyncio.run(scenario())
    (timeout,) = events.of(EventName.TIMEOUT)
    assert timeout.attributes == {"scope": "test", "limit_ms": 10.0}
    assert timeout.error_category == "timeout"


def test_strict_deadline_passes_results_through_in_time() -> None:
    async def scenario() -> str:
        async with strict_deadline(1.0, scope="test"):
            await asyncio.sleep(0)
            return "ok"

    with capture_events() as events:
        assert asyncio.run(scenario()) == "ok"
    assert events.of(EventName.TIMEOUT) == []


def test_strict_deadline_leaves_no_background_work() -> None:
    finished: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0.2)
        finished.append("done")

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            async with strict_deadline(0.01, scope="test"):
                await work()
        await asyncio.sleep(0.3)  # the work would have finished by now if it survived

    asyncio.run(scenario())
    assert finished == []


# =============================================================================================
# metrics (brief § 10)
# =============================================================================================


def test_metrics_refuse_undeclared_metrics_and_labels() -> None:
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.inc("agent_turns_total", outcome="completed", tenant_id="acme")
    with pytest.raises(ValueError):
        registry.inc("agent_customer_total", customer="Rahul")
    with pytest.raises(ValueError):
        registry.observe("agent_turns_total", 1, outcome="x")  # a counter, not a histogram


def test_no_metric_declares_a_high_cardinality_label() -> None:
    forbidden = {
        "tenant",
        "tenant_id",
        "user",
        "user_id",
        "customer",
        "invoice",
        "amount",
        "prompt",
        "thread",
        "thread_id",
        "request_id",
        "warehouse",
    }
    for definition in METRICS.values():
        assert forbidden.isdisjoint(definition.labels), definition.name


def test_label_values_are_sanitised_and_cardinality_is_bounded() -> None:
    registry = MetricsRegistry()
    registry.inc("agent_tool_calls_total", tool="Rahul Gupta ka bill", status="ok", category="x")
    assert registry.counter("agent_tool_calls_total", tool="other") == 1
    for i in range(MAX_SERIES + 50):
        registry.inc("agent_timeouts_total", scope=f"s{i}")
    series = registry.snapshot()["counters"]["agent_timeouts_total"]
    assert len(series) == MAX_SERIES + 1  # the cap, plus one folded "other" series
    assert registry.counter("agent_timeouts_total", scope="other") == 50


def test_histogram_percentiles() -> None:
    registry = MetricsRegistry()
    for value in range(1, 101):
        registry.observe("agent_turn_duration_ms", value, outcome="completed")
    summary = registry.histogram("agent_turn_duration_ms", outcome="completed")
    assert summary is not None
    assert (summary["count"], summary["p50"], summary["p95"], summary["max"]) == (100, 50, 95, 100)


def test_metrics_sink_maps_events() -> None:
    registry = MetricsRegistry()
    sink = MetricsSink(registry)
    sink.handle(
        ObsEvent(
            name=EventName.TURN_COMPLETED,
            status="ok",
            duration_ms=12.0,
            attributes={"outcome": "completed"},
        )
    )
    sink.handle(
        ObsEvent(
            name=EventName.PLANNER_COMPLETED, stage=Stage.PLANNER, status="ok", duration_ms=3.0
        )
    )
    sink.handle(
        ObsEvent(
            name=EventName.TOOL_FAILED,
            tool="business.sales",
            status="error",
            error_category="authorization",
            error_code="PERMISSION_DENIED",
        )
    )
    sink.handle(
        ObsEvent(
            name=EventName.LLM_CALL_COMPLETED,
            status="ok",
            duration_ms=5.0,
            attributes={
                "model": "claude-opus-5-5",
                "llm_stage": "planner",
                "input_tokens": 100,
                "output_tokens": 20,
            },
        )
    )
    sink.handle(ObsEvent(name=EventName.VERIFICATION_RESULT, attributes={"replaced": True}))
    sink.handle(ObsEvent(name=EventName.TIMEOUT, attributes={"scope": "domain"}))
    sink.handle(ObsEvent(name=EventName.AUTH_FAILED, error_code="INVALID_CREDENTIALS"))
    assert registry.counter("agent_turns_total", outcome="completed") == 1
    assert registry.histogram("agent_stage_duration_ms", stage="planner") is not None
    assert registry.counter("agent_security_refusals_total", kind="authorization") == 1
    assert registry.counter("agent_security_refusals_total", kind="authentication") == 1
    assert registry.counter("agent_llm_tokens_total", direction="input") == 100
    assert registry.counter("agent_grounding_total", result="replaced") == 1
    assert registry.counter("agent_timeouts_total", scope="domain") == 1


# =============================================================================================
# spans and correlation
# =============================================================================================


def test_span_records_failure_and_parenting() -> None:
    with (
        capture_events() as events,
        bind_trace(request_id="req1", turn_id="turn1", thread_id="t1"),
        pytest.raises(e.ToolExecutionError),
        span(
            Stage.TOOL,
            started=EventName.TOOL_STARTED,
            completed=EventName.TOOL_COMPLETED,
            failed=EventName.TOOL_FAILED,
            tool="system.x",
        ) as outer,
    ):
        emit(EventName.RETRY_ATTEMPTED, attempt=2)
        raise e.ToolExecutionError("boom")
    _, retry, failed = events.events
    assert failed.name is EventName.TOOL_FAILED and failed.error_category == "tool"
    assert retry.parent_span_id == outer.span_id == failed.span_id
    assert {ev.request_id for ev in events.events} == {"req1"}
    assert {ev.turn_id for ev in events.events} == {"turn1"}


def test_logging_sink_levels(caplog: pytest.LogCaptureFixture) -> None:
    sink = LoggingSink()
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"):
        sink.handle(ObsEvent(name=EventName.TOOL_FAILED, status="error"))
        sink.handle(ObsEvent(name=EventName.CHECKPOINT_SAVED, status="ok"))
        sink.handle(ObsEvent(name=EventName.TURN_COMPLETED, status="ok"))
    levels = {r.getMessage(): r.levelno for r in caplog.records}
    assert levels == {
        "tool.failed": logging.WARNING,
        "checkpoint.saved": logging.DEBUG,
        "turn.completed": logging.INFO,
    }


def test_a_broken_sink_never_breaks_emit() -> None:
    from agentic_platform.observability import hub

    class Broken:
        def handle(self, event: ObsEvent) -> None:
            raise RuntimeError("sink down")

    token = hub._extra_sinks.set((Broken(),))
    try:
        assert emit(EventName.TIMEOUT) is not None
    finally:
        hub._extra_sinks.reset(token)


# =============================================================================================
# checkpoint instrumentation (brief § 14)
# =============================================================================================


def _config() -> Any:
    return {"configurable": {"thread_id": "acme/t1", "checkpoint_ns": ""}}


def test_checkpoint_load_and_save_are_observed() -> None:
    saver = instrument_checkpointer(InMemorySaver(serde=checkpoint_serializer()))
    assert instrument_checkpointer(saver) is saver  # idempotent
    assert isinstance(saver, InMemorySaver)  # the class is unchanged

    async def scenario() -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        await saver.aput(_config(), empty_checkpoint(), {}, {})
        assert await saver.aget_tuple(_config()) is not None

    with capture_events() as events:
        asyncio.run(scenario())
    assert events.names() == ["checkpoint.saved", "checkpoint.loaded"]
    assert events.events[1].attributes == {"operation": "load", "found": True}


class _ExplodingSerde:
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        raise TypeError("Object of type Secret(Rahul) is not serializable")

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        raise ValueError("corrupted msgpack: \\x93Rahul")


def test_checkpoint_serialization_failure_is_classified_and_observable() -> None:
    saver = instrument_checkpointer(InMemorySaver(serde=_ExplodingSerde()))

    async def scenario() -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        await saver.aput(_config(), empty_checkpoint(), {}, {})

    with capture_events() as events, pytest.raises(e.CheckpointSerializationError) as raised:
        asyncio.run(scenario())
    assert "Rahul" not in str(raised.value) and raised.value.__cause__ is None
    (failed,) = events.of(EventName.CHECKPOINT_SAVE_FAILED)
    assert (failed.error_category, failed.error_code) == (
        "serialization",
        "CHECKPOINT_SERIALIZATION_FAILED",
    )


def test_corrupted_checkpoint_load_fails_safely() -> None:
    inner = InMemorySaver(serde=checkpoint_serializer())

    async def scenario() -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        await inner.aput(_config(), empty_checkpoint(), {}, {})
        inner.serde = _ExplodingSerde()  # now unreadable
        await instrument_checkpointer(inner).aget_tuple(_config())

    with capture_events() as events, pytest.raises(e.CheckpointError) as raised:
        asyncio.run(scenario())
    assert raised.value.code == "CHECKPOINT_UNREADABLE" and "Rahul" not in str(raised.value)
    (failed,) = events.of(EventName.CHECKPOINT_LOAD_FAILED)
    assert failed.error_category == "checkpoint"


def test_store_outage_keeps_its_platform_code() -> None:
    class Down(InMemorySaver):
        async def aget_tuple(self, config: Any) -> Any:
            raise e.DatabaseUnavailableError("down")

    saver = instrument_checkpointer(Down())
    with capture_events() as events, pytest.raises(e.DatabaseUnavailableError):
        asyncio.run(saver.aget_tuple(_config()))
    (failed,) = events.of(EventName.CHECKPOINT_LOAD_FAILED)
    assert failed.error_category == "dependency_unavailable"


class _Smuggled(BaseModel):
    payload: str = "rm -rf /"


def test_an_unlisted_type_is_rejected_observably_and_never_constructed() -> None:
    from agentic_platform.observability.checkpoints import install_serde_listener

    install_serde_listener()
    serde = checkpoint_serializer()
    blob = serde.dumps_typed({"value": _Smuggled()})
    with capture_events() as events:
        revived = serde.loads_typed(blob)
    assert not isinstance(revived["value"], _Smuggled)
    (rejected,) = events.of(EventName.CHECKPOINT_REJECTED)
    assert rejected.error_code == "CHECKPOINT_TYPE_REJECTED"
    assert rejected.attributes["type_name"] == "_Smuggled"


def test_the_allow_list_is_not_widened() -> None:
    from agentic_platform.agents.serialization import CHECKPOINT_TYPES

    assert len(CHECKPOINT_TYPES) == 9  # Phase 12's list, unchanged by Phase 13
    allowed = checkpoint_serializer()._allowed_msgpack_modules
    assert isinstance(allowed, set)  # an explicit list, not True ("allow everything")


# =============================================================================================
# diagnostics (brief § 17)
# =============================================================================================


def _turn_events() -> list[ObsEvent]:
    with capture_events() as events, bind_trace(request_id="r-1", turn_id="u-1", thread_id="t-1"):
        for stage in (Stage.PLANNER, Stage.SUPERVISOR, Stage.DOMAIN):
            with span(
                stage,
                started=EventName(f"{stage.value}.started"),
                completed=EventName(f"{stage.value}.completed"),
                failed=EventName(f"{stage.value}.failed"),
            ):
                if stage is Stage.SUPERVISOR:
                    emit(
                        EventName.SUPERVISOR_DECIDED,
                        domain="business",
                        attributes={"outcome": "ROUTE", "reason": "classified"},
                    )
                if stage is Stage.DOMAIN:
                    emit(
                        EventName.TOOL_COMPLETED,
                        tool="business.customer_outstanding",
                        status="ok",
                        duration_ms=2.0,
                    )
        emit(EventName.VERIFICATION_RESULT, attributes={"grounded": True, "replaced": False})
        emit(
            EventName.TURN_COMPLETED,
            status="ok",
            duration_ms=30.0,
            attributes={"outcome": "completed"},
        )
    return events.events


def test_diagnostics_render_the_turn_tree() -> None:
    text = TurnDiagnostics.from_events(_turn_events()).render()
    for line in (
        "request_id=r-1",
        "turn_id=u-1",
        "Planner",
        "Supervisor",
        "domain=business",
        "tool=business.customer_outstanding status=ok",
        "Response",
        "status=completed",
        "Total",
    ):
        assert line in text, line


def test_api_key_shaped_values_are_never_kept() -> None:
    assert safe_attributes({"value": "sk-ant-api03-AAAAAAAAAAAAAAAA"}) == {"value": REDACTED}
    assert safe_attributes({"input_tokens": 12}) == {"input_tokens": 12}


def test_diagnostics_rebuild_from_json_logs(caplog: pytest.LogCaptureFixture) -> None:
    formatter = JsonFormatter()
    lines: list[str] = []
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"):
        _turn_events()  # the hub's logging sink writes one line per event
    for record in caplog.records:
        _ContextFilter().filter(record)
        lines.append(formatter.format(record))
    lines.append("not json at all")
    lines.append(json.dumps({"event": "turn.completed", "kind": "audit", "request_id": "r-1"}))
    events = events_from_log_lines(lines, "r-1")
    assert len(events) == len(_turn_events())
    diag = TurnDiagnostics.from_events(events).to_dict()
    assert (diag["outcome"], diag["domain"]) == ("completed", "business")
    assert events_from_log_lines(lines, "other-request") == []


def test_diagnose_cli(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_platform.observability.diagnose import main

    log = tmp_path / "agent.log"
    rows = [json.dumps({"event": ev.name.value, **ev.log_fields()}) for ev in _turn_events()]
    log.write_text("\n".join(rows), encoding="utf-8")
    assert main(["--request-id", "r-1", "--file", str(log)]) == 0
    assert "domain=business" in capsys.readouterr().out
    assert main(["--request-id", "nope", "--file", str(log)]) == 1
