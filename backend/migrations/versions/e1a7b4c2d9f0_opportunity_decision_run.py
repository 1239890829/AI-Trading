"""Append-only opportunity decision run ledger.

revision: e1a7b4c2d9f0
down: d9e4c2b7a1f6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1a7b4c2d9f0"
down_revision = "d9e4c2b7a1f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_decision_run",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("data_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("snapshot_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("snapshot_as_of", sa.String(40), nullable=False, server_default=""),
        sa.Column("theme_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("participant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_audit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hard_gate_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notification_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage_counts", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("decision_counts", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("linkage_stats", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("caveats", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("run_id"),
    )
    for column in ("trade_date", "as_of", "scenario", "data_state"):
        op.create_index(
            f"ix_opportunity_decision_run_{column}",
            "opportunity_decision_run", [column], unique=False,
        )


def downgrade() -> None:
    raise RuntimeError("opportunity decision run evidence must be archived before schema removal")
