from alembic import op
import sqlalchemy as sa


revision = '0021_knowledge_withdrawals'
down_revision = '0020_user_teams'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_withdrawals',
        sa.Column('job_id', sa.String(36), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('knowledge_withdrawals')