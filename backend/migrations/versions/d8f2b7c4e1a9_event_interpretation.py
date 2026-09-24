"""Record immutable event interpretations and explicit review transitions.

Revision ID: d8f2b7c4e1a9
Revises: c7e1a8f3b4d2
"""

from alembic import op
import sqlalchemy as sa


revision = "d8f2b7c4e1a9"
down_revision = "c7e1a8f3b4d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_interpretation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("event_card.id"), nullable=False),
        sa.Column("observation_id", sa.Integer(), sa.ForeignKey("event_observation.id"), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_event_interpretation_event_id", "event_interpretation", ["event_id"])
    op.create_index("ix_event_interpretation_effective_at", "event_interpretation", ["effective_at"])


def downgrade() -> None:
    op.drop_index("ix_event_interpretation_effective_at", table_name="event_interpretation")
    op.drop_index("ix_event_interpretation_event_id", table_name="event_interpretation")
    op.drop_table("event_interpretation")
