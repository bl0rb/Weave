from alembic import op
import sqlalchemy as sa


revision = '0006_user_locale'
down_revision = '0005_user_teams'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('locale', sa.String(length=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('locale')
