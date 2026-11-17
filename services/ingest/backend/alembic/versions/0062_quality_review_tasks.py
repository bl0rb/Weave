"""add review_tasks / quality_signals (Prüfaufgaben: Wissenslücken, Antwort-Feedback)

Revision ID: 0062_quality_review_tasks
Revises: 0061_branding

review_tasks first: quality_signals.task_id references it. The "one active
task per (collection_id, kind, cluster_key)" rule is enforced in code
(app/services/quality_tasks.py), not by a unique index, because closed
tasks with the same key must coexist. sqlite-compatible on purpose (plain
op.create_table, no postgres-only DDL), same discipline as 0060/0061.
"""

import sqlalchemy as sa
from alembic import op

revision = '0062_quality_review_tasks'
down_revision = '0061_branding'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'review_tasks',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('collection_id', sa.String(length=36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='open'),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('cluster_key', sa.String(length=255), nullable=False),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('related_job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('assignee_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('resolved_by_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_review_tasks_collection_status', 'review_tasks', ['collection_id', 'status'])
    op.create_index('ix_review_tasks_kind', 'review_tasks', ['kind'])
    op.create_index('ix_review_tasks_job_id', 'review_tasks', ['job_id'])
    op.create_index('ix_review_tasks_related_job_id', 'review_tasks', ['related_job_id'])
    op.create_index('ix_review_tasks_assignee_id', 'review_tasks', ['assignee_id'])
    op.create_table(
        'quality_signals',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('signal_key', sa.String(length=128), nullable=False, unique=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('rating', sa.String(length=16), nullable=True),
        sa.Column('collection_id', sa.String(length=36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=True),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('review_tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('retracted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_quality_signals_kind', 'quality_signals', ['kind'])
    op.create_index('ix_quality_signals_collection_id', 'quality_signals', ['collection_id'])
    op.create_index('ix_quality_signals_job_id', 'quality_signals', ['job_id'])
    op.create_index('ix_quality_signals_task_id', 'quality_signals', ['task_id'])


def downgrade() -> None:
    op.drop_index('ix_quality_signals_task_id', table_name='quality_signals')
    op.drop_index('ix_quality_signals_job_id', table_name='quality_signals')
    op.drop_index('ix_quality_signals_collection_id', table_name='quality_signals')
    op.drop_index('ix_quality_signals_kind', table_name='quality_signals')
    op.drop_table('quality_signals')
    op.drop_index('ix_review_tasks_assignee_id', table_name='review_tasks')
    op.drop_index('ix_review_tasks_related_job_id', table_name='review_tasks')
    op.drop_index('ix_review_tasks_job_id', table_name='review_tasks')
    op.drop_index('ix_review_tasks_kind', table_name='review_tasks')
    op.drop_index('ix_review_tasks_collection_status', table_name='review_tasks')
    op.drop_table('review_tasks')
