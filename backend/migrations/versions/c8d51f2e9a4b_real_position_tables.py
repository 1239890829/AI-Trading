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

    from app.core.db import get_engine

    engine = get_engine()
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

    metadata.create_all(engine)


def downgrade() -> None:
    import sqlalchemy as sa

    from app.core.db import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS real_position_override"))
        conn.execute(sa.text("DROP TABLE IF EXISTS real_trade"))
        conn.commit()
