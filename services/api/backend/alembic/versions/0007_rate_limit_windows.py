"""Shared rate-limit counters for multiple Weave-API replicas.

Revision ID: 0007_rate_limit_windows
Revises: 0006_user_locale
"""

from alembic import op
import sqlalchemy as sa


revision = '0007_rate_limit_windows'
down_revision = '0006_user_locale'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rate_limit_windows',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('window_start', sa.BigInteger(), primary_key=True),
        sa.Column('count', sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('rate_limit_windows')
