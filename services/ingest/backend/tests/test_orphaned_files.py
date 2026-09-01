"""AUFGABE 2/Punkt 2: the orphaned-file audit.

Covers both layers:
  - app.services.storage.find_orphaned_files (the actual disk-vs-DB diff)
  - GET /api/v1/auth/admin/storage/orphaned-files (the admin-only report
    endpoint wrapping it -- auth gating, pagination, and the
    total_count/total_bytes full-scan totals)

Same tmp_path-based settings.uploads_dir/results_dir override as
test_api.py's uploads/results tests (e.g. test_markdown_browser_ignores_orphan_disk_files),
and the same create_test_user/login_as helpers test_job_authz.py and friends
use for a real, DB-backed admin session.
"""

import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.models import Job, JobStatus, UserRole
from app.services.storage import find_orphaned_files
from tests.conftest import BROWSER_HEADERS, TestingSessionLocal, create_test_user, login_as


def _db():
    return TestingSessionLocal()


def _make_job(*, upload_path: str, result_path: str | None = None) -> None:
    db = _db()
    try:
        db.add(
            Job(
                original_filename='referenced.pdf',
                upload_path=upload_path,
                upload_size_bytes=1,
                result_path=result_path,
                status=JobStatus.FINISHED,
            )
        )
        db.commit()
    finally:
        db.close()


# --- find_orphaned_files (service layer) -------------------------------------

def test_find_orphaned_files_flags_only_unreferenced_disk_files(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    referenced_upload = settings.uploads_dir / 'inbox' / 'job-1.pdf'
    referenced_upload.parent.mkdir(parents=True, exist_ok=True)
    referenced_upload.write_bytes(b'referenced upload bytes')

    referenced_result = settings.results_dir / 'inbox' / 'job-1.md'
    referenced_result.parent.mkdir(parents=True, exist_ok=True)
    referenced_result.write_text('# referenced', encoding='utf-8')

    _make_job(upload_path=str(referenced_upload.resolve()), result_path=str(referenced_result.resolve()))

    orphan_upload = settings.uploads_dir / 'inbox' / 'stray.pdf'
    orphan_upload.write_bytes(b'twelve bytes')  # 12 bytes

    orphan_result = settings.results_dir / 'inbox' / 'stray.md'
    orphan_result.write_text('# orphaned result', encoding='utf-8')

    db = _db()
    try:
        orphans = find_orphaned_files(db)
    finally:
        db.close()

    orphan_paths = {(item.kind, item.path) for item in orphans}
    assert ('upload', 'inbox/stray.pdf') in orphan_paths
    assert ('result', 'inbox/stray.md') in orphan_paths
    # The referenced files must never show up as orphaned.
    assert not any('job-1' in path for _, path in orphan_paths)

    upload_entry = next(item for item in orphans if item.path == 'inbox/stray.pdf')
    assert upload_entry.size_bytes == len(b'twelve bytes')
    assert upload_entry.modified_at.tzinfo is not None


def test_find_orphaned_files_returns_empty_when_storage_dirs_do_not_exist(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'never-created-uploads'
    settings.results_dir = tmp_path / 'never-created-results'

    db = _db()
    try:
        assert find_orphaned_files(db) == []
    finally:
        db.close()


def test_find_orphaned_files_ignores_directories(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'
    (settings.uploads_dir / 'inbox' / 'empty-subdir').mkdir(parents=True, exist_ok=True)
    settings.results_dir.mkdir(parents=True, exist_ok=True)

    db = _db()
    try:
        assert find_orphaned_files(db) == []
    finally:
        db.close()


# --- GET /auth/admin/storage/orphaned-files (endpoint) -----------------------

def test_orphaned_files_endpoint_requires_admin_role(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'
    user = create_test_user(username='plain-audit-user', email='plain-audit-user@example.com')
    client = login_as(user.username)

    resp = client.get('/api/v1/auth/admin/storage/orphaned-files')
    assert resp.status_code == 403


def test_orphaned_files_endpoint_requires_authentication(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'
    anon_client = TestClient(app, headers=BROWSER_HEADERS)

    resp = anon_client.get('/api/v1/auth/admin/storage/orphaned-files')
    assert resp.status_code == 401


def test_orphaned_files_endpoint_reports_size_and_age_and_never_deletes(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    referenced = settings.uploads_dir / 'inbox' / 'kept.pdf'
    referenced.parent.mkdir(parents=True, exist_ok=True)
    referenced.write_bytes(b'kept')
    _make_job(upload_path=str(referenced.resolve()))

    orphan = settings.uploads_dir / 'inbox' / 'orphan.pdf'
    orphan.write_bytes(b'0123456789')  # 10 bytes
    old_mtime = datetime.now(timezone.utc).timestamp() - 3600  # 1h old
    os.utime(orphan, (old_mtime, old_mtime))

    admin = create_test_user(
        username='storage-audit-admin', email='storage-audit-admin@example.com', role=UserRole.ADMIN
    )
    client = login_as(admin.username)

    resp = client.get('/api/v1/auth/admin/storage/orphaned-files')
    assert resp.status_code == 200
    body = resp.json()

    assert body['total_count'] == 1
    assert body['total_bytes'] == 10
    assert len(body['items']) == 1
    item = body['items'][0]
    assert item['kind'] == 'upload'
    assert item['path'] == 'inbox/orphan.pdf'
    assert item['size_bytes'] == 10
    assert item['age_seconds'] >= 3500  # ~1h, allowing for test runtime slack

    # Report-only: the file must still be sitting on disk afterwards.
    assert orphan.exists()
    assert referenced.exists()


def test_orphaned_files_endpoint_totals_are_not_truncated_by_pagination(tmp_path) -> None:
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    for i in range(3):
        (settings.uploads_dir / f'orphan-{i}.pdf').write_bytes(b'x' * (i + 1))

    admin = create_test_user(
        username='storage-audit-admin-2', email='storage-audit-admin-2@example.com', role=UserRole.ADMIN
    )
    client = login_as(admin.username)

    resp = client.get('/api/v1/auth/admin/storage/orphaned-files', params={'limit': 1})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body['items']) == 1
    assert body['total_count'] == 3
    assert body['total_bytes'] == 1 + 2 + 3
    # Biggest offender first.
    assert body['items'][0]['size_bytes'] == 3
