"""event_card 加 summary（正文摘要）列

东财快讯源每条带 summary（正文摘要），但 EventCard 无此字段 → 快讯摘要
在 `_to_event` 环节被丢弃。补 summary 供「无原文/抓原文失败」时弹窗降级展示。
SQLite add_column 加可空列无需默认值；存量行 summary 为 NULL（不回填，自然过期）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e5c3d9a2f4"
down_revision = "a9c3e5f7b1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [r[1] for r in conn.execute(sa.text("pragma table_info(event_card)")).fetchall()]
    if "summary" not in cols:
        op.add_column("event_card", sa.Column("summary", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    cols = [r[1] for r in conn.execute(sa.text("pragma table_info(event_card)")).fetchall()]
    if "summary" in cols:
        op.drop_column("event_card", "summary")
