"""Exact manual fill time, fee completeness and original selection receipt."""
from alembic import op
import sqlalchemy as sa
revision = 'a8d4f2c6e9b1'
down_revision = 'f2a7c9e4b6d8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('real_trade') as batch:
        batch.add_column(sa.Column('executed_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('fee_known', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('selection_ref', sa.String(96), nullable=True))
        batch.add_column(sa.Column('selection_evidence', sa.Text(), nullable=True))
        batch.create_index('ix_real_trade_traded_at', ['traded_at'])


def downgrade():
    raise RuntimeError('manual fill downgrade requires a reviewed evidence preservation plan')
