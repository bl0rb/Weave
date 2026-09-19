"""add managed_bots.agent_config for agent-mode subagents"""

from alembic import op
import sqlalchemy as sa

revision = '0028_managed_bot_agent_config'
down_revision = '0027_bot_tombstones'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('agent_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('managed_bots', 'agent_config')
