"""never hand out the slug of a deleted knowledge space again

Knowledge, Retrieval, bots and technical identities refer to a space by its
slug, and the withdrawal of its documents from Knowledge is asynchronous. A
later space reusing the slug would inherit whatever still points at the old
one (ADR 0008). Slugs deleted before this revision are unknown and are not
backfilled.
"""

from alembic import op
import sqlalchemy as sa


revision = '0038_collection_slug_tombstones'
down_revision = '0037_locale_auto'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'collection_slug_tombstones',
        sa.Column('slug', sa.String(length=255), primary_key=True),
        sa.Column('deleted_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('collection_slug_tombstones')
