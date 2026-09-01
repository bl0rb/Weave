"""add users.is_admin

Revision ID: 0002_add_users_is_admin
Revises: 0001_init
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = '0002_add_users_is_admin'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('users', 'is_admin')
