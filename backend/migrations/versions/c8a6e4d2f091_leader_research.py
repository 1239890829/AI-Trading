"""Research observation history and independent closing census.

Revision ID: c8a6e4d2f091
Revises: a4e8c2d9f6b1
"""
from alembic import op
import sqlalchemy as sa

revision = "c8a6e4d2f091"
down_revision = "a4e8c2d9f6b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leader_research_observation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("observation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("version", sa.String(48), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("ix_leader_research_observation_symbol", "leader_research_observation", ["symbol"])
    op.create_index("ix_leader_research_observation_trade_date", "leader_research_observation", ["trade_date"])
    op.create_table(
        "leader_research_session",
        sa.Column("trade_date", sa.String(10), primary_key=True),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("version", sa.String(48), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("leader_research_session")
    op.drop_table("leader_research_observation")
