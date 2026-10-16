"""Keep explicit implementation, deployment and verification references on review items."""
from alembic import op
import sqlalchemy as sa

revision = "d2f4b6e8a0c1"
down_revision = "c9e3a7b5d1f2"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("review_action_items"):
        from app.review.models import ReviewActionItemRow
        ReviewActionItemRow.__table__.create(op.get_bind())
        return
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("review_action_items")}
    if "implementation_evidence" not in columns:
        op.add_column("review_action_items", sa.Column("implementation_evidence", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM review_action_items WHERE implementation_evidence NOT IN ('', '{}')")).scalar()
    if count:
        raise RuntimeError("Review implementation evidence requires a preservation plan before downgrade")
    op.drop_column("review_action_items", "implementation_evidence")
