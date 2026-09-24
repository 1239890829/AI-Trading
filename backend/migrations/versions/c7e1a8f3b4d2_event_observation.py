"""Preserve source observations and pause stale event interpretations.

Revision ID: c7e1a8f3b4d2
Revises: b5c9e7a2d4f1
"""

from alembic import op
import sqlalchemy as sa


revision = "c7e1a8f3b4d2"
down_revision = "b5c9e7a2d4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_card", sa.Column("revision_pending_at", sa.DateTime(), nullable=True))
    op.create_table(
        "event_observation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("event_card.id"), nullable=False),
        sa.Column("observation_key", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_item_id", sa.String(128), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("source_published_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("source_symbols_json", sa.Text(), nullable=False),
        sa.Column("board_codes_json", sa.Text(), nullable=False),
        sa.Column("change_kind", sa.String(24), nullable=False),
    )
    op.create_index("ix_event_observation_event_id", "event_observation", ["event_id"])
    op.create_index("ix_event_observation_observation_key", "event_observation", ["observation_key"], unique=True)
    op.create_index("ix_event_observation_source_item_id", "event_observation", ["source_item_id"])
    with op.batch_alter_table("event_direction") as batch:
        batch.add_column(sa.Column("observation_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_event_direction_observation_id", "event_observation", ["observation_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("event_direction") as batch:
        batch.drop_constraint("fk_event_direction_observation_id", type_="foreignkey")
        batch.drop_column("observation_id")
    op.drop_index("ix_event_observation_source_item_id", table_name="event_observation")
    op.drop_index("ix_event_observation_observation_key", table_name="event_observation")
    op.drop_index("ix_event_observation_event_id", table_name="event_observation")
    op.drop_table("event_observation")
    op.drop_column("event_card", "revision_pending_at")
