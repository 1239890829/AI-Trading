"""event tables (linkage-design §4 E1)

事件卡 + 事件方向映射两张表。抽取由规则引擎（app/events/extract.py）产出，
status 只存人工裁决；active/expired 由读取方按 half_life 实时计算。

Revision ID: 9c4d7e2a1b3f
Revises: 6f2ab91c4d70
Create Date: 2026-08-31
"""

revision: str = "9c4d7e2a1b3f"
down_revision = "6f2ab91c4d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import sqlalchemy as sa

    from app.core.db import get_engine

    engine = get_engine()
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

    metadata.create_all(engine)


def downgrade() -> None:
    import sqlalchemy as sa

    from app.core.db import get_engine

    engine = get_engine()
    for table in ("event_direction", "event_card"):
        sa.Table(table, sa.MetaData(), autoload_with=engine).drop(engine)
