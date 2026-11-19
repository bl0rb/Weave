"""per-space switch for Knowledge's question/answer cards

Revision ID: 0064_memory_cards
Revises: 0063_document_freshness

- collections.memory_cards_enabled: whether Knowledge condenses the space's
  released documents into question/answer cards ("Wissenskarten",
  ADR 0009). Default off; carried to Knowledge by the registry sync.

sqlite-compatible on purpose (plain op.add_column, batch_alter_table for the
drop), same discipline as 0063, so tests/test_migrations.py can drive it.
"""

import sqlalchemy as sa
from alembic import op

revision = '0064_memory_cards'
down_revision = '0063_document_freshness'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'collections',
        sa.Column('memory_cards_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table('collections') as batch:
        batch.drop_column('memory_cards_enabled')
