"""Disaster-recovery admin API over the app/services/backup.py engine.
Mirrors app/api/technical_identities.py's shape: one `router` gated by
`require_admin` + `origin_guard` at the router level.

Built for several API replicas without shared storage:

- Export: POST /exports streams the archive straight into the response,
  generated from one consistent database snapshot. No file is kept on any
  pod, so there is nothing to download later from a different replica.
  The BackupRun row records the run for the history.
- Import: the uploaded archive is spooled to this pod's scratch space
  (BACKUP_DIR) and imported by a background thread on the same pod; status
  and result land in the BackupRun row, readable from every replica. Large
  archives (migrations between installations) belong in the CLI
  (`python -m app.cli backup import`), run inside a pod or as a Job.

Live import progress is kept in-memory (`_live_progress`, keyed by run id):
import_backup keeps its wipe+insert inside ONE transaction, and a progress
write against the same database from that thread would deadlock on SQLite's
file lock. GET /runs overlays it for a RUNNING run when the poll reaches the
pod doing the import; elsewhere only the status is visible.

Single active run: the check-then-insert runs under a PostgreSQL advisory
lock (all replicas) plus a per-process lock (SQLite, tests). A run whose
pod died never finishes; after backup_run_stale_seconds it is marked
failed so it stops blocking new runs.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import SessionLocal, get_db
from app.models.models import BackupRun, BackupRunKind, BackupRunStatus, User
from app.schemas.backup import BackupExportRequest, BackupRunListResponse, BackupRunResponse, TargetStateResponse
from app.services.backup import (
    BackupError,
    backups_dir,
    import_backup,
    inspect_backup,
    iter_export_backup,
    target_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix='/api/v1/admin/backup',
    tags=['backup-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)

_run_lock = threading.Lock()
# Fixed advisory-lock key for "at most one backup run" across replicas.
_RUN_LOCK_KEY = 7302000101
_ACTIVE_RUN_DETAIL = (
    'Es läuft bereits eine Sicherung oder Wiederherstellung. Bitte warten Sie, bis dieser Vorgang abgeschlossen ist.'
)

# See module docstring's "Progress is kept in-memory" section.
_progress_lock = threading.Lock()
_live_progress: dict[str, dict] = {}


def _active_run(db: Session) -> BackupRun | None:
    return db.scalar(
        select(BackupRun).where(BackupRun.status.in_([BackupRunStatus.QUEUED, BackupRunStatus.RUNNING]))
    )


def _fail_stale_runs(db: Session) -> None:
    """A run whose pod died never reaches FINISHED/FAILED by itself."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.backup_run_stale_seconds)
    stale = db.scalars(
        select(BackupRun)
        .where(BackupRun.status.in_([BackupRunStatus.QUEUED, BackupRunStatus.RUNNING]))
        .where(BackupRun.created_at < cutoff)
    ).all()
    for run in stale:
        run.status = BackupRunStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = 'Der Vorgang wurde nicht abgeschlossen (Pod beendet?).'
    db.flush()  # the session does not autoflush; _active_run must see this


def _start_run(db: Session, run: BackupRun) -> BackupRun:
    """Insert `run` unless another run is active -- atomically across
    replicas (PostgreSQL advisory lock) and threads (_run_lock)."""
    with _run_lock:
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _RUN_LOCK_KEY})
        _fail_stale_runs(db)
        if _active_run(db) is not None:
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_ACTIVE_RUN_DETAIL)
        db.add(run)
        db.commit()
        db.refresh(run)
    return run


def _set_live_progress(run_id: str, table: str, rows_done: int) -> None:
    with _progress_lock:
        _live_progress[run_id] = {'table': table, 'rows_done': rows_done}


def _pop_live_progress(run_id: str) -> dict | None:
    with _progress_lock:
        return _live_progress.pop(run_id, None)


def _serialize_run(run: BackupRun) -> BackupRunResponse:
    response = BackupRunResponse.model_validate(run)
    if run.status == BackupRunStatus.RUNNING:
        with _progress_lock:
            live = _live_progress.get(run.id)
        if live is not None:
            response.progress = live
    return response


def _fail_run(db: Session, run_id: str, exc: Exception) -> None:
    db.rollback()
    run = db.get(BackupRun, run_id)
    if run is not None:
        run.status = BackupRunStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = exc.detail if isinstance(exc, BackupError) else str(exc)
        db.commit()


# --- Background thread runners ----------------------------------------------

def _stream_export(run_id: str, passphrase: str):
    """Generate the archive into the response. Runs after the request's own
    session is gone, so it opens its own; the run row is updated through a
    separate session once the snapshot transaction has ended."""
    db = SessionLocal()
    manifest: dict = {}
    size_bytes = 0
    try:
        for chunk in iter_export_backup(db, passphrase=passphrase, manifest_out=manifest):
            size_bytes += len(chunk)
            yield chunk
        db.rollback()  # end the read-only snapshot
        with SessionLocal() as run_db:
            run = run_db.get(BackupRun, run_id)
            if run is not None:
                run.status = BackupRunStatus.FINISHED
                run.finished_at = datetime.now(timezone.utc)
                run.size_bytes = size_bytes
                run.report = {'tables': manifest.get('tables', {})}
                run_db.commit()
    except BaseException as exc:  # GeneratorExit included: the download was aborted
        db.rollback()
        if not isinstance(exc, GeneratorExit):
            logger.exception('backup export run %s failed', run_id)
        reason = exc if isinstance(exc, Exception) else RuntimeError('Der Download wurde abgebrochen.')
        with SessionLocal() as run_db:
            _fail_run(run_db, run_id, reason)
        raise
    finally:
        db.close()


def _run_import(run_id: str, path: Path, passphrase: str, importing_admin_id: str, force: bool) -> None:
    db = SessionLocal()
    try:
        run = db.get(BackupRun, run_id)
        if run is None:
            return
        run.status = BackupRunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        def progress_cb(table: str, rows_done: int) -> None:
            _set_live_progress(run_id, table, rows_done)

        report = import_backup(
            db, path=path, passphrase=passphrase, importing_admin_id=importing_admin_id, force=force,
            progress_cb=progress_cb,
        )
        db.commit()

        run = db.get(BackupRun, run_id)
        run.status = BackupRunStatus.FINISHED
        run.finished_at = datetime.now(timezone.utc)
        run.report = report
        run.progress = _pop_live_progress(run_id) or {}
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- any failure must still flip the run to FAILED
        logger.exception('backup import run %s failed', run_id)
        _fail_run(db, run_id, exc)
    finally:
        _pop_live_progress(run_id)
        path.unlink(missing_ok=True)
        db.close()


# --- Routes -------------------------------------------------------------

@router.post('/exports')
def create_export(
    payload: BackupExportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> StreamingResponse:
    """Stream a new archive straight to the caller (see module docstring)."""
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    file_name = f'{timestamp}-{run_id[:8]}.weave-backup.tar.gz'
    _start_run(db, BackupRun(
        id=run_id,
        kind=BackupRunKind.EXPORT,
        status=BackupRunStatus.RUNNING,
        created_by=user.id,
        file_name=file_name,
        started_at=datetime.now(timezone.utc),
    ))
    # Contains a decrypt-with-passphrase-able copy of every secret in the
    # system -- never cacheable by an intermediary or the browser's own
    # disk cache.
    return StreamingResponse(
        _stream_export(run_id, payload.passphrase),
        media_type='application/gzip',
        headers={
            'Content-Disposition': f'attachment; filename="{file_name}"',
            'Cache-Control': 'no-store',
        },
    )


@router.get('/runs', response_model=BackupRunListResponse)
def list_runs(db: Session = Depends(get_db)) -> BackupRunListResponse:
    runs = db.scalars(select(BackupRun).order_by(BackupRun.created_at.desc())).all()
    return BackupRunListResponse(runs=[_serialize_run(run) for run in runs])


@router.get('/runs/{run_id}', response_model=BackupRunResponse)
def get_run(run_id: str, db: Session = Depends(get_db)) -> BackupRunResponse:
    run = db.get(BackupRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Lauf nicht gefunden.')
    return _serialize_run(run)


@router.delete('/exports/{run_id}', response_model=BackupRunResponse)
def delete_export(run_id: str, db: Session = Depends(get_db)) -> BackupRunResponse:
    run = db.get(BackupRun, run_id)
    if run is None or run.kind != BackupRunKind.EXPORT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sicherung nicht gefunden.')
    if run.status in (BackupRunStatus.QUEUED, BackupRunStatus.RUNNING):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Eine laufende Sicherung kann nicht gelöscht werden.')

    # Only the history entry: the archive itself was streamed, never stored.
    response = BackupRunResponse.model_validate(run)
    db.delete(run)
    db.commit()
    return response


@router.get('/target-state', response_model=TargetStateResponse)
def get_target_state(db: Session = Depends(get_db)) -> TargetStateResponse:
    return TargetStateResponse(**target_state(db))


@router.post('/imports', status_code=status.HTTP_202_ACCEPTED, response_model=BackupRunResponse)
def create_import(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> BackupRun:
    incoming_dir = backups_dir() / 'incoming'
    incoming_dir.mkdir(parents=True, exist_ok=True)
    incoming_path = incoming_dir / f'{uuid.uuid4()}.weave-backup.tar.gz'

    # 1. Spool the upload to disk (never fully in memory -- a full-instance
    # archive can be far larger than a single document upload), bounded by
    # its own cap (settings.backup_max_upload_bytes; see app/core/config.py).
    total_bytes = 0
    try:
        with incoming_path.open('wb') as handle:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.backup_max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail='Das Archiv ist zu groß.'
                    )
                handle.write(chunk)
    except HTTPException:
        incoming_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        incoming_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Der Upload ist fehlgeschlagen.') from exc

    # 2. Validate format + passphrase synchronously, BEFORE touching any
    # data or starting a background run -- fast, specific failure instead
    # of a run the admin has to poll just to learn the passphrase was wrong.
    try:
        inspect_backup(incoming_path, passphrase)
    except BackupError as exc:
        incoming_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail) from exc
    except Exception as exc:
        incoming_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Das Archiv ist beschädigt oder kein gültiges Sicherungsarchiv.',
        ) from exc

    if not force:
        state = target_state(db)
        if not state['fresh']:
            incoming_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Das Ziel enthält bereits Daten. Setzen Sie "Vorhandene Daten überschreiben", um fortzufahren.',
            )
    try:
        run = _start_run(db, BackupRun(
            kind=BackupRunKind.IMPORT,
            status=BackupRunStatus.QUEUED,
            created_by=user.id,
            file_name=incoming_path.name,
            size_bytes=total_bytes,
        ))
    except HTTPException:
        incoming_path.unlink(missing_ok=True)
        raise

    threading.Thread(
        target=_run_import, args=(run.id, incoming_path, passphrase, user.id, force), daemon=True
    ).start()
    return run
