"""persist suppression of read-only Runtime/YAML bots"""

from alembic import op
import sqlalchemy as sa


revision = '0027_bot_tombstones'
down_revision = '0026_legacy_team_memberships'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bot_tombstones',
        sa.Column('id', sa.String(length=255), primary_key=True),
        sa.Column('deleted_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('bot_tombstones')
