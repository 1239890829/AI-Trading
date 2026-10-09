"""Bind daily observations to their selection version and performance window."""
from alembic import op
import sqlalchemy as sa

revision = "f2a7c9e4b6d8"
down_revision = "e2c6a8f4b9d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("daily_pick_review") as batch:
        batch.add_column(sa.Column("review_context", sa.Text(), nullable=True))
        batch.add_column(sa.Column("selection_version", sa.String(64), nullable=True))
        batch.drop_constraint("uq_daily_pick_review_date_symbol", type_="unique")
        batch.create_unique_constraint("uq_daily_pick_review_generation", ["date", "symbol", "selection_version"])


def downgrade():
    # Dropping this evidence would make bound reviews indistinguishable from legacy rows.
    raise RuntimeError("daily-review downgrade requires a reviewed evidence preservation plan")
