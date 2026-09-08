"""add service grouping to worker logs"""

from alembic import op
import sqlalchemy as sa


revision = '0022_worker_log_service'
down_revision = '0021_knowledge_withdrawals'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('worker_log_entries', sa.Column('service', sa.String(64), nullable=True))
    op.execute("UPDATE worker_log_entries SET service = 'ingest-worker' WHERE service IS NULL")
    with op.batch_alter_table('worker_log_entries') as batch:
        batch.alter_column('service', nullable=False, server_default='ingest-worker')
        batch.create_index('ix_worker_log_entries_service', ['service'])


def downgrade() -> None:
    with op.batch_alter_table('worker_log_entries') as batch:
        batch.drop_index('ix_worker_log_entries_service')
        batch.drop_column('service')