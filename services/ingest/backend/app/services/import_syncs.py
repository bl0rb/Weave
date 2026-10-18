"""Import configurations (ImportSync) -- shared helpers.

`backfill_import_syncs` turns the pre-0050 model (one auto-sync per
Confluence source, re-running its newest finished run) into one ImportSync
per distinct (source, scope, collection) of the existing runs. It works on
a plain Connection with table definitions only (no ORM models), so the same
code serves migration 0050 and the restore of a backup archive taken before
that migration (app/services/backup.py). It is idempotent: a group that
already has a sync is only linked, never duplicated.
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

# Run-only keys that are recomputed for every run and never belong to a sync.
RUN_ONLY_OPTION_KEYS = frozenset({'collection_id', 'collection_slug', 'collection_name', 'is_refresh', 'sync_template_run_id'})

_runs = sa.table(
    'import_runs',
    sa.column('id', sa.String),
    sa.column('source_id', sa.String),
    sa.column('sync_id', sa.String),
    sa.column('status', sa.String),
    sa.column('scope_type', sa.String),
    sa.column('scope_value', sa.String),
    sa.column('root_page_title', sa.String),
    sa.column('options', sa.JSON),
    sa.column('created_at', sa.DateTime(timezone=True)),
)
_sources = sa.table(
    'import_sources',
    sa.column('id', sa.String),
    sa.column('owner_id', sa.String),
    sa.column('refresh_enabled', sa.Boolean),
    sa.column('refresh_interval_seconds', sa.Integer),
    sa.column('last_refresh_at', sa.DateTime(timezone=True)),
    sa.column('last_refresh_error', sa.Text),
)
_syncs = sa.table(
    'import_syncs',
    sa.column('id', sa.String),
    sa.column('source_id', sa.String),
    sa.column('owner_id', sa.String),
    sa.column('scope_type', sa.String),
    sa.column('scope_value', sa.String),
    sa.column('root_page_title', sa.String),
    sa.column('collection_id', sa.String),
    sa.column('options', sa.JSON),
    sa.column('enabled', sa.Boolean),
    sa.column('interval_seconds', sa.Integer),
    sa.column('last_success_at', sa.DateTime(timezone=True)),
    sa.column('last_error', sa.Text),
    sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('updated_at', sa.DateTime(timezone=True)),
)
_collections = sa.table('collections', sa.column('id', sa.String))


def sync_options(run_options: dict | None) -> dict:
    """A run's options minus the per-run keys -- what a sync stores."""
    options = run_options if isinstance(run_options, dict) else {}
    return {key: value for key, value in options.items() if key not in RUN_ONLY_OPTION_KEYS}


def _options(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def backfill_import_syncs(conn: sa.Connection) -> int:
    """Create the missing syncs for existing runs and link the runs. Returns
    the number of syncs created."""
    collections = {row.id for row in conn.execute(sa.select(_collections.c.id))}
    sources = {row.id: row for row in conn.execute(sa.select(_sources))}
    existing = {
        (row.source_id, row.scope_type, row.scope_value, row.collection_id): row.id
        for row in conn.execute(sa.select(_syncs.c.id, _syncs.c.source_id, _syncs.c.scope_type, _syncs.c.scope_value, _syncs.c.collection_id))
    }

    groups: dict[tuple, list] = {}
    newest_finished: dict[str, tuple] = {}
    for run in conn.execute(
        sa.select(_runs).where(_runs.c.source_id.is_not(None)).order_by(_runs.c.created_at, _runs.c.id)
    ):
        if run.source_id not in sources:
            continue
        collection_id = _options(run.options).get('collection_id') or None
        if collection_id is not None and collection_id not in collections:
            continue  # space deleted: nothing left to sync into
        key = (run.source_id, run.scope_type, run.scope_value, collection_id)
        groups.setdefault(key, []).append(run)
        if str(run.status).lower() == 'finished':
            newest_finished[run.source_id] = key  # ascending order: the last one wins

    now = datetime.now(timezone.utc)
    created = 0
    for key, runs in groups.items():
        source_id, scope_type, scope_value, collection_id = key
        sync_id = existing.get(key)
        if sync_id is None:
            source = sources[source_id]
            newest = runs[-1]
            # The pre-0050 scheduler re-ran the source's newest finished run:
            # that group inherits the source's schedule, every other one is off.
            template = newest_finished.get(source_id) == key
            sync_id = str(uuid.uuid4())
            conn.execute(sa.insert(_syncs).values(
                id=sync_id,
                source_id=source_id,
                owner_id=source.owner_id,
                scope_type=scope_type,
                scope_value=scope_value,
                root_page_title=next((r.root_page_title for r in reversed(runs) if r.root_page_title), ''),
                collection_id=collection_id,
                options=sync_options(_options(newest.options)),
                enabled=bool(template and source.refresh_enabled),
                interval_seconds=source.refresh_interval_seconds if template else None,
                last_success_at=source.last_refresh_at if template else None,
                last_error=source.last_refresh_error if template else None,
                created_at=now,
                updated_at=now,
            ))
            created += 1
        conn.execute(
            sa.update(_runs).where(_runs.c.id.in_([run.id for run in runs]), _runs.c.sync_id.is_(None)).values(sync_id=sync_id)
        )
    return created
