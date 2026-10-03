"""named LLM endpoints and per-bot endpoint selection

Revision ID: 0045_llm_endpoints
Revises: 0044_runtime_settings

sqlite-compatible on purpose (plain op.add_column), see 0030's docstring.
"""

from alembic import op
import sqlalchemy as sa


revision = '0045_llm_endpoints'
down_revision = '0044_runtime_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_provider_config', sa.Column('name', sa.String(255), nullable=False, server_default=''))
    op.add_column('managed_bots', sa.Column('llm_endpoint', sa.String(36), nullable=True))
    op.add_column('managed_bots', sa.Column('llm_endpoints', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    op.drop_column('managed_bots', 'llm_endpoints')
    op.drop_column('managed_bots', 'llm_endpoint')
    op.drop_column('chat_provider_config', 'name')
