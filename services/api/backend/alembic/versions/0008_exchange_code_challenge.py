from alembic import op
import sqlalchemy as sa


revision = '0008_exchange_code_challenge'
down_revision = '0007_rate_limit_windows'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('session_exchange_codes', sa.Column('code_challenge', sa.String(length=43), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('session_exchange_codes') as batch:
        batch.drop_column('code_challenge')
