"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # native_enum=False -> plain VARCHAR + CHECK constraint on every dialect
    # (no CREATE TYPE/DROP TYPE step needed on postgres either) -- same
    # discipline every other Weave service's enum columns use.
    message_role = sa.Enum(
        'user', 'assistant', name='message_role', native_enum=False, validate_strings=True,
    )

    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('username', sa.String(length=255), nullable=False, unique=True),
        sa.Column('team', sa.String(length=255), nullable=True),
        sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_users_team', 'users', ['team'])

    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('token_sha256', sa.String(length=64), nullable=False, unique=True),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_api_tokens_user_id', 'api_tokens', ['user_id'])
    op.create_index('ix_api_tokens_token_sha256', 'api_tokens', ['token_sha256'], unique=True)

    op.create_table(
        'conversations',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('bot_id', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
    op.create_index('ix_conversations_bot_id', 'conversations', ['bot_id'])

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            'conversation_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('role', message_role, nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.Column('trace', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])


def downgrade() -> None:
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('api_tokens')
    op.drop_table('users')
