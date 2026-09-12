"""每日进化议程两表迁移（agent_agenda，docs/summary/ai-evolution.md P0）"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e9f3a1c5b8"
down_revision = "c4d8e1a6f2b7"
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
    if not _table_exists(conn, "agent_agenda"):
        op.create_table(
            "agent_agenda",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("date", sa.String(length=10), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("inputs", sa.Text(), nullable=True),
            sa.Column("items", sa.Text(), nullable=True),
            sa.Column("budget", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("date", name="uq_agent_agenda_date"),
        )
        op.create_index("ix_agent_agenda_date", "agent_agenda", ["date"], unique=True)
        op.create_index("ix_agent_agenda_status", "agent_agenda", ["status"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_agenda"):
        for idx in ("ix_agent_agenda_status", "ix_agent_agenda_date"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_agenda")
