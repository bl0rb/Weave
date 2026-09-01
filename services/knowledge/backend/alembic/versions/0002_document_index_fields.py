"""add documents.markdown_url and documents.index_attempts

Revision ID: 0002_document_index_fields
Revises: 0001_init
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = '0002_document_index_fields'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable -- see app/models/models.py's Document.markdown_url
    # docstring: app/api/events.py always sets it for a real document.processed
    # webhook, but not every Document row in this codebase goes through that
    # path (plenty of ORM-level tests build bare rows for unrelated columns).
    op.add_column('documents', sa.Column('markdown_url', sa.String(length=2048), nullable=True))
    op.add_column(
        'documents',
        sa.Column('index_attempts', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('documents', 'index_attempts')
    op.drop_column('documents', 'markdown_url')
