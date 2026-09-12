"""AI 控制台两表（agent_task / agent_audit，docs/summary/ai-evolution.md P0）

- `agent_task`：任务中心持久化——状态机 + 步骤轨迹（可追溯三件套第一件）。
- `agent_audit`：执行层审计——写操作的 before/after + 回滚点，任何状态变更留痕，
  否则"AI 改了什么"无法回答，全系统结论失去可信度。

幂等：已存在则跳过（fresh-at-baseline 之外的空库/重跑场景安全）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c7e5d9b2f4"
down_revision = "f6b2c8e4a9d3"
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

    if not _table_exists(conn, "agent_task"):
        op.create_table(
            "agent_task",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("params", sa.Text(), nullable=True),
            sa.Column("steps", sa.Text(), nullable=True),
            sa.Column("result_ref", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("risk_level", sa.String(length=2), nullable=True),
            sa.Column("created_by", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_task_type", "agent_task", ["type"], unique=False)
        op.create_index("ix_agent_task_status", "agent_task", ["status"], unique=False)
        op.create_index("ix_agent_task_created_at", "agent_task", ["created_at"], unique=False)

    if not _table_exists(conn, "agent_audit"):
        op.create_table(
            "agent_audit",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("actor", sa.String(length=16), nullable=True),
            sa.Column("action", sa.String(length=48), nullable=True),
            sa.Column("target", sa.String(length=128), nullable=True),
            sa.Column("before", sa.Text(), nullable=True),
            sa.Column("after", sa.Text(), nullable=True),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("rollback_ref", sa.String(length=64), nullable=True),
            sa.Column("at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agent_audit_action", "agent_audit", ["action"], unique=False)
        op.create_index("ix_agent_audit_target", "agent_audit", ["target"], unique=False)
        op.create_index("ix_agent_audit_task_id", "agent_audit", ["task_id"], unique=False)
        op.create_index("ix_agent_audit_at", "agent_audit", ["at"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_audit"):
        for idx in ("ix_agent_audit_at", "ix_agent_audit_task_id",
                    "ix_agent_audit_target", "ix_agent_audit_action"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_audit")
    if _table_exists(conn, "agent_task"):
        for idx in ("ix_agent_task_created_at", "ix_agent_task_status", "ix_agent_task_type"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_task")
