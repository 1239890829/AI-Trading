"""sentiment_history 情绪周期序列表（retro #17）

Revision ID: a7c3e91d2f44
Revises: 91f8ea3c3a3e
Create Date: 2026-08-30

每日情绪判定一行（review 钩子 / live 惰性补录），trade_date 主键、不覆盖历史。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c3e91d2f44"
down_revision = "91f8ea3c3a3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentiment_history",
        sa.Column("trade_date", sa.String(8), primary_key=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(8), nullable=True),
        sa.Column("phase_unreliable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sentiment_history")
