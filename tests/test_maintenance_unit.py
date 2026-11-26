"""Phase 6 scheduled maintenance without external servers: jobs apply only existing
expiry rules, run in bounded batches, are safe to repeat, survive partial failure,
record statistics (log + audit) and skip when another runner holds the job."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agentic_platform.agents.dependencies import AgentDependencies, build_dependencies
from agentic_platform.cache import InMemoryBackend, LockManager
from agentic_platform.core.config import AppSettings, MaintenanceSettings
from agentic_platform.core.errors import DatabaseUnavailableError
from agentic_platform.maintenance import (
    BatchResult,
    ExpiredIdempotencyJob,
    MaintenanceRunner,
    MaintenanceScheduler,
    build_maintenance_runner,
    default_jobs,
)
from agentic_platform.memory.adapters.in_memory import InMemoryMemoryStore
from agentic_platform.memory.models import Episode
from agentic_platform.security import SecurityAudit
from agentic_platform.security.policy import ApprovalPolicy
from agentic_platform.services.approvals import InMemoryApprovalStore
from tests.api_helpers import RecordingAuditor
from tests.conftest import principal
from tests.fakes import ScriptedLLM


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def deps_for(settings: AppSettings, auditor: RecordingAuditor) -> AgentDependencies:
    return build_dependencies(settings, llm=ScriptedLLM(), auditor=auditor)


def memory_store(deps: AgentDependencies) -> InMemoryMemoryStore:
    assert deps.memory is not None
    store = deps.memory._store
    assert isinstance(store, InMemoryMemoryStore)
    return store


def advance(store: Any, delta: timedelta) -> None:
    attribute = "_clock" if isinstance(store, InMemoryMemoryStore) else "now"
    real = getattr(store, attribute)
    setattr(store, attribute, lambda: real() + delta)


async def seed_memory(deps: AgentDependencies) -> None:
    assert deps.memory is not None
    asha = principal().memory_scope
    ravi = principal("globex", "ravi").memory_scope
    long_term = deps.memory.long_term
    await long_term.remember(asha, "prefs", "lang", "Prefers Hindi.", ttl=timedelta(hours=1))
    await long_term.remember(asha, "prefs", "tz", "Lives in IST.")  # no TTL: kept forever
    await long_term.remember(ravi, "prefs", "lang", "Prefers Tamil.", ttl=timedelta(hours=1))
    for i, scope in enumerate((asha, ravi)):
        await deps.memory.episodic.record(
            scope,
            Episode(thread_id=f"t{i}", request_id=f"r{i}", outcome="completed", summary="Hi"),
        )


def test_default_jobs_follow_configuration(settings: AppSettings) -> None:
    deps = deps_for(settings, RecordingAuditor())
    assert [j.name for j in default_jobs(deps)] == ["memory.expired", "approvals.expired"]
    no_memory = settings.model_copy(
        update={"memory": settings.memory.model_copy(update={"enabled": False})}
    )
    assert [j.name for j in default_jobs(deps_for(no_memory, RecordingAuditor()))] == [
        "approvals.expired"
    ]
    for job in default_jobs(deps):
        assert job.policy and job.description


def test_expired_memory_is_removed_and_the_rest_kept(settings: AppSettings) -> None:
    auditor = RecordingAuditor()
    deps = deps_for(settings, auditor)
    run(seed_memory(deps))
    store = memory_store(deps)
    runner = build_maintenance_runner(deps)

    nothing = run(runner.run_job("memory.expired"))  # nothing has expired yet
    assert (nothing.status, nothing.affected, nothing.complete) == ("ok", 0, True)
    assert len(store._rows) == 5

    advance(store, timedelta(hours=2))  # the two TTL facts expire (both tenants)
    facts = run(runner.run_job("memory.expired"))
    assert (facts.status, facts.affected, facts.details) == ("ok", 2, {"long_term": 2})
    assert len(store._rows) == 3

    advance(store, timedelta(days=91))  # episodes expire after 90 days
    episodes = run(runner.run_job("memory.expired"))
    assert (episodes.affected, episodes.details) == (2, {"episodic": 2})
    (kept,) = store._rows.values()
    assert kept.record.key == "tz" and kept.record.expires_at is None

    again = run(runner.run_job("memory.expired"))  # repeating is harmless
    assert (again.status, again.affected, again.errors) == ("ok", 0, 0)


def test_jobs_run_in_bounded_batches(settings: AppSettings) -> None:
    deps = deps_for(settings, RecordingAuditor())
    assert deps.memory is not None
    scope = principal().memory_scope

    async def seed() -> None:
        for i in range(5):
            await deps.memory.long_term.remember(  # type: ignore[union-attr]
                scope, "n", f"k{i}", f"fact {i}", ttl=timedelta(minutes=1)
            )

    run(seed())
    advance(memory_store(deps), timedelta(hours=1))
    small = MaintenanceRunner(
        default_jobs(deps), settings=MaintenanceSettings(batch_size=2, max_batches=2)
    )
    first = run(small.run_job("memory.expired"))
    assert (first.batches, first.processed, first.complete) == (2, 4, False)  # budget used up
    second = run(small.run_job("memory.expired"))
    assert (second.batches, second.processed, second.complete) == (1, 1, True)
    assert memory_store(deps)._rows == {}


def test_overdue_approvals_expire_and_open_ones_stay(settings: AppSettings) -> None:
    auditor = RecordingAuditor()
    deps = deps_for(settings, auditor)
    approvals = deps.security.approvals

    async def request(tenant: str, ttl: int | None) -> Any:
        return await approvals.request(
            principal(tenant, "asha"), operation="system.test_op", operation_hash="h" * 64,
            summary="op", required_permission="tool.execute", risk="high",
            policy=ApprovalPolicy(ttl_seconds=ttl), thread_id="t1", request_id="r1",
        )  # fmt: skip

    short_acme = run(request("acme", 60))
    short_globex = run(request("globex", 60))
    long_lived = run(request("acme", 7 * 24 * 3600))
    store = approvals.store
    assert isinstance(store, InMemoryApprovalStore)
    advance(store, timedelta(hours=1))

    runner = build_maintenance_runner(deps)
    report = run(runner.run_job("approvals.expired"))
    assert (report.status, report.affected) == ("ok", 2)
    for record, status in ((short_acme, "expired"), (short_globex, "expired"),
                           (long_lived, "pending")):  # fmt: skip
        assert store._rows[record.id].status == status
    expired_events = auditor.of("approval.expired")
    assert {(e.tenant_id, e.actor_type) for e in expired_events} == {
        ("acme", "system"),
        ("globex", "system"),
    }
    assert run(runner.run_job("approvals.expired")).affected == 0  # idempotent
    assert len(auditor.of("approval.expired")) == 2


class FlakyJob:
    """Two good batches, then a failure."""

    name = "test.flaky"
    description = "test"
    policy = "test"

    def __init__(self, error: Exception, good_batches: int = 2) -> None:
        self.error = error
        self.calls = 0
        self.good = good_batches

    async def run_batch(self, limit: int) -> BatchResult:
        self.calls += 1
        if self.calls > self.good:
            raise self.error
        return BatchResult(processed=limit, affected=limit, details={"rows": limit})


class CountingJob:
    name = "test.counting"
    description = "test"
    policy = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def run_batch(self, limit: int) -> BatchResult:
        self.calls += 1
        return BatchResult(processed=0, affected=0)


def test_partial_failure_is_reported_and_other_jobs_still_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    auditor = RecordingAuditor()
    flaky = FlakyJob(DatabaseUnavailableError("connection refused"))
    crashing = FlakyJob(RuntimeError("secret detail"), good_batches=0)
    crashing.name = "test.crashing"
    counting = CountingJob()
    runner = MaintenanceRunner(
        [flaky, crashing, counting],
        settings=MaintenanceSettings(batch_size=10),
        audit=SecurityAudit(auditor),
    )
    caplog.set_level(logging.INFO, logger="agentic_platform")
    reports = run(runner.run_all())
    partial, failed, ok = reports
    assert (partial.status, partial.batches, partial.processed, partial.errors) == (
        "partial",
        2,
        20,
        1,
    )
    assert partial.error_codes == ["DATABASE_UNAVAILABLE"] and not partial.complete
    assert (failed.status, failed.batches, failed.error_codes) == (
        "failed",
        0,
        ["INTERNAL_ERROR"],
    )
    assert (ok.status, counting.calls) == ("ok", 1)
    assert "secret detail" not in json.dumps([r.model_dump(mode="json") for r in reports])
    # statistics recorded in the audit trail as system events
    events = auditor.of("maintenance.run")
    assert [(e.tool_name, e.status, e.actor_type) for e in events] == [
        ("test.flaky", "error", "system"),
        ("test.crashing", "error", "system"),
        ("test.counting", "ok", "system"),
    ]
    first = events[0]
    assert first.tenant_id is None and first.request_id == partial.run_id
    assert first.error_code == "DATABASE_UNAVAILABLE"
    for field in ("job", "status", "batches", "processed", "affected", "errors", "started_at",
                  "finished_at", "duration_ms", "complete"):  # fmt: skip
        assert field in first.details, field
    assert first.details["processed"] == 20 and first.details["details"] == {"rows": 20}
    # and in the logs (counts and codes only)
    finished = [r for r in caplog.records if r.getMessage() == "maintenance.job.finished"]
    assert [r.fields["job"] for r in finished] == [  # type: ignore[attr-defined]
        "test.flaky",
        "test.crashing",
        "test.counting",
    ]
    assert finished[0].fields["processed"] == 20  # type: ignore[attr-defined]
    assert "secret detail" not in " ".join(str(r.__dict__.get("fields")) for r in finished)


def test_a_job_held_by_another_runner_is_skipped() -> None:
    locks = LockManager(InMemoryBackend())
    counting = CountingJob()
    runner = MaintenanceRunner([counting], locks=locks)

    async def body() -> Any:
        async with locks.hold("maintenance:test.counting", ttl_seconds=30, wait_seconds=0):
            return await runner.run_job("test.counting")

    report = run(body())
    assert (report.status, counting.calls) == ("skipped", 0)
    assert run(runner.run_job("test.counting")).status == "ok"  # free again


def test_runner_rejects_unknown_and_duplicate_jobs() -> None:
    from agentic_platform.core.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        MaintenanceRunner([CountingJob(), CountingJob()])
    with pytest.raises(ConfigurationError):
        run(MaintenanceRunner([CountingJob()]).run_job("nope"))


def test_idempotency_job_purges_in_batches() -> None:
    class FakeIdempotency:
        def __init__(self) -> None:
            self.remaining = 7

        async def purge_expired(self, limit: int | None = None) -> int:
            assert limit is not None
            purged = min(limit, self.remaining)
            self.remaining -= purged
            return purged

    service = FakeIdempotency()
    runner = MaintenanceRunner(
        [ExpiredIdempotencyJob(service)],  # type: ignore[arg-type]
        settings=MaintenanceSettings(batch_size=3),
    )
    report = run(runner.run_job("idempotency.expired"))
    assert (report.batches, report.affected, report.complete) == (3, 7, True)


def test_scheduler_runs_until_stopped() -> None:
    counting = CountingJob()
    scheduler = MaintenanceScheduler(MaintenanceRunner([counting]), interval_seconds=0.01)

    async def body() -> None:
        scheduler.start()
        while scheduler.runs < 3:
            await asyncio.sleep(0.01)
        await scheduler.stop()

    run(asyncio.wait_for(body(), timeout=10))
    assert counting.calls >= 3
    with pytest.raises(ValueError):
        MaintenanceScheduler(MaintenanceRunner([]), interval_seconds=0)


def test_cli_and_worker_run_the_jobs(capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_platform.__main__ import main as cli
    from agentic_platform.maintenance.__main__ import main as worker

    assert cli(["--memory-purge"]) == 0
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["job"] for r in reports] == ["memory.expired", "approvals.expired"]
    assert all(r["status"] == "ok" for r in reports)

    assert worker(["--list"]) == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [j["job"] for j in listed] == ["memory.expired", "approvals.expired"]
    assert worker(["--job", "approvals.expired"]) == 0
    (report,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert report["job"] == "approvals.expired" and report["status"] == "ok"
    assert datetime.fromisoformat(report["finished_at"]) <= datetime.now(UTC)
