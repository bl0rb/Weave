"""add central embedding and reranker configuration"""

from alembic import op
import sqlalchemy as sa

revision = '0025_retrieval_provider_config'
down_revision = '0024_user_team_roles'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'retrieval_provider_config',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('embedding_provider', sa.String(32), nullable=False, server_default='fake'),
        sa.Column('embedding_base_url', sa.String(1024), nullable=False, server_default=''),
        sa.Column('embedding_model', sa.String(255), nullable=False, server_default='fake-embed'),
        sa.Column('embedding_dimension', sa.Integer(), nullable=False, server_default='1536'),
        sa.Column('embedding_batch_size', sa.Integer(), nullable=False, server_default='64'),
        sa.Column('embedding_api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('rerank_provider', sa.String(32), nullable=False, server_default='none'),
        sa.Column('rerank_base_url', sa.String(1024), nullable=False, server_default=''),
        sa.Column('rerank_model', sa.String(255), nullable=False, server_default=''),
        sa.Column('rerank_max_documents', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('rerank_batch_size', sa.Integer(), nullable=False, server_default='16'),
        sa.Column('rerank_threads', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('rerank_api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('semantic_weight', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('lexical_weight', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('updated_by_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('retrieval_provider_config')