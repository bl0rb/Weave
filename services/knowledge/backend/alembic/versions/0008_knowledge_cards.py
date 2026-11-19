"""knowledge_cards: FAQ/fact cards per knowledge space, switchable per space

Revision ID: 0008_knowledge_cards
Revises: 0007_knowledge_memory
Create Date: 2026-11-19

Additive per contracts/chunk-store.md's versioning rule (a new table, two new
nullable/defaulted columns). Weave-Retrieval's read-only role picks the table
up through the default SELECT privilege on new tables; otherwise
`GRANT SELECT ON knowledge_cards`.
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from app.core.config import settings


revision = '0008_knowledge_cards'
down_revision = '0007_knowledge_memory'
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == 'postgresql'
    # Same width source and dialect split as 0001_init's chunks.embedding.
    embedding_type = Vector(settings.embedding_dimension) if is_postgres else sa.JSON()

    op.create_table(
        'knowledge_cards',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('collection_slug', sa.String(length=255), nullable=False),
        sa.Column('document_id', sa.Uuid(), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_document_ids', sa.JSON(), nullable=False),
        sa.Column('source_sha256', sa.String(length=64), nullable=True),
        sa.Column('card_sha256', sa.String(length=64), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('keywords', sa.JSON(), nullable=False),
        sa.Column('embedding', embedding_type, nullable=True),
        sa.Column('embedding_model', sa.String(length=255), nullable=True),
        sa.Column('generator', sa.String(length=32), nullable=True),
        sa.Column('model', sa.String(length=255), nullable=True),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('collection_slug', 'card_sha256', name='uq_knowledge_cards_collection_card'),
    )
    op.create_index('ix_knowledge_cards_collection_slug', 'knowledge_cards', ['collection_slug'])
    op.create_index('ix_knowledge_cards_document_id', 'knowledge_cards', ['document_id'])
    if is_postgres:
        # Cosine ANN index, same as 0001_init's ix_chunks_embedding_hnsw;
        # dropped together with the table on downgrade.
        op.create_index(
            'ix_knowledge_cards_embedding_hnsw', 'knowledge_cards', ['embedding'],
            postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'},
        )

    # Per-space switch, mirrored from Weave-Ingest's registry; OFF by default.
    op.add_column(
        'collections',
        sa.Column('memory_cards_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Snapshot sha the cards of a document were last built for; NULL = not built.
    op.add_column('knowledge_memory', sa.Column('cards_sha256', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_memory', 'cards_sha256')
    op.drop_column('collections', 'memory_cards_enabled')
    op.drop_index('ix_knowledge_cards_document_id', table_name='knowledge_cards')
    op.drop_index('ix_knowledge_cards_collection_slug', table_name='knowledge_cards')
    op.drop_table('knowledge_cards')
