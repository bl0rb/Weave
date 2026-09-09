"""add centrally managed n8n bots"""

from alembic import op
import sqlalchemy as sa


revision = '0019_managed_bots'
down_revision = '0018_chat_provider_config'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'managed_bots',
        sa.Column('id', sa.String(length=255), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('webhook_url', sa.String(length=2048), nullable=False),
        sa.Column('streaming', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('auth_token_encrypted', sa.Text(), nullable=True),
        sa.Column('timeout_seconds', sa.Integer(), nullable=False, server_default='120'),
        sa.Column('teams', sa.JSON(), nullable=False),
        sa.Column('collections', sa.JSON(), nullable=False),
        sa.Column('require_sources', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            'no_context_reply',
            sa.Text(),
            nullable=False,
            server_default='Ich habe dazu keine belegten Informationen gefunden.',
        ),
        sa.Column('updated_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('managed_bots')
