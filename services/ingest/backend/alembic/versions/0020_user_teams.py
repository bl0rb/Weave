from alembic import op
import sqlalchemy as sa


revision = '0020_user_teams'
down_revision = '0019_managed_bots'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_teams',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('team_id', sa.String(36), sa.ForeignKey('teams.id', ondelete='CASCADE'), primary_key=True),
    )
    op.execute('INSERT INTO user_teams (user_id, team_id) SELECT id, team_id FROM users WHERE team_id IS NOT NULL')


def downgrade() -> None:
    op.drop_table('user_teams')