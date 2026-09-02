"""add immutable portal document releases and their delivery outbox"""

from alembic import op
import sqlalchemy as sa


revision = '0017_document_releases'
down_revision = '0016_login_handoff'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'document_releases',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column(
            'job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='RESTRICT'), nullable=False,
        ),
        sa.Column('owner_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('markdown_snapshot', sa.Text(), nullable=False),
        sa.Column('markdown_sha256', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_token', sa.String(length=36), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_document_releases_job_id', 'document_releases', ['job_id'], unique=True)
    op.create_index('ix_document_releases_owner_id', 'document_releases', ['owner_id'])
    op.create_index('ix_document_releases_next_attempt_at', 'document_releases', ['next_attempt_at'])
    op.create_index('ix_document_releases_lease_token', 'document_releases', ['lease_token'])
    op.create_index('ix_document_releases_lease_until', 'document_releases', ['lease_until'])


def downgrade() -> None:
    op.drop_index('ix_document_releases_lease_until', table_name='document_releases')
    op.drop_index('ix_document_releases_lease_token', table_name='document_releases')
    op.drop_index('ix_document_releases_next_attempt_at', table_name='document_releases')
    op.drop_index('ix_document_releases_owner_id', table_name='document_releases')
    op.drop_index('ix_document_releases_job_id', table_name='document_releases')
    op.drop_table('document_releases')
