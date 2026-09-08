"""盘中跟踪台账表（猎场系统性升级批次 A）。

需求 7/8/9/10/11 的持久化底座：盘中筛选出的个股必须持续保留（不被中途
移除）、记录入选时间/原因/逻辑、入场价与收盘自动比对、成功/失败判定与
统计、收盘后仍可查历史。

revision: a9c3e5f7b1d4
down: e8f4b2d7c9a3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9c3e5f7b1d4"
down_revision = "e8f4b2d7c9a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trade_date", sa.String(10), nullable=False, index=True),  # YYYY-MM-DD
        sa.Column("symbol", sa.String(10), nullable=False, index=True),
        sa.Column("name", sa.String(32), default=""),
        # 机会三层（批次 B 复用）：today_strongest / quiet_starting / brewing
        sa.Column("layer", sa.String(16), default="today_strongest"),
        sa.Column("source_theme", sa.String(64), default=""),  # 题材归属（官方概念名优先）
        sa.Column("reason", sa.Text, default="{}"),  # JSON：题材催化/资金异动/技术形态/龙头角色
        sa.Column("is_leader", sa.Integer, default=0),
        sa.Column("boards", sa.Integer, default=0),
        sa.Column("entry_price", sa.Float, default=None),  # 入选时价格
        sa.Column("entry_time", sa.String(8), default=""),  # HH:MM:SS
        sa.Column("status", sa.String(16), default="tracking", index=True),  # tracking/settled
        sa.Column("close_price", sa.Float, default=None),
        sa.Column("pnl_pct", sa.Float, default=None),  # (close-entry)/entry*100
        sa.Column("verdict", sa.String(16), default=None),  # success/fail/flat
        sa.Column("verdict_reason", sa.Text, default=None),
        sa.Column("merged_into_picks", sa.Integer, default=0),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("trade_date", "symbol", name="uq_watch_ledger_date_symbol"),
    )


def downgrade() -> None:
    op.drop_table("watch_ledger")
