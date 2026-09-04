"""paper 三表加 scope（影子账户，picks-intraday-fusion-assessment §4）

main = 用户交易页签；shadow = 每日精选影子持仓对照账户。
paper_position 的 symbol 全局唯一改为 (scope, symbol) 唯一。

Revision ID: d5e9a7f3b1c2
Revises: b7d4f8a2e6c9
Create Date: 2026-09-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5e9a7f3b1c2"
down_revision = "b7d4f8a2e6c9"
branch_labels = None
depends_on = None


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        sa.text("select count(*) from sqlite_master where type='table' and name=:n"),
        {"n": name},
    ).scalar()
    return bool(row)


def _has_column(conn, table: str, col: str) -> bool:
    cols = conn.execute(
        sa.text(f"select name from pragma_table_info('{table}')")
    ).fetchall()
    return any(c[0] == col for c in cols)


def upgrade() -> None:
    conn = op.get_bind()
    for table in ("paper_account", "paper_position", "paper_order"):
        if not _table_exists(conn, table):
            continue  # fresh-at-baseline 之外的空库场景：ORM create_all 会带全量结构
        if not _has_column(conn, table, "scope"):
            op.add_column(
                table,
                sa.Column("scope", sa.String(length=12), nullable=False,
                          server_default="main"),
            )
        op.create_index(f"ix_{table}_scope", table, ["scope"], unique=False)

    # 持仓唯一性：symbol 全局唯一 → (scope, symbol) 唯一
    if _table_exists(conn, "paper_position"):
        # SQLite：先删旧唯一索引（baseline 建为 unique index），再建新约束索引
        old = conn.execute(
            sa.text("select name from sqlite_master where type='index' and tbl_name='paper_position' and name like 'ix_paper_position_symbol'")
        ).scalar()
        if old:
            op.drop_index("ix_paper_position_symbol", table_name="paper_position")
        conn.execute(sa.text(
            "create unique index if not exists uq_paper_position_scope_symbol on paper_position (scope, symbol)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "paper_position"):
        conn.execute(sa.text("drop index if exists uq_paper_position_scope_symbol"))
        op.create_index(op.f("ix_paper_position_symbol"), "paper_position", ["symbol"], unique=True)
    for table in ("paper_account", "paper_position", "paper_order"):
        if _table_exists(conn, table) and _has_column(conn, table, "scope"):
            op.drop_index(f"ix_{table}_scope", table_name=table)
            op.drop_column(table, "scope")
