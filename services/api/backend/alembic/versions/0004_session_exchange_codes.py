"""add session_exchange_codes table

Revision ID: 0004_session_exchange_codes
Revises: 0003_oidc_sessions
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = '0004_session_exchange_codes'
down_revision = '0003_oidc_sessions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'session_exchange_codes',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('code_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_session_exchange_codes_user_id', 'session_exchange_codes', ['user_id'])
    op.create_index('ix_session_exchange_codes_code_hash', 'session_exchange_codes', ['code_hash'], unique=True)
    op.create_index('ix_session_exchange_codes_expires_at', 'session_exchange_codes', ['expires_at'])


def downgrade() -> None:
    op.drop_table('session_exchange_codes')
