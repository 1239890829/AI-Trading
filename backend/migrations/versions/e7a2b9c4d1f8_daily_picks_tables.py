"""daily picks tables（每日精选组合 + 选股复盘日志）

Revision ID: e7a2b9c4d1f8
Revises: c8d51f2e9a4b
Create Date: 2026-08-31
"""

revision: str = "e7a2b9c4d1f8"
down_revision = "c8d51f2e9a4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    # ⚠️ 必须走迁移上下文连接（op.get_bind()）——本迁移曾用 get_engine()
    # （默认指向 settings 主库），全新库/测试库上表被建到**错误的库**，
    # 整链建完后 daily_pick 两表仍不存在，后续迁移 reflect 时
    # NoSuchTableError（2026-09-08 excess_pct nullable 迁移实测炸出）。
    # 真实主库已应用过本迁移，此处修改只影响全新库与测试库路径。
    bind = op.get_bind()
    metadata = sa.MetaData()

    _daily_pick_set = sa.Table(
        "daily_pick_set",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("items", sa.String(length=8192), nullable=False, server_default="[]"),
        sa.Column("meta", sa.String(length=2048), nullable=False, server_default="{}"),
        sa.Column("replaced", sa.String(length=1024), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("date", name="uq_daily_pick_set_date"),
    )

    _daily_pick_review = sa.Table(
        "daily_pick_review",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("verdict", sa.String(length=8), nullable=False, server_default="flat"),
        sa.Column("reason_category", sa.String(length=24), nullable=False, server_default="gone_well"),
        sa.Column("excess_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("date", "symbol", name="uq_daily_pick_review_date_symbol"),
    )

    metadata.create_all(bind)


def downgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS daily_pick_review"))
    bind.execute(sa.text("DROP TABLE IF EXISTS daily_pick_set"))
