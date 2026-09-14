"""Repair primary-team memberships missing after upgrades or legacy writes."""

from alembic import op
import sqlalchemy as sa


revision = '0026_legacy_team_memberships'
down_revision = '0025_retrieval_provider_config'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing memberships (especially explicit readers) are authoritative.
    op.execute(sa.text("""
        INSERT INTO user_teams (user_id, team_id, role)
        SELECT users.id, users.team_id, 'member'
        FROM users
        JOIN teams ON teams.id = users.team_id
        WHERE NOT EXISTS (
            SELECT 1 FROM user_teams
            WHERE user_teams.user_id = users.id
              AND user_teams.team_id = users.team_id
        )
    """))


def downgrade() -> None:
    # Memberships are user data; repaired rows cannot safely be distinguished
    # from subsequently managed memberships and must survive a downgrade.
    pass
