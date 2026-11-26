"""Everything the graph nodes need, wired once and injected explicitly."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agentic_platform.agents.domains import DomainRegistry, GeneralDomainHandler
from agentic_platform.agents.domains.business import BusinessDomainHandler
from agentic_platform.business.factory import build_erp_port
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.business.service import BusinessReadService
from agentic_platform.cache import CacheRuntime, InMemoryBackend, LockManager, build_cache_runtime
from agentic_platform.core.config import AppSettings
from agentic_platform.db.engine import Database
from agentic_platform.llm import LLMProvider, create_llm_provider
from agentic_platform.memory import MemoryManager, build_memory_manager
from agentic_platform.memory.authorized import AuthorizedMemory
from agentic_platform.memory.ports import Embedder
from agentic_platform.observability.llm import ObservedLLM
from agentic_platform.prompts import PromptRegistry
from agentic_platform.security.audit import Auditor, SecurityAudit
from agentic_platform.security.authentication import (
    HmacTokenAuthenticator,
    LocalIdentity,
    build_token_authenticator,
)
from agentic_platform.security.authorization import Authorizer
from agentic_platform.security.identity import IdentityResolver
from agentic_platform.security.permissions import RoleCatalog
from agentic_platform.services.approvals import (
    ApprovalService,
    InMemoryApprovalStore,
    PostgresApprovalStore,
)
from agentic_platform.services.audit import AuditService
from agentic_platform.services.clock import ClockService
from agentic_platform.services.conversations import (
    ConversationService,
    InMemoryConversationStore,
    PostgresConversationStore,
)
from agentic_platform.services.database_health import DatabaseHealthService
from agentic_platform.services.idempotency import IdempotencyService
from agentic_platform.tools import ToolRegistry
from agentic_platform.tools.business import register_business_tools
from agentic_platform.tools.database import database_health_tool
from agentic_platform.tools.memory import MEMORY_DOMAIN, remember_fact_tool
from agentic_platform.tools.system import register_system_tools


def _local_cache() -> CacheRuntime:
    return CacheRuntime(backend=InMemoryBackend(), redis=None)


@dataclass(frozen=True)
class SecurityServices:
    """Identity, authorization, ownership, approvals and audit (Phase 5)."""

    resolver: IdentityResolver
    audit: SecurityAudit
    authorizer: Authorizer
    conversations: ConversationService
    approvals: ApprovalService
    local_identity: LocalIdentity
    # None without AUTH_TOKEN_SECRET.
    tokens: HmacTokenAuthenticator | None = None


def build_security(
    settings: AppSettings, database: Database | None, *, auditor: Auditor | None = None
) -> SecurityServices:
    """PostgreSQL-backed (RLS) with a database; process-local stores otherwise.

    `auditor` overrides the audit sink (default: `AuditService` when a database is
    configured, none otherwise)."""
    if auditor is None and database is not None:
        auditor = AuditService(database)
    audit = SecurityAudit(auditor)
    authorizer = Authorizer(audit)
    resolver = IdentityResolver(RoleCatalog.from_json(settings.auth.role_catalog_json))
    return SecurityServices(
        resolver=resolver,
        audit=audit,
        authorizer=authorizer,
        conversations=ConversationService(
            PostgresConversationStore(database)
            if database is not None
            else InMemoryConversationStore(),
            authorizer,
        ),
        approvals=ApprovalService(
            PostgresApprovalStore(database)
            if database is not None
            else InMemoryApprovalStore(auditor=auditor),
            authorizer,
            settings.approval,
        ),
        local_identity=LocalIdentity(settings, resolver),
        tokens=build_token_authenticator(settings, resolver, audit),
    )


@dataclass(frozen=True)
class AgentDependencies:
    settings: AppSettings
    llm: LLMProvider
    prompts: PromptRegistry
    tools: ToolRegistry
    domains: DomainRegistry
    security: SecurityServices
    # None when DATABASE_URL is not set: the agent still runs, without database tools.
    database: Database | None = None
    # Redis when REDIS_URL is set, otherwise a process-local fallback (ADR-030).
    cache: CacheRuntime = field(default_factory=_local_cache)
    locks: LockManager = field(default_factory=lambda: LockManager(InMemoryBackend()))
    # Turn idempotency (needs the database; Redis is only its fast-path).
    idempotency: IdempotencyService | None = None
    # Agent memory (None when MEMORY_ENABLED=false). Callers acting for a user go
    # through `authorized_memory()`; the raw manager is for trusted platform code.
    memory: MemoryManager | None = None
    # Phase 10: read-only business layer (None while ERP_ADAPTER=none).
    business: BusinessReadService | None = None

    def authorized_memory(self) -> AuthorizedMemory | None:
        if self.memory is None:
            return None
        return AuthorizedMemory(self.memory, self.security.authorizer, self.security.conversations)

    def closers(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        """Phase 15 (ADR-064): what to close at shutdown, in order: Redis, the Agent DB,
        the ERP read-only pool. Only what is configured. Each close leaves the component
        usable (pools reconnect lazily), so `Agent.run` can close after every call."""
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        if self.cache.redis is not None:
            steps.append(("redis", self.cache.redis.dispose))
        if self.database is not None:
            steps.append(("database", self.database.dispose))
        if self.business is not None:
            steps.append(("erp", self.business.aclose))
        return steps


def _observed(llm: LLMProvider) -> LLMProvider:
    return llm if isinstance(llm, ObservedLLM) else ObservedLLM(llm)


def build_dependencies(
    settings: AppSettings,
    *,
    llm: LLMProvider | None = None,
    clock: ClockService | None = None,
    prompts: PromptRegistry | None = None,
    database: Database | None = None,
    cache: CacheRuntime | None = None,
    embedder: Embedder | None = None,
    auditor: Auditor | None = None,
    erp_port: ErpReadPort | None = None,
) -> AgentDependencies:
    """`erp_port`: an already built ERP read port (tests; otherwise built from
    `settings.erp`). Only used when given; `ERP_ADAPTER=none` still applies otherwise."""
    if database is None and settings.database.configured:
        database = Database(settings.database)
    cache = cache or build_cache_runtime(settings.redis)
    prompts = prompts or PromptRegistry()
    if auditor is None and database is not None:
        auditor = AuditService(database)
    security = build_security(settings, database, auditor=auditor)

    idempotency = (
        IdempotencyService(
            database,
            fast_path=cache.backend if cache.shared else None,
            fast_path_ttl_seconds=settings.redis.idempotency_cache_ttl_seconds,
        )
        if database is not None
        else None
    )

    # With a database, every tool call is recorded in audit_log (ADR-011). Protected
    # calls go through human approval (ADR-040).
    tools = ToolRegistry(
        auditor=auditor,
        approvals=security.approvals,
        idempotency=idempotency,
        approval_settings=settings.approval,
    )
    register_system_tools(tools, clock or ClockService())
    if database is not None:
        tools.register(database_health_tool(DatabaseHealthService(database)))

    memory = build_memory_manager(
        settings, database=database, cache=cache, prompts=prompts, embedder=embedder
    )
    extra_domains: tuple[str, ...] = ()
    if memory is not None and settings.memory.agent_fact_writes == "approval":
        authorized = AuthorizedMemory(memory, security.authorizer, security.conversations)
        tools.register(remember_fact_tool(authorized))
        extra_domains = (MEMORY_DOMAIN,)

    domains = DomainRegistry()
    domains.register(
        GeneralDomainHandler(
            tools,
            max_tool_calls=settings.agent.max_tool_calls_per_turn,
            extra_tool_domains=extra_domains,
        )
    )

    # Phase 10 (ADR-059): business reads only when an ERP adapter is configured.
    clock = clock or ClockService()
    business: BusinessReadService | None = None
    port = erp_port if erp_port is not None else build_erp_port(settings, clock)
    if port is not None:
        business = BusinessReadService(port, settings=settings.erp, clock=clock, auditor=auditor)
        register_business_tools(tools, business, settings.erp)
        domains.register(
            BusinessDomainHandler(tools, max_tool_calls=settings.agent.max_tool_calls_per_turn)
        )

    return AgentDependencies(
        settings=settings,
        # Phase 13 (ADR-062): one observability event per LLM call (no extra call, no retry).
        llm=_observed(llm or create_llm_provider(settings.llm)),
        prompts=prompts,
        tools=tools,
        domains=domains,
        security=security,
        database=database,
        cache=cache,
        locks=LockManager(
            cache.backend, default_ttl_seconds=settings.agent.thread_lock_ttl_seconds
        ),
        idempotency=idempotency,
        memory=memory,
        business=business,
    )
