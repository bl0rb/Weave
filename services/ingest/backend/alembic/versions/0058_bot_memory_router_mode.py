"""add managed_bots.memory and managed_bots.router_mode

Revision ID: 0058_bot_memory_router_mode
Revises: 0057_bot_collection_notices

Weave-Runtime's RetrievalConfig.memory (use the knowledge memory while it
is on centrally) and BotConfig.router_mode (None = runtime-wide
ROUTER_MODE), per bot. Existing rows keep today's behaviour.
"""

from alembic import op
import sqlalchemy as sa

revision = '0058_bot_memory_router_mode'
down_revision = '0057_bot_collection_notices'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('memory', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('managed_bots', sa.Column('router_mode', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('router_mode')
        batch_op.drop_column('memory')
