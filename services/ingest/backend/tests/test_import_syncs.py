"""Import configurations (ImportSync): several synced scopes per Confluence
connection -- API (create via POST /runs, list, edit, run now, delete),
owner privacy, and the scheduler running every due sync of one connection
one after another."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api import import_routes
from app.core.config import settings
from app.models.models import ImportRun, ImportRunStatus, ImportSync, UserRole
from app.services.import_syncs import backfill_import_syncs
from app.workers import refresh_tasks
from conftest import TestingSessionLocal, add_legacy_collection, login_as
from tests.test_import_api import _db, _make_source, _user


@pytest.fixture()
def sent(monkeypatch):
    captured: list[tuple] = []
    record = lambda name, args=None, **kw: captured.append((name, list(args or [])))  # noqa: E731
    monkeypatch.setattr(import_routes.celery_app, 'send_task', record)
    monkeypatch.setattr(refresh_tasks.celery_app, 'send_task', record)
    monkeypatch.setattr(refresh_tasks, 'SessionLocal', TestingSessionLocal)
    return captured


def _collection(owner_id: str, slug: str):
    with _db() as db:
        collection = add_legacy_collection(db, owner_id=owner_id, slug=slug, name=slug)
        db.commit()
        return collection.id


def _create_run(client, source_id, scope_value, collection_id, *, sync=None, scope_type='space'):
    body = {'source_id': source_id, 'scope': {'type': scope_type, 'value': scope_value},
            'options': {'collection_id': collection_id, 'max_pages': 20}}
    if sync is not None:
        body['sync'] = sync
    return client.post('/api/v1/import/runs', json=body)


def _finish(run_id: str) -> None:
    with _db() as db:
        run = db.get(ImportRun, run_id)
        run.status = ImportRunStatus.FINISHED
        run.finished_at = datetime.now(timezone.utc)
        db.commit()


def test_runs_create_one_sync_per_scope_and_keep_its_schedule(sent):
    owner = _user('sync-owner')
    source = _make_source(owner.id, server_kind='cloud')
    space_a = _collection(owner.id, 'sync-space-a')
    client = login_as(owner.username)

    first = _create_run(client, source.id, 'DOC', space_a, sync={'enabled': True, 'interval_seconds': 60})
    assert first.status_code == 201, first.text
    _finish(first.json()['id'])
    second = _create_run(client, source.id, 'HR', space_a)
    assert second.status_code == 201, second.text
    _finish(second.json()['id'])
    # The same scope again (an edit via the wizard) reuses its configuration
    # and keeps the schedule when the request carries none.
    again = _create_run(client, source.id, 'DOC', space_a)
    assert again.status_code == 201, again.text
    assert again.json()['sync_id'] == first.json()['sync_id']

    items = client.get('/api/v1/import/syncs', params={'source_id': source.id}).json()['items']
    by_scope = {item['scope_value']: item for item in items}
    assert set(by_scope) == {'DOC', 'HR'}
    assert by_scope['DOC']['enabled'] is True
    assert by_scope['DOC']['interval_seconds'] == settings.confluence_refresh_min_interval_seconds  # clamped up
    assert by_scope['DOC']['collection_name'] == 'sync-space-a'
    assert by_scope['DOC']['options']['max_pages'] == 20
    assert by_scope['DOC']['last_run']['id'] == again.json()['id']
    assert by_scope['HR']['enabled'] is False and by_scope['HR']['next_refresh_at'] is None
    assert client.get('/api/v1/import/sources').json()['items'][0]['sync_count'] == 2


def test_sync_edit_run_now_and_delete(sent):
    owner = _user('sync-edit-owner')
    source = _make_source(owner.id, server_kind='cloud')
    space_a = _collection(owner.id, 'sync-edit-a')
    space_b = _collection(owner.id, 'sync-edit-b')
    client = login_as(owner.username)
    run = _create_run(client, source.id, 'DOC', space_a).json()
    _finish(run['id'])
    sync_id = run['sync_id']

    patched = client.patch(f'/api/v1/import/syncs/{sync_id}', json={
        'enabled': True, 'interval_seconds': 7200,
        'options': {'max_pages': 5, 'include_attachments': False, 'tags': ['Handbuch']},
    })
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body['enabled'], body['interval_seconds']) == (True, 7200)
    assert body['options']['max_pages'] == 5 and body['options']['include_attachments'] is False
    assert body['options']['collection_id'] == space_a  # unchanged
    moved = client.patch(f'/api/v1/import/syncs/{sync_id}', json={'options': {'collection_id': space_b}})
    assert moved.status_code == 422

    started = client.post(f'/api/v1/import/syncs/{sync_id}/run')
    assert started.status_code == 202, started.text
    with _db() as db:
        new_run = db.get(ImportRun, started.json()['id'])
        assert new_run.sync_id == sync_id
        assert new_run.options['is_refresh'] is True
        assert new_run.options['max_pages'] == 5
        assert new_run.options['collection_id'] == space_a
    assert ('import_confluence', [started.json()['id'], 0]) in sent
    # One crawl at a time per connection.
    assert client.post(f'/api/v1/import/syncs/{sync_id}/run').status_code == 409
    assert client.delete(f'/api/v1/import/syncs/{sync_id}').status_code == 409  # still running

    _finish(started.json()['id'])
    assert client.delete(f'/api/v1/import/syncs/{sync_id}').status_code == 200
    with _db() as db:
        assert db.get(ImportSync, sync_id) is None
        assert db.get(ImportRun, run['id']).sync_id is None  # history stays
    assert client.post(f'/api/v1/import/runs/{run["id"]}/sync').status_code == 409


def test_syncs_are_owner_private(sent):
    owner = _user('sync-private-owner')
    admin = _user('sync-private-admin', role=UserRole.ADMIN)
    source = _make_source(owner.id, server_kind='cloud')
    run = _create_run(login_as(owner.username), source.id, 'DOC', _collection(owner.id, 'sync-private')).json()
    other = login_as(admin.username)
    assert run['sync_id'] not in {item['id'] for item in other.get('/api/v1/import/syncs').json()['items']}
    assert other.get(f'/api/v1/import/syncs/{run["sync_id"]}').status_code == 404
    assert other.patch(f'/api/v1/import/syncs/{run["sync_id"]}', json={'enabled': True}).status_code == 404
    assert other.delete(f'/api/v1/import/syncs/{run["sync_id"]}').status_code == 404


def test_scheduler_runs_every_due_sync_of_a_source_one_after_another(sent):
    owner = _user('sync-sched-owner')
    source = _make_source(owner.id, server_kind='cloud')
    long_ago = datetime.now(timezone.utc) - timedelta(days=2)
    with _db() as db:
        syncs = [
            ImportSync(source_id=source.id, owner_id=owner.id, scope_type='space', scope_value=key, options={},
                       enabled=True, interval_seconds=3600, last_success_at=long_ago + timedelta(minutes=i))
            for i, key in enumerate(('A', 'B'))
        ] + [ImportSync(source_id=source.id, owner_id=owner.id, scope_type='space', scope_value='OFF', options={}, enabled=False)]
        db.add_all(syncs)
        db.commit()
        ids = [sync.id for sync in syncs]

    def started_scopes():
        with _db() as db:
            return [run.scope_value for run in db.scalars(
                select(ImportRun).where(ImportRun.source_id == source.id).order_by(ImportRun.created_at)
            )]

    refresh_tasks._dispatch_due_refreshes()
    assert started_scopes() == ['A']  # longest waiting first, one crawl per source
    refresh_tasks._dispatch_due_refreshes()
    assert started_scopes() == ['A']  # A still running
    with _db() as db:
        run = db.scalar(select(ImportRun).where(ImportRun.sync_id == ids[0]))
        run.status = ImportRunStatus.FINISHED
        db.get(ImportSync, ids[0]).last_success_at = datetime.now(timezone.utc)
        db.commit()
    refresh_tasks._dispatch_due_refreshes()
    assert started_scopes() == ['A', 'B']


def test_backfill_is_idempotent_and_links_runs_without_sync():
    owner = _user('sync-backfill-owner')
    source = _make_source(owner.id, server_kind='cloud')
    with _db() as db:
        for scope_value in ('X', 'X', 'Y'):
            db.add(ImportRun(source_id=source.id, owner_id=owner.id, kind='confluence', scope_type='space',
                             scope_value=scope_value, status=ImportRunStatus.FINISHED, options={'max_pages': 3}, state={}))
        db.commit()
        backfill_import_syncs(db.connection())  # the shared test DB may hold other legacy runs too
        assert backfill_import_syncs(db.connection()) == 0
        db.commit()
        assert len(db.scalars(select(ImportSync).where(ImportSync.source_id == source.id)).all()) == 2
        runs = db.scalars(select(ImportRun).where(ImportRun.source_id == source.id)).all()
        assert all(run.sync_id for run in runs)
        assert len({run.sync_id for run in runs}) == 2
