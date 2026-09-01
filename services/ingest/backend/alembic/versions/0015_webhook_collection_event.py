"""add webhook_deliveries.collection_id for the 'collection.updated' event

Revision ID: 0015_webhook_collection_event
Revises: 0014_collection_registry
Create Date: 2026-08-31

sqlite-compatible on purpose (batch_alter_table, no postgres-only DDL) so
tests/test_migrations.py can drive it through real alembic against sqlite,
same as every migration since 0004_auth.

Part of the 'collection.updated' event (see contracts/events/collection.updated.md
+ app/workers/webhook_tasks.dispatch_collection_event): a WebhookDelivery row
for this event has no job/import run to point at, only the collection whose
registry entry changed -- SET NULL (not CASCADE), same reasoning as
job_id/import_run_id already on this table: a delivery is an audit/log entry
of an attempted HTTP call and must outlive the collection it was about.
"""

from alembic import op
import sqlalchemy as sa


revision = '0015_webhook_collection_event'
down_revision = '0014_collection_registry'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Same two-step batch_alter_table pattern 0004_auth/0005_import/
    # 0007_versioning_tokens/0008_vl_benchmarks/0009_mail_ingestion use for
    # an added FK column: plain add_column first, then a named
    # create_foreign_key -- sqlite's batch (table-rebuild) mode requires the
    # constraint to have an explicit name, which a bare
    # sa.Column(..., sa.ForeignKey(...)) does not carry.
    with op.batch_alter_table('webhook_deliveries') as batch_op:
        batch_op.add_column(sa.Column('collection_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_webhook_deliveries_collection_id', 'collections', ['collection_id'], ['id'], ondelete='SET NULL'
        )
    op.create_index(
        'ix_webhook_deliveries_collection_id', 'webhook_deliveries', ['collection_id'], if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index('ix_webhook_deliveries_collection_id', table_name='webhook_deliveries')
    with op.batch_alter_table('webhook_deliveries') as batch_op:
        batch_op.drop_constraint('fk_webhook_deliveries_collection_id', type_='foreignkey')
        batch_op.drop_column('collection_id')
