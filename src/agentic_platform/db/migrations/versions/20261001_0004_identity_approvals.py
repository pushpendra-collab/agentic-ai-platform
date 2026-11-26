"""Identity, ownership and approvals: conversation_thread, approval_request, tenant RLS.

Phase 5 (ADR-039 to ADR-041). No business tables.

* `conversation_thread`: which tenant and user own each conversation.
* `approval_request`: human-in-the-loop approval lifecycle (no tool arguments,
  only their SHA-256).
* Row-level security, in addition to the application's tenant filters:
  - `conversation_thread`, `approval_request`: rows of the tenant in
    `app.tenant_id`; `app.maintenance = 'on'` for cross-tenant jobs (expiry).
  - `audit_log`: tenants read only their own rows; tenant-less rows (system
    events such as failed sign-ins) are visible to maintenance only. Inserts may
    carry the bound tenant or no tenant.
  - `agent_checkpoints`, `agent_checkpoint_blobs`, `agent_checkpoint_writes`:
    checkpoint keys are `<tenant>/<thread>`; only the bound tenant's keys are
    visible or writable.
  The table owner (migration role) is not subject to RLS.
* `audit_log`: UPDATE and DELETE are revoked from every role except the owner,
  on top of the Phase 3 append-only triggers.

Downgrade restores the Phase 4 state (RLS off on the Phase 3 tables, UPDATE/DELETE
re-granted to the roles that can insert into `audit_log`).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_TENANT = "current_setting('app.tenant_id', true)"
_MAINTENANCE = "current_setting('app.maintenance', true) = 'on'"
_CHECKPOINT_TABLES = ("agent_checkpoints", "agent_checkpoint_blobs", "agent_checkpoint_writes")


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING (tenant_id = {_TENANT} OR {_MAINTENANCE}) "
        f"WITH CHECK (tenant_id = {_TENANT} OR {_MAINTENANCE})"
    )


def upgrade() -> None:
    # --- conversation ownership --------------------------------------------------------
    op.create_table(
        "conversation_thread",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "char_length(tenant_id) BETWEEN 1 AND 128", name="ck_conversation_thread_tenant_length"
        ),
        sa.CheckConstraint(
            "char_length(thread_id) BETWEEN 1 AND 128", name="ck_conversation_thread_thread_length"
        ),
        sa.CheckConstraint(
            "char_length(owner_user_id) BETWEEN 1 AND 128",
            name="ck_conversation_thread_owner_length",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "thread_id", name="pk_conversation_thread"),
    )
    op.create_index(
        "ix_conversation_thread_owner",
        "conversation_thread",
        ["tenant_id", "owner_user_id", "updated_at"],
    )
    _tenant_policy("conversation_thread")

    # --- approval requests -------------------------------------------------------------
    op.create_table(
        "approval_request",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("requester_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("operation_hash", sa.Text(), nullable=False),
        sa.Column("required_permission", sa.Text(), nullable=False),
        sa.Column("approver_permission", sa.Text(), nullable=False),
        sa.Column(
            "allow_self_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("risk", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "resource_refs",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_status", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled', 'executed')",
            name="ck_approval_request_status",
        ),
        sa.CheckConstraint("risk IN ('low', 'medium', 'high')", name="ck_approval_request_risk"),
        sa.CheckConstraint(
            "execution_status IS NULL OR execution_status IN ('ok', 'error')",
            name="ck_approval_request_execution_status",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR (decided_at IS NULL AND decided_by IS NULL)",
            name="ck_approval_request_pending_undecided",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected', 'executed') OR decided_by IS NOT NULL",
            name="ck_approval_request_decision_has_approver",
        ),
        sa.CheckConstraint(
            "(executed_at IS NULL) = (status <> 'executed')", name="ck_approval_request_executed_at"
        ),
        sa.CheckConstraint(
            "char_length(tenant_id) BETWEEN 1 AND 128", name="ck_approval_request_tenant_length"
        ),
        sa.CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 1000", name="ck_approval_request_summary_length"
        ),
        sa.CheckConstraint(
            "decision_note IS NULL OR char_length(decision_note) <= 1000",
            name="ck_approval_request_note_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_request"),
    )
    op.create_index(
        "ix_approval_request_tenant_status",
        "approval_request",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index("ix_approval_request_thread", "approval_request", ["tenant_id", "thread_id"])
    op.create_index(
        "ix_approval_request_requester",
        "approval_request",
        ["tenant_id", "requester_id", "created_at"],
    )
    op.create_index(
        "ix_approval_request_pending_expiry",
        "approval_request",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    _tenant_policy("approval_request")

    # --- audit_log: tenant RLS + no UPDATE/DELETE for runtime roles ----------------------
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_log_tenant_read ON audit_log FOR SELECT "
        f"USING (tenant_id = {_TENANT} OR {_MAINTENANCE})"
    )
    op.execute(
        "CREATE POLICY audit_log_tenant_insert ON audit_log FOR INSERT "
        f"WITH CHECK (tenant_id IS NULL OR tenant_id = {_TENANT})"
    )
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT DISTINCT grantee FROM information_schema.role_table_grants
                WHERE table_schema = current_schema() AND table_name = 'audit_log'
                  AND privilege_type IN ('UPDATE', 'DELETE', 'TRUNCATE')
                  AND grantee <> current_user
            LOOP
                EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM %I', r.grantee);
            END LOOP;
        END
        $$
        """
    )

    # --- checkpoints: tenant RLS on the key prefix --------------------------------------
    for table in _CHECKPOINT_TABLES:
        tenant_of_key = "split_part(thread_id, '/', 1)"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({tenant_of_key} = {_TENANT} OR {_MAINTENANCE}) "
            f"WITH CHECK ({tenant_of_key} = {_TENANT} OR {_MAINTENANCE})"
        )


def downgrade() -> None:
    for table in _CHECKPOINT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT DISTINCT grantee FROM information_schema.role_table_grants
                WHERE table_schema = current_schema() AND table_name = 'audit_log'
                  AND privilege_type = 'INSERT' AND grantee <> current_user
            LOOP
                EXECUTE format('GRANT UPDATE, DELETE ON audit_log TO %I', r.grantee);
            END LOOP;
        END
        $$
        """
    )
    op.execute("DROP POLICY IF EXISTS audit_log_tenant_insert ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_log_tenant_read ON audit_log")
    op.execute("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS approval_request_tenant_isolation ON approval_request")
    op.drop_index("ix_approval_request_pending_expiry", table_name="approval_request")
    op.drop_index("ix_approval_request_requester", table_name="approval_request")
    op.drop_index("ix_approval_request_thread", table_name="approval_request")
    op.drop_index("ix_approval_request_tenant_status", table_name="approval_request")
    op.drop_table("approval_request")

    op.execute("DROP POLICY IF EXISTS conversation_thread_tenant_isolation ON conversation_thread")
    op.drop_index("ix_conversation_thread_owner", table_name="conversation_thread")
    op.drop_table("conversation_thread")
