"""Tests for the manual 'Index aus Freigaben neu aufbauen' admin action
(app/api/knowledge_maintenance.py)."""

import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.models.models import (
    BackupRun,
    BackupRunKind,
    BackupRunStatus,
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

    with TestingSessionLocal() as db:
        before = db.query(DocumentRelease).count()
        before_withdrawn = db.query(KnowledgeWithdrawal).count()

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
