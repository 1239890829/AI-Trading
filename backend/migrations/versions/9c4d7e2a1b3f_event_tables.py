"""event tables (linkage-design §4 E1)

事件卡 + 事件方向映射两张表。抽取由规则引擎（app/events/extract.py）产出，
status 只存人工裁决；active/expired 由读取方按 half_life 实时计算。

Revision ID: 9c4d7e2a1b3f
Revises: 6f2ab91c4d70
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op

revision: str = "9c4d7e2a1b3f"
down_revision = "6f2ab91c4d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    # 2026-09-09：历史缺陷修复——此前用 get_engine()（默认库 engine）建表，
    # 迁移实际跑在共享连接上（op.get_bind），fresh 测试/多库场景表会建到默认库
    # 而非迁移目标库（真实库当年碰巧建对，从未暴露；b7e5c3d9a2f4 首次在迁移里
    # 触碰 event_card 加列才在测试库报 no such table）。改共享连接。
    bind = op.get_bind()
    metadata = sa.MetaData()

    event_card = sa.Table(
        "event_card",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_tier", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("fact_kind", sa.String(length=8), nullable=False, server_default="fact"),
        sa.Column("certainty", sa.String(length=8), nullable=False, server_default="done"),
        sa.Column("category", sa.String(length=16), nullable=False, server_default="other"),
        sa.Column("half_life_hours", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("source_symbol", sa.String(length=6), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    sa.Index("ix_event_card_fingerprint", event_card.c.fingerprint, unique=True)

    event_direction = sa.Table(
        "event_direction",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=8), nullable=False, server_default="theme"),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strength", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chain", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("basis", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("matched_by", sa.String(length=16), nullable=False, server_default="name"),
        sa.UniqueConstraint("event_id", "target_type", "target", name="uq_event_direction"),
    )
    sa.Index("ix_event_direction_event_id", event_direction.c.event_id)

    metadata.create_all(bind)


def downgrade() -> None:
    import sqlalchemy as sa

    bind = op.get_bind()
    for table in ("event_direction", "event_card"):
        sa.Table(table, sa.MetaData(), autoload_with=bind).drop(bind)
