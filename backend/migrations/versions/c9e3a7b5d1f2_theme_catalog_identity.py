"""Separate official catalog freshness from membership freshness.

Revision ID: c9e3a7b5d1f2
Revises: a8d4f2c6e9b1
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e3a7b5d1f2"
down_revision = "a8d4f2c6e9b1"
branch_labels = None
depends_on = None


def upgrade():
    # Fresh baseline creates current ORM tables; upgrade must also support it.
    if not sa.inspect(op.get_bind()).has_table("theme"):
        from app.models.theme_catalog import Theme
        Theme.__table__.create(op.get_bind())
        return
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("theme")}
    if "catalog_active" not in columns:
        op.add_column("theme", sa.Column("catalog_active", sa.Boolean(), nullable=False, server_default="1"))
    if "catalog_synced_at" not in columns:
        op.add_column("theme", sa.Column("catalog_synced_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("theme", "catalog_synced_at")
    op.drop_column("theme", "catalog_active")
