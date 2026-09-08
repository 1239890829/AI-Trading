"""daily_pick_review.excess_pct nullable 化（strategy-evolution-plan §方向5 P1）。

缺陷（2026-09-08 P0 复盘定案）：原列 NOT NULL（ORM client-side default=0.0），
daily_review 在大盘基准缺失时写 None 会被 ORM default 固化成 0.0——与
「超额恰为 0」不可区分，污染 signal_health 的 CUSUM/均值统计
（三态纪律：「没参与」≠「超额为 0」，评审 B21）。

真实库核验（2026-09-08）：无 0.0 污染行（止血注记期间无基准缺失落库），
本迁移只改列约束、不动数据；downgrade 先把 NULL 回填 0 再恢复 NOT NULL
（旧行为口径，与 ORM default=0.0 时代的语义一致）。

Revision ID: f6b2c8e4a9d3
Revises: d5e9a7f3b1c2
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f6b2c8e4a9d3"
down_revision = "d5e9a7f3b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 存在性守卫（与 b7d4/d5e9 同款）：fresh-at-baseline 等异常路径下表可能
    # 缺失——此时 ORM create_all 会按当前模型（已 nullable）建全量结构，跳过即可。
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name='daily_pick_review'")
    ).scalar()
    if not exists:
        return
    # SQLite 不支持直接 ALTER COLUMN → batch 模式重建表（数据原样拷贝）。
    # 同时移除遗留的 server DEFAULT 0：nullable 列上留默认，会让任何
    # 「省略该列」的写入方重新引入「缺失→0」歧义（正是本迁移要消灭的语义）。
    with op.batch_alter_table("daily_pick_review") as batch:
        batch.alter_column(
            "excess_pct",
            existing_type=sa.Float(),
            nullable=True,
            server_default=sa.text("NULL"),
        )


def downgrade() -> None:
    op.execute("UPDATE daily_pick_review SET excess_pct = 0 WHERE excess_pct IS NULL")
    with op.batch_alter_table("daily_pick_review") as batch:
        batch.alter_column(
            "excess_pct",
            existing_type=sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        )
