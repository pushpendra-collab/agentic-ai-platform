"""Risk classification and approval policy for protected operations (ADR-040).

A protected operation (today: a tool) declares a risk level and, optionally, an
approval policy. Whether approval is needed is decided here, by code:

* every `write` tool needs approval (ADR-010; exemptions need business sign-off);
* any tool flagged `requires_approval=True` needs it;
* any tool at or above `APPROVAL_MIN_RISK` needs it (default `high`).

The policy says who may approve (`approver_permission`) and whether the requester
may approve their own request. No business thresholds (amounts, document types)
live here: future domain agents attach their own policy to their own tools.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_platform.security.permissions import Permission

RiskLevel = Literal["low", "medium", "high"]
_RANK = {"low": 0, "medium": 1, "high": 2, "never": 3}


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approver_permission: str = Permission.APPROVAL_DECIDE
    allow_self_approval: bool = False
    # Private operations (Phase 6): only the requester may see and decide the request,
    # even if other users hold `approver_permission`. Implies self-approval.
    requester_only: bool = False
    ttl_seconds: int | None = Field(default=None, ge=60)  # None: APPROVAL_TTL_SECONDS
    reason: str = Field(default="protected_operation", max_length=100)


    @model_validator(mode="after")
    def _requester_only_needs_self_approval(self) -> ApprovalPolicy:
        if self.requester_only and not self.allow_self_approval:
            raise ValueError("requester_only needs allow_self_approval.")
        return self


DEFAULT_APPROVAL_POLICY = ApprovalPolicy()


def approval_required(
    *, is_write: bool, requires_approval: bool, risk: RiskLevel, min_risk: str
) -> bool:
    return is_write or requires_approval or _RANK[risk] >= _RANK[min_risk]
