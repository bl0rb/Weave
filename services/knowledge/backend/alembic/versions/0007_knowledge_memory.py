"""knowledge_memory: condensed summaries per document and collection (ADR-0009)

Revision ID: 0007_knowledge_memory
Revises: 0006_visibility_fail_closed
Create Date: 2026-10-18

Additive per contracts/chunk-store.md's versioning rule (a new table).
Weave-Retrieval's read-only role picks it up through the default SELECT
privilege on new tables; otherwise `GRANT SELECT ON knowledge_memory`.
"""

from alembic import op
import sqlalchemy as sa


revision = '0007_knowledge_memory'
down_revision = '0006_visibility_fail_closed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_memory',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('collection_slug', sa.String(length=255), nullable=False),
        sa.Column(
            'document_id', sa.Uuid(), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=True, unique=True
        ),
        sa.Column('title', sa.String(length=500), nullable=True),
        sa.Column('document_type', sa.String(length=100), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('key_points', sa.JSON(), nullable=False),
        sa.Column('topics', sa.JSON(), nullable=False),
        sa.Column('source_document_ids', sa.JSON(), nullable=False),
        sa.Column('source_sha256', sa.String(length=64), nullable=True),
        sa.Column('generator', sa.String(length=16), nullable=True),
        sa.Column('model', sa.String(length=255), nullable=True),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dirty_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('building_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_duration_ms', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('llm_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('llm_failures', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_index('ix_knowledge_memory_collection_slug', 'knowledge_memory', ['collection_slug'])
    op.create_index(
        'uq_knowledge_memory_collection', 'knowledge_memory', ['collection_slug'], unique=True,
        postgresql_where=sa.text("kind = 'collection'"), sqlite_where=sa.text("kind = 'collection'"),
    )


def downgrade() -> None:
    op.drop_index('uq_knowledge_memory_collection', table_name='knowledge_memory')
    op.drop_index('ix_knowledge_memory_collection_slug', table_name='knowledge_memory')
    op.drop_table('knowledge_memory')
