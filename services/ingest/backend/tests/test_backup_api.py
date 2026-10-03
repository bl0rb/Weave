"""Tests for the disaster-recovery admin API (app/api/backup.py).

The background export/import threads open their own SessionLocal() (see
app/api/backup.py's module docstring) -- app.database.session.SessionLocal
is bound to settings.database_url at import time, which is NOT the sqlite
test.db conftest.py's `get_db` override points requests at. `_use_test_db`
below monkeypatches app.api.backup's own module-level `SessionLocal` name
(the "module-object access ... keeps them monkeypatchable in tests"
discipline import_routes.py already documents) so background runs land in
the same database the TestClient's requests read back from.

Polling for a run to finish reads BackupRun directly via TestingSessionLocal
rather than through the authenticated API: a force-import wipes and
re-populates `users`, which -- with SQLite FK enforcement on (see
conftest.py) -- cascades to `sessions` and invalidates the very session
cookie the polling client would otherwise be using.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.models import BackupRun, BackupRunStatus, UserRole
from app.services import backup as backup_engine
from conftest import TestingSessionLocal, create_test_user, login_as

import app.api.backup as backup_api


@pytest.fixture(autouse=True)
def _use_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(backup_api, 'SessionLocal', TestingSessionLocal)
    # Isolated, disposable archive location per test -- backups_dir() (see
    # app/services/backup.py) is settings.backup_dir, covering both the
    # export and the incoming-import archives.
    monkeypatch.setattr(settings, 'backup_dir', tmp_path / 'backups')


def _admin(prefix: str):
    unique = f'{prefix}-{uuid.uuid4().hex[:8]}'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.ADMIN)
    return login_as(user.username), user


def _wait_for_run(run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with TestingSessionLocal() as db:
            run = db.get(BackupRun, run_id)
            if run is not None and run.status in (BackupRunStatus.FINISHED, BackupRunStatus.FAILED):
                return {
                    'status': run.status,
                    'report': run.report,
                    'error_message': run.error_message,
                    'file_name': run.file_name,
                }
        time.sleep(0.02)
    raise AssertionError(f'run {run_id} did not finish within {timeout}s')


def _build_archive(passphrase: str, tmp_path: Path) -> Path:
    """Build a valid archive of the current DB state directly through the
    engine (already covered by tests/test_backup_engine.py) -- bypasses the
    API/background-thread path so import tests can set up a known-good
    fixture archive without waiting on an export run."""
    out_path = tmp_path / f'seed-{uuid.uuid4().hex[:8]}.weave-backup.tar.gz'
    with TestingSessionLocal() as db:
        backup_engine.export_backup(db, passphrase=passphrase, out_path=out_path)
    return out_path


def test_non_admin_cannot_start_export():
    user = create_test_user(username=f'backup-user-{uuid.uuid4().hex[:8]}', email=f'u-{uuid.uuid4().hex[:8]}@example.com')
    client = login_as(user.username)
    resp = client.post('/api/v1/admin/backup/exports', json={'passphrase': 'a-decent-passphrase-1234'})
    assert resp.status_code == 403, resp.text


def test_export_streams_the_archive_and_records_the_run(tmp_path):
    admin, _ = _admin('backup-export')

    resp = admin.post('/api/v1/admin/backup/exports', json={'passphrase': 'a-decent-passphrase-1234'})
    assert resp.status_code == 200, resp.text
    assert resp.headers['cache-control'] == 'no-store'
    assert resp.headers['content-disposition'].startswith('attachment; filename="')
    assert resp.content[:2] == b'\x1f\x8b'  # gzip magic bytes

    # The streamed archive is a complete, importable backup.
    archive = tmp_path / 'streamed.weave-backup.tar.gz'
    archive.write_bytes(resp.content)
    manifest = backup_engine.inspect_backup(archive, 'a-decent-passphrase-1234')
    assert manifest['tables']['jobs'] >= 0

    listed = admin.get('/api/v1/admin/backup/runs')
    assert listed.status_code == 200, listed.text
    run = next(row for row in listed.json()['runs'] if row['kind'] == 'export' and row['size_bytes'] == len(resp.content))
    assert run['status'] == 'finished'
    assert 'jobs' in run['report']['tables']

    fetched = admin.get(f"/api/v1/admin/backup/runs/{run['id']}")
    assert fetched.status_code == 200, fetched.text

    # Nothing was kept on the pod: the old download route is gone, delete
    # only removes the history entry.
    assert admin.get(f"/api/v1/admin/backup/exports/{run['id']}/download").status_code in (404, 405)
    deleted = admin.delete(f"/api/v1/admin/backup/exports/{run['id']}")
    assert deleted.status_code == 200, deleted.text
    assert not list((settings.backup_dir).rglob('*.tar.gz')) if settings.backup_dir.exists() else True


def test_a_run_whose_pod_died_stops_blocking_new_runs():
    admin, _ = _admin('backup-stale')
    with TestingSessionLocal() as db:
        lost = BackupRun(
            kind='export', status=BackupRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=settings.backup_run_stale_seconds + 60),
        )
        db.add(lost)
        db.commit()
        lost_id = lost.id

    resp = admin.post('/api/v1/admin/backup/exports', json={'passphrase': 'a-decent-passphrase-1234'})
    assert resp.status_code == 200, resp.text
    with TestingSessionLocal() as db:
        lost = db.get(BackupRun, lost_id)
        assert lost.status == BackupRunStatus.FAILED
        assert 'nicht abgeschlossen' in lost.error_message


def test_second_concurrent_run_is_refused():
    admin, _ = _admin('backup-conflict')
    with TestingSessionLocal() as db:
        active = BackupRun(kind='export', status=BackupRunStatus.RUNNING)
        db.add(active)
        db.commit()
        active_id = active.id

    try:
        resp = admin.post('/api/v1/admin/backup/exports', json={'passphrase': 'a-decent-passphrase-1234'})
        assert resp.status_code == 409, resp.text
    finally:
        # This run was never "finished" by a real background thread --
        # clear it so it doesn't block every later test in this module from
        # starting its own run (BackupRun rows persist across tests, unlike
        # tests/test_backup_engine.py's dedicated per-test sqlite files).
        with TestingSessionLocal() as db:
            db.get(BackupRun, active_id).status = BackupRunStatus.FINISHED
            db.commit()


def test_import_wrong_passphrase_rejected_before_any_write(tmp_path):
    admin, _ = _admin('backup-wrongpass')
    archive_path = _build_archive('the-correct-passphrase-1', tmp_path)

    runs_before = admin.get('/api/v1/admin/backup/runs').json()['runs']

    resp = admin.post(
        '/api/v1/admin/backup/imports',
        files={'file': ('backup.weave-backup.tar.gz', archive_path.read_bytes(), 'application/gzip')},
        data={'passphrase': 'totally-the-wrong-passphrase', 'force': 'true'},
    )
    assert resp.status_code == 422, resp.text
    assert 'Passphrase' in resp.json()['detail']

    runs_after = admin.get('/api/v1/admin/backup/runs').json()['runs']
    assert len(runs_after) == len(runs_before)

    incoming_dir = settings.backup_dir / 'incoming'
    assert not incoming_dir.exists() or not list(incoming_dir.iterdir())


def test_import_refused_when_target_not_fresh(tmp_path):
    admin, _ = _admin('backup-notfresh')
    # A second user makes `users` non-fresh (see target_state's own "more
    # than the bootstrap admin" rule in app/services/backup.py).
    create_test_user(username=f'backup-extra-{uuid.uuid4().hex[:8]}', email=f'extra-{uuid.uuid4().hex[:8]}@example.com')
    archive_path = _build_archive('the-correct-passphrase-2', tmp_path)

    resp = admin.post(
        '/api/v1/admin/backup/imports',
        files={'file': ('backup.weave-backup.tar.gz', archive_path.read_bytes(), 'application/gzip')},
        data={'passphrase': 'the-correct-passphrase-2'},
    )
    assert resp.status_code == 409, resp.text
    assert 'überschreiben' in resp.json()['detail']


def test_import_with_force_overwrites_and_reports(tmp_path):
    admin, _ = _admin('backup-force')
    archive_path = _build_archive('the-correct-passphrase-3', tmp_path)

    resp = admin.post(
        '/api/v1/admin/backup/imports',
        files={'file': ('backup.weave-backup.tar.gz', archive_path.read_bytes(), 'application/gzip')},
        data={'passphrase': 'the-correct-passphrase-3', 'force': 'true'},
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()['id']
    assert resp.json()['kind'] == 'import'

    finished = _wait_for_run(run_id, timeout=15.0)
    assert finished['status'] == BackupRunStatus.FINISHED, finished
    report = finished['report']
    assert report is not None
    assert 'users' in report['tables']
    assert report['requires_relogin'] is True
    assert 'indexing_note' in report
