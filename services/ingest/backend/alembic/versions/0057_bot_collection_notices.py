"""add managed_bots.collection_notices

Revision ID: 0057_bot_collection_notices
Revises: 0056_source_catalog_website

Weave-Runtime's BotConfig.collection_notices: a notice (e.g. "Nur intern")
flagged on answers that use a source from a given knowledge space.
Existing rows have none.
"""

from alembic import op
import sqlalchemy as sa

revision = '0057_bot_collection_notices'
down_revision = '0056_source_catalog_website'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('collection_notices', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('collection_notices')
