"""Admin re-ingest (app/services/reingest.py, app/api/reingest.py).

Runs on its own SQLite database: the planner scans every job, so the shared
test database (filled by other modules) would make the plan unpredictable --
and advancing a run mutates the jobs it plans.
"""

import hashlib
import uuid

import pytest
from app.core.config import settings
from app.database.session import get_db
from app.main import app
from app.models.models import (
    Base,
    Collection,
    CollectionGrant,
    CollectionRole,
    DocumentRelease,
    ImportAuthType,
    ImportPageState,
    ImportRun,
    ImportRunStatus,
    ImportSource,
    ImportSync,
    Job,
    JobStatus,
    KnowledgeWithdrawal,
    ReingestItem,
    ReingestRun,
    ReingestRunStatus,
    User,
    UserRole,
)
from app.services import object_store, reingest, security
from app.services.security import hash_password
from app.workers import publication_tasks
from app.workers.celery_app import celery_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from tests.conftest import BROWSER_HEADERS, DEFAULT_TEST_PASSWORD

BASE = '/api/v1/admin/reingest'
BODY = '# Leitfaden\n\nInhalt alt.\n'


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/reingest.db', future=True)

    @event.listens_for(engine, 'connect')
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(reingest, 'SessionLocal', session_factory)
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'publication-secret')
    monkeypatch.setattr(settings, 'reingest_max_in_flight', 2)
    security.rate_limiter.reset()

    sent: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kwargs: sent.append((name, list(args or []))))
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args, **kwargs: None)
    inventory: dict[str, str] = {}
    monkeypatch.setattr(reingest, 'knowledge_inventory', lambda: dict(inventory))

    class Env:
        Session = session_factory
        tasks = sent
        knowledge = inventory

        def db(self):
            return session_factory()

    yield Env()
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    engine.dispose()


def _user(env, *, role=UserRole.USER, name=None) -> User:
    with env.db() as db:
        username = name or f'user-{uuid.uuid4().hex[:8]}'
        user = User(
            username=username, email=f'{username}@example.com', password_hash=hash_password(DEFAULT_TEST_PASSWORD),
            role=role, is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _login(user: User) -> TestClient:
    client = TestClient(app, headers=BROWSER_HEADERS)
    response = client.post('/api/v1/auth/login', json={'identifier': user.username, 'password': DEFAULT_TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def _collection(env, owner: User) -> Collection:
    with env.db() as db:
        collection = Collection(slug=f'space-{uuid.uuid4().hex[:8]}', name='Space', created_by_id=owner.id)
        db.add(collection)
        db.flush()
        db.add(CollectionGrant(collection_id=collection.id, user_id=owner.id, role=CollectionRole.OWNER))
        db.commit()
        db.refresh(collection)
        db.expunge(collection)
        return collection


def _markdown(job_id: str, body: str = BODY) -> str:
    return f'---\njob_id: {job_id}\nengine: text-md\nprocessed_at: now\n---\n{body}'


def _job(env, owner: User, collection: Collection, *, released=False, extra_info=None, body=BODY,
         grade='A', status=JobStatus.FINISHED, with_original=True, mode='collection') -> Job:
    with env.db() as db:
        upload = object_store.put_bytes(db, b'# Leitfaden\n').id if with_original else None
        job = Job(
            original_filename='leitfaden.md', upload_path='inbox/x/leitfaden.md', upload_object_id=upload,
            status=status, owner_id=owner.id, content_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            processing_info={
                'settings': {
                    'mode': mode, 'collection_id': collection.id, 'profile_id': 'ppocrv6_tiny',
                    'webhook_connection_id': 'hook-1',
                },
                'execution': {'status': 'finished', 'quality_gate': {'grade': grade, 'recommendation': 'allow'}},
                'source_uploaded_at': '2026-01-30T10:00:00+00:00',
                **(extra_info or {}),
            },
        )
        db.add(job)
        db.flush()
        job.result_markdown = _markdown(job.id, body)
        if released:
            db.add(DocumentRelease(
                job_id=job.id, owner_id=owner.id, markdown_snapshot='x', markdown_sha256='0' * 64,
                payload={'job_id': job.id}, status='sent',
            ))
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job


def _finish(env, job_id: str, *, body: str, grade: str = 'A', failed: bool = False) -> None:
    """Stand-in for process_job: what it leaves behind."""
    with env.db() as db:
        job = db.get(Job, job_id)
        info = dict(job.processing_info or {})
        info['execution'] = {'status': 'failed' if failed else 'finished', 'quality_gate': {'grade': grade, 'recommendation': 'warn' if grade == 'C' else 'allow'}}
        job.processing_info = info
        if failed:
            job.status = JobStatus.FAILED
            job.error_message = 'kaputt'
        else:
            job.status = JobStatus.FINISHED
            job.result_markdown = _markdown(job.id, body)
        db.commit()


def _start(client: TestClient, **overrides) -> dict:
    preview = client.get(f'{BASE}/preview', params={
        'include_edited': overrides.get('include_edited', False), 'rerelease': overrides.get('rerelease', True),
    }).json()
    payload = {
        'include_documents': True, 'include_connections': False, 'rerelease': True,
        'accept_quality_warnings': True, 'include_edited': False,
        'expected_documents': preview['documents']['rerelease'] + preview['documents']['reprocess'],
        'expected_connections': 0,
        **overrides,
    }
    response = client.post(f'{BASE}/runs', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _item(env, run_id: str, job_id: str) -> ReingestItem:
    with env.db() as db:
        return db.scalar(select(ReingestItem).where(ReingestItem.run_id == run_id, ReingestItem.job_id == job_id))


def _process_dispatches(env) -> list[str]:
    return [args[0] for name, args in env.tasks if name == 'process_job']


def test_non_admin_is_refused(env):
    user = _user(env)
    client = _login(user)
    assert client.get(f'{BASE}/preview').status_code == 403
    assert client.post(f'{BASE}/runs', json={'rerelease': True, 'expected_documents': 0, 'expected_connections': 0}).status_code == 403


def test_preview_classifies_documents(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    live = _job(env, owner, collection, released=True)
    superseded = _job(env, owner, collection, released=True)
    unreleased = _job(env, owner, collection)
    edited = _job(env, owner, collection, extra_info={'editor': {'version': 2}})
    _job(env, owner, collection, with_original=False)
    _job(env, owner, collection, mode='import')
    withdrawn = _job(env, owner, collection, released=True)
    with env.db() as db:
        db.add(KnowledgeWithdrawal(job_id=withdrawn.id, status='sent'))
        db.commit()
    env.knowledge.update({live.id: 'indexed', superseded.id: 'superseded'})

    client = _login(admin)
    preview = client.get(f'{BASE}/preview').json()
    assert preview['documents']['rerelease'] == 1
    assert preview['documents']['reprocess'] == 1
    # The imported page belongs to no connection any more.
    assert preview['documents']['skipped'] == {
        'not_live': 1, 'edited': 1, 'no_original': 1, 'withdrawn': 1, 'connection_missing': 1,
    }
    assert preview['documents']['imported'] == 0
    assert preview['knowledge_available'] is True

    kept = client.get(f'{BASE}/preview', params={'rerelease': False}).json()
    assert kept['documents']['rerelease'] == 0
    assert kept['documents']['skipped']['released_kept'] == 2
    with_edited = client.get(f'{BASE}/preview', params={'include_edited': True}).json()
    assert with_edited['documents']['reprocess'] == 2
    assert unreleased.id and edited.id


def test_start_refuses_a_changed_inventory_and_a_second_run(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    _job(env, owner, collection)
    client = _login(admin)

    stale = client.post(f'{BASE}/runs', json={
        'include_documents': True, 'include_connections': False, 'rerelease': True,
        'expected_documents': 5, 'expected_connections': 0,
    })
    assert stale.status_code == 409
    assert 'geändert' in stale.json()['detail']

    run = _start(client)
    assert run['status'] == 'running'
    assert ('reingest_advance', [run['id']]) in env.tasks
    again = client.post(f'{BASE}/runs', json={
        'include_documents': True, 'include_connections': False, 'rerelease': True,
        'expected_documents': 1, 'expected_connections': 0,
    })
    assert again.status_code == 409


def test_rerelease_requires_the_knowledge_inventory(env, monkeypatch):
    from app.services.knowledge_orphans import KnowledgeInventoryError

    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    _job(env, owner, _collection(env, owner), released=True)

    def _unavailable():
        raise KnowledgeInventoryError('down')

    monkeypatch.setattr(reingest, 'knowledge_inventory', _unavailable)
    client = _login(admin)
    preview = client.get(f'{BASE}/preview').json()
    assert preview['knowledge_available'] is False
    assert preview['documents']['skipped'] == {'knowledge_unavailable': 1}
    response = client.post(f'{BASE}/runs', json={
        'include_documents': True, 'include_connections': False, 'rerelease': True,
        'expected_documents': 0, 'expected_connections': 0,
    })
    assert response.status_code == 503


def test_released_document_becomes_a_new_version_and_is_released_again(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    released = _job(env, owner, collection, released=True)
    env.knowledge[released.id] = 'indexed'
    client = _login(admin)
    run = _start(client)

    assert reingest.advance_run(run['id']) == 'active'
    item = _item(env, run['id'], released.id)
    assert item.status == 'queued' and item.action == 'rerelease'
    assert _process_dispatches(env) == [item.new_job_id]
    with env.db() as db:
        version = db.get(Job, item.new_job_id)
        assert version.status == JobStatus.PENDING
        assert version.previous_job_id == released.id and version.document_version == released.document_version + 1
        assert version.upload_object_id == released.upload_object_id
        assert version.processing_info['settings']['reingest'] == {'run_id': run['id'], 'source_job_id': released.id, 'webhook_connection_id': 'hook-1'}
        assert version.processing_info['source_uploaded_at'] == '2026-01-30T10:00:00+00:00'
        assert 'webhook_connection_id' not in version.processing_info['settings']

    _finish(env, item.new_job_id, body='# Leitfaden\n\nInhalt neu.\n', grade='C')
    assert reingest.advance_run(run['id']) == 'done'
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result) == ('done', 'released')
    with env.db() as db:
        version = db.get(Job, item.new_job_id)
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == version.id))
        # Released as the person who released the previous version; grade C
        # confirmed by the admin's choice.
        assert release.owner_id == owner.id
        assert release.payload.get('quality_override') is True
        assert release.payload['frontmatter']['uploaded_at'] == '2026-01-30T10:00:00+00:00'
        assert version.processing_info['settings']['webhook_connection_id'] == 'hook-1'
        assert 'reingest' not in version.processing_info['settings']
        assert db.get(ReingestRun, run['id']).status == ReingestRunStatus.FINISHED


def test_unchanged_conversion_is_discarded(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    released = _job(env, owner, _collection(env, owner), released=True)
    env.knowledge[released.id] = 'indexed'
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    new_id = _item(env, run['id'], released.id).new_job_id

    _finish(env, new_id, body=BODY)
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result, item.new_job_id) == ('done', 'unchanged', None)
    with env.db() as db:
        assert db.get(Job, new_id) is None
        assert db.scalar(select(Job.id).where(Job.previous_job_id == released.id)) is None


def test_failed_conversion_keeps_the_released_version(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    released = _job(env, owner, _collection(env, owner), released=True)
    env.knowledge[released.id] = 'indexed'
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    new_id = _item(env, run['id'], released.id).new_job_id

    _finish(env, new_id, body='', failed=True)
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result, item.message) == ('failed', 'failed', 'kaputt')
    with env.db() as db:
        assert db.get(Job, new_id) is None


def test_document_withdrawn_during_the_run_is_not_published_again(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    released = _job(env, owner, _collection(env, owner), released=True)
    env.knowledge[released.id] = 'indexed'
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    new_id = _item(env, run['id'], released.id).new_job_id
    with env.db() as db:
        db.add(KnowledgeWithdrawal(job_id=released.id, status='pending'))
        db.commit()

    _finish(env, new_id, body='# Neu\n')
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result) == ('skipped', 'withdrawn')
    with env.db() as db:
        assert db.get(Job, new_id) is None


def test_unreleased_document_is_reprocessed_in_place_keeping_review_decision(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    parked = _job(env, owner, _collection(env, owner), extra_info={'portal_review': {'decision': 'parked', 'by': 'x'}})
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], parked.id)
    assert (item.status, item.action, item.new_job_id) == ('queued', 'reprocess', None)
    with env.db() as db:
        job = db.get(Job, parked.id)
        # The current markdown stays until a new one is written.
        assert job.status == JobStatus.PENDING and job.result_markdown == _markdown(parked.id)
    assert _process_dispatches(env) == [parked.id]

    # process_job drops everything but settings/runtime/execution at its claim.
    with env.db() as db:
        job = db.get(Job, parked.id)
        job.processing_info = {'settings': job.processing_info['settings'], 'execution': {}}
        db.commit()
    _finish(env, parked.id, body='# Neu\n')
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], parked.id)
    assert (item.status, item.result) == ('done', 'reprocessed')
    with env.db() as db:
        info = db.get(Job, parked.id).processing_info
        assert info['portal_review']['decision'] == 'parked'
        assert info['source_uploaded_at'] == '2026-01-30T10:00:00+00:00'
        assert info['settings']['webhook_connection_id'] == 'hook-1'
        assert db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == parked.id)) is None


def test_failed_in_place_conversion_keeps_the_document(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    good = _job(env, owner, _collection(env, owner), extra_info={'portal_review': {'decision': 'skipped'}})
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    with env.db() as db:
        job = db.get(Job, good.id)
        job.processing_info = {'settings': job.processing_info['settings'], 'execution': {'status': 'running'}}
        db.commit()
    _finish(env, good.id, body='', failed=True)
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], good.id)
    assert (item.status, item.result, item.message) == ('failed', 'failed', 'kaputt')
    with env.db() as db:
        job = db.get(Job, good.id)
        assert job.status == JobStatus.FINISHED
        assert job.result_markdown == _markdown(good.id)
        assert job.processing_info['execution']['quality_gate']['grade'] == 'A'
        assert job.processing_info['portal_review']['decision'] == 'skipped'


def test_disabled_vl_connection_is_skipped(env):
    from app.models.models import VlConnection

    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    with env.db() as db:
        connection = VlConnection(name='VL', base_url='https://vl.example', model='m', api_key_encrypted='x', enabled=False)
        db.add(connection)
        db.commit()
        connection_id = connection.id
    job = _job(env, owner, collection)
    with env.db() as db:
        row = db.get(Job, job.id)
        row.processing_info = {**row.processing_info, 'settings': {**row.processing_info['settings'], 'vl_connection_id': connection_id}}
        db.commit()
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], job.id)
    assert (item.status, item.result) == ('skipped', 'profile_unavailable')
    assert _process_dispatches(env) == []


def test_a_broken_item_does_not_stall_the_run(env, monkeypatch):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    broken, fine = _job(env, owner, collection), _job(env, owner, collection)
    run = _start(_login(admin))
    real = reingest._start_document

    def _flaky(db, run_row, item):
        if item.job_id == broken.id:
            raise RuntimeError('boom')
        return real(db, run_row, item)

    monkeypatch.setattr(reingest, '_start_document', _flaky)
    for _ in range(3):
        reingest.advance_run(run['id'])
    item = _item(env, run['id'], broken.id)
    assert (item.status, item.result, item.message) == ('failed', 'error', 'boom')
    assert _item(env, run['id'], fine.id).status == 'queued'


def test_expected_rereleases_tolerates_reprocess_drift(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    released = _job(env, owner, collection, released=True)
    env.knowledge[released.id] = 'indexed'
    _job(env, owner, collection)
    client = _login(admin)
    body = {'include_documents': True, 'include_connections': False, 'rerelease': True, 'expected_connections': 0}
    # One more unreleased upload arrived after the preview (2 -> 3 documents).
    _job(env, owner, collection)
    assert client.post(f'{BASE}/runs', json={**body, 'expected_documents': 2}).status_code == 409
    response = client.post(f'{BASE}/runs', json={**body, 'expected_documents': 2, 'expected_rereleases': 1})
    assert response.status_code == 201, response.text
    assert client.post(f'{BASE}/runs/{response.json()["id"]}/cancel').status_code == 200


def test_in_flight_limit_and_changed_items(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    jobs = [_job(env, owner, collection) for _ in range(3)]
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    assert len(_process_dispatches(env)) == 2  # settings.reingest_max_in_flight

    # The third was released by someone after the admin confirmed the plan.
    with env.db() as db:
        db.add(DocumentRelease(job_id=jobs[2].id, owner_id=owner.id, markdown_snapshot='x', markdown_sha256='0' * 64, payload={}, status='sent'))
        db.commit()
    for job in jobs[:2]:
        _finish(env, job.id, body='# Neu\n')
    reingest.advance_run(run['id'])
    assert (_item(env, run['id'], jobs[2].id).status, _item(env, run['id'], jobs[2].id).result) == ('skipped', 'changed')


def test_cancel_finishes_queued_items_and_skips_the_rest(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    jobs = [_job(env, owner, collection) for _ in range(3)]
    client = _login(admin)
    run = _start(client)
    reingest.advance_run(run['id'])
    cancelled = client.post(f'{BASE}/runs/{run["id"]}/cancel').json()
    assert cancelled['status'] == 'cancelling'
    for job in jobs[:2]:
        _finish(env, job.id, body='# Neu\n')
    assert reingest.advance_run(run['id']) == 'done'
    listed = client.get(f'{BASE}/runs').json()['items'][0]
    assert listed['status'] == 'cancelled'
    assert listed['counts']['document']['results'] == {'reprocessed': 2, 'cancelled': 1}
    problems = client.get(f'{BASE}/runs/{run["id"]}/items', params={'status': ['skipped']}).json()
    assert problems['total'] == 1 and problems['items'][0]['result'] == 'cancelled'


def test_advance_is_lease_guarded(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    _job(env, owner, _collection(env, owner))
    run = _start(_login(admin))
    with env.db() as db:
        from datetime import datetime, timedelta, timezone

        row = db.get(ReingestRun, run['id'])
        row.lease_token = 'other'
        row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.commit()
    assert reingest.advance_run(run['id']) == 'busy'
    assert _process_dispatches(env) == []


def test_backup_restore_is_refused_while_a_reingest_runs(env):
    from app.api.backup import _refuse_during_reingest
    from fastapi import HTTPException

    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    _job(env, owner, _collection(env, owner))
    _start(_login(admin))
    with env.db() as db, pytest.raises(HTTPException) as refused:
        _refuse_during_reingest(db)
    assert refused.value.status_code == 409


# --- Connections ---------------------------------------------------------------------

def _sync(env, owner: User, collection: Collection, **source_fields) -> ImportSync:
    with env.db() as db:
        source = ImportSource(
            owner_id=owner.id, name='Wiki', kind='confluence', base_url='https://wiki.example', server_kind='cloud',
            api_base_path='/wiki/api/v2', auth_type=ImportAuthType.CLOUD_BASIC, auth_username='a@example.com',
            credential_encrypted=security.encrypt_import_credential('token'), **source_fields,
        )
        db.add(source)
        db.flush()
        sync = ImportSync(
            source_id=source.id, owner_id=owner.id, scope_type='space', scope_value='KB', collection_id=collection.id,
            options={'webhook_connection_id': 'hook-1', 'auto_release': True},
        )
        db.add(sync)
        db.commit()
        db.refresh(sync)
        db.expunge(sync)
        return sync


def _import_page(env, owner, collection, run_id, *, previous: Job | None = None, released=False, forced=False,
                 mode='import', status=JobStatus.FINISHED, name='seite.md') -> Job:
    with env.db() as db:
        job = Job(
            original_filename=name, upload_path='kb/x/x.html', status=status, owner_id=owner.id,
            import_run_id=run_id, content_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            document_version=previous.document_version + 1 if previous else 1,
            previous_job_id=previous.id if previous else None,
            processing_info={'settings': {
                'mode': mode, 'collection_id': collection.id,
                'import': {
                    'source_page_id': '42', 'source_page_version': 3, 'source_url': 'https://wiki.example/42',
                    **({'forced_reimport': True} if forced else {}),
                },
            }, 'execution': {'status': 'finished', 'engine': 'confluence-import'}},
        )
        db.add(job)
        db.flush()
        job.result_markdown = _markdown(job.id, '# Seite\n')
        if released:
            db.add(DocumentRelease(job_id=job.id, owner_id=owner.id, markdown_snapshot='x', markdown_sha256='0' * 64, payload={}, status='sent'))
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job


def test_connection_is_force_reimported_and_released_pages_released_again(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    sync = _sync(env, owner, collection)
    with env.db() as db:
        first = ImportRun(source_id=sync.source_id, sync_id=sync.id, owner_id=owner.id, kind='confluence',
                          scope_type='space', scope_value='KB', status=ImportRunStatus.FINISHED, options={})
        db.add(first)
        db.commit()
        first_id = first.id
    page = _import_page(env, owner, collection, first_id, released=True)
    env.knowledge[page.id] = 'indexed'
    client = _login(admin)

    preview = client.get(f'{BASE}/preview').json()
    assert preview['connections'] == {'ready': 1, 'blocked': {}}
    run = _start(client, include_documents=False, include_connections=True, expected_documents=0, expected_connections=1)
    reingest.advance_run(run['id'])
    with env.db() as db:
        item = db.scalar(select(ReingestItem).where(ReingestItem.run_id == run['id'], ReingestItem.kind == 'connection'))
        forced = db.get(ImportRun, item.import_run_id)
        assert item.status == 'queued'
        assert forced.options['force_reimport'] is True
        assert forced.options['reingest_run_id'] == run['id']
        assert forced.options['is_refresh'] is True
        assert db.get(ReingestRun, run['id']).options['live_job_ids'] == [page.id]
    assert 'live_job_ids' not in client.get(f'{BASE}/runs').json()['items'][0]['options']
    assert ('import_confluence', [forced.id, 0]) in env.tasks

    # What the forced run leaves behind: the page converted again, chained.
    new_page = _import_page(env, owner, collection, forced.id, previous=page, forced=True)
    with env.db() as db:
        db.get(ImportRun, forced.id).status = ImportRunStatus.FINISHED
        db.commit()
    assert reingest.advance_run(run['id']) == 'done'
    with env.db() as db:
        item = db.get(ReingestItem, item.id)
        assert (item.status, item.result) == ('done', 'reimported')
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == new_page.id))
        assert release is not None and release.owner_id == owner.id


def test_failed_forced_run_is_rolled_back(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    sync = _sync(env, owner, collection)
    with env.db() as db:
        first = ImportRun(source_id=sync.source_id, sync_id=sync.id, owner_id=owner.id, kind='confluence',
                          scope_type='space', scope_value='KB', status=ImportRunStatus.FINISHED, options={})
        db.add(first)
        db.commit()
        first_id = first.id
    page = _import_page(env, owner, collection, first_id, released=True)
    with env.db() as db:
        db.add(ImportPageState(source_id=sync.source_id, collection_id=collection.id, page_id='42', page_version=3, job_id=page.id))
        db.commit()
    env.knowledge[page.id] = 'indexed'
    run = _start(_login(admin), include_documents=False, include_connections=True, expected_documents=0, expected_connections=1)
    reingest.advance_run(run['id'])
    with env.db() as db:
        forced_id = db.scalar(select(ReingestItem.import_run_id).where(ReingestItem.run_id == run['id']))
    new_page = _import_page(env, owner, collection, forced_id, previous=page)
    with env.db() as db:
        state = db.scalar(select(ImportPageState).where(ImportPageState.page_id == '42'))
        state.job_id, state.page_version = new_page.id, 4
        forced = db.get(ImportRun, forced_id)
        forced.status, forced.error_message = ImportRunStatus.FAILED, 'Zugang abgelaufen'
        db.commit()

    reingest.advance_run(run['id'])
    with env.db() as db:
        item = db.scalar(select(ReingestItem).where(ReingestItem.run_id == run['id']))
        assert (item.status, item.result) == ('failed', 'import_failed')
        assert 'Zugang abgelaufen' in item.message
        state = db.scalar(select(ImportPageState).where(ImportPageState.page_id == '42'))
        assert (state.job_id, state.page_version) == (page.id, 3)
        assert db.get(Job, new_page.id) is None


def test_blocked_connection_is_reported(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    _sync(env, owner, collection)
    with env.db() as db:
        db.get(User, owner.id).is_active = False
        db.commit()
    preview = _login(admin).get(f'{BASE}/preview').json()
    assert preview['connections'] == {'ready': 0, 'blocked': {'owner_inactive': 1}}


# --- Import worker hooks ---------------------------------------------------------------

def test_force_reimport_gating(env):
    from app.workers.import_tasks import _force_reimport

    owner = _user(env)
    collection = _collection(env, owner)
    released = _import_page(env, owner, collection, None, released=True)
    stale = _import_page(env, owner, collection, None, released=True)
    with env.db() as db:
        run = ReingestRun(options={'live_job_ids': [released.id]})
        db.add(run)
        db.commit()
        run_id = run.id
    forced = {'force_reimport': True, 'reingest_run_id': run_id, 'reingest_rerelease': True}
    with env.db() as db:
        released = db.get(Job, released.id)
        assert _force_reimport(db, {}, released) is False
        assert _force_reimport(db, forced, released) is True
        # Released but no longer live in Knowledge: left alone.
        assert _force_reimport(db, forced, db.get(Job, stale.id)) is False
        # Without the decision to release again, a released page stays as it is.
        assert _force_reimport(db, {**forced, 'reingest_rerelease': False}, released) is False
        released.processing_info = {**released.processing_info, 'portal_edit': {'by': 'x'}}
        assert _force_reimport(db, forced, released) is False
        assert _force_reimport(db, {**forced, 'reingest_include_edited': True}, released) is True
    # An unreleased version on top of a released one is not stacked on.
    pending = _import_page(env, owner, collection, None, previous=stale)
    with env.db() as db:
        assert _force_reimport(db, forced, db.get(Job, pending.id)) is False


def test_auto_release_skips_only_forced_versions(env):
    owner = _user(env)
    collection = _collection(env, owner)
    with env.db() as db:
        run = ImportRun(owner_id=owner.id, kind='confluence', scope_type='space', scope_value='KB',
                        status=ImportRunStatus.FINISHED, options={'auto_release': True, 'force_reimport': True})
        db.add(run)
        db.commit()
        run_id = run.id
    forced = _import_page(env, owner, collection, run_id, forced=True)
    new_page = _import_page(env, owner, collection, run_id, name='neu.md')
    with env.db() as db:
        assert publication_tasks.auto_release_import_jobs(db, run_id) == 1
        assert db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == forced.id)) is None
        assert db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == new_page.id)) is not None


def test_run_only_options_never_reach_the_sync():
    from app.services.import_syncs import sync_options

    stored = sync_options({'force_reimport': True, 'reingest_run_id': 'r', 'reingest_include_edited': True, 'tags': ['a']})
    assert stored == {'tags': ['a']}


def test_cancel_during_a_step_ends_cancelled(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    job = _job(env, owner, _collection(env, owner))
    client = _login(admin)
    run = _start(client)
    reingest.advance_run(run['id'])
    _finish(env, job.id, body='# Neu\n')
    real = reingest._finalize_documents

    def _cancel_meanwhile(step, run_row):
        real(step, run_row)
        assert client.post(f'{BASE}/runs/{run["id"]}/cancel').status_code == 200

    original = reingest._finalize_documents
    reingest._finalize_documents = _cancel_meanwhile
    try:
        assert reingest.advance_run(run['id']) == 'done'
    finally:
        reingest._finalize_documents = original
    with env.db() as db:
        assert db.get(ReingestRun, run['id']).status == ReingestRunStatus.CANCELLED


def test_a_queued_item_that_keeps_failing_is_cleaned_up(env, monkeypatch):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    released = _job(env, owner, _collection(env, owner), released=True)
    env.knowledge[released.id] = 'indexed'
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    new_id = _item(env, run['id'], released.id).new_job_id
    _finish(env, new_id, body='# Neu\n')

    def _broken(old, new):
        raise OSError('object store down')

    monkeypatch.setattr(reingest, '_unchanged', _broken)
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert item.status == 'queued' and item.carry['attempts'] == 1 and 'retry_at' in item.carry
    # Backed off: the next step leaves it alone.
    reingest.advance_run(run['id'])
    assert _item(env, run['id'], released.id).carry['attempts'] == 1
    for _ in range(reingest._QUEUED_MAX_ATTEMPTS - 1):
        with env.db() as db:
            row = db.get(ReingestItem, item.id)
            row.carry = {**row.carry, 'retry_at': '2000-01-01T00:00:00+00:00'}
            db.commit()
        reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result) == ('failed', 'error')
    with env.db() as db:
        assert db.get(Job, new_id) is None  # no unreleased version left on top of the live one
        assert db.get(ReingestRun, run['id']).status == ReingestRunStatus.FINISHED


def test_version_built_on_by_an_upload_is_not_released(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    released = _job(env, owner, collection, released=True)
    env.knowledge[released.id] = 'indexed'
    run = _start(_login(admin))
    reingest.advance_run(run['id'])
    new_id = _item(env, run['id'], released.id).new_job_id
    with env.db() as db:
        version = db.get(Job, new_id)
        db.add(Job(original_filename=version.original_filename, upload_path='x', status=JobStatus.PENDING,
                   owner_id=owner.id, previous_job_id=new_id, document_version=version.document_version + 1))
        db.commit()
    _finish(env, new_id, body='# Neu\n')
    reingest.advance_run(run['id'])
    item = _item(env, run['id'], released.id)
    assert (item.status, item.result) == ('skipped', 'superseded')
    with env.db() as db:
        assert db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == new_id)) is None
        assert 'reingest' not in db.get(Job, new_id).processing_info['settings']


def test_orphaned_forced_run_is_adopted(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    sync = _sync(env, owner, collection)
    run = _start(_login(admin), include_documents=False, include_connections=True, expected_documents=0, expected_connections=1)
    with env.db() as db:
        orphan = ImportRun(source_id=sync.source_id, sync_id=sync.id, owner_id=owner.id, kind='confluence',
                           scope_type='space', scope_value='KB', status=ImportRunStatus.RUNNING,
                           options={'force_reimport': True, 'reingest_run_id': run['id']})
        db.add(orphan)
        db.commit()
        orphan_id = orphan.id
    reingest.advance_run(run['id'])
    with env.db() as db:
        item = db.scalar(select(ReingestItem).where(ReingestItem.run_id == run['id']))
        assert (item.status, item.import_run_id) == ('queued', orphan_id)
    assert not [name for name, _ in env.tasks if name == 'import_confluence']


def test_failed_attachment_version_is_discarded(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    sync = _sync(env, owner, collection)
    run = _start(_login(admin), include_documents=False, include_connections=True, expected_documents=0, expected_connections=1)
    reingest.advance_run(run['id'])
    with env.db() as db:
        forced_id = db.scalar(select(ReingestItem.import_run_id).where(ReingestItem.run_id == run['id']))
        db.get(ImportRun, forced_id).status = ImportRunStatus.FINISHED
        db.commit()
    attachment = _import_page(env, owner, collection, None, mode='import_attachment', released=True, name='a.pdf')
    failed = _import_page(env, owner, collection, forced_id, previous=attachment, forced=True,
                          mode='import_attachment', status=JobStatus.FAILED, name='a.pdf')
    reingest.advance_run(run['id'])
    with env.db() as db:
        assert db.get(Job, failed.id) is None
        assert sync.id and db.scalar(select(Job.id).where(Job.previous_job_id == attachment.id)) is None


def test_source_waits_for_the_rollback_of_a_forced_run(env):
    from app.workers.refresh_tasks import _has_active_run

    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    sync = _sync(env, owner, collection)
    run = _start(_login(admin), include_documents=False, include_connections=True, expected_documents=0, expected_connections=1)
    reingest.advance_run(run['id'])
    with env.db() as db:
        forced = db.get(ImportRun, db.scalar(select(ReingestItem.import_run_id).where(ReingestItem.run_id == run['id'])))
        forced.status = ImportRunStatus.CANCELLED
        db.commit()
        assert _has_active_run(db, sync.source_id) is True
    reingest.advance_run(run['id'])
    with env.db() as db:
        assert _has_active_run(db, sync.source_id) is False


def test_documents_of_a_deleted_sync_are_reported(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    with env.db() as db:
        orphan_run = ImportRun(owner_id=owner.id, kind='confluence', scope_type='space', scope_value='KB',
                               status=ImportRunStatus.FINISHED, options={})
        db.add(orphan_run)
        db.commit()
        orphan_run_id = orphan_run.id
    _import_page(env, owner, collection, orphan_run_id, released=True)
    preview = _login(admin).get(f'{BASE}/preview').json()
    assert preview['documents']['imported'] == 0
    assert preview['documents']['skipped'] == {'connection_missing': 1}


def test_backup_export_is_refused_while_a_reingest_runs(env):
    from app.services import backup

    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    _job(env, owner, _collection(env, owner))
    _start(_login(admin))
    with env.db() as db, pytest.raises(backup.BackupError):
        next(backup.iter_export_backup(db, passphrase='x' * 16))


def test_problem_items_list_failures_first(env):
    admin = _user(env, role=UserRole.ADMIN)
    owner = _user(env)
    collection = _collection(env, owner)
    _job(env, owner, collection, extra_info={'editor': {'version': 2}})
    failing = _job(env, owner, collection)
    client = _login(admin)
    run = _start(client)
    reingest.advance_run(run['id'])
    _finish(env, failing.id, body='', failed=True)
    reingest.advance_run(run['id'])
    items = client.get(f'{BASE}/runs/{run["id"]}/items', params={'status': ['failed', 'skipped']}).json()['items']
    assert [item['status'] for item in items] == ['failed', 'skipped']
