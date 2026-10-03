"""Tests for the manual 'Index aus Freigaben neu aufbauen' admin action
(app/api/knowledge_maintenance.py)."""

import uuid
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.models.models import (
    BackupRun,
    BackupRunKind,
    BackupRunStatus,
    Collection,
    CollectionSlugTombstone,
    DocumentRelease,
    Job,
    JobStatus,
    KnowledgeWithdrawal,
    UserRole,
)
from conftest import TestingSessionLocal, create_test_user, login_as

BASE = '/api/v1/admin/knowledge'


def _admin(prefix: str):
    unique = f'{prefix}-{uuid.uuid4().hex[:8]}'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.ADMIN)
    return login_as(user.username), user


def _configure_publication(monkeypatch):
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'publication-secret')


def _make_job_and_release(db, *, owner_id: str, status: str = 'sent', withdrawn: bool = False) -> str:
    job = Job(
        original_filename='doc.pdf', upload_path='/tmp/doc.pdf', status=JobStatus.FINISHED, owner_id=owner_id,
    )
    db.add(job)
    db.flush()
    release = DocumentRelease(
        job_id=job.id, owner_id=owner_id, markdown_snapshot='snap', markdown_sha256='a' * 64,
        payload={'event': 'document.released', 'job_id': job.id}, status=status, attempts=1,
    )
    db.add(release)
    if withdrawn:
        db.add(KnowledgeWithdrawal(job_id=job.id, status='sent'))
    db.commit()
    return job.id


def test_rebuild_requeues_non_withdrawn_releases_only(monkeypatch):
    admin, user = _admin('rebuild-admin')
    _configure_publication(monkeypatch)

    with TestingSessionLocal() as db:
        live_job_id = _make_job_and_release(db, owner_id=user.id, status='failed', withdrawn=False)
        withdrawn_job_id = _make_job_and_release(db, owner_id=user.id, status='failed', withdrawn=True)

    response = admin.post(f'{BASE}/rebuild')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['requeued'] == 1
    assert body['worker_required'] is True

    with TestingSessionLocal() as db:
        live = db.query(DocumentRelease).filter_by(job_id=live_job_id).one()
        withdrawn = db.query(DocumentRelease).filter_by(job_id=withdrawn_job_id).one()
        assert live.status == 'pending'
        assert withdrawn.status == 'failed'  # untouched -- withdrawn releases are never requeued


def test_rebuild_503_when_publication_not_configured(monkeypatch):
    admin, _ = _admin('rebuild-unconfigured-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', '')

    response = admin.post(f'{BASE}/rebuild')
    assert response.status_code == 503, response.text


def test_rebuild_409_while_import_is_running(monkeypatch):
    admin, user = _admin('rebuild-conflict-admin')
    _configure_publication(monkeypatch)

    with TestingSessionLocal() as db:
        run = BackupRun(kind=BackupRunKind.IMPORT, status=BackupRunStatus.RUNNING, created_by=user.id)
        db.add(run)
        db.commit()
        run_id = run.id

    try:
        response = admin.post(f'{BASE}/rebuild')
        assert response.status_code == 409, response.text
    finally:
        # This is the shared, process-wide test.db (see conftest.py) -- a
        # RUNNING BackupRun left behind here would make every later test's
        # own "is a backup run already active?" check (app/api/backup.py's
        # _active_run) see a phantom conflict, so clean it up regardless of
        # the assertion outcome.
        with TestingSessionLocal() as db:
            db.query(BackupRun).filter_by(id=run_id).delete()
            db.commit()


def test_rebuild_denies_non_admin():
    unique = 'rebuild-nonadmin'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.USER)
    client = login_as(user.username)
    response = client.post(f'{BASE}/rebuild')
    assert response.status_code == 403


def test_rebuild_status_reports_counts(monkeypatch):
    admin, user = _admin('rebuild-status-admin')

    # Baseline from the endpoint itself: counting KnowledgeWithdrawal rows
    # directly also picks up withdrawals other tests left without a release,
    # which the endpoint (releases whose job was withdrawn) doesn't count.
    baseline = admin.get(f'{BASE}/rebuild-status')
    assert baseline.status_code == 200, baseline.text
    before = baseline.json()['total_releases']
    before_withdrawn = baseline.json()['withdrawn']

    with TestingSessionLocal() as db:
        _make_job_and_release(db, owner_id=user.id, status='sent', withdrawn=False)
        _make_job_and_release(db, owner_id=user.id, status='pending', withdrawn=False)
        _make_job_and_release(db, owner_id=user.id, status='failed', withdrawn=False)
        _make_job_and_release(db, owner_id=user.id, status='sent', withdrawn=True)

    response = admin.get(f'{BASE}/rebuild-status')
    assert response.status_code == 200, response.text
    body = response.json()
    # Other tests in this module (and the shared conftest.py database) may
    # have left their own DocumentRelease/KnowledgeWithdrawal rows behind --
    # assert the DELTA this test's own four releases produced, not an
    # absolute total.
    assert body['total_releases'] - before == 4
    assert body['withdrawn'] - before_withdrawn == 1
    assert body['pending'] >= 1
    assert body['sent'] >= 2
    assert body['failed'] >= 1
    assert body['last_sent_at'] is not None


def test_rebuild_status_denies_non_admin():
    unique = 'rebuild-status-nonadmin'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.USER)
    client = login_as(user.username)
    response = client.get(f'{BASE}/rebuild-status')
    assert response.status_code == 403


# --- 'Verwaiste Dokumente zurückziehen' (POST /orphans) ---------------------


def _fake_knowledge(monkeypatch, documents: list[dict], *, fail: bool = False) -> None:
    """Serves `documents` as Knowledge's paged inventory (three per page, so
    paging is exercised) through the service's own httpx client seam."""
    from app.services import knowledge_orphans

    inventory = sorted(documents, key=lambda item: item['job_id'])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/internal/documents'
        assert request.headers['X-Weave-Reindex-Token'] == 'publication-secret'
        if fail:
            return httpx.Response(500)
        after = request.url.params.get('after', '')
        limit = int(request.url.params['limit'])
        page = [item for item in inventory if item['job_id'] > after][:limit]
        return httpx.Response(200, json={'items': page, 'next_after': page[-1]['job_id'] if len(page) == limit else None})

    monkeypatch.setattr(knowledge_orphans, 'PAGE_SIZE', 3)
    monkeypatch.setattr(knowledge_orphans, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def _orphan_fixture(owner_id: str) -> tuple[list[dict], dict[str, str]]:
    """Two documents Ingest still stands behind and five orphans, one per
    reason plus a missing job whose withdrawal is already pending."""
    unique = uuid.uuid4().hex[:8]
    live, gone, never = f'live-{unique}', f'gone-{unique}', f'never-{unique}'
    with TestingSessionLocal() as db:
        db.add(Collection(slug=live, name='Live'))
        db.add(CollectionSlugTombstone(slug=gone))

        def job() -> str:
            row = Job(original_filename='doc.pdf', upload_path='/tmp/doc.pdf', status=JobStatus.FINISHED, owner_id=owner_id)
            db.add(row)
            db.flush()
            return row.id

        ids = {
            'live': job(), 'no_space': job(), 'in_gone_space': job(), 'in_unknown_space': job(),
            'withdrawn_but_present': job(), 'missing': str(uuid.uuid4()), 'missing_pending': str(uuid.uuid4()),
        }
        db.add(KnowledgeWithdrawal(job_id=ids['withdrawn_but_present'], status='sent', attempts=1))
        db.add(KnowledgeWithdrawal(job_id=ids['missing_pending']))
        db.commit()
    slugs = {
        'live': live, 'no_space': None, 'in_gone_space': gone, 'in_unknown_space': never,
        'withdrawn_but_present': live, 'missing': live, 'missing_pending': never,
    }
    documents = [{'job_id': ids[key], 'collection_slug': slugs[key], 'status': 'indexed'} for key in ids]
    return documents, ids


def _withdrawal_states(job_ids: dict[str, str]) -> dict[str, str | None]:
    with TestingSessionLocal() as db:
        return {key: getattr(db.get(KnowledgeWithdrawal, job_id), 'status', None) for key, job_id in job_ids.items()}


def test_orphans_dry_run_reports_without_queueing(monkeypatch):
    admin, user = _admin('orphans-dry-run-admin')
    _configure_publication(monkeypatch)
    documents, ids = _orphan_fixture(user.id)
    _fake_knowledge(monkeypatch, documents)

    response = admin.post(f'{BASE}/orphans', json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['dry_run'] is True
    assert body['scanned'] == 7
    assert body['orphans'] == 5
    assert body['by_reason'] == {
        'job_missing': 2, 'collection_deleted': 1, 'collection_unknown': 1, 'withdrawn': 1,
    }
    assert body['already_pending'] == 1
    assert body['queued'] == 0
    assert body['truncated'] is False
    reasons = {item['job_id']: (item['reason'], item['withdrawal']) for item in body['items']}
    assert reasons == {
        ids['missing']: ('job_missing', 'none'),
        ids['missing_pending']: ('job_missing', 'pending'),
        ids['in_gone_space']: ('collection_deleted', 'none'),
        ids['in_unknown_space']: ('collection_unknown', 'none'),
        ids['withdrawn_but_present']: ('withdrawn', 'sent'),
    }
    assert _withdrawal_states(ids) == {
        'live': None, 'no_space': None, 'in_gone_space': None, 'in_unknown_space': None,
        'withdrawn_but_present': 'sent', 'missing': None, 'missing_pending': 'pending',
    }


def test_orphans_apply_needs_the_reviewed_count_and_is_idempotent(monkeypatch):
    admin, user = _admin('orphans-apply-admin')
    _configure_publication(monkeypatch)
    documents, ids = _orphan_fixture(user.id)
    _fake_knowledge(monkeypatch, documents)

    assert admin.post(f'{BASE}/orphans', json={'dry_run': False}).status_code == 422
    stale = admin.post(f'{BASE}/orphans', json={'dry_run': False, 'expected_orphans': 4})
    assert stale.status_code == 409, stale.text
    assert _withdrawal_states(ids)['missing'] is None

    response = admin.post(f'{BASE}/orphans', json={'dry_run': False, 'expected_orphans': 5})
    assert response.status_code == 200, response.text
    assert response.json()['queued'] == 4
    assert _withdrawal_states(ids) == {
        'live': None, 'no_space': None, 'in_gone_space': 'pending', 'in_unknown_space': 'pending',
        'withdrawn_but_present': 'pending', 'missing': 'pending', 'missing_pending': 'pending',
    }

    # Knowledge still lists them until the publication tick delivers.
    again = admin.post(f'{BASE}/orphans', json={'dry_run': False, 'expected_orphans': 5})
    assert again.status_code == 200, again.text
    assert again.json()['queued'] == 0
    assert again.json()['already_pending'] == 5


def test_orphans_refuses_partial_inventory_and_unsafe_states(monkeypatch):
    admin, user = _admin('orphans-guard-admin')
    _configure_publication(monkeypatch)
    documents, ids = _orphan_fixture(user.id)

    _fake_knowledge(monkeypatch, documents, fail=True)
    response = admin.post(f'{BASE}/orphans', json={'dry_run': False, 'expected_orphans': 5})
    assert response.status_code == 503, response.text
    assert _withdrawal_states(ids)['missing'] is None

    _fake_knowledge(monkeypatch, documents)
    with TestingSessionLocal() as db:
        run = BackupRun(kind=BackupRunKind.IMPORT, status=BackupRunStatus.RUNNING, created_by=user.id)
        db.add(run)
        db.commit()
        run_id = run.id
    try:
        assert admin.post(f'{BASE}/orphans', json={}).status_code == 409
    finally:
        with TestingSessionLocal() as db:
            db.query(BackupRun).filter_by(id=run_id).delete()
            db.commit()

    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')
    assert admin.post(f'{BASE}/orphans', json={}).status_code == 503


def test_orphans_denies_non_admin():
    unique = 'orphans-nonadmin'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.USER)
    assert login_as(user.username).post(f'{BASE}/orphans', json={}).status_code == 403
