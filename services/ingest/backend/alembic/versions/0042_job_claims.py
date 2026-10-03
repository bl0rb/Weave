"""Job claim token, heartbeat and recovery counter for multi-worker processing.

Revision ID: 0042_job_claims
Revises: 0041_oidc_object_identity

sqlite-compatible on purpose (batch_alter_table), same as the other jobs
column migrations, so tests/test_migrations.py can drive it.
"""

from alembic import op
import sqlalchemy as sa

revision = '0042_job_claims'
down_revision = '0041_oidc_object_identity'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('claim_token', sa.String(36), nullable=True))
        batch.add_column(sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('recovery_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch:
        batch.drop_column('recovery_count')
        batch.drop_column('heartbeat_at')
        batch.drop_column('claim_token')
