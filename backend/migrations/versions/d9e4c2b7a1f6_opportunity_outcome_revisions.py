"""Append-only close-outcome revisions.

revision: d9e4c2b7a1f6
down: c7f3e1a9b4d2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d9e4c2b7a1f6"
down_revision = "c7f3e1a9b4d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_outcome_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_outcome_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("target_date", sa.String(10), nullable=False),
        sa.Column("revision_version", sa.String(96), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("label", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("outcome_price", sa.Float(), nullable=True),
        sa.Column("return_pct", sa.Float(), nullable=True),
        sa.Column("fill_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("cost_pct", sa.Float(), nullable=True),
        sa.Column("net_return_pct", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="daily_close"),
        sa.Column("basis_reference_price", sa.Float(), nullable=True),
        sa.Column("reference_adjustment_factor", sa.Float(), nullable=True),
        sa.Column("price_basis_version", sa.String(48), nullable=False, server_default=""),
        sa.Column("price_basis_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("cost_model_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("labeled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["base_outcome_id"], ["opportunity_outcome_label.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["opportunity_decision_snapshot.snapshot_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_outcome_id", "revision_version",
            name="uq_opportunity_outcome_revision_base_version",
        ),
    )
    for column in (
        "base_outcome_id", "snapshot_id", "horizon", "target_date",
        "revision_version", "state", "fill_state",
    ):
        op.create_index(
            f"ix_opportunity_outcome_revision_{column}",
            "opportunity_outcome_revision", [column], unique=False,
        )


def downgrade() -> None:
    raise RuntimeError("outcome revision evidence must be archived before schema removal")
