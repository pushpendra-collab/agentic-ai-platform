"""Approval requests that only their requester may see and decide.

Phase 6 (ADR-040 amendment). `memory.remember_fact` lets users approve their own
private facts by naming `memory.write` as the approver permission. Every member
holds `memory.write`, so any other member of the tenant could list, read (the
summary contains the fact) and decide someone else's private request. The new
`requester_only` flag makes such a request visible to, and decidable by, its
requester only. Existing rows keep today's behaviour (`false`).

* `approval_request.requester_only boolean NOT NULL DEFAULT false`
* check `ck_approval_request_requester_only_self`: requester-only implies that
  self-approval is allowed (otherwise nobody could ever decide the request).

No table, RLS policy or privilege changes. Downgrade drops the column.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approval_request",
        sa.Column(
            "requester_only", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_approval_request_requester_only_self"),
        "approval_request",
        "NOT requester_only OR allow_self_approval",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_approval_request_requester_only_self"), "approval_request", type_="check"
    )
    op.drop_column("approval_request", "requester_only")
