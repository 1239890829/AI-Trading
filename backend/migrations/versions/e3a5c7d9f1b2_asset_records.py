"""Namespaced manual fund records, no migration of A-share accounts.

Revision ID: e3a5c7d9f1b2
Revises: d2f4b6e8a0c1
"""
from alembic import op
import sqlalchemy as sa

revision = "e3a5c7d9f1b2"
down_revision = "d2f4b6e8a0c1"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy bootstrap may have created the missing table from current metadata.
    if sa.inspect(op.get_bind()).has_table("asset_record"):
        return
    op.create_table(
        "asset_record",
        sa.Column("instrument_id", sa.String(96), primary_key=True),
        sa.Column("asset_type", sa.String(24), nullable=False),
        sa.Column("source_symbol", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("name", sa.String(80), nullable=True),
        sa.Column("is_watched", sa.Boolean(), nullable=False),
        sa.Column("units", sa.Numeric(28, 8), nullable=True),
        sa.Column("cost_total", sa.Numeric(28, 8), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM asset_record")).scalar_one():
        raise RuntimeError("manual fund records require a preservation plan before schema removal")
    op.drop_table("asset_record")
