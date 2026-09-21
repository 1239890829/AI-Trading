"""Add corporate-action-safe price basis fields to opportunity outcomes.

revision: c7f3e1a9b4d2
down: a6e2c9f4b7d1

Legacy labels keep an empty basis version so readers cannot silently reinterpret
old raw-reference/qfq-close arithmetic under the new contract.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7f3e1a9b4d2"
down_revision = "a6e2c9f4b7d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "opportunity_outcome_label"
    op.add_column(table, sa.Column("basis_reference_price", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("reference_adjustment_factor", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("price_basis_version", sa.String(48), nullable=False, server_default=""))
    op.add_column(table, sa.Column("price_basis_source", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    raise RuntimeError("opportunity price-basis evidence must be archived before schema removal")
