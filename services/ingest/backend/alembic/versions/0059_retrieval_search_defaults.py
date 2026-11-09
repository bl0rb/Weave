"""add retrieval_provider_config.search_top_k / search_final_k

Revision ID: 0059_retrieval_search_defaults
Revises: 0058_bot_memory_router_mode

Central defaults for Weave-Retrieval's hit counts (SEARCH_TOP_K /
SEARCH_FINAL_K), editable in the admin UI. NULL keeps the service's own
environment default, so existing installations behave exactly as before.
"""

from alembic import op
import sqlalchemy as sa

revision = '0059_retrieval_search_defaults'
down_revision = '0058_bot_memory_router_mode'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('retrieval_provider_config') as batch:
        batch.add_column(sa.Column('search_top_k', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('search_final_k', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('retrieval_provider_config') as batch:
        batch.drop_column('search_final_k')
        batch.drop_column('search_top_k')
