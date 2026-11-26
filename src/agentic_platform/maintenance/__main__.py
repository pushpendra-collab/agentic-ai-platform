"""Maintenance worker.

    python -m agentic_platform.maintenance                # every job once, then exit (cron)
    python -m agentic_platform.maintenance --job memory.expired
    python -m agentic_platform.maintenance --loop         # long-running worker / local scheduler
    python -m agentic_platform.maintenance --list         # jobs and the rule each applies
    python -m agentic_platform.maintenance --check-config # production policy report

Runs as a trusted operator process (system actor), separate from the API process.
Prints one JSON report per job run; exit code 1 when any job failed or the run was
stopped early, 2 on a configuration error.

Production (Phase 7, ADR-050): refuses to start while the production policy reports
an error (`core/production.py`, role `worker`). SIGTERM / SIGINT stop gracefully:
the batch in progress commits, the remaining batches and jobs wait for the next run.
Phase 15: the connections are then closed by the same bounded sequence as the API
(services/lifecycle.py: Redis, Agent DB, ERP pool, observability), so a hung dependency
cannot keep a stopping worker alive.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys

from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import get_settings
from agentic_platform.core.errors import AgentPlatformError
from agentic_platform.core.logging import configure_logging, get_logger
from agentic_platform.core.production import check_configuration, enforce_production_policy
from agentic_platform.maintenance import (
    JobRunReport,
    MaintenanceRunner,
    MaintenanceScheduler,
    build_maintenance_runner,
)
from agentic_platform.observability.hub import HUB
from agentic_platform.services.lifecycle import ShutdownStep, in_daemon_thread, run_shutdown

logger = get_logger(__name__)


def _print(reports: list[JobRunReport]) -> None:
    for report in reports:
        print(report.model_dump_json())


def _install_stop_handlers(target: MaintenanceRunner | MaintenanceScheduler) -> None:
    """SIGTERM/SIGINT → graceful stop. Windows event loops cannot install signal handlers
    (Ctrl+C still raises KeyboardInterrupt there); containers run Linux."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, target.request_stop)


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.logging)
    if args.check_config:
        report = check_configuration(settings, role="worker")
        print(report.model_dump_json(indent=2))
        return 1 if report.errors else 0
    for issue in enforce_production_policy(settings, role="worker").warnings:
        logger.warning("config.production.warning", extra={"fields": issue.model_dump()})
    deps = build_dependencies(settings)
    runner = build_maintenance_runner(deps)
    try:
        if args.list:
            for job in runner.jobs:
                print(json.dumps({"job": job.name, "description": job.description,
                                  "policy": job.policy}))  # fmt: skip
            return 0
        if args.loop:
            scheduler = MaintenanceScheduler(
                runner, interval_seconds=settings.maintenance.interval_seconds
            )
            _install_stop_handlers(scheduler)
            await scheduler.run_forever()
            return 0
        _install_stop_handlers(runner)
        if args.job:
            reports = []
            for name in args.job:
                if runner.stopping:
                    break
                reports.append(await runner.run_job(name))
        else:
            reports = await runner.run_all()
        _print(reports)
        failed = any(r.status in ("failed", "partial") for r in reports)
        return 1 if failed or runner.stopping else 0
    finally:
        timeout = settings.observability.flush_timeout_seconds
        steps = [ShutdownStep(name, close) for name, close in deps.closers()]
        steps.append(
            ShutdownStep("observability", lambda: in_daemon_thread(lambda: HUB.flush(timeout)))
        )
        await run_shutdown(steps, budget_seconds=settings.api.shutdown_cleanup_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic_platform.maintenance")
    parser.add_argument("--job", action="append", help="Run only this job (repeatable).")
    parser.add_argument("--loop", action="store_true", help="Run every interval until stopped.")
    parser.add_argument("--list", action="store_true", help="List the jobs and exit.")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print the production configuration report (no values) and exit.",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0
    except AgentPlatformError as exc:
        print(f"Maintenance error: {exc.detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
