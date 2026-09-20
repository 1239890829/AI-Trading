"""Add nullable durable dedup key to alert_event; old events remain untouched."""
from alembic import op
import sqlalchemy as sa

revision = "f8c1d4e7a2b6"
down_revision = "d2e4a6b8c0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_event", sa.Column("dedup_key", sa.String(64), nullable=True))
    # SQLite UNIQUE index permits multiple NULLs, preserving all legacy rows while
    # giving new buy-point events an atomic create-once key.
    op.create_index("ux_alert_event_dedup_key", "alert_event", ["dedup_key"], unique=True)


def downgrade() -> None:
    # The key may be the only durable proof that a notification intent was created.
    # Do not destroy evidence as part of a code rollback.
    raise RuntimeError("alert_event dedup evidence must be archived before schema removal")
