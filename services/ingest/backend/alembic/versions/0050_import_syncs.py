"""Confluence import configurations: several synced scopes per connection

Revision ID: 0050_import_syncs
Revises: 0049_oidc_access_group

Until now a Confluence source had one auto-sync (`import_sources.refresh_*`)
that re-ran the source's newest finished run, whatever its scope. Users sync
several spaces per connection, so a sync is now its own row: scope + target
knowledge space + options + schedule, and runs point at it (`sync_id`).

Backfill: one sync per distinct (source, scope, space) of the existing runs,
options from the group's newest run. The group the old scheduler would have
re-run (the source's newest finished run) inherits the source's schedule;
every other one starts disabled. The `refresh_*` columns stay for downgrade.

The backfill is a frozen copy of app/services/import_syncs.py at this
revision (migrations do not import app code). sqlite-compatible on purpose
(batch_alter_table, row-wise Python, no JSON SQL functions).
"""

import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '0050_import_syncs'
down_revision = '0049_oidc_access_group'
branch_labels = None
depends_on = None

_RUN_ONLY_OPTION_KEYS = frozenset({'collection_id', 'collection_slug', 'collection_name', 'is_refresh', 'sync_template_run_id'})

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


def _options(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _backfill(conn) -> None:
    collections = {row.id for row in conn.execute(sa.select(_collections.c.id))}
    sources = {row.id: row for row in conn.execute(sa.select(_sources))}
    groups: dict[tuple, list] = {}
    newest_finished: dict[str, tuple] = {}
    for run in conn.execute(
        sa.select(_runs).where(_runs.c.source_id.is_not(None)).order_by(_runs.c.created_at, _runs.c.id)
    ):
        if run.source_id not in sources:
            continue
        collection_id = _options(run.options).get('collection_id') or None
        if collection_id is not None and collection_id not in collections:
            continue
        key = (run.source_id, run.scope_type, run.scope_value, collection_id)
        groups.setdefault(key, []).append(run)
        if str(run.status).lower() == 'finished':
            newest_finished[run.source_id] = key

    now = datetime.now(timezone.utc)
    for key, runs in groups.items():
        source_id, scope_type, scope_value, collection_id = key
        source = sources[source_id]
        template = newest_finished.get(source_id) == key
        sync_id = str(uuid.uuid4())
        options = _options(runs[-1].options)
        conn.execute(sa.insert(_syncs).values(
            id=sync_id,
            source_id=source_id,
            owner_id=source.owner_id,
            scope_type=scope_type,
            scope_value=scope_value,
            root_page_title=next((r.root_page_title for r in reversed(runs) if r.root_page_title), ''),
            collection_id=collection_id,
            options={k: v for k, v in options.items() if k not in _RUN_ONLY_OPTION_KEYS},
            enabled=bool(template and source.refresh_enabled),
            interval_seconds=source.refresh_interval_seconds if template else None,
            last_success_at=source.last_refresh_at if template else None,
            last_error=source.last_refresh_error if template else None,
            created_at=now,
            updated_at=now,
        ))
        conn.execute(sa.update(_runs).where(_runs.c.id.in_([run.id for run in runs])).values(sync_id=sync_id))


def upgrade() -> None:
    op.create_table(
        'import_syncs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_id', sa.String(36), sa.ForeignKey('import_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('scope_type', sa.String(16), nullable=False),
        sa.Column('scope_value', sa.String(512), nullable=False),
        sa.Column('root_page_title', sa.String(512), nullable=False, server_default=''),
        sa.Column('collection_id', sa.String(36), sa.ForeignKey('collections.id', ondelete='CASCADE'), nullable=True),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('interval_seconds', sa.Integer(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            'source_id', 'scope_type', 'scope_value', 'collection_id', name='uq_import_syncs_source_scope_collection'
        ),
    )
    op.create_index('ix_import_syncs_source_id', 'import_syncs', ['source_id'])
    op.create_index('ix_import_syncs_owner_id', 'import_syncs', ['owner_id'])
    op.create_index('ix_import_syncs_collection_id', 'import_syncs', ['collection_id'])
    op.create_index(
        'uq_import_syncs_unassigned_scope', 'import_syncs', ['source_id', 'scope_type', 'scope_value'], unique=True,
        sqlite_where=sa.text('collection_id IS NULL'), postgresql_where=sa.text('collection_id IS NULL'),
    )
    with op.batch_alter_table('import_runs') as batch:
        batch.add_column(sa.Column('sync_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_import_runs_sync_id', 'import_syncs', ['sync_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_import_runs_sync_id', ['sync_id'])
    _backfill(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table('import_runs') as batch:
        batch.drop_index('ix_import_runs_sync_id')
        batch.drop_constraint('fk_import_runs_sync_id', type_='foreignkey')
        batch.drop_column('sync_id')
    op.drop_index('uq_import_syncs_unassigned_scope', table_name='import_syncs')
    op.drop_index('ix_import_syncs_collection_id', table_name='import_syncs')
    op.drop_index('ix_import_syncs_owner_id', table_name='import_syncs')
    op.drop_index('ix_import_syncs_source_id', table_name='import_syncs')
    op.drop_table('import_syncs')
