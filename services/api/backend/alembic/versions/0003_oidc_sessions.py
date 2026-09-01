"""add users.oidc_subject and sessions table

Revision ID: 0003_oidc_sessions
Revises: 0002_add_users_is_admin
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = '0003_oidc_sessions'
down_revision = '0002_add_users_is_admin'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('oidc_subject', sa.String(length=255), nullable=True))
    op.create_index('ix_users_oidc_subject', 'users', ['oidc_subject'], unique=True)

    op.create_table(
        'sessions',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('ix_sessions_token_hash', 'sessions', ['token_hash'], unique=True)
    op.create_index('ix_sessions_expires_at', 'sessions', ['expires_at'])


def downgrade() -> None:
    op.drop_table('sessions')
    op.drop_index('ix_users_oidc_subject', table_name='users')
    op.drop_column('users', 'oidc_subject')
