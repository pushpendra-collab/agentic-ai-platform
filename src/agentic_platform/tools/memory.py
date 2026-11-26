"""Memory tools (domain `memory`): the agent's only way to write long-term memory.

`memory.remember_fact` lets the agent propose remembering a fact or preference
about the user. It is a **write** tool, so it never runs without the user's
explicit approval (HITL, ADR-040), and it is registered only when
MEMORY_AGENT_FACT_WRITES=approval (default: off, the Phase 4 behaviour).

Safety properties, all enforced in code:

* the fact is written to the authenticated user's private memory; the tool has
  no tenant, user or `shared` argument for the model to set (unknown fields are
  rejected);
* it needs `memory.write`, checked before the approval request, again after the
  approval, and a third time by `AuthorizedMemory` (the final authority);
* the user approves their own fact (`allow_self_approval`), because it is their
  personal memory; approving still requires `memory.write`;
* the write is audited (`memory.fact.create` / `.update`, content hashed only).
"""

from __future__ import annotations

from pydantic import Field

from agentic_platform.memory.authorized import AuthorizedMemory
from agentic_platform.security.context import require_principal
from agentic_platform.security.permissions import Permission
from agentic_platform.security.policy import ApprovalPolicy
from agentic_platform.tools.base import Tool, ToolInput, ToolKind, ToolOutput, ToolSpec

MEMORY_DOMAIN = "memory"
REMEMBER_FACT = "memory.remember_fact"
_NAME = r"^[A-Za-z0-9_.:\-]{1,100}$"


class RememberFactInput(ToolInput):
    namespace: str = Field(
        default="preferences",
        pattern=_NAME,
        description="Group of the fact, e.g. 'preferences' or 'profile'.",
    )
    key: str = Field(pattern=_NAME, description="Short id of the fact, e.g. 'reply_language'.")
    content: str = Field(
        min_length=1, max_length=1000, description="The fact itself, as one plain sentence."
    )


class RememberFactOutput(ToolOutput):
    memory_id: str
    namespace: str
    key: str


def _describe(tool_input: RememberFactInput) -> str:
    return f"Remember for future conversations [{tool_input.namespace}/{tool_input.key}]: " + (
        tool_input.content
    )


def remember_fact_tool(memory: AuthorizedMemory) -> Tool:
    async def handler(tool_input: RememberFactInput) -> RememberFactOutput:
        record = await memory.remember_fact(
            require_principal(),
            tool_input.namespace,
            tool_input.key,
            tool_input.content,
            source_type="agent",
            actor_type="agent",
        )
        return RememberFactOutput(
            memory_id=str(record.id), namespace=tool_input.namespace, key=tool_input.key
        )

    return Tool(
        spec=ToolSpec(
            name=REMEMBER_FACT,
            description=(
                "Remember a fact or preference the user stated about themselves, for future "
                "conversations. The user is always asked to approve before anything is stored. "
                "Never use it for amounts, statuses or business records."
            ),
            domain=MEMORY_DOMAIN,
            kind=ToolKind.WRITE,
            input_model=RememberFactInput,
            output_model=RememberFactOutput,
            timeout_seconds=15.0,
            required_permission=Permission.MEMORY_WRITE,
            risk="medium",
            approval=ApprovalPolicy(
                approver_permission=Permission.MEMORY_WRITE,
                allow_self_approval=True,
                # Private memory: nobody else in the tenant may see or decide it.
                requester_only=True,
                reason="agent_memory_write",
            ),
            describe_action=_describe,
        ),
        handler=handler,
    )
