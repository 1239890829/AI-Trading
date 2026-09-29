"""Atomic simulated user action receipts."""
from alembic import op
import sqlalchemy as sa

revision = "d7b3e6a209f4"
down_revision = "c8a6e4d2f091"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_action_receipt",
        sa.Column("scope", sa.String(12), primary_key=True),
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("draft", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "buy_point_consumption",
        sa.Column("event_id", sa.Integer(), primary_key=True),
        sa.Column("consumer", sa.String(16), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "paper_reset_backup",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(12), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("after_digest", sa.String(64), nullable=False),
        sa.Column("restored", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("paper_reset_backup")
    op.drop_table("buy_point_consumption")
    op.drop_table("paper_action_receipt")
