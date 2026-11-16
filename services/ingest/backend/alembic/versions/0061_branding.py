"""add branding_config / branding_assets (Administration › Betrieb › Erscheinungsbild)

Revision ID: 0061_branding
Revises: 0060_reingest_runs

Singleton colour row plus one row per uploaded asset (logo, busy
animation) with the bytes inline. sqlite-compatible on purpose (plain
op.create_table, no postgres-only DDL), same discipline as 0060.
"""

import sqlalchemy as sa
from alembic import op

revision = '0061_branding'
down_revision = '0060_reingest_runs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'branding_config',
        sa.Column('id', sa.String(length=16), primary_key=True),
        sa.Column('primary_color', sa.String(length=7), nullable=True),
        sa.Column('accent_color', sa.String(length=7), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_table(
        'branding_assets',
        sa.Column('kind', sa.String(length=16), primary_key=True),
        sa.Column('content_type', sa.String(length=32), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('branding_assets')
    op.drop_table('branding_config')
