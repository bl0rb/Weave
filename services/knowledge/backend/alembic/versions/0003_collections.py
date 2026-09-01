"""add collections registry mirror + documents.collection_slug

Revision ID: 0003_collections
Revises: 0002_document_index_fields
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = '0003_collections'
down_revision = '0002_document_index_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, no FK to collections.slug -- see app/models/models.py's
    # Document.collection_slug docstring: the registry below is a
    # periodically-synced mirror that may legitimately lag behind a
    # document that already names a brand-new collection.
    op.add_column('documents', sa.Column('collection_slug', sa.String(length=255), nullable=True))
    op.create_index('ix_documents_collection_slug', 'documents', ['collection_slug'])

    # slug is the primary key on purpose (see Collection's class docstring)
    # -- no separate surrogate id column. `read_teams` is plain JSON with no
    # server_default, same as `documents.tags`/`chunks.meta` above (an
    # application-level Python default via the ORM column, not a
    # dialect-portable JSON literal default).
    op.create_table(
        'collections',
        sa.Column('slug', sa.String(length=255), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('read_teams', sa.JSON(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('collections')
    op.drop_index('ix_documents_collection_slug', table_name='documents')
    op.drop_column('documents', 'collection_slug')
