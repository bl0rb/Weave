"""add reader/member roles to team memberships"""

from alembic import op
import sqlalchemy as sa

revision = '0024_user_team_roles'
down_revision = '0023_managed_bot_kinds'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_teams', sa.Column('role', sa.String(16), nullable=False, server_default='member'))


def downgrade() -> None:
    op.drop_column('user_teams', 'role')
