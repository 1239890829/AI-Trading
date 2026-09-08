"""告警 AI 判读表（agent_triage，docs/ai-agent-console-plan.md P1）

规则触发 ≠ 值得提醒。本表存 AI 对每条告警事件的判读结论：
notify（值得提醒，进悬浮球）/ ignore（噪音，不上界面）/ escalate（升级，进任务中心）。
`model` 字段区分 llm 判读与 rules 兜底——LLM 不可用时按规则提醒并显式标注，
绝不伪装成 AI 判断（09-04 有 LLM 全天降级先例）。

单独一个迁移：a1c7e5d9b2f4 已执行过，按规范不改已执行的迁移。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3f7c2d8a1e5"
down_revision = "a1c7e5d9b2f4"
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
    if not _table_exists(conn, "agent_triage"):
        op.create_table(
            "agent_triage",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=False),
            sa.Column("verdict", sa.String(length=16), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("model", sa.String(length=16), nullable=True),
            sa.Column("acked", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_agent_triage_event"),
        )
        op.create_index("ix_agent_triage_event_id", "agent_triage", ["event_id"], unique=True)
        op.create_index("ix_agent_triage_verdict", "agent_triage", ["verdict"], unique=False)
        op.create_index("ix_agent_triage_created_at", "agent_triage", ["created_at"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "agent_triage"):
        for idx in ("ix_agent_triage_created_at", "ix_agent_triage_verdict", "ix_agent_triage_event_id"):
            conn.execute(sa.text(f"drop index if exists {idx}"))
        op.drop_table("agent_triage")
