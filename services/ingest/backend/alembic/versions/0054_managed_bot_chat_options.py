"""add managed_bots.show_sources and managed_bots.disclaimer

Revision ID: 0054_managed_bot_chat_options
Revises: 0053_managed_bot_config_extras

Weave-Runtime's BotConfig.show_sources/disclaimer: hide the chat's sources
panel, and an optional note next to the composer. Existing rows keep
showing sources and have no disclaimer.
"""

from alembic import op
import sqlalchemy as sa

revision = '0054_managed_bot_chat_options'
down_revision = '0053_managed_bot_config_extras'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('show_sources', sa.Boolean(), server_default='1', nullable=False))
    op.add_column('managed_bots', sa.Column('disclaimer', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('disclaimer')
        batch_op.drop_column('show_sources')
