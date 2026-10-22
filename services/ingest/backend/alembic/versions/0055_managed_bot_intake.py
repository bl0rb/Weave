"""add managed_bots.intake_config, blocked_topics and blocked_topic_reply

Revision ID: 0055_managed_bot_intake
Revises: 0054_managed_bot_chat_options

Weave-Runtime's BotConfig.intake (a mandatory question at conversation
start) and guard.blocked_topics/blocked_topic_reply (a technical topic
block). Existing rows have neither.
"""

from alembic import op
import sqlalchemy as sa

revision = '0055_managed_bot_intake'
down_revision = '0054_managed_bot_chat_options'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('intake_config', sa.JSON(), nullable=True))
    op.add_column('managed_bots', sa.Column('blocked_topics', sa.JSON(), nullable=True))
    op.add_column('managed_bots', sa.Column('blocked_topic_reply', sa.String(length=2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('blocked_topic_reply')
        batch_op.drop_column('blocked_topics')
        batch_op.drop_column('intake_config')
