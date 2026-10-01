"""keep the Confluence refresh state per knowledge space

`import_page_states` held one row per (source, page). Importing the same
page into two spaces made the row point at whichever space imported last,
so a sync of the other space chained its new version onto a document of a
foreign space -- releasing it withdrew that document there (ADR 0008). The
state is now kept per (source, space, page).

Every existing row moves to the space of the job it points at; a row whose
space was deleted goes with it, a row without a job stays without a space.
Each space that lost its row to another space gets one back from its newest
still-imported page job, exactly what the first refresh's seeding would
pick, so the next sync neither duplicates nor re-chains those pages.

sqlite-compatible on purpose (batch_alter_table, row-wise Python, no JSON
SQL functions).
"""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '0040_space_scoped_page_states'
down_revision = '0039_space_scoped_versions'
branch_labels = None
depends_on = None


_states = sa.table(
    'import_page_states',
    sa.column('id', sa.String),
    sa.column('source_id', sa.String),
    sa.column('collection_id', sa.String),
    sa.column('page_id', sa.String),
    sa.column('page_version', sa.Integer),
    sa.column('job_id', sa.String),
    sa.column('title', sa.String),
    sa.column('url', sa.String),
    sa.column('updated_at', sa.DateTime(timezone=True)),
)
_jobs = sa.table(
    'jobs',
    sa.column('id', sa.String),
    sa.column('import_run_id', sa.String),
    sa.column('original_filename', sa.String),
    sa.column('processing_info', sa.JSON),
    sa.column('created_at', sa.DateTime(timezone=True)),
)
_runs = sa.table('import_runs', sa.column('id', sa.String), sa.column('source_id', sa.String))
_collections = sa.table('collections', sa.column('id', sa.String))
_withdrawals = sa.table('knowledge_withdrawals', sa.column('job_id', sa.String))


def _settings(processing_info) -> dict:
    info = processing_info if isinstance(processing_info, dict) else {}
    return info.get('settings') if isinstance(info.get('settings'), dict) else {}


def _collection_id(processing_info) -> str | None:
    value = _settings(processing_info).get('collection_id')
    return value if isinstance(value, str) and value else None


def upgrade() -> None:
    with op.batch_alter_table('import_page_states') as batch_op:
        batch_op.add_column(sa.Column('collection_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_import_page_states_collection_id', 'collections', ['collection_id'], ['id'], ondelete='CASCADE'
        )
        batch_op.drop_constraint('uq_import_page_states_source_id_page_id', type_='unique')

    bind = op.get_bind()
    collection_ids = set(bind.execute(sa.select(_collections.c.id)).scalars().all())

    for state_id, job_id in bind.execute(
        sa.select(_states.c.id, _states.c.job_id).where(_states.c.job_id.is_not(None))
    ).all():
        processing_info = bind.execute(
            sa.select(_jobs.c.processing_info).where(_jobs.c.id == job_id)
        ).scalar_one_or_none()
        collection_id = _collection_id(processing_info)
        if collection_id is None:
            continue
        if collection_id in collection_ids:
            bind.execute(sa.update(_states).where(_states.c.id == state_id).values(collection_id=collection_id))
        else:
            bind.execute(sa.delete(_states).where(_states.c.id == state_id))

    source_by_run = {
        run_id: source_id
        for run_id, source_id in bind.execute(
            sa.select(_runs.c.id, _runs.c.source_id).where(_runs.c.source_id.is_not(None))
        ).all()
    }
    withdrawn = set(bind.execute(sa.select(_withdrawals.c.job_id)).scalars().all())
    latest: dict[tuple[str, str | None, str], tuple] = {}
    for job in bind.execute(
        sa.select(_jobs.c.id, _jobs.c.import_run_id, _jobs.c.original_filename, _jobs.c.processing_info)
        .where(_jobs.c.import_run_id.is_not(None))
        .order_by(_jobs.c.created_at.asc())
    ).all():
        source_id = source_by_run.get(job.import_run_id)
        settings = _settings(job.processing_info)
        import_info = settings.get('import') if isinstance(settings.get('import'), dict) else {}
        page_id = import_info.get('source_page_id')
        collection_id = _collection_id(job.processing_info)
        if (
            source_id is None or job.id in withdrawn or settings.get('mode') != 'import'
            or not isinstance(page_id, str) or not page_id
            or not isinstance(import_info.get('source_page_version'), int)
            or (collection_id is not None and collection_id not in collection_ids)
        ):
            continue
        latest[(source_id, collection_id, page_id)] = (job, import_info)  # ascending created_at: last wins

    tracked = {
        (source_id, collection_id, page_id)
        for source_id, collection_id, page_id in bind.execute(
            sa.select(_states.c.source_id, _states.c.collection_id, _states.c.page_id)
        ).all()
    }
    now = datetime.now(timezone.utc)
    for (source_id, collection_id, page_id), (job, import_info) in latest.items():
        if (source_id, collection_id, page_id) in tracked:
            continue
        bind.execute(sa.insert(_states).values(
            id=str(uuid.uuid4()),
            source_id=source_id,
            collection_id=collection_id,
            page_id=page_id,
            page_version=import_info['source_page_version'],
            job_id=job.id,
            title=job.original_filename,
            url=str(import_info.get('source_url') or '')[:2048],
            updated_at=now,
        ))

    with op.batch_alter_table('import_page_states') as batch_op:
        batch_op.create_unique_constraint(
            'uq_import_page_states_source_collection_page', ['source_id', 'collection_id', 'page_id']
        )
    op.create_index(
        'uq_import_page_states_unassigned_page', 'import_page_states', ['source_id', 'page_id'], unique=True,
        sqlite_where=sa.text('collection_id IS NULL'), postgresql_where=sa.text('collection_id IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_import_page_states_unassigned_page', table_name='import_page_states')
    # One row per (source, page) again: the most recently updated one wins.
    bind = op.get_bind()
    kept: set[tuple[str, str]] = set()
    for state_id, source_id, page_id in bind.execute(
        sa.select(_states.c.id, _states.c.source_id, _states.c.page_id).order_by(_states.c.updated_at.desc())
    ).all():
        if (source_id, page_id) in kept:
            bind.execute(sa.delete(_states).where(_states.c.id == state_id))
        else:
            kept.add((source_id, page_id))
    with op.batch_alter_table('import_page_states') as batch_op:
        batch_op.drop_constraint('uq_import_page_states_source_collection_page', type_='unique')
        batch_op.drop_constraint('fk_import_page_states_collection_id', type_='foreignkey')
        batch_op.drop_column('collection_id')
        batch_op.create_unique_constraint('uq_import_page_states_source_id_page_id', ['source_id', 'page_id'])
