"""实验记录本两表迁移（agent_experiment，AI 大脑 v2 P1）"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8f4b2d7c9a3"
down_revision = "d6e9f3a1c5b8"
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
    if not _table_exists(conn, "agent_experiment"):
        op.create_table(
            "agent_experiment",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("change_id", sa.Integer(), nullable=False),
            sa.Column("param_key", sa.String(length=64), nullable=False),
            sa.Column("hypothesis", sa.Text(), nullable=True),
            sa.Column("baseline", sa.Text(), nullable=True),
            sa.Column("verification_date", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("extensions", sa.Integer(), nullable=True),
            sa.Column("max_extensions", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("concluded_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_experiment_change_id", "agent_experiment", ["change_id"], unique=False)
        op.create_index("ix_agent_experiment_param_key", "agent_experiment", ["param_key"], unique=False)
        op.create_index("ix_agent_experiment_status", "agent_experiment", ["status"], unique=False)
        op.create_index("ix_agent_experiment_verification_date", "agent_experiment", ["verification_date"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_experiment"):
        for idx in ("ix_agent_experiment_verification_date", "ix_agent_experiment_status",
                    "ix_agent_experiment_param_key", "ix_agent_experiment_change_id"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_experiment")
