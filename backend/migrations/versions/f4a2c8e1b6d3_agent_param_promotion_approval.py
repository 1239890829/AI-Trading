"""IMP-052: independent, one-shot parameter promotion approvals."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4a2c8e1b6d3"
down_revision = "e1a7b4c2d9f0"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name=:n"),
        {"n": name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_param_promotion_approval"):
        return
    op.create_table(
        "agent_param_promotion_approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("baseline_value", sa.Text(), nullable=True),
        sa.Column("baseline_digest", sa.String(length=64), nullable=False),
        sa.Column("shadow_evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("effect_evidence_ref", sa.Text(), nullable=False),
        sa.Column("effect_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("reviewer", sa.String(length=32), nullable=False),
        sa.Column("approval_source", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("approval_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_param_promotion_approval_change_id", "agent_param_promotion_approval", ["change_id"])
    op.create_index("ix_agent_param_promotion_approval_candidate_digest", "agent_param_promotion_approval", ["candidate_digest"])
    op.create_index("ix_agent_param_promotion_approval_approval_digest", "agent_param_promotion_approval", ["approval_digest"], unique=True)
    op.create_index("ix_agent_param_promotion_approval_expires_at", "agent_param_promotion_approval", ["expires_at"])
    op.create_index("ix_agent_param_promotion_approval_created_at", "agent_param_promotion_approval", ["created_at"])
    op.create_index("ix_agent_param_promotion_approval_consumed_at", "agent_param_promotion_approval", ["consumed_at"])
    op.create_index("ix_agent_param_promotion_approval_revoked_at", "agent_param_promotion_approval", ["revoked_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "agent_param_promotion_approval"):
        return
    for idx in (
        "ix_agent_param_promotion_approval_revoked_at",
        "ix_agent_param_promotion_approval_consumed_at",
        "ix_agent_param_promotion_approval_created_at",
        "ix_agent_param_promotion_approval_expires_at",
        "ix_agent_param_promotion_approval_approval_digest",
        "ix_agent_param_promotion_approval_candidate_digest",
        "ix_agent_param_promotion_approval_change_id",
    ):
        conn.execute(sa.text(f"drop index if exists {idx}"))
    op.drop_table("agent_param_promotion_approval")
