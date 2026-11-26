"""What the API process holds for its lifetime: one agent, one authenticator, services.

Built once by `create_app` (configuration errors stop the process at startup, not
on the first request). Route handlers get it through `api.dependencies`.

Phase 15 (ADR-064): it also counts in-flight requests, caps concurrent agent turns and
owns the shutdown sequence (`services/lifecycle.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_platform.agents.agent import Agent
from agentic_platform.agents.dependencies import AgentDependencies
from agentic_platform.application import ApprovalWorkflow, AuditReader
from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import FeatureUnavailableError
from agentic_platform.core.production import llm_credential_source
from agentic_platform.maintenance import MaintenanceScheduler, build_maintenance_runner
from agentic_platform.memory.authorized import AuthorizedMemory
from agentic_platform.observability.hub import HUB
from agentic_platform.security.authentication import Authenticator
from agentic_platform.security.identity_provider import build_authenticator
from agentic_platform.services.lifecycle import (
    InFlight,
    ShutdownReport,
    ShutdownStep,
    TurnSlots,
    in_daemon_thread,
    run_shutdown,
)
from agentic_platform.services.rate_limit import RateLimiter
from agentic_platform.services.readiness import ReadinessService


@dataclass
class ApiRuntime:
    settings: AppSettings
    agent: Agent
    authenticator: Authenticator
    readiness: ReadinessService
    approvals: ApprovalWorkflow
    audit: AuditReader
    scheduler: MaintenanceScheduler | None = None
    # None = rate limiting off (RATE_LIMIT_ENABLED; on by default in production only).
    rate_limiter: RateLimiter | None = None
    # Phase 15: requests in progress (the middleware counts them) and the turn cap.
    in_flight: InFlight = field(default_factory=InFlight)
    turn_slots: TurnSlots = field(default_factory=lambda: TurnSlots(0))

    @property
    def deps(self) -> AgentDependencies:
        return self.agent.dependencies

    def memory(self) -> AuthorizedMemory:
        memory = self.deps.authorized_memory()
        if memory is None:
            raise FeatureUnavailableError(
                "Memory is disabled (MEMORY_ENABLED=false).",
                public_message="Memory is not available on this server.",
            )
        return memory

    def shutdown_steps(self) -> list[ShutdownStep]:
        """In order (ADR-064): let cancelled requests unwind, stop background work, close
        Redis, the Agent DB and the ERP pool, flush observability."""
        budget = self.settings.api.shutdown_cleanup_seconds
        # At most half the budget: closing the pools must keep its share.
        steps = [ShutdownStep("in_flight_requests", self.in_flight.wait_idle, budget / 2)]
        if self.scheduler is not None:
            steps.append(ShutdownStep("maintenance", self._stop_scheduler))
        steps += [ShutdownStep(name, close) for name, close in self.deps.closers()]
        timeout = self.settings.observability.flush_timeout_seconds
        steps.append(
            ShutdownStep("observability", lambda: in_daemon_thread(lambda: HUB.flush(timeout)))
        )
        return steps

    async def _stop_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.request_stop()  # the batch in progress commits, nothing new starts
            await self.scheduler.stop()

    async def aclose(self) -> ShutdownReport:
        """The bounded shutdown sequence (API_SHUTDOWN_CLEANUP_SECONDS). Never raises."""
        return await run_shutdown(
            self.shutdown_steps(), budget_seconds=self.settings.api.shutdown_cleanup_seconds
        )


def build_runtime(
    settings: AppSettings,
    *,
    agent: Agent | None = None,
    authenticator: Authenticator | None = None,
) -> ApiRuntime:
    agent = agent or Agent.from_settings(settings)
    deps = agent.dependencies
    if authenticator is None:
        authenticator = build_authenticator(
            settings, deps.security.resolver, deps.security.audit
        )
    scheduler = (
        MaintenanceScheduler(
            build_maintenance_runner(deps),
            interval_seconds=settings.maintenance.interval_seconds,
        )
        if settings.api.run_maintenance
        else None
    )
    return ApiRuntime(
        settings=settings,
        agent=agent,
        authenticator=authenticator,
        readiness=ReadinessService(
            database=deps.database,
            redis=deps.cache.redis,
            production=settings.env == "production",
            authentication_configured=True,
            # Other providers registered through llm.factory manage their own credentials.
            llm_configured=settings.llm.provider != "anthropic"
            or llm_credential_source(settings) is not None,
            business=deps.business,
        ),
        approvals=ApprovalWorkflow(agent),
        audit=AuditReader(deps.security.authorizer, deps.database),
        scheduler=scheduler,
        rate_limiter=RateLimiter(deps.cache.backend, settings.rate_limit)
        if settings.rate_limit_enabled
        else None,
        turn_slots=TurnSlots(settings.api.max_concurrent_turns),
    )
