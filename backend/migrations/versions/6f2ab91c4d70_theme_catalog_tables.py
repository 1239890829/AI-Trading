"""theme catalog tables (linkage-design §3 T1)

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

    from app.core.db import get_engine

    engine = get_engine()
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

    metadata.create_all(engine)


def downgrade() -> None:
    import sqlalchemy as sa

    from app.core.db import get_engine

    engine = get_engine()
    for table in ("theme_override", "theme_member", "theme"):
        sa.Table(table, sa.MetaData(), autoload_with=engine).drop(engine)
