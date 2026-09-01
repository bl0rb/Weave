"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from app.core.config import settings


revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # native_enum=False -> plain VARCHAR + CHECK constraint on every dialect
    # (no CREATE TYPE/DROP TYPE step needed on postgres either) -- same
    # discipline Weave-Ingest uses for UserRole/ImportAuthType/etc.
    document_status = sa.Enum(
        'pending', 'indexed', 'blocked', 'superseded', 'failed',
        name='document_status', native_enum=False, validate_strings=True,
    )

    op.create_table(
        'documents',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('source_job_id', sa.String(length=36), nullable=False, unique=True),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('document_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('previous_job_id', sa.String(length=36), nullable=True),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('engine', sa.String(length=64), nullable=False),
        sa.Column('quality_grade', sa.String(length=8), nullable=True),
        sa.Column('quality_recommendation', sa.String(length=16), nullable=True),
        sa.Column('frontmatter', sa.JSON(), nullable=False),
        sa.Column('markdown_body', sa.Text(), nullable=True),
        sa.Column('team', sa.String(length=255), nullable=True),
        sa.Column('department', sa.String(length=255), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('embedding_model', sa.String(length=255), nullable=True),
        sa.Column('status', document_status, nullable=False, server_default='pending'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_documents_content_sha256', 'documents', ['content_sha256'])
    op.create_index('ix_documents_previous_job_id', 'documents', ['previous_job_id'])
    op.create_index('ix_documents_team', 'documents', ['team'])
    op.create_index('ix_documents_department', 'documents', ['department'])
    op.create_index('ix_documents_status', 'documents', ['status'])

    # Vector column width is read from settings.embedding_dimension (same
    # source app/models/models.py's VectorType reads from) rather than a
    # hardcoded literal, so this migration can never silently drift from
    # app/core/config.py's actual configured dimension. Changing the
    # embedding dimension later still means a new migration (a fresh
    # vector(N) column, since pgvector can't ALTER an existing one's
    # dimension in place) plus a full reindex of every existing chunk's
    # embedding, never just an env var flip -- reading the setting here only
    # keeps this migration's *initial* value honest, it doesn't make a
    # dimension change migration-free. SQLite has no pgvector extension, so
    # the same column is plain JSON there -- good enough to round-trip a
    # vector in tests, with no similarity search.
    embedding_type = Vector(settings.embedding_dimension) if is_postgres else sa.JSON()

    op.create_table(
        'chunks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            'document_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('heading_path', sa.JSON(), nullable=False),
        sa.Column('page_start', sa.Integer(), nullable=True),
        sa.Column('page_end', sa.Integer(), nullable=True),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('embedding', embedding_type, nullable=True),
        sa.Column('embedding_model', sa.String(length=255), nullable=True),
        sa.UniqueConstraint('document_id', 'chunk_index', name='uq_chunks_document_id_chunk_index'),
    )
    op.create_index('ix_chunks_document_id', 'chunks', ['document_id'])

    if is_postgres:
        # ANN index for pgvector similarity search on chunks.embedding --
        # without it, every similarity query (Weave-Retrieval's cosine
        # search) does a full table scan instead of using the index
        # app/models/models.py's VectorType docstring already claims exists.
        # HNSW (over IVFFlat) needs no separate training/ANALYZE step before
        # it's usable, and pgvector/pgvector:pg16 supports it out of the
        # box. `vector_cosine_ops` matches cosine similarity, the distance
        # function the embedding/retrieval path actually uses (see
        # app/services/embeddings.py) -- an index built with the L2 or
        # inner-product ops class would silently never be used by a
        # cosine-distance query. SQLite has no pgvector extension at all
        # (chunks.embedding is plain JSON there, see embedding_type above),
        # so this index is postgres-only, same as the tsv/GIN index below.
        # No explicit drop in downgrade() -- dropping the `chunks` table
        # removes this index too, same as it already implicitly removes the
        # tsv column's GIN index below.
        op.create_index(
            'ix_chunks_embedding_hnsw', 'chunks', ['embedding'],
            postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'},
        )

        # GENERATED tsvector column for fulltext matching alongside pgvector
        # similarity search (see README's "tsvector-Indizes fuer Volltext-
        # Matching"). SQLite has neither tsvector nor STORED generated
        # columns of this kind, so this whole block -- column + GIN index --
        # is postgres-only; a SQLite `chunks` table simply has no `tsv`
        # column at all, which is fine since nothing on that path queries it.
        op.execute(
            "ALTER TABLE chunks ADD COLUMN tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED"
        )
        op.execute('CREATE INDEX ix_chunks_tsv ON chunks USING GIN (tsv)')

    op.create_table(
        'ingest_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_key', sa.String(length=160), nullable=False, unique=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('ingest_events')
    op.drop_table('chunks')
    op.drop_table('documents')
