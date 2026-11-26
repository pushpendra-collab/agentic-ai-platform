"""Phase 5 without external servers: identity, authorization, conversation ownership,
tenant isolation, memory permissions, the human-in-the-loop approval lifecycle and
the security audit trail (in-memory stores, InMemorySaver, recording auditor)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.core.config import AppSettings, AuthSettings, MemorySettings
from agentic_platform.core.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalInvalidError,
    ApprovalNotFoundError,
    ApprovalPendingError,
    ApprovalStaleError,
    ConfigurationError,
    ConversationNotFoundError,
    CrossTenantAccessError,
    InvalidIdentityError,
    InvalidResumeContextError,
    MissingTenantError,
    PermissionDeniedError,
    ResourceNotFoundError,
    UnauthenticatedError,
)
from agentic_platform.memory.authorized import AuthorizedMemory
from agentic_platform.security import (
    PLATFORM_PERMISSIONS,
    Authorizer,
    HmacTokenAuthenticator,
    IdentityResolver,
    LocalIdentity,
    Permission,
    Principal,
    RoleCatalog,
    SecurityAudit,
)
from agentic_platform.security.policy import ApprovalPolicy, approval_required
from agentic_platform.services.approvals import (
    ApprovalService,
    InMemoryApprovalStore,
    OperationBinding,
)
from agentic_platform.services.audit import AuditEvent
from agentic_platform.services.clock import ClockService
from agentic_platform.services.conversations import ConversationService, InMemoryConversationStore
from agentic_platform.tools import ToolRegistry
from agentic_platform.tools.memory import REMEMBER_FACT
from agentic_platform.tools.system import SYSTEM_DOMAIN, register_system_tools
from tests.conftest import LOCAL_TENANT, principal, understanding
from tests.fakes import ScriptedLLM

SECRET = "s" * 40
FACT_ARGS = '{"key": "reply_language", "content": "Asha prefers Hindi replies."}'


class RecordingAuditor:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> int:
        self.events.append(event)
        return len(self.events)

    def actions(self) -> list[str]:
        return [e.action for e in self.events]

    def of(self, action: str) -> list[AuditEvent]:
        return [e for e in self.events if e.action == action]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# =============================================================================================
# Identity
# =============================================================================================


def _tokens(now: float = 1_000_000.0, audit: SecurityAudit | None = None) -> HmacTokenAuthenticator:
    return HmacTokenAuthenticator(
        SECRET,
        IdentityResolver(),
        issuer="test-issuer",
        max_ttl_seconds=7200,
        audit=audit,
        now=lambda: now,
    )


def test_token_round_trip_resolves_permissions_on_the_server() -> None:
    tokens = _tokens()
    token = tokens.issue(user_id="asha", tenant_id="acme", roles=["member", "approver"])
    p = run(tokens.authenticate(token))
    assert (p.user_id, p.tenant_id, p.auth_method) == ("asha", "acme", "token")
    assert p.roles == {"member", "approver"}
    assert p.has(Permission.CONVERSATION_WRITE) and p.has(Permission.APPROVAL_DECIDE)
    assert not p.has(Permission.MEMORY_SHARED_WRITE)
    assert p.session_id and p.memory_scope.tenant_id == "acme"


def test_token_cannot_carry_permissions_or_unknown_roles() -> None:
    tokens = _tokens()
    claims = {
        "v": 1, "iss": "test-issuer", "sub": "eve", "tid": "acme", "iat": 1_000_000,
        "exp": 1_000_600, "roles": ["superuser"], "permissions": sorted(PLATFORM_PERMISSIONS),
    }  # fmt: skip
    import base64

    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    forged = f"{payload}.{tokens._sign(payload)}"  # validly signed, but asks for too much
    p = run(tokens.authenticate(forged))
    assert p.roles == frozenset() and p.permissions == frozenset()


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda t: None, UnauthenticatedError),
        (lambda t: "", UnauthenticatedError),
        (lambda t: "not-a-token", InvalidIdentityError),
        (lambda t: t[:-3] + "abc", InvalidIdentityError),  # bad signature
        (lambda t: "x" + t, InvalidIdentityError),  # payload tampered
        (lambda t: t + ".extra", InvalidIdentityError),
    ],
)
def test_invalid_credentials_are_rejected(mutate: Callable[[str], Any], error: type) -> None:
    tokens = _tokens()
    token = tokens.issue(user_id="asha", tenant_id="acme", roles=["member"])
    with pytest.raises(error):
        run(tokens.authenticate(mutate(token)))


def test_expired_wrong_issuer_and_overlong_tokens_are_rejected() -> None:
    token = _tokens(now=1_000_000).issue(
        user_id="asha", tenant_id="acme", roles=["member"], ttl_seconds=60
    )
    with pytest.raises(InvalidIdentityError, match="expired"):
        run(_tokens(now=1_000_061).authenticate(token))
    other_issuer = HmacTokenAuthenticator(
        SECRET, IdentityResolver(), issuer="someone-else", max_ttl_seconds=7200
    )
    with pytest.raises(InvalidIdentityError):
        run(other_issuer.authenticate(token))
    with pytest.raises(ConfigurationError):
        _tokens().issue(user_id="a", tenant_id="t", roles=[], ttl_seconds=999_999)


def test_identity_without_tenant_or_with_invalid_ids() -> None:
    resolver = IdentityResolver()
    with pytest.raises(MissingTenantError):
        resolver.resolve(user_id="asha", tenant_id=None, roles=[], auth_method="token")
    with pytest.raises(MissingTenantError):
        resolver.resolve(user_id="asha", tenant_id="  ", roles=[], auth_method="token")
    with pytest.raises(InvalidIdentityError):
        resolver.resolve(user_id="a/b", tenant_id="acme", roles=[], auth_method="token")
    with pytest.raises(InvalidIdentityError):
        resolver.resolve(user_id="asha", tenant_id="ac me", roles=[], auth_method="token")
    tokens = _tokens()
    no_tenant = tokens.issue(user_id="asha", tenant_id="", roles=["member"])
    with pytest.raises(MissingTenantError):
        run(tokens.authenticate(no_tenant))


def test_failed_sign_in_is_audited_without_the_credential(caplog: pytest.LogCaptureFixture) -> None:
    auditor = RecordingAuditor()
    tokens = _tokens(audit=SecurityAudit(auditor))
    token = tokens.issue(user_id="asha", tenant_id="acme", roles=["member"])
    forged = token[:-4] + "AAAA"
    with caplog.at_level(logging.DEBUG, logger="agentic_platform"), pytest.raises(
        InvalidIdentityError
    ):
        run(tokens.authenticate(forged))
    (event,) = auditor.events
    assert (event.action, event.status, event.error_code) == (
        "auth.failed",
        "denied",
        "INVALID_CREDENTIALS",
    )
    assert event.tenant_id is None and event.actor_id is None  # nothing trusted
    dumped = event.model_dump_json() + caplog.text
    assert forged not in dumped and token.split(".")[1] not in dumped and SECRET not in dumped


def test_local_identity_is_development_only() -> None:
    resolver = IdentityResolver()
    dev = LocalIdentity(AppSettings(env="development"), resolver)
    p = dev.principal(tenant_id="acme", user_id="asha", roles=["member"])
    assert (p.tenant_id, p.user_id, p.auth_method) == ("acme", "asha", "local")
    default = dev.principal()
    assert (default.tenant_id, default.user_id, default.roles) == ("default", "local", {"member"})

    production = LocalIdentity(AppSettings(env="production"), resolver)
    with pytest.raises(UnauthenticatedError):
        production.principal()
    allowed = AppSettings(env="production", auth=AuthSettings(allow_local_identity=True))
    assert LocalIdentity(allowed, resolver).allowed
    denied = AppSettings(env="development", auth=AuthSettings(allow_local_identity=False))
    assert not LocalIdentity(denied, resolver).allowed


def test_settings_reject_short_token_secrets() -> None:
    with pytest.raises(ValueError):
        AuthSettings(token_secret="short")


def test_blank_auth_settings_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A copied .env.example has empty values: they must mean 'not set'."""
    for name in ("AUTH_TOKEN_SECRET", "AUTH_ALLOW_LOCAL_IDENTITY", "AUTH_ROLE_CATALOG_JSON"):
        monkeypatch.setenv(name, "")
    auth = AuthSettings()
    assert (auth.token_secret, auth.allow_local_identity, auth.role_catalog_json) == (
        None,
        None,
        None,
    )


def test_agent_refuses_anonymous_calls_when_local_identity_is_disabled(
    settings: AppSettings, clock: ClockService
) -> None:
    locked = settings.model_copy(update={"auth": AuthSettings(allow_local_identity=False)})
    agent = Agent(build_dependencies(locked, llm=ScriptedLLM(), clock=clock))
    with pytest.raises(UnauthenticatedError):
        agent.run("hello")
    with pytest.raises(UnauthenticatedError):
        agent.thread_status("t-1")


# =============================================================================================
# Role catalogue and authorization
# =============================================================================================


def test_role_catalogue_is_configurable_and_validated() -> None:
    catalog = RoleCatalog.from_json('{"clerk": ["conversation.write", "purchase.order.read"]}')
    assert catalog.permissions_for(["clerk", "unknown"]) == {
        "conversation.write",
        "purchase.order.read",  # future domain permissions plug in without code changes
    }
    with pytest.raises(ConfigurationError):
        RoleCatalog.from_json("{not json")
    with pytest.raises(ConfigurationError):
        RoleCatalog.from_json('{"clerk": ["DROP TABLE"]}')
    with pytest.raises(ConfigurationError):
        RoleCatalog.from_json('["member"]')
    assert RoleCatalog().permissions_for(["tenant_admin"]) == PLATFORM_PERMISSIONS


def test_authorizer_allows_denies_and_audits() -> None:
    auditor = RecordingAuditor()
    authorizer = Authorizer(SecurityAudit(auditor))
    member = principal()
    run(authorizer.require(member, Permission.MEMORY_WRITE))
    assert auditor.events == []
    with pytest.raises(PermissionDeniedError) as denied:
        run(authorizer.require(member, Permission.AUDIT_READ, resource="audit_log"))
    assert denied.value.public_message == "You do not have permission to do this."
    (event,) = auditor.events
    assert (event.action, event.status, event.tenant_id, event.actor_id) == (
        "authz.denied",
        "denied",
        "acme",
        "asha",
    )
    assert event.details["permission"] == "audit.read"


def test_cross_tenant_access_looks_like_not_found() -> None:
    auditor = RecordingAuditor()
    authorizer = Authorizer(SecurityAudit(auditor))
    with pytest.raises(CrossTenantAccessError) as caught:
        run(authorizer.require_tenant(principal("acme"), "globex", resource="approval:1"))
    missing = ResourceNotFoundError()
    assert (caught.value.code, caught.value.public_message) == (
        missing.code,
        missing.public_message,
    )
    assert "globex" not in caught.value.public_message
    assert auditor.events[0].details["reason"] == "cross_tenant"


def test_approval_is_required_for_writes_and_risky_tools() -> None:
    assert approval_required(is_write=True, requires_approval=False, risk="low", min_risk="never")
    assert approval_required(is_write=False, requires_approval=True, risk="low", min_risk="never")
    assert approval_required(is_write=False, requires_approval=False, risk="high", min_risk="high")
    assert not approval_required(
        is_write=False, requires_approval=False, risk="medium", min_risk="high"
    )


# =============================================================================================
# Tools: backend enforcement, the LLM cannot bypass it
# =============================================================================================


def test_tool_needs_its_permission_and_denials_are_audited(clock: ClockService) -> None:
    auditor = RecordingAuditor()
    registry = ToolRegistry(auditor=auditor)
    register_system_tools(registry, clock)
    no_tools = IdentityResolver(RoleCatalog({"viewer": ["conversation.read"]})).resolve(
        user_id="eve", tenant_id="acme", roles=["viewer"], auth_method="token"
    )
    result = run(
        registry.execute(
            "system.current_time", "{}", allowed_domains=(SYSTEM_DOMAIN,), principal=no_tools
        )
    )
    assert (result.status, result.error_code) == ("error", "PERMISSION_DENIED")
    (event,) = auditor.events
    assert (event.status, event.error_code, event.tenant_id, event.actor_id) == (
        "denied",
        "PERMISSION_DENIED",
        "acme",
        "eve",
    )
    # The model is not even shown tools the user cannot run.
    assert registry.specs([SYSTEM_DOMAIN], principal=no_tools) == []
    assert [s.name for s in registry.specs([SYSTEM_DOMAIN], principal=principal())] == [
        "system.current_time"
    ]


def test_llm_planned_tool_call_without_permission_is_refused(
    settings: AppSettings, clock: ClockService
) -> None:
    viewer = IdentityResolver(
        RoleCatalog({"viewer": ["conversation.write", "conversation.read"]})
    ).resolve(user_id="eve", tenant_id="acme", roles=["viewer"], auth_method="token")
    llm = ScriptedLLM(
        script=[understanding(tool_calls=[("system.current_time", "{}")]), "I can't check."]
    )
    agent = Agent(build_dependencies(settings, llm=llm, clock=clock))
    response = agent.run("What time is it?", principal=viewer)
    assert response.status == "completed"
    (result,) = response.tool_results
    assert (result.status, result.error_code) == ("error", "PERMISSION_DENIED")
    assert "system.current_time" not in llm.calls[0].system  # not offered to the model


# =============================================================================================
# Conversation ownership and tenant isolation
# =============================================================================================


def _agent(
    settings: AppSettings, clock: ClockService, *script: object, auditor: Any = None
) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(script=list(script))
    deps = build_dependencies(settings, llm=llm, clock=clock, auditor=auditor)
    return Agent(deps, checkpointer=InMemorySaver()), llm


def test_other_users_cannot_use_a_conversation(settings: AppSettings, clock: ClockService) -> None:
    agent, _ = _agent(settings, clock, understanding(), "Hello Asha.")
    asha, ravi = principal("acme", "asha"), principal("acme", "ravi")
    assert agent.run("Hi", thread_id="t-private", principal=asha).status == "completed"

    with pytest.raises(ConversationNotFoundError):
        agent.run("Show me", thread_id="t-private", principal=ravi)
    with pytest.raises(ConversationNotFoundError):
        agent.resume("t-private", principal=ravi)
    with pytest.raises(ConversationNotFoundError):
        agent.forget_conversation("t-private", principal=ravi)
    assert agent.thread_status("t-private", principal=ravi).exists is False
    assert agent.thread_status("t-private", principal=asha).exists is True
    # "not yours" and "missing" are indistinguishable from outside
    with pytest.raises(ConversationNotFoundError) as missing:
        agent.resume("t-nothing", principal=ravi)
    with pytest.raises(ConversationNotFoundError) as other:
        agent.resume("t-private", principal=ravi)
    assert (missing.value.code, missing.value.public_message) == (
        other.value.code,
        other.value.public_message,
    )


def test_tenants_never_share_a_conversation_even_with_the_same_id(
    settings: AppSettings, clock: ClockService
) -> None:
    agent, llm = _agent(
        settings, clock, understanding(), "Noted, acme.", understanding(), "Hello globex."
    )
    agent.run("Our secret code is 7781", thread_id="same-id", principal=principal("acme", "u1"))
    response = agent.run("Hello", thread_id="same-id", principal=principal("globex", "u1"))
    assert response.status == "completed"
    globex_prompt = " ".join(m.content for m in llm.calls[2].messages)
    assert "7781" not in globex_prompt  # globex's thread starts empty
    status = agent.thread_status("same-id", principal=principal("globex", "u1"))
    assert status.message_count == 2  # only globex's own turn


def test_conversation_needs_permission(settings: AppSettings, clock: ClockService) -> None:
    agent, _ = _agent(settings, clock)
    reader = IdentityResolver(RoleCatalog({"r": ["conversation.read"]})).resolve(
        user_id="eve", tenant_id="acme", roles=["r"], auth_method="token"
    )
    with pytest.raises(PermissionDeniedError):
        agent.run("hello", principal=reader)


def test_forget_conversation_removes_state_and_is_audited(
    settings: AppSettings, clock: ClockService
) -> None:
    auditor = RecordingAuditor()
    agent, _ = _agent(settings, clock, understanding(), "Hi.", auditor=auditor)
    asha = principal()
    agent.run("Hello", thread_id="t-forget", principal=asha)
    counts = agent.forget_conversation("t-forget", principal=asha)
    assert counts["episodic"] == 1 and counts["approvals_cancelled"] == 0
    assert agent.thread_status("t-forget", principal=asha).exists is False
    (event,) = auditor.of("conversation.forget")
    assert (event.tenant_id, event.actor_id) == ("acme", "asha")
    with pytest.raises(ConversationNotFoundError):
        agent.forget_conversation("t-forget", principal=asha)


def test_conversation_service_ownership_rules() -> None:
    service = ConversationService(InMemoryConversationStore(), Authorizer())
    asha, ravi = principal("acme", "asha"), principal("acme", "ravi")

    async def body() -> None:
        ref = await service.open_for_turn(asha, "t1")
        assert ref.storage_key == "acme/t1"
        assert (await service.open_for_turn(asha, "t1")).owner_user_id == "asha"
        with pytest.raises(ConversationNotFoundError):
            await service.open_for_turn(ravi, "t1")
        assert await service.find(ravi, "t1") is None
        assert await service.find(principal("globex", "asha"), "t1") is None
        assert [r.thread_id for r in await service.list_mine(asha)] == ["t1"]
        assert await service.list_mine(ravi) == []

    run(body())


# =============================================================================================
# Memory authorization
# =============================================================================================


def _memory(auditor: RecordingAuditor) -> AuthorizedMemory:
    deps = build_dependencies(
        AppSettings(env="test"), llm=ScriptedLLM(), auditor=auditor
    )
    memory = deps.authorized_memory()
    assert memory is not None
    return memory


def test_private_fact_write_update_delete_and_audit() -> None:
    auditor = RecordingAuditor()
    memory = _memory(auditor)
    asha = principal()

    async def body() -> None:
        first = await memory.remember_fact(asha, "prefs", "lang", "Prefers Hindi.")
        second = await memory.remember_fact(asha, "prefs", "lang", "Prefers Tamil.")
        assert first.id == second.id and first.user_id == "asha" and first.tenant_id == "acme"
        assert (await memory.get_fact(asha, "prefs", "lang")).content == "Prefers Tamil."  # type: ignore[union-attr]
        assert await memory.forget_fact(asha, "prefs", "lang") is True

    run(body())
    assert auditor.actions() == ["memory.fact.create", "memory.fact.update", "memory.fact.delete"]
    for event in auditor.events:
        assert "Hindi" not in event.model_dump_json() and "Tamil" not in event.model_dump_json()
    assert auditor.events[0].input_hash and auditor.events[0].tenant_id == "acme"


def test_shared_memory_needs_shared_permissions() -> None:
    auditor = RecordingAuditor()
    memory = _memory(auditor)
    member = principal("acme", "asha")
    manager = principal("acme", "kim", roles=("member", "knowledge_manager"))

    async def body() -> None:
        with pytest.raises(PermissionDeniedError):
            await memory.remember_fact(member, "company", "fy", "FY starts April.", shared=True)
        with pytest.raises(PermissionDeniedError):
            await memory.ingest_document(member, source_ref="hr", text="Leave is 18 days.")
        await memory.ingest_document(member, source_ref="note", text="Mine.", shared=False)
        await memory.remember_fact(manager, "company", "fy", "FY starts April.", shared=True)
        await memory.ingest_document(manager, source_ref="hr", text="Leave is 18 days.")
        await memory.define_procedure(manager, "greeting", "Greet formally.")
        # visible to the member (tenant-shared), but not deletable by them
        assert [f.content for f in await memory.list_facts(member)] == ["FY starts April."]
        with pytest.raises(PermissionDeniedError):
            await memory.forget_fact(member, "company", "fy", shared=True)
        with pytest.raises(PermissionDeniedError):
            await memory.delete_document(member, "hr")
        with pytest.raises(PermissionDeniedError):
            await memory.delete_procedure_version(member, "greeting", 1)
        assert await memory.forget_fact(manager, "company", "fy", shared=True) is True
        assert await memory.delete_document(manager, "hr") > 0

    run(body())
    denied = auditor.of("authz.denied")
    assert len(denied) == 5 and {e.details["permission"] for e in denied} == {
        "memory.shared.write",
        "memory.shared.delete",
    }
    assert "memory.document.write" in auditor.actions()
    assert "memory.procedure.write" in auditor.actions()


def test_memory_without_permissions_is_skipped_or_refused() -> None:
    auditor = RecordingAuditor()
    memory = _memory(auditor)
    nobody = IdentityResolver(RoleCatalog({"guest": []})).resolve(
        user_id="g", tenant_id="acme", roles=["guest"], auth_method="token"
    )
    asha = principal()

    async def body() -> None:
        await memory.remember_fact(asha, "prefs", "lang", "Prefers Hindi replies.")
        context = await memory.recall(nobody, thread_id="t", query="Hindi replies")
        assert context.is_empty()  # automatic recall is skipped, not failed
        with pytest.raises(PermissionDeniedError):
            await memory.remember_fact(nobody, "prefs", "lang", "x")
        with pytest.raises(PermissionDeniedError):
            await memory.list_facts(nobody)
        with pytest.raises(PermissionDeniedError):
            await memory.forget_me(nobody)
        # tenant isolation: the scope always comes from the principal
        assert await memory.list_facts(principal("globex", "asha")) == []
        assert await memory.forget_me(asha) == 1

    run(body())


# =============================================================================================
# Human-in-the-loop approvals
# =============================================================================================


@pytest.fixture
def hitl_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"memory": MemorySettings(agent_fact_writes="approval")})


def _remember_turn(hitl_settings: AppSettings, clock: ClockService, *after: object) -> Any:
    auditor = RecordingAuditor()
    agent, llm = _agent(
        hitl_settings,
        clock,
        understanding(intent="remember_preference", tool_calls=[(REMEMBER_FACT, FACT_ARGS)]),
        *after,
        auditor=auditor,
    )
    asha = principal()
    response = agent.run("Remember I prefer Hindi replies", thread_id="t-hitl", principal=asha)
    return agent, llm, auditor, asha, response


def _fact(agent: Agent, who: Principal) -> Any:
    memory = agent.dependencies.authorized_memory()
    assert memory is not None
    return run(memory.get_fact(who, "preferences", "reply_language"))


def test_memory_write_tool_is_off_by_default(settings: AppSettings) -> None:
    deps = build_dependencies(settings, llm=ScriptedLLM())
    assert REMEMBER_FACT not in {s.name for s in deps.tools.specs()}


def test_protected_operation_pauses_for_approval(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, llm, auditor, asha, response = _remember_turn(hitl_settings, clock)
    assert response.status == "interrupted"
    assert response.message == "This action needs approval before it can run."
    (interrupt,) = response.interrupts
    value = interrupt.value
    assert value["type"] == "approval_required" and value["operation"] == REMEMBER_FACT
    assert "Hindi" in value["summary"]
    (result,) = response.tool_results
    assert (result.status, result.approval_id) == ("pending_approval", value["approval_id"])
    assert _fact(agent, asha) is None  # nothing was written
    assert REMEMBER_FACT in llm.calls[0].system  # offered to the model

    approvals = agent.dependencies.security.approvals
    record = run(approvals.get(asha, value["approval_id"]))
    assert (record.status, record.requester_id, record.tenant_id) == ("pending", "asha", "acme")
    assert record.thread_id == "t-hitl" and record.request_id == response.request_id
    assert [r.id for r in run(approvals.list_open(asha))] == [record.id]
    status = agent.thread_status("t-hitl", principal=asha)
    assert status.interrupted and status.next_nodes == ["approval_gate"]
    assert "approval.requested" in auditor.actions()
    (requested,) = auditor.of("approval.requested")
    assert requested.request_id == response.request_id and requested.thread_id == "t-hitl"

    with pytest.raises(ApprovalPendingError):  # resumed before anyone decided
        agent.resume("t-hitl", principal=asha)
    with pytest.raises(Exception, match="unfinished"):  # the paused turn blocks new input
        agent.run("Something else", thread_id="t-hitl", principal=asha)


def test_approve_then_resume_executes_once_and_audits(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, _, auditor, asha, response = _remember_turn(
        hitl_settings, clock, "Done, I'll remember that."
    )
    approval_id = response.interrupts[0].value["approval_id"]
    approvals = agent.dependencies.security.approvals
    decided = run(approvals.decide(asha, approval_id, approve=True, note="yes"))
    assert (decided.status, decided.decided_by) == ("approved", "asha")

    resumed = agent.resume("t-hitl", principal=asha)
    assert resumed.status == "completed"
    executed = resumed.tool_results[-1]
    assert (executed.tool_name, executed.status, executed.approval_id) == (
        REMEMBER_FACT,
        "ok",
        approval_id,
    )
    fact = _fact(agent, asha)
    assert fact is not None and fact.content == "Asha prefers Hindi replies."
    assert fact.source_type == "agent" and fact.user_id == "asha"
    assert run(approvals.get(asha, approval_id)).status == "executed"

    actions = auditor.actions()
    for expected in (
        "approval.requested",
        "approval.decided",
        "memory.fact.create",
        "operation.executed",
    ):
        assert actions.count(expected) == 1, expected
    (decision,) = auditor.of("approval.decided")
    assert (decision.approval_decision, decision.approver_id) == ("approved", "asha")
    (memory_write,) = auditor.of("memory.fact.create")
    assert memory_write.actor_type == "agent" and memory_write.actor_id == "asha"
    # one correlation id from request to result
    request_id = response.request_id
    assert all(
        e.request_id == request_id
        for e in auditor.events
        if e.action in ("approval.requested", "operation.executed", "memory.fact.create")
    )

    # The approval is single-use: running it again does nothing.
    registry = agent.dependencies.tools
    pending = asyncio.run(_pending_action(registry, asha, approval_id, request_id))
    again = run(
        registry.execute_approved(
            pending, principal=asha, resume_value=None, thread_id="t-hitl", request_id=request_id
        )
    )
    assert (again.status, again.error_code) == ("error", "APPROVAL_ALREADY_USED")
    assert auditor.actions().count("memory.fact.create") == 1


async def _pending_action(
    registry: ToolRegistry, who: Principal, approval_id: str, request_id: str
) -> Any:
    from agentic_platform.tools.base import PendingAction
    from agentic_platform.tools.memory import RememberFactInput
    from agentic_platform.tools.registry import operation_hash

    tool_input = RememberFactInput.model_validate_json(FACT_ARGS)
    return PendingAction(
        approval_id=approval_id,
        tool_name=REMEMBER_FACT,
        arguments_json=tool_input.model_dump_json(),
        operation_hash=operation_hash(REMEMBER_FACT, tool_input),
        summary="s",
        risk="medium",
        expires_at="",
    )


def test_rejected_approval_never_executes(hitl_settings: AppSettings, clock: ClockService) -> None:
    agent, _, auditor, asha, response = _remember_turn(
        hitl_settings, clock, "Okay, I won't remember it."
    )
    approval_id = response.interrupts[0].value["approval_id"]
    run(agent.dependencies.security.approvals.decide(asha, approval_id, approve=False))
    resumed = agent.resume("t-hitl", principal=asha)
    assert resumed.status == "completed"
    assert (resumed.tool_results[-1].status, resumed.tool_results[-1].error_code) == (
        "error",
        "APPROVAL_REJECTED",
    )
    assert _fact(agent, asha) is None
    assert "operation.rejected" in auditor.actions()
    assert "memory.fact.create" not in auditor.actions()
    with pytest.raises(ApprovalAlreadyDecidedError):
        run(agent.dependencies.security.approvals.decide(asha, approval_id, approve=True))


def test_expired_approval_cannot_be_decided_or_used(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, _, auditor, asha, response = _remember_turn(hitl_settings, clock, "It expired.")
    approval_id = response.interrupts[0].value["approval_id"]
    approvals = agent.dependencies.security.approvals
    store = approvals.store
    assert isinstance(store, InMemoryApprovalStore)
    real_now = store.now
    store.now = lambda: real_now() + timedelta(days=2)  # past the 24 h default TTL

    with pytest.raises(ApprovalExpiredError):
        run(approvals.decide(asha, approval_id, approve=True))
    assert run(approvals.get(asha, approval_id)).status == "expired"
    resumed = agent.resume("t-hitl", principal=asha)  # an expired request may be resumed...
    assert resumed.tool_results[-1].error_code == "APPROVAL_EXPIRED"  # ...but never runs
    assert _fact(agent, asha) is None
    assert "approval.expired" in auditor.actions()


def test_approved_but_unused_approval_expires(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, _, _, asha, response = _remember_turn(hitl_settings, clock, "Too late.")
    approval_id = response.interrupts[0].value["approval_id"]
    approvals = agent.dependencies.security.approvals
    run(approvals.decide(asha, approval_id, approve=True))
    store = approvals.store
    assert isinstance(store, InMemoryApprovalStore)
    real_now = store.now
    store.now = lambda: real_now() + timedelta(days=2)
    resumed = agent.resume("t-hitl", principal=asha)
    assert resumed.tool_results[-1].error_code == "APPROVAL_EXPIRED"
    assert _fact(agent, asha) is None
    assert run(approvals.expire_due()) == 0  # already expired by the resume


def test_authorization_is_rechecked_after_resume(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, _, auditor, asha, response = _remember_turn(hitl_settings, clock, "Not allowed.")
    approval_id = response.interrupts[0].value["approval_id"]
    run(agent.dependencies.security.approvals.decide(asha, approval_id, approve=True))

    # Between approval and resume, Asha's role loses memory.write.
    demoted = IdentityResolver(
        RoleCatalog(
            {"chat_only": ["conversation.read", "conversation.write", "approval.request"]}
        )
    ).resolve(user_id="asha", tenant_id="acme", roles=["chat_only"], auth_method="token")
    resumed = agent.resume("t-hitl", principal=demoted)
    assert (resumed.tool_results[-1].status, resumed.tool_results[-1].error_code) == (
        "error",
        "PERMISSION_DENIED",
    )
    assert _fact(agent, asha) is None
    assert "memory.fact.create" not in auditor.actions()


def test_resume_by_another_actor_is_refused(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    agent, _, auditor, asha, response = _remember_turn(hitl_settings, clock)
    approval_id = response.interrupts[0].value["approval_id"]
    run(agent.dependencies.security.approvals.decide(asha, approval_id, approve=True))
    # Simulate an ownership record pointing at another user (bug or tampering).
    conversations = agent.dependencies.security.conversations
    store = conversations._store
    assert isinstance(store, InMemoryConversationStore)
    run(store.delete("acme", "t-hitl"))
    ravi = principal("acme", "ravi")
    run(store.open("acme", "t-hitl", "ravi"))
    with pytest.raises(InvalidResumeContextError):
        agent.resume("t-hitl", principal=ravi)
    assert _fact(agent, asha) is None and _fact(agent, ravi) is None
    assert any(e.details.get("reason") == "resume_actor_mismatch" for e in auditor.events)


def test_stale_approval_is_refused(hitl_settings: AppSettings, clock: ClockService) -> None:
    agent, _, auditor, asha, response = _remember_turn(hitl_settings, clock, "Changed.")
    approval_id = response.interrupts[0].value["approval_id"]
    approvals = agent.dependencies.security.approvals
    run(approvals.decide(asha, approval_id, approve=True))
    store = approvals.store
    assert isinstance(store, InMemoryApprovalStore)
    record = store._rows[uuid.UUID(approval_id)]
    store._rows[record.id] = record.model_copy(update={"operation_hash": "0" * 64})
    resumed = agent.resume("t-hitl", principal=asha)
    assert resumed.tool_results[-1].error_code == "APPROVAL_STALE"
    assert _fact(agent, asha) is None
    rejected = auditor.of("operation.rejected")
    assert rejected and rejected[0].error_code == "APPROVAL_STALE"


def _service(auditor: RecordingAuditor | None = None) -> ApprovalService:
    authorizer = Authorizer(SecurityAudit(auditor))
    return ApprovalService(InMemoryApprovalStore(auditor=auditor), authorizer)


def _request(service: ApprovalService, who: Principal, policy: ApprovalPolicy | None = None) -> Any:
    return run(
        service.request(
            who,
            operation="purchase.test_op",
            operation_hash="h" * 64,
            summary="Do the thing",
            required_permission="tool.execute",
            risk="high",
            policy=policy or ApprovalPolicy(),
            thread_id="t1",
            request_id="r1",
        )
    )


def test_self_approval_needs_policy_and_approver_permission() -> None:
    auditor = RecordingAuditor()
    service = _service(auditor)
    asha = principal("acme", "asha")
    record = _request(service, asha)
    with pytest.raises(PermissionDeniedError):  # members cannot approve
        run(service.decide(asha, record.id, approve=True))
    boss_asha = principal("acme", "asha", roles=("member", "approver"))
    with pytest.raises(PermissionDeniedError, match="Self-approval"):
        run(service.decide(boss_asha, record.id, approve=True))
    with pytest.raises(ApprovalNotFoundError):  # an unrelated member cannot even see it
        run(service.get(principal("acme", "ravi"), record.id))
    approver = principal("acme", "kim", roles=("member", "approver"))
    assert run(service.decide(approver, record.id, approve=True)).decided_by == "kim"
    reasons = [e.details.get("reason") for e in auditor.of("authz.denied")]
    assert reasons == ["missing_permission", "self_approval", "not_visible"]


def test_approval_belongs_to_one_tenant_user_workflow_and_operation() -> None:
    service = _service()
    asha = principal("acme", "asha")
    policy = ApprovalPolicy(allow_self_approval=True, approver_permission="tool.execute")
    record = _request(service, asha, policy)
    run(service.decide(asha, record.id, approve=True))
    good = OperationBinding(
        operation="purchase.test_op", operation_hash="h" * 64, thread_id="t1", request_id="r1"
    )
    assert run(service.validate_for_execution(asha, record.id, good)).status == "approved"

    with pytest.raises(ApprovalInvalidError):  # another tenant: invisible
        run(service.validate_for_execution(principal("globex", "asha"), record.id, good))
    with pytest.raises(ApprovalNotFoundError):
        run(service.get(principal("globex", "kim", roles=("tenant_admin",)), record.id))
    with pytest.raises(ApprovalInvalidError):  # another user of the same tenant
        run(service.validate_for_execution(principal("acme", "ravi"), record.id, good))
    with pytest.raises(ApprovalInvalidError):  # another workflow (turn)
        run(
            service.validate_for_execution(
                asha, record.id, good.model_copy(update={"request_id": "r2"})
            )
        )
    with pytest.raises(ApprovalInvalidError):  # another operation
        run(
            service.validate_for_execution(
                asha, record.id, good.model_copy(update={"operation": "x.y"})
            )
        )
    with pytest.raises(ApprovalStaleError):  # same operation, different arguments
        run(
            service.validate_for_execution(
                asha, record.id, good.model_copy(update={"operation_hash": "0" * 64})
            )
        )
    with pytest.raises(ApprovalInvalidError):  # garbage id
        run(service.validate_for_execution(asha, "not-a-uuid", good))


def test_requester_can_cancel_and_forget_cancels_open_approvals(
    hitl_settings: AppSettings, clock: ClockService
) -> None:
    service = _service()
    asha = principal()
    record = _request(service, asha)
    with pytest.raises(PermissionDeniedError):
        run(service.cancel(principal("acme", "kim", roles=("member", "approver")), record.id))
    assert run(service.cancel(asha, record.id)).status == "cancelled"

    agent, _, _, asha, response = _remember_turn(hitl_settings, clock)
    approval_id = response.interrupts[0].value["approval_id"]
    counts = agent.forget_conversation("t-hitl", principal=asha)
    assert counts["approvals_cancelled"] == 1
    approvals = agent.dependencies.security.approvals
    assert run(approvals.get(asha, approval_id)).status == "cancelled"


def test_llm_cannot_widen_a_protected_call(hitl_settings: AppSettings, clock: ClockService) -> None:
    """The model cannot add identity or scope fields to a tool call."""
    agent, _ = _agent(
        hitl_settings,
        clock,
        understanding(
            tool_calls=[
                (
                    REMEMBER_FACT,
                    '{"key": "k", "content": "c", "tenant_id": "globex", "shared": true}',
                )
            ]
        ),
        "That did not work.",
    )
    response = agent.run("Remember this for everyone", principal=principal())
    assert response.status == "completed"
    assert response.tool_results[0].error_code == "TOOL_INVALID_INPUT"
    assert run(agent.dependencies.security.approvals.list_open(principal())) == []


def test_local_identity_is_used_when_no_principal_is_passed(
    settings: AppSettings, clock: ClockService
) -> None:
    agent, _ = _agent(settings, clock, understanding(), "Hi.")
    response = agent.run("hello", thread_id="t-local")
    assert response.status == "completed"
    local = agent.dependencies.security.local_identity.principal()
    assert local.tenant_id == LOCAL_TENANT
    assert agent.thread_status("t-local", principal=local).exists
