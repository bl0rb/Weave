"""add central OpenAI-compatible chat provider configuration"""

from alembic import op
import sqlalchemy as sa


revision = '0018_chat_provider_config'
down_revision = '0017_document_releases'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'chat_provider_config',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('base_url', sa.String(length=1024), nullable=False, server_default=''),
        sa.Column('model', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('timeout_seconds', sa.Float(), nullable=False, server_default='60'),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('updated_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('chat_provider_config')
