"""daily_pick_set 加 rejected 列（消融验证 P3：深评落选者落库）

Revision ID: b7d4f8a2e6c9
Revises: f3b8d6e2c7a1
Create Date: 2026-09-01

落选者 = 深评过但未进最终组合的候选（分数不够/门槛拦截/上限截断）。
落库内容为精简摘要（symbol/name/score/tech 分/排名），供消融回放
（tech-only 对照）在 30 个交易日积累后做「组合 vs 单维」的科学对比。
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d4f8a2e6c9"
down_revision = "f3b8d6e2c7a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ⚠️ 回填必须走 op.get_bind()（迁移上下文连接）——另开 engine.connect()
    # 看不到 baseline 在外层事务中未提交建的表（test_db_migrations 实测踩坑）。
    bind = op.get_bind()
    has_table = bind.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name='daily_pick_set'")
    ).scalar()
    if not has_table:
        # fresh-at-baseline 场景（测试只跑到 baseline）：daily_pick_set 由
        # e7a2b9c4d1f8 建（在本迁移之前），此场景下整链不会走到这里——防御跳过。
        return
    op.add_column(
        "daily_pick_set",
        sa.Column("rejected", sa.String(length=16384), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_table = bind.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name='daily_pick_set'")
    ).scalar()
    if not has_table:
        return
    has_col = bind.execute(
        sa.text("select count(*) from pragma_table_info('daily_pick_set') where name='rejected'")
    ).scalar()
    if has_col:
        op.drop_column("daily_pick_set", "rejected")
