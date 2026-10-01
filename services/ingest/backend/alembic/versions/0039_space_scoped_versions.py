"""cut version chains that cross knowledge spaces

Before ADR 0008 an upload chained onto the uploader's latest same-named
document wherever it lived, so a version could point at a predecessor in
another space (or outside any space). Such a link made the foreign original
look outdated (the portal refuses to edit it) and carried a foreign job id
into the release frontmatter. New uploads only chain inside their own space;
this revision cuts the existing cross-space links. `document_version` is left
as it is: later versions in the same space already count on from it.

sqlite-compatible on purpose (row-wise Python, no JSON SQL functions).
"""

from alembic import op
import sqlalchemy as sa


revision = '0039_space_scoped_versions'
down_revision = '0038_collection_slug_tombstones'
branch_labels = None
depends_on = None


_jobs = sa.table(
    'jobs',
    sa.column('id', sa.String),
    sa.column('previous_job_id', sa.String),
    sa.column('processing_info', sa.JSON),
)


def _collection_id(processing_info) -> str | None:
    info = processing_info if isinstance(processing_info, dict) else {}
    settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    value = settings.get('collection_id')
    return value if isinstance(value, str) and value else None


def upgrade() -> None:
    bind = op.get_bind()
    chained = bind.execute(
        sa.select(_jobs.c.id, _jobs.c.previous_job_id, _jobs.c.processing_info)
        .where(_jobs.c.previous_job_id.is_not(None))
    ).all()
    for job_id, previous_job_id, processing_info in chained:
        previous_info = bind.execute(
            sa.select(_jobs.c.processing_info).where(_jobs.c.id == previous_job_id)
        ).scalar_one_or_none()
        if _collection_id(previous_info) != _collection_id(processing_info):
            bind.execute(sa.update(_jobs).where(_jobs.c.id == job_id).values(previous_job_id=None))


def downgrade() -> None:
    # A cross-space chain is the defect this revision repairs; it is not
    # restored.
    pass
