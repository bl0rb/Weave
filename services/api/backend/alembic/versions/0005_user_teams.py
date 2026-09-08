from alembic import op
import sqlalchemy as sa


revision = '0005_user_teams'
down_revision = '0004_session_exchange_codes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('teams', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('teams')