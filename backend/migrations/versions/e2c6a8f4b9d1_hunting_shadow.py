"""Hunting shadow attempt evidence and explicit longer account scope."""
from alembic import op
import sqlalchemy as sa

revision = "e2c6a8f4b9d1"
down_revision = "d7b3e6a209f4"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("paper_account", "paper_position", "paper_order", "paper_action_receipt", "paper_reset_backup"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("scope", existing_type=sa.String(12), type_=sa.String(32))
    op.create_table(
        "hunting_shadow_attempt",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("decision_id", sa.String(64), nullable=False),
        sa.Column("decision_version", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("entry_order_id", sa.Integer(), nullable=True),
        sa.Column("exit_order_id", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_hunting_shadow_attempt_trade_date", "hunting_shadow_attempt", ["trade_date"])
    op.create_index("ix_hunting_shadow_attempt_decision_id", "hunting_shadow_attempt", ["decision_id"])


def downgrade():
    # Preserve execution evidence and accounts; rollback code does not erase fills.
    raise RuntimeError("hunting-shadow downgrade requires a reviewed data preservation plan")
