"""add collections.visibility + collections.read_users

Revision ID: 0005_collection_visibility
Revises: 0004_embedding_attempts
Create Date: 2026-09-24
"""

from alembic import op
import sqlalchemy as sa


revision = '0005_collection_visibility'
down_revision = '0004_embedding_attempts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-existing rows get the same derivation as Weave-Ingest's own
    # 0032_collection_visibility backfill. A blanket 'public' default would
    # expose every team-restricted collection to everyone until the next
    # successful registry sync -- indefinitely if Weave-Ingest is down.
    op.add_column(
        'collections',
        sa.Column('visibility', sa.String(length=20), nullable=False, server_default='public'),
    )
    op.add_column(
        'collections',
        sa.Column('read_users', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    bind = op.get_bind()
    collections_table = sa.table(
        'collections',
        sa.column('slug', sa.String),
        sa.column('read_teams', sa.JSON),
        sa.column('visibility', sa.String),
    )
    for row in bind.execute(sa.select(collections_table.c.slug, collections_table.c.read_teams)).fetchall():
        if row.read_teams:
            bind.execute(
                collections_table.update().where(collections_table.c.slug == row.slug).values(visibility='restricted')
            )


def downgrade() -> None:
    op.drop_column('collections', 'read_users')
    op.drop_column('collections', 'visibility')
