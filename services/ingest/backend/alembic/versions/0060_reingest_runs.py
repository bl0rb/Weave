"""add reingest_runs / reingest_items (admin re-ingest of documents and connections)

Revision ID: 0060_reingest_runs
Revises: 0059_retrieval_search_defaults

sqlite-compatible on purpose (plain op.create_table, no postgres-only DDL),
same discipline as 0031_backup_runs; revision id kept <= 32 chars (see
0012's docstring).
"""

import sqlalchemy as sa
from alembic import op

revision = '0060_reingest_runs'
down_revision = '0059_retrieval_search_defaults'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'reingest_runs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='running'),
        sa.Column('options', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('lease_token', sa.String(length=36), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.create_index('ix_reingest_runs_status', 'reingest_runs', ['status'], if_not_exists=True)
    op.create_index('ix_reingest_runs_created_by', 'reingest_runs', ['created_by'], if_not_exists=True)
    op.create_table(
        'reingest_items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('run_id', sa.String(length=36), sa.ForeignKey('reingest_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('result', sa.String(length=32), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('new_job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('sync_id', sa.String(length=36), sa.ForeignKey('import_syncs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('import_run_id', sa.String(length=36), sa.ForeignKey('import_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('carry', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        if_not_exists=True,
    )
    op.create_index('ix_reingest_items_run_id', 'reingest_items', ['run_id'], if_not_exists=True)
    op.create_index('ix_reingest_items_status', 'reingest_items', ['status'], if_not_exists=True)
    op.create_index('ix_reingest_items_job_id', 'reingest_items', ['job_id'], if_not_exists=True)
    op.create_index('ix_reingest_items_new_job_id', 'reingest_items', ['new_job_id'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_reingest_items_new_job_id', table_name='reingest_items')
    op.drop_index('ix_reingest_items_job_id', table_name='reingest_items')
    op.drop_index('ix_reingest_items_status', table_name='reingest_items')
    op.drop_index('ix_reingest_items_run_id', table_name='reingest_items')
    op.drop_table('reingest_items')
    op.drop_index('ix_reingest_runs_created_by', table_name='reingest_runs')
    op.drop_index('ix_reingest_runs_status', table_name='reingest_runs')
    op.drop_table('reingest_runs')
