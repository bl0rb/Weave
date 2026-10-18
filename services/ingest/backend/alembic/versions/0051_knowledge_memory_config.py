"""Knowledge memory admin settings (ADR-0009)

Revision ID: 0051_knowledge_memory_config
Revises: 0050_import_syncs

Singleton row like retrieval_provider_config: mode, LLM endpoint and the
rebuild limits, editable under Administration › KI › Wissensgedächtnis and
projected to Weave-Knowledge and Weave-Runtime.
"""

from alembic import op
import sqlalchemy as sa


revision = '0051_knowledge_memory_config'
down_revision = '0050_import_syncs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_memory_config',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('mode', sa.String(length=16), server_default='off', nullable=False),
        sa.Column('llm_endpoint', sa.String(length=64), server_default='default', nullable=False),
        sa.Column('debounce_seconds', sa.Integer(), server_default='120', nullable=False),
        sa.Column('max_documents_per_run', sa.Integer(), server_default='25', nullable=False),
        sa.Column('max_input_chars', sa.Integer(), server_default='12000', nullable=False),
        sa.Column('updated_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('knowledge_memory_config')
