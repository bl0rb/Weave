"""Runtime settings in PostgreSQL instead of Redis.

Revision ID: 0044_runtime_settings
Revises: 0043_stored_objects
"""

from alembic import op
import sqlalchemy as sa

revision = '0044_runtime_settings'
down_revision = '0043_stored_objects'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'runtime_settings',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('runtime_settings')
