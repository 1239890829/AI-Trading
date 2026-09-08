"""AI 控制台参数配置两表（agent_param / agent_param_change，方案 P1-B）

- `agent_param`：运行时覆盖层——改参数免重启，删行即回静态配置。
- `agent_param_change`：变更单——before/after + 来源（哪份复盘哪条改进项）+
  证据（样本/IC/胜率），支持一键回滚；这是"LLM 复盘 → 系统优化"闭环的落点
  （此前 suggest_methodology_changes 没有任何消费方）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d8e1a6f2b7"
down_revision = "b3f7c2d8a1e5"
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

    if not _table_exists(conn, "agent_param"):
        op.create_table(
            "agent_param",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("key"),
        )

    if not _table_exists(conn, "agent_param_change"):
        op.create_table(
            "agent_param_change",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("before", sa.Text(), nullable=True),
            sa.Column("after", sa.Text(), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=True),
            sa.Column("source_id", sa.String(length=64), nullable=True),
            sa.Column("evidence", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_param_change_key", "agent_param_change", ["key"], unique=False)
        op.create_index("ix_agent_param_change_status", "agent_param_change", ["status"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_param_change"):
        for idx in ("ix_agent_param_change_status", "ix_agent_param_change_key"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_param_change")
    if _table_exists(conn, "agent_param"):
        op.drop_table("agent_param")
