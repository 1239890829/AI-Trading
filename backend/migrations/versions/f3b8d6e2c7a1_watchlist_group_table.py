"""watchlist group table（分组管理：持久化空分组，评审报告 A1）

Revision ID: f3b8d6e2c7a1
Revises: e7a2b9c4d1f8
Create Date: 2026-09-01
"""

revision: str = "f3b8d6e2c7a1"
down_revision = "e7a2b9c4d1f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    from alembic import op

    # ⚠️ 全部通过 op.get_bind()（alembic 迁移上下文连接）执行：
    # 全新库走全链 upgrade 时，baseline 建的表还在外层事务里未提交，
    # 开新连接看不到（test_db_migrations 实测踩坑），回填 SELECT 会报 no such table。
    op.create_table(
        "watchlist_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_watchlist_group_name"),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT DISTINCT group_name FROM watchlist WHERE group_name IS NOT NULL AND group_name != '默认'")
    ).fetchall()
    for (name,) in rows:
        bind.execute(
            sa.text("INSERT OR IGNORE INTO watchlist_group (name, created_at) VALUES (:name, CURRENT_TIMESTAMP)"),
            {"name": name},
        )


def downgrade() -> None:
    from alembic import op

    op.drop_table("watchlist_group")
