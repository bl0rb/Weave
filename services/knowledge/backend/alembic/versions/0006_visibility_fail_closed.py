"""collections.visibility server default: 'restricted' (fail closed)

Revision ID: 0006_visibility_fail_closed
Revises: 0005_collection_visibility
Create Date: 2026-09-25

0005 needed server_default='public' for its backfill but left it in place,
so any insert that bypasses the ORM default would mirror a collection as
public. Match the model's fail-closed 'restricted'; existing rows are
untouched.
"""

from alembic import op
import sqlalchemy as sa


revision = '0006_visibility_fail_closed'
down_revision = '0005_collection_visibility'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.alter_column(
            'visibility', existing_type=sa.String(length=20), existing_nullable=False, server_default='restricted'
        )


def downgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.alter_column(
            'visibility', existing_type=sa.String(length=20), existing_nullable=False, server_default='public'
        )
