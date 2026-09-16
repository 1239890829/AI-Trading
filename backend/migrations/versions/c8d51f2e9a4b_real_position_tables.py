"""real position tables（真实持仓账本：成交流水 + 持仓覆盖）

真实持仓 = 用户在券商实际成交后手工录入的账本（CONTEXT.md: Real Position）。
按实际成交价记账，与模拟账户（paper）完全独立。流水是事实层不可篡改，
持仓视图可被 RealPositionOverride 覆盖。

Revision ID: c8d51f2e9a4b
Revises: 9c4d7e2a1b3f
Create Date: 2026-08-31
"""

revision: str = "c8d51f2e9a4b"
down_revision = "9c4d7e2a1b3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    # ⚠️ 必须走迁移上下文连接（op.get_bind()）——本迁移曾用 get_engine()，
    # 那是 **settings 默认库**的 engine（本仓 = data/ashare.db）⇒ 在全新库 /
    # 临时库 / 测试库路径上，real_trade / real_position_override 两表会被建到
    # **默认库**而非迁移目标库（生产库当年"碰巧建对"，因为默认库正是它，故从未暴露）。
    # 后果：任何换 DATABASE_URL 的新环境跑完整链仍**缺这两张表** ⇒ 真实持仓功能运行时炸；
    # 且 run_migrations(临时 engine) 会顺带往默认库写表。
    # 与 core/migrations.py 的承诺（"迁移全程在传入 engine 的连接上执行……不会把表建到别处"）
    # 直接冲突 ⇒ 改共享连接。**表结构与内容零变化**，故对已应用本迁移的生产库无影响。
    # 同族第 4 例（前三例：e7a2b9c4d1f8 / 9c4d7e2a1b3f / 6f2ab91c4d70，
    # 2026-09-16 账本 BUG-014 收口）。
    bind = op.get_bind()
    metadata = sa.MetaData()

    _real_trade = sa.Table(
        "real_trade",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("side", sa.String(length=4), nullable=False, server_default="buy"),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("traded_at", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    sa.Index("ix_real_trade_symbol", _real_trade.c.symbol)

    sa.Table(
        "real_position_override",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=12), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("total_cost", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("symbol", name="uq_real_position_override_symbol"),
    )

    metadata.create_all(bind)


def downgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    # 走迁移上下文连接：**不再自行 connect()/commit()**——那会在 alembic 的事务之外
    # 另开一条连接，既可能drop错库，也会与外层事务边界冲突（同族 e7a2b9c4d1f8 的写法）。
    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS real_position_override"))
    bind.execute(sa.text("DROP TABLE IF EXISTS real_trade"))
