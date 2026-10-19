"""Generated markdown may live in stored objects (S3 backend).

Revision ID: 0052_markdown_objects
Revises: 0051_knowledge_memory_config

jobs.result_markdown, job_markdown_versions.content and
document_releases.markdown_snapshot each get an optional reference to a
StoredObject; with OBJECT_STORAGE_BACKEND=s3 the text goes there and the
column stays NULL (app/models/models.py: _offloaded_text), so the two NOT
NULL text columns become nullable. Existing rows are untouched.

Downgrade refuses while any markdown is offloaded: move it back first with
`python -m app.cli storage migrate --to db`.
"""

from alembic import op
import sqlalchemy as sa

revision = '0052_markdown_objects'
down_revision = '0051_knowledge_memory_config'
branch_labels = None
depends_on = None

_REFERENCES = (
    # table, object id column, text column, text column was NOT NULL
    ('jobs', 'result_markdown_object_id', 'result_markdown', False),
    ('job_markdown_versions', 'content_object_id', 'content', True),
    ('document_releases', 'markdown_object_id', 'markdown_snapshot', True),
)


def upgrade() -> None:
    for table, object_column, text_column, was_not_null in _REFERENCES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(object_column, sa.String(36), nullable=True))
            batch.create_foreign_key(f'fk_{table}_{object_column}', 'stored_objects', [object_column], ['id'])
            batch.create_index(f'ix_{table}_{object_column}', [object_column])
            if was_not_null:
                batch.alter_column(text_column, existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table, object_column, _text_column, _was_not_null in _REFERENCES:
        offloaded = bind.execute(
            sa.text(f'SELECT COUNT(*) FROM {table} WHERE {object_column} IS NOT NULL')
        ).scalar()
        if offloaded:
            raise RuntimeError(
                f'{offloaded} rows in {table} keep their markdown in object storage; '
                'run `python -m app.cli storage migrate --to db` before downgrading.'
            )
    for table, object_column, text_column, was_not_null in reversed(_REFERENCES):
        with op.batch_alter_table(table) as batch:
            if was_not_null:
                batch.alter_column(text_column, existing_type=sa.Text(), nullable=False)
            batch.drop_index(f'ix_{table}_{object_column}')
            batch.drop_constraint(f'fk_{table}_{object_column}', type_='foreignkey')
            batch.drop_column(object_column)
