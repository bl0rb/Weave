"""A website import through the unchanged chunked crawl task: pages keyed by
URL, main content converted with fetched_at/legal_status frontmatter, linked
documents stored as attachments, a refresh that skips unchanged pages and
chains changed ones, and release-time provenance (source_kind 'website')."""

import uuid

import pytest
import yaml
from sqlalchemy import select

from app.models.models import ImportAuthType, ImportPageState, ImportRun, ImportRunStatus, ImportSource, ImportSync, Job, JobArtifact, JobStatus
from app.services import security, website
from app.services.publications import canonical_snapshot
from app.workers import import_tasks
from app.workers import tasks as worker_tasks
from app.workers.celery_app import celery_app
from app.workers.import_tasks import import_confluence
from tests.conftest import TestingSessionLocal, add_legacy_collection, create_test_user
from tests.test_website_client import PAGES, START, FakeFetch


@pytest.fixture()
def sent(monkeypatch):
    monkeypatch.setattr(import_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(worker_tasks, 'SessionLocal', TestingSessionLocal)
    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []))))
    return captured


@pytest.fixture()
def fetch(monkeypatch):
    fake = FakeFetch({
        **PAGES,
        START: PAGES[START].replace('<main>', '<main><p>Stand: Zuletzt geändert durch Art. 2 G v. 1.2.2023 I 100</p>'),
    })
    monkeypatch.setattr(website, 'safe_fetch', fake)
    return fake


def _db():
    return TestingSessionLocal()


def _drain(sent, max_rounds=20):
    others = []
    rounds = 0
    while sent:
        name, args = sent.pop(0)
        if name == 'import_confluence':
            rounds += 1
            assert rounds <= max_rounds
            import_confluence(*args)
        else:
            others.append((name, args))
    return others


def _setup(*, legal_source=True, ocr_attachments=False):
    suffix = uuid.uuid4().hex[:8]
    owner = create_test_user(username=f'web-{suffix}', email=f'web-{suffix}@example.com')
    with _db() as db:
        collection = add_legacy_collection(db, owner_id=owner.id, slug=f'web-{suffix}', name='Web space')
        source = ImportSource(
            owner_id=owner.id, name='Docs', kind='website', base_url='https://docs.example.com', server_kind='website',
            auth_type=ImportAuthType.NONE, credential_encrypted=security.encrypt_import_credential(''),
        )
        db.add(source)
        db.flush()
        options = {
            'max_pages': 50, 'max_depth': 10, 'include_attachments': True, 'ocr_attachments': ocr_attachments,
            'ocr_profile_id': None, 'folder': '', 'subfolder': '', 'tags': ['web'], 'email': '',
            'crawl_scope': 'prefix', 'legal_source': legal_source,
        }
        sync = ImportSync(source_id=source.id, owner_id=owner.id, scope_type='url', scope_value=START, collection_id=collection.id, options=options)
        db.add(sync)
        db.flush()
        run_options = {**options, 'collection_id': collection.id, 'collection_slug': collection.slug, 'collection_name': collection.name}
        run = ImportRun(
            source_id=source.id, sync_id=sync.id, owner_id=owner.id, kind='website', scope_type='url', scope_value=START,
            options=run_options, state={'frontier': [[START, 0]], 'visited': {}, 'errors': []},
        )
        db.add(run)
        db.commit()
        return owner, source.id, sync.id, run.id, collection.id, run_options


def _frontmatter(markdown: str) -> dict:
    end = markdown.find('\n---\n', 4)
    return yaml.safe_load(markdown[4:end + 1])


def _jobs(run_id):
    with _db() as db:
        jobs = db.scalars(select(Job).where(Job.import_run_id == run_id).order_by(Job.created_at)).all()
        for job in jobs:
            db.refresh(job)
            db.expunge(job)
        return jobs


def test_website_run_imports_pages_documents_and_provenance(sent, fetch):
    owner, source_id, sync_id, run_id, collection_id, _ = _setup()
    import_confluence(run_id, 0)
    assert _drain(sent) == []

    with _db() as db:
        run = db.get(ImportRun, run_id)
        assert run.status == ImportRunStatus.FINISHED
        assert run.pages_imported == 2 and run.pages_failed == 1  # kapitel-2 is a 404
        assert run.attachments_saved == 2
        assert run.root_page_title == 'Index'
        assert run.state['unavailable_pages'] == ['https://docs.example.com/handbuch/kapitel-2.html']
        states = db.scalars(select(ImportPageState).where(ImportPageState.source_id == source_id)).all()
        assert sorted(state.page_id for state in states) == [START, 'https://docs.example.com/handbuch/kapitel-1.html']
        assert all(state.collection_id == collection_id and state.job_id for state in states)
        sync = db.get(ImportSync, sync_id)
        assert sync.last_success_at is not None and sync.last_error is None and sync.root_page_title == 'Index'

    jobs = _jobs(run_id)
    by_url = {job.processing_info['settings']['import']['source_page_id']: job for job in jobs}
    root = by_url[START]
    assert root.status == JobStatus.FINISHED and root.original_filename == 'index.md'
    assert root.processing_info['execution'] == {'status': 'finished', 'engine': 'website-import'}
    provenance = root.processing_info['settings']['import']
    assert provenance['source_kind'] == 'website' and provenance['source_url'] == START
    assert provenance['fetched_at'].endswith('Z')
    assert provenance['legal_status'] == 'Zuletzt geändert durch Art. 2 G v. 1.2.2023 I 100'
    meta = _frontmatter(root.result_markdown)
    assert meta['title'] == 'Index' and meta['source'] == START and meta['site'] == 'docs.example.com'
    assert meta['fetched_at'] == provenance['fetched_at'] and meta['legal_status'] == provenance['legal_status']
    assert 'confluence_page_id' not in meta
    # Navigation chrome never reaches the markdown; the in-scope child link
    # is rewritten to the imported document at finalize.
    assert 'Andere' not in root.result_markdown
    child = by_url['https://docs.example.com/handbuch/kapitel-1.html']
    assert 'legal_status' not in _frontmatter(child.result_markdown)  # nothing stated on that page: nothing guessed
    with _db() as db:
        artifacts = db.scalars(select(JobArtifact).where(JobArtifact.job_id == root.id).order_by(JobArtifact.filename)).all()
        assert [(a.filename, a.kind, a.content_type) for a in artifacts] == [
            ('anleitung.pdf', 'attachment', 'application/pdf'),
            ('formular.docx', 'attachment', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        ]
        # Release-time provenance comes from the rows, not the editable YAML.
        from app.models.models import Collection
        collection = db.get(Collection, collection_id)
        job = db.get(Job, root.id)
        _snapshot, _digest, released = canonical_snapshot(db, job, collection)
        assert released['source_kind'] == 'website' and released['source_url'] == START
        assert released['fetched_at'] == provenance['fetched_at'] and released['legal_status'] == provenance['legal_status']
        assert released['uploaded_by'] == owner.username


def test_website_refresh_skips_unchanged_and_chains_changed_pages(sent, fetch):
    from app.workers.refresh_tasks import start_sync_run

    owner, source_id, sync_id, run_id, collection_id, run_options = _setup(legal_source=False)
    import_confluence(run_id, 0)
    _drain(sent)
    first_jobs = {job.processing_info['settings']['import']['source_page_id']: job for job in _jobs(run_id)}

    with _db() as db:
        sync = db.get(ImportSync, sync_id)
        refresh = start_sync_run(db, sync)
        assert refresh is not None and refresh.kind == 'website' and refresh.options['is_refresh'] is True
        refresh_id = refresh.id
    sent.clear()
    import_confluence(refresh_id, 0)
    _drain(sent)
    with _db() as db:
        run = db.get(ImportRun, refresh_id)
        assert run.status == ImportRunStatus.FINISHED and run.pages_imported == 0  # all unchanged
    assert _jobs(refresh_id) == []

    # The chapter changes upstream: only it is re-imported, as version 2 of its predecessor.
    child_url = 'https://docs.example.com/handbuch/kapitel-1.html'
    fetch.pages[child_url] = '<html><title>K1</title><body><main><p>Eins, neu</p></main></body></html>'
    with _db() as db:
        second = start_sync_run(db, db.get(ImportSync, sync_id))
        second_id = second.id
    sent.clear()
    import_confluence(second_id, 0)
    _drain(sent)
    with _db() as db:
        assert db.get(ImportRun, second_id).pages_imported == 1
    [changed] = _jobs(second_id)
    assert changed.processing_info['settings']['import']['source_page_id'] == child_url
    assert changed.document_version == 2 and changed.previous_job_id == first_jobs[child_url].id
    assert 'Eins, neu' in changed.result_markdown


def test_website_run_fails_cleanly_when_start_page_is_gone(sent, fetch):
    owner, source_id, sync_id, run_id, collection_id, _ = _setup()
    with _db() as db:
        run = db.get(ImportRun, run_id)
        run.scope_value = 'https://docs.example.com/handbuch/fehlt.html'
        run.state = {'frontier': [[run.scope_value, 0]], 'visited': {}, 'errors': []}
        db.commit()
    import_confluence(run_id, 0)
    _drain(sent)
    with _db() as db:
        run = db.get(ImportRun, run_id)
        assert run.status == ImportRunStatus.FINISHED and run.pages_imported == 0 and run.pages_failed == 1
        assert 'HTTP 404' in run.state['errors'][0]['error'] and 'page not found' in run.state['errors'][0]['error']
