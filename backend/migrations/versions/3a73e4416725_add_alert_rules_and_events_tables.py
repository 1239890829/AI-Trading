"""add alert rules and events tables

Revision ID: 3a73e4416725
Revises: a7c3e91d2f44
Create Date: 2026-08-30 20:06:33.910905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a73e4416725'
down_revision: Union[str, Sequence[str], None] = 'a7c3e91d2f44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'alert_rule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('enabled', sa.Integer(), nullable=False),
        sa.Column('condition_type', sa.String(length=32), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('symbols', sa.String(length=512), nullable=True),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('cooldown_seconds', sa.Integer(), nullable=False),
        sa.Column('channels', sa.String(length=256), nullable=False),
        sa.Column('last_triggered_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'alert_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=12), nullable=False),
        sa.Column('trigger_value', sa.Float(), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('triggered_at', sa.DateTime(), nullable=False),
        sa.Column('acknowledged', sa.Integer(), nullable=False),
        sa.Column('delivered_channels', sa.String(length=256), nullable=True),
        sa.Column('snapshot', sa.String(length=1024), nullable=True),
        sa.ForeignKeyConstraint(['rule_id'], ['alert_rule.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alert_event_rule_id', 'alert_event', ['rule_id'])
    op.create_index('ix_alert_event_symbol', 'alert_event', ['symbol'])
    op.create_index('ix_alert_event_triggered_at', 'alert_event', ['triggered_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_alert_event_triggered_at', table_name='alert_event')
    op.drop_index('ix_alert_event_symbol', table_name='alert_event')
    op.drop_index('ix_alert_event_rule_id', table_name='alert_event')
    op.drop_table('alert_event')
    op.drop_table('alert_rule')
