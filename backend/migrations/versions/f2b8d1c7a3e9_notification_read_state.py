"""notification_read_state：站内通知已读状态（2026-09-12 缺陷修复）

修复「重启后已读全部变未读、显示 65 条未读」：已读状态此前**只存浏览器
localStorage**，按 origin 命名空间，换源/换 profile/清站点数据即整体归零。
本表把它落成服务端权威状态（前端 localStorage 降级为首帧缓存，两侧单调合并）。

单行表（id 恒为 1）；时间戳一律 epoch 毫秒（BigInteger，int32 装不下）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2b8d1c7a3e9"
down_revision = "e9b3c7a1d5f2"
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
    if _table_exists(conn, "notification_read_state"):
        return
    op.create_table(
        "notification_read_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seen_before", sa.BigInteger(), nullable=True),
        sa.Column("read_ids", sa.Text(), nullable=True),
        sa.Column("clear_before", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "notification_read_state"):
        op.drop_table("notification_read_state")
