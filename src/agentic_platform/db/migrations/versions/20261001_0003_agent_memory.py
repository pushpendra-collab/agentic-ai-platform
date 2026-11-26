"""Agent memory: pgvector extension and the `memory_item` table with tenant RLS.

Holds long-term facts, semantic knowledge chunks, episodes, procedural
playbooks and conversation summaries (ADR-033, ADR-034). No business tables.

Requires the pgvector extension to be installable on the server (the local
Docker image `pgvector/pgvector:pg16-trixie` ships it; managed services such
as RDS, Azure Database and Cloud SQL offer it). The extension is not dropped on
downgrade, because other objects may depend on it.

Row-level security: the runtime role sees only rows whose tenant matches
`current_setting('app.tenant_id')`, set per transaction by the application;
maintenance jobs set `app.memory_maintenance = 'on'`. The table owner (the
migration role) is not subject to RLS.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from agentic_platform.db.memory import Vector

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "memory_item",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), server_default="", nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), server_default="", nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "kind IN ('long_term', 'semantic', 'episodic', 'procedural', 'conversation_summary')",
            name="ck_memory_item_kind",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 100000", name="ck_memory_item_content_length"
        ),
        sa.CheckConstraint(
            "char_length(tenant_id) BETWEEN 1 AND 128", name="ck_memory_item_tenant_length"
        ),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name="ck_memory_item_embedding_has_model",
        ),
        sa.CheckConstraint("version >= 1", name="ck_memory_item_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_item"),
    )
    op.create_index(
        "ix_memory_item_scope", "memory_item", ["tenant_id", "kind", "user_id", "namespace"]
    )
    op.create_index("ix_memory_item_thread", "memory_item", ["tenant_id", "kind", "thread_id"])
    op.create_index("ix_memory_item_source", "memory_item", ["tenant_id", "kind", "source_ref"])
    op.create_index("ix_memory_item_expires_at", "memory_item", ["expires_at"])
    op.create_index(
        "uq_memory_item_long_term_key",
        "memory_item",
        ["tenant_id", "user_id", "namespace", "key"],
        unique=True,
        postgresql_where=sa.text("kind = 'long_term'"),
    )
    op.create_index(
        "uq_memory_item_procedural_version",
        "memory_item",
        ["tenant_id", "namespace", "version"],
        unique=True,
        postgresql_where=sa.text("kind = 'procedural'"),
    )
    op.create_index(
        "uq_memory_item_conversation_summary",
        "memory_item",
        ["tenant_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'conversation_summary'"),
    )

    op.execute("ALTER TABLE memory_item ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY memory_item_tenant_isolation ON memory_item "
        "USING (tenant_id = current_setting('app.tenant_id', true) "
        "OR current_setting('app.memory_maintenance', true) = 'on') "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memory_item_tenant_isolation ON memory_item")
    op.drop_index("uq_memory_item_conversation_summary", table_name="memory_item")
    op.drop_index("uq_memory_item_procedural_version", table_name="memory_item")
    op.drop_index("uq_memory_item_long_term_key", table_name="memory_item")
    op.drop_index("ix_memory_item_expires_at", table_name="memory_item")
    op.drop_index("ix_memory_item_source", table_name="memory_item")
    op.drop_index("ix_memory_item_thread", table_name="memory_item")
    op.drop_index("ix_memory_item_scope", table_name="memory_item")
    op.drop_table("memory_item")
