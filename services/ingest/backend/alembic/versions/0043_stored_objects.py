"""Binary objects in PostgreSQL chunks instead of blob columns and files.

Revision ID: 0043_stored_objects
Revises: 0042_job_claims

Originals and imported artifacts move into stored_objects /
stored_object_chunks (app/services/object_store.py); jobs.result_path goes
away with the on-disk result files. This revision targets new deployments:
it does not carry existing blob bytes over, and its downgrade restores the
columns empty.
"""

from alembic import op
import sqlalchemy as sa

revision = '0043_stored_objects'
down_revision = '0042_job_claims'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'stored_objects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('content_type', sa.String(128), nullable=True),
        sa.Column('backend', sa.String(8), nullable=False, server_default='db'),
        sa.Column('storage_key', sa.String(1024), nullable=True),
        sa.Column('chunk_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_stored_objects_sha256', 'stored_objects', ['sha256'])
    op.create_table(
        'stored_object_chunks',
        sa.Column(
            'object_id', sa.String(36),
            sa.ForeignKey('stored_objects.id', ondelete='CASCADE'), primary_key=True,
        ),
        sa.Column('seq', sa.Integer(), primary_key=True),
        sa.Column('data', sa.LargeBinary(), nullable=False),
    )
    if op.get_bind().dialect.name == 'postgresql':
        # PDFs and images are already compressed: skip TOAST compression.
        op.execute('ALTER TABLE stored_object_chunks ALTER COLUMN data SET STORAGE EXTERNAL')

    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('upload_object_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_jobs_upload_object_id', 'stored_objects', ['upload_object_id'], ['id'])
        batch.create_index('ix_jobs_upload_object_id', ['upload_object_id'])
        batch.drop_column('upload_content')
        batch.drop_column('result_path')

    with op.batch_alter_table('job_artifacts') as batch:
        batch.add_column(sa.Column('object_id', sa.String(36), nullable=False))
        batch.create_foreign_key('fk_job_artifacts_object_id', 'stored_objects', ['object_id'], ['id'])
        batch.create_index('ix_job_artifacts_object_id', ['object_id'])
        batch.drop_column('content')


def downgrade() -> None:
    with op.batch_alter_table('job_artifacts') as batch:
        batch.add_column(sa.Column('content', sa.LargeBinary(), nullable=True))
        batch.drop_index('ix_job_artifacts_object_id')
        batch.drop_constraint('fk_job_artifacts_object_id', type_='foreignkey')
        batch.drop_column('object_id')

    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('result_path', sa.String(1024), nullable=True))
        batch.add_column(sa.Column('upload_content', sa.LargeBinary(), nullable=True))
        batch.drop_index('ix_jobs_upload_object_id')
        batch.drop_constraint('fk_jobs_upload_object_id', type_='foreignkey')
        batch.drop_column('upload_object_id')

    op.drop_table('stored_object_chunks')
    op.drop_index('ix_stored_objects_sha256', table_name='stored_objects')
    op.drop_table('stored_objects')
