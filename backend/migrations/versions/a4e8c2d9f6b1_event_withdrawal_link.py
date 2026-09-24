"""Bind a human-confirmed new-ID withdrawal to the old observation and decision.

Revision ID: a4e8c2d9f6b1
Revises: e9a3c8d5f2b1
"""

from alembic import op
import sqlalchemy as sa


revision = "a4e8c2d9f6b1"
down_revision = "e9a3c8d5f2b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_withdrawal_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_observation_id", sa.Integer(),
                  sa.ForeignKey("event_observation.id"), nullable=False),
        sa.Column("notice_observation_id", sa.Integer(),
                  sa.ForeignKey("event_observation.id"), nullable=False),
        sa.Column("prior_interpretation_id", sa.Integer(),
                  sa.ForeignKey("event_interpretation.id"), nullable=False, unique=True),
        sa.Column("withdrawn_interpretation_id", sa.Integer(),
                  sa.ForeignKey("event_interpretation.id"), nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("target_observation_id", "notice_observation_id",
                            name="uq_event_withdrawal_observations"),
    )
    op.create_index("ix_event_withdrawal_link_target_observation_id", "event_withdrawal_link",
                    ["target_observation_id"])
    op.create_index("ix_event_withdrawal_link_notice_observation_id", "event_withdrawal_link",
                    ["notice_observation_id"])


def downgrade() -> None:
    op.drop_index("ix_event_withdrawal_link_notice_observation_id", table_name="event_withdrawal_link")
    op.drop_index("ix_event_withdrawal_link_target_observation_id", table_name="event_withdrawal_link")
    op.drop_table("event_withdrawal_link")
