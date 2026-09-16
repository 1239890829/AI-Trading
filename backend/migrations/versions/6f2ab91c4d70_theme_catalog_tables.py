"""theme catalog tables (architecture-design §1 T1)

题材字典 + 官方成分快照 + 人工归属纠错三张表。
目录来自同花顺官方 fuyao API（885995.TI 粮食概念等 390 个概念板块），
成分是"当前成分"快照（官方不提供历史调入调出），消失成员按官方口径删除。

Revision ID: 6f2ab91c4d70
Revises: 3a73e4416725
Create Date: 2026-08-31
"""

revision: str = "6f2ab91c4d70"
down_revision = "3a73e4416725"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    # ⚠️ 必须走迁移上下文连接（op.get_bind()）——本迁移曾用 get_engine()，
    # 那是 **settings 默认库**的 engine（本仓 = data/ashare.db）⇒ 在全新库 /
    # 临时库 / 测试库路径上，theme / theme_member / theme_override 三表会被建到
    # **默认库**而非迁移目标库（生产库当年"碰巧建对"，因为默认库正是它，故从未暴露）。
    # 后果：任何换 DATABASE_URL 的新环境跑完整链仍**缺这三张表** ⇒ 题材目录运行时炸；
    # 且 run_migrations(临时 engine) 会顺带往默认库写表。
    # 与 core/migrations.py 的承诺（"迁移全程在传入 engine 的连接上执行……不会把表建到别处"）
    # 直接冲突 ⇒ 改共享连接。**表结构与内容零变化**，故对已应用本迁移的生产库无影响。
    # 同族第 3 例（前两例：e7a2b9c4d1f8 / 9c4d7e2a1b3f，2026-09-16 账本 BUG-014 收口）。
    bind = op.get_bind()
    metadata = sa.MetaData()

    theme = sa.Table(
        "theme",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ths_official"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    sa.Index("ix_theme_code", theme.c.code, unique=True)

    theme_member = sa.Table(
        "theme_member",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("theme_code", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=6), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("attribution_source", sa.String(length=16), nullable=False, server_default="ths_official"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("theme_code", "symbol", name="uq_theme_member"),
    )
    sa.Index("ix_theme_member_theme_code", theme_member.c.theme_code)
    sa.Index("ix_theme_member_symbol", theme_member.c.symbol)

    theme_override = sa.Table(
        "theme_override",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("theme_code", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=6), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False, server_default="include"),
        sa.Column("reason", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    sa.Index("ix_theme_override_theme_code", theme_override.c.theme_code)
    sa.Index("ix_theme_override_symbol", theme_override.c.symbol)

    metadata.create_all(bind)


def downgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    bind = op.get_bind()
    for table in ("theme_override", "theme_member", "theme"):
        sa.Table(table, sa.MetaData(), autoload_with=bind).drop(bind)
