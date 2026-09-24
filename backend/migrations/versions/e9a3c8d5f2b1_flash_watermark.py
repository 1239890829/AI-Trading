"""Persist flash coverage frontier and unresolved gaps per source channel.

Revision ID: e9a3c8d5f2b1
Revises: d8f2b7c4e1a9
"""

from alembic import op
import sqlalchemy as sa


revision = "e9a3c8d5f2b1"
down_revision = "d8f2b7c4e1a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flash_watermark",
        sa.Column("channel", sa.Integer(), primary_key=True),
        sa.Column("last_code", sa.String(128), nullable=True),
        sa.Column("last_show_time", sa.DateTime(), nullable=True),
        sa.Column("baseline_at", sa.DateTime(), nullable=True),
        sa.Column("last_fetch_at", sa.DateTime(), nullable=True),
        sa.Column("last_complete_at", sa.DateTime(), nullable=True),
        sa.Column("gap_at", sa.DateTime(), nullable=True),
        sa.Column("gap_reason", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("flash_watermark")
