"""document freshness: review dates, topic subscriptions, change notifications

Revision ID: 0063_document_freshness
Revises: 0062_quality_review_tasks

- collections.review_interval_days: periodic content review (NULL = none).
- jobs.review_due_at / last_reviewed_at / last_reviewed_by_id: per version,
  set on release and by the portal's review endpoints
  (app/services/document_freshness.py).
- document_releases.subscribers_notified_at: the change_notification_tick's
  progress marker. Every release that exists before this migration is
  backfilled as notified, so enabling the feature never floods the feeds.
- topic_subscriptions / change_notifications: 'Thema abonnieren' and the
  per-user change feed (app/services/change_feed.py).

sqlite-compatible on purpose (batch_alter_table for the jobs FK column,
plain op.add_column / op.create_table otherwise), same discipline as
0042/0062, so tests/test_migrations.py can drive it.
"""

import sqlalchemy as sa
from alembic import op

revision = '0063_document_freshness'
down_revision = '0062_quality_review_tasks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('collections', sa.Column('review_interval_days', sa.Integer(), nullable=True))
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('review_due_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('last_reviewed_by_id', sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            'fk_jobs_last_reviewed_by_id', 'users', ['last_reviewed_by_id'], ['id'], ondelete='SET NULL'
        )
    op.create_index('ix_jobs_review_due_at', 'jobs', ['review_due_at'])
    op.add_column('document_releases', sa.Column('subscribers_notified_at', sa.DateTime(timezone=True), nullable=True))
    op.execute('UPDATE document_releases SET subscribers_notified_at = created_at WHERE subscribers_notified_at IS NULL')

    op.create_table(
        'topic_subscriptions',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('collection_id', sa.String(length=36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=True),
        sa.Column('query', sa.String(length=200), nullable=True),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_topic_subscriptions_user_id', 'topic_subscriptions', ['user_id'])
    op.create_index('ix_topic_subscriptions_collection_id', 'topic_subscriptions', ['collection_id'])
    op.create_table(
        'change_notifications',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'subscription_id', sa.String(length=36),
            sa.ForeignKey('topic_subscriptions.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('previous_job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('collection_id', sa.String(length=36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=False),
        sa.Column('change_type', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('matched_terms', sa.JSON(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('subscription_id', 'job_id', name='uq_change_notifications_subscription_job'),
    )
    op.create_index('ix_change_notifications_user_read', 'change_notifications', ['user_id', 'read_at'])
    op.create_index('ix_change_notifications_subscription_id', 'change_notifications', ['subscription_id'])
    op.create_index('ix_change_notifications_job_id', 'change_notifications', ['job_id'])
    op.create_index('ix_change_notifications_collection_id', 'change_notifications', ['collection_id'])


def downgrade() -> None:
    op.drop_index('ix_change_notifications_collection_id', table_name='change_notifications')
    op.drop_index('ix_change_notifications_job_id', table_name='change_notifications')
    op.drop_index('ix_change_notifications_subscription_id', table_name='change_notifications')
    op.drop_index('ix_change_notifications_user_read', table_name='change_notifications')
    op.drop_table('change_notifications')
    op.drop_index('ix_topic_subscriptions_collection_id', table_name='topic_subscriptions')
    op.drop_index('ix_topic_subscriptions_user_id', table_name='topic_subscriptions')
    op.drop_table('topic_subscriptions')
    with op.batch_alter_table('document_releases') as batch:
        batch.drop_column('subscribers_notified_at')
    op.drop_index('ix_jobs_review_due_at', table_name='jobs')
    with op.batch_alter_table('jobs') as batch:
        batch.drop_constraint('fk_jobs_last_reviewed_by_id', type_='foreignkey')
        batch.drop_column('last_reviewed_by_id')
        batch.drop_column('last_reviewed_at')
        batch.drop_column('review_due_at')
    with op.batch_alter_table('collections') as batch:
        batch.drop_column('review_interval_days')
