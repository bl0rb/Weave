"""add backup_runs (disaster-recovery export/import engine)

Revision ID: 0031_backup_runs
Revises: 0030_chat_provider_supports_
Create Date: 2026-09-21

sqlite-compatible on purpose (plain op.create_table, no postgres-only DDL),
same discipline as 0013_webhooks -- see that migration's own docstring for
why (tests/test_migrations.py drives every migration through real alembic
against sqlite).

Revision id kept <= 32 chars on purpose -- see 0012's docstring for the
production CrashLoopBackOff a longer id caused once, since PostgreSQL's
alembic_version.version_num column is VARCHAR(32) where sqlite (the test
database) silently isn't.
"""

from alembic import op
import sqlalchemy as sa


revision = '0031_backup_runs'
down_revision = '0030_chat_provider_supports_'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'backup_runs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='queued'),
        sa.Column('file_name', sa.String(length=255), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('progress', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('report', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.create_index('ix_backup_runs_created_by', 'backup_runs', ['created_by'], if_not_exists=True)
    op.create_index('ix_backup_runs_status', 'backup_runs', ['status'], if_not_exists=True)
    op.create_index('ix_backup_runs_finished_at', 'backup_runs', ['finished_at'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_backup_runs_finished_at', table_name='backup_runs')
    op.drop_index('ix_backup_runs_status', table_name='backup_runs')
    op.drop_index('ix_backup_runs_created_by', table_name='backup_runs')
    op.drop_table('backup_runs')
