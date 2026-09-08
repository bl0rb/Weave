"""extend managed bots to n8n and LLM configurations"""

from alembic import op
import sqlalchemy as sa

revision = '0023_managed_bot_kinds'
down_revision = '0022_worker_log_service'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('kind', sa.String(16), nullable=True))
    op.add_column('managed_bots', sa.Column('system_prompt', sa.Text(), nullable=True))
    op.add_column('managed_bots', sa.Column('temperature', sa.Float(), nullable=True))
    op.add_column('managed_bots', sa.Column('retrieval_enabled', sa.Boolean(), nullable=True))
    op.add_column('managed_bots', sa.Column('retrieval_filters', sa.JSON(), nullable=True))
    op.add_column('managed_bots', sa.Column('top_k', sa.Integer(), nullable=True))
    op.add_column('managed_bots', sa.Column('final_k', sa.Integer(), nullable=True))
    op.add_column('managed_bots', sa.Column('rerank', sa.Boolean(), nullable=True))
    op.add_column('managed_bots', sa.Column('include_uncollected', sa.Boolean(), nullable=True))
    with op.batch_alter_table('managed_bots') as batch:
        batch.alter_column('webhook_url', existing_type=sa.String(2048), nullable=True)
    op.execute("UPDATE managed_bots SET kind='n8n', retrieval_enabled=0, retrieval_filters='{}', top_k=20, final_k=5, rerank=1, include_uncollected=1 WHERE kind IS NULL")
    op.execute("UPDATE managed_bots SET system_prompt='Du führst Anfragen über den konfigurierten n8n-Workflow aus.' WHERE system_prompt IS NULL")
    # The application model supplies defaults for newly inserted rows. Keeping
    # these added columns nullable keeps the fresh-install migration portable
    # across PostgreSQL and SQLite's limited ALTER TABLE implementation.


def downgrade() -> None:
    for column in ('include_uncollected', 'rerank', 'final_k', 'top_k', 'retrieval_filters', 'retrieval_enabled', 'temperature', 'system_prompt', 'kind'):
        op.drop_column('managed_bots', column)
    with op.batch_alter_table('managed_bots') as batch:
        batch.alter_column('webhook_url', existing_type=sa.String(2048), nullable=False)