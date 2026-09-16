"""Point-in-time opportunity evidence and outcome labels.

revision: 7d4e2c9a6b1f
down: f2b8d1c7a3e9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7d4e2c9a6b1f"
down_revision = "f2b8d1c7a3e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_decision_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("as_of", sa.DateTime(), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_theme", sa.String(64), nullable=False, server_default=""),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("data_state", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "run_id", "stage", "symbol", "source_theme",
            name="uq_opportunity_snapshot_run_stage_symbol_theme",
        ),
    )
    # ⚠️ `snapshot_id` 的唯一性由**表级 UniqueConstraint** 承担、索引保持非唯一——
    # 这是本迁移**已应用到生产库**（`data/ashare.db` 版本号 = 本 revision，2026-09-16 实测）
    # 的既成形态，**不得事后改写**：已应用迁移是历史，改它只会让"全新库"与"生产库"分叉。
    # 因此模型侧改为与之逐字对齐（`unique=True, index=True` → `index=True` + 显式
    # UniqueConstraint），三条建库路径（生产 / alembic 新建 / create_all 兜底）从此同形。
    for name, column in (
        ("ix_opportunity_decision_snapshot_snapshot_id", "snapshot_id"),
        ("ix_opportunity_decision_snapshot_run_id", "run_id"),
        ("ix_opportunity_decision_snapshot_trade_date", "trade_date"),
        ("ix_opportunity_decision_snapshot_as_of", "as_of"),
        ("ix_opportunity_decision_snapshot_scenario", "scenario"),
        ("ix_opportunity_decision_snapshot_stage", "stage"),
        ("ix_opportunity_decision_snapshot_symbol", "symbol"),
        ("ix_opportunity_decision_snapshot_decision", "decision"),
    ):
        op.create_index(name, "opportunity_decision_snapshot", [column], unique=False)

    op.create_table(
        "opportunity_outcome_label",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False, server_default="d0_close"),
        sa.Column("target_date", sa.String(10), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("label", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("outcome_price", sa.Float(), nullable=True),
        sa.Column("return_pct", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="daily_close"),
        sa.Column("labeled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["snapshot_id"], ["opportunity_decision_snapshot.snapshot_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "horizon", name="uq_opportunity_outcome_snapshot_horizon"),
    )
    op.create_index(
        "ix_opportunity_outcome_label_snapshot_id", "opportunity_outcome_label", ["snapshot_id"], unique=False
    )
    op.create_index(
        "ix_opportunity_outcome_label_target_date", "opportunity_outcome_label", ["target_date"], unique=False
    )
    op.create_index(
        "ix_opportunity_outcome_label_state", "opportunity_outcome_label", ["state"], unique=False
    )


def downgrade() -> None:
    op.drop_table("opportunity_outcome_label")
    op.drop_table("opportunity_decision_snapshot")
