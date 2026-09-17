"""Add durable notification intent/attempts; no legacy events are queued."""
from alembic import op
import sqlalchemy as sa

revision = "d2e4a6b8c0f1"
down_revision = "c5d2f8a3b7e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("alert_event.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("expires_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_until_ms", sa.BigInteger(), nullable=True),
        sa.Column("send_started_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("accepted_at_ms", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("event_id", "channel", name="uq_outbox_event_channel"),
    )
    op.create_index("ix_notification_outbox_event_id", "notification_outbox", ["event_id"])
    op.create_index("ix_notification_outbox_state", "notification_outbox", ["state"])
    op.create_table(
        "notification_attempt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outbox_id", sa.Integer(), sa.ForeignKey("notification_outbox.id"), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False, unique=True),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("finished_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
    )
    op.create_index("ix_notification_attempt_outbox_id", "notification_attempt", ["outbox_id"])


def downgrade() -> None:
    # Do not drop evidence during a code rollback. Use the old code with these
    # additive tables retained; destructive schema retirement is a separate task.
    raise RuntimeError("outbox evidence must be archived before schema removal")
