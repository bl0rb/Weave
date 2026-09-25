"""collections.visibility server default: 'restricted' (fail closed)

Revision ID: 0034_visibility_fail_closed
Revises: 0033_user_locale
Create Date: 2026-09-25

0032_collection_visibility needed server_default='public' so its backfill
could keep every pre-existing row's readability, but left it in place. Any
insert that bypasses the ORM default (raw SQL, a data migration, a restore
path) would then silently become public. Switch the database default to
match the model's fail-closed 'restricted'; existing rows are untouched.

sqlite-compatible on purpose (batch_alter_table), same discipline as 0032.
"""

from alembic import op
import sqlalchemy as sa


revision = '0034_visibility_fail_closed'
down_revision = '0033_user_locale'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.alter_column(
            'visibility', existing_type=sa.String(length=10), existing_nullable=False, server_default='restricted'
        )


def downgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.alter_column(
            'visibility', existing_type=sa.String(length=10), existing_nullable=False, server_default='public'
        )
