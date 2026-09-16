"""Add cost and fillability columns to opportunity outcome labels.

revision: b4f1a7c2e9d3
down: 7d4e2c9a6b1f

`RSH-026` 第二批（2026-09-16）：把机会阶段的结果标签从**毛收益**扩展为
**成本后净收益 + 可成交性**。三列**只增不改**——`return_pct` 的语义保持原样
（信号方向毛收益），既有数据零改写 ⇒ 对已打标签的历史行无任何影响。

⚠️ 本迁移的 `down_revision` = `7d4e2c9a6b1f`（建本表的迁移），
且**全程只用 `op.*` 接口**，不由本文件自建 engine —— `BUG-014` 的成因正是
迁移里用 `get_engine()`（指向 settings 默认库）而非迁移目标连接。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4f1a7c2e9d3"
down_revision = "7d4e2c9a6b1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `fill_state` 为 NOT NULL ⇒ 必须给 `server_default`：SQLite 的
    # `ALTER TABLE ADD COLUMN` 要给既有行一个可填值。存量行语义 =
    # 「本列引入前打的历史标签，未判定可成交性」→ 按默认 "ok" 处理（与模型默认一致）。
    op.add_column(
        "opportunity_outcome_label",
        sa.Column("fill_state", sa.String(16), nullable=False, server_default="ok"),
    )
    op.add_column(
        "opportunity_outcome_label",
        sa.Column("cost_pct", sa.Float(), nullable=True),
    )
    op.add_column(
        "opportunity_outcome_label",
        sa.Column("net_return_pct", sa.Float(), nullable=True),
    )
    # 索引名与 SQLAlchemy 对 `index=True` 的默认命名一致（`ix_<table>_<column>`），
    # 否则 `_schema_drift` 的索引比对会在「alembic 新建库 vs create_all 兜底库」之间分叉。
    op.create_index(
        "ix_opportunity_outcome_label_fill_state",
        "opportunity_outcome_label",
        ["fill_state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_opportunity_outcome_label_fill_state", table_name="opportunity_outcome_label")
    op.drop_column("opportunity_outcome_label", "net_return_pct")
    op.drop_column("opportunity_outcome_label", "cost_pct")
    op.drop_column("opportunity_outcome_label", "fill_state")
