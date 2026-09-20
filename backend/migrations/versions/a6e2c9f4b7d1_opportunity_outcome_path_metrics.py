"""Add point-in-time path outcome fields to opportunity labels.

revision: a6e2c9f4b7d1
down: f8c1d4e7a2b6

RSH-026: D0 post-decision MFE/MAE and first-limit timing are **outcomes**.
They belong on the existing versioned outcome row, never back in the immutable
decision snapshot. Legacy rows stay `unknown`; no historical value is invented.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6e2c9f4b7d1"
down_revision = "f8c1d4e7a2b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "opportunity_outcome_label"
    op.add_column(table, sa.Column("path_state", sa.String(16), nullable=False, server_default="unknown"))
    op.add_column(table, sa.Column("path_high_price", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("path_low_price", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("mfe_pct", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("mae_pct", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("path_bar_count", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("limit_state", sa.String(24), nullable=False, server_default="unknown"))
    op.add_column(table, sa.Column("first_limit_time", sa.String(8), nullable=True))
    op.add_column(table, sa.Column("time_to_limit_minutes", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("path_source", sa.String(64), nullable=False, server_default=""))
    op.add_column(table, sa.Column("path_version", sa.String(32), nullable=False, server_default=""))
    op.add_column(table, sa.Column("path_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column(table, sa.Column("path_resolved_at", sa.DateTime(), nullable=True))
    op.create_index("ix_opportunity_outcome_label_path_state", table, ["path_state"], unique=False)


def downgrade() -> None:
    # Path outcomes can be the only retained evidence of post-decision excursion/timing.
    # A code rollback may stop reading them, but schema rollback must not erase evidence.
    raise RuntimeError("opportunity path outcome evidence must be archived before schema removal")
