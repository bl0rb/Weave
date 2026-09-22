"""add documents.embedding_attempts

Revision ID: 0004_embedding_attempts
Revises: 0003_collections
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa


revision = '0004_embedding_attempts'
down_revision = '0003_collections'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Incident 2026-09-22: a separate retry-attempt counter for the embed
    # step (app/workers/tasks.py's embedding-failure backoff path), kept
    # independent of the existing index_attempts (markdown-fetch retries) --
    # see app/models/models.py's Document.embedding_attempts docstring.
    op.add_column(
        'documents',
        sa.Column('embedding_attempts', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('documents', 'embedding_attempts')
