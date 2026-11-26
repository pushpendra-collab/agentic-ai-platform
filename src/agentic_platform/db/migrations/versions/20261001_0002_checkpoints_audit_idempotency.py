"""Platform tables: LangGraph checkpoints, append-only audit log, idempotency records.

No business tables. See ADR-027 (checkpoints), ADR-028 (audit log) and ADR-029
(idempotency). `audit_log` gets triggers that reject UPDATE, DELETE and TRUNCATE
for every role.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = sa.Text(collation="C")
_NOW = sa.text("now()")


def upgrade() -> None:
    # --- LangGraph checkpoints -------------------------------------------------------
    op.create_table(
        "agent_checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", _ID, nullable=False),
        sa.Column("parent_checkpoint_id", _ID, nullable=True),
        sa.Column("checkpoint_type", sa.Text(), nullable=False),
        sa.Column("checkpoint", sa.LargeBinary(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", name="pk_agent_checkpoints"
        ),
    )
    op.create_table(
        "agent_checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "channel", "version", name="pk_agent_checkpoint_blobs"
        ),
    )
    op.create_table(
        "agent_checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", _ID, nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            name="pk_agent_checkpoint_writes",
        ),
    )

    # --- audit log (append-only) -----------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("entity_refs", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("approval_decision", sa.Text(), nullable=True),
        sa.Column("approver_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system')", name="ck_audit_log_actor_type"
        ),
        sa.CheckConstraint("status IN ('ok', 'error', 'denied')", name="ck_audit_log_status"),
        sa.CheckConstraint(
            "approval_decision IS NULL OR approval_decision IN ('approved', 'rejected', 'edited')",
            name="ck_audit_log_approval_decision",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])
    op.create_index("ix_audit_log_thread_id_occurred_at", "audit_log", ["thread_id", "occurred_at"])
    op.create_index("ix_audit_log_tenant_id_occurred_at", "audit_log", ["tenant_id", "occurred_at"])
    op.execute(
        """
        CREATE FUNCTION audit_log_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only'
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change()"
    )

    # --- idempotency -----------------------------------------------------------------
    op.create_table(
        "idempotency_record",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("claim_token", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')", name="ck_idempotency_record_status"
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 255",
            name="ck_idempotency_record_key_length",
        ),
        sa.CheckConstraint(
            "char_length(scope) BETWEEN 1 AND 255", name="ck_idempotency_record_scope_length"
        ),
        sa.PrimaryKeyConstraint("scope", "idempotency_key", name="pk_idempotency_record"),
    )
    op.create_index("ix_idempotency_record_expires_at", "idempotency_record", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_record_expires_at", table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_change()")
    op.drop_index("ix_audit_log_tenant_id_occurred_at", table_name="audit_log")
    op.drop_index("ix_audit_log_thread_id_occurred_at", table_name="audit_log")
    op.drop_index("ix_audit_log_occurred_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("agent_checkpoint_writes")
    op.drop_table("agent_checkpoint_blobs")
    op.drop_table("agent_checkpoints")
