"""Disaster-recovery admin API: kicks off export/import runs of the
app/services/backup.py engine as in-process background threads (no Celery
worker is guaranteed to run -- see that module's own docstring), tracks
their progress in a BackupRun row, and streams the resulting archive back
down. Mirrors app/api/technical_identities.py's shape: one `router` gated
by `require_admin` + `origin_guard` at the router level.

Progress is kept in-memory (`_live_progress`, keyed by run id, guarded by
`_progress_lock`) rather than written to the BackupRun row as it happens:
export_backup streams table rows via one open `yield_per` cursor per table,
and import_backup keeps its wipe+insert entirely inside ONE transaction the
caller commits only at the very end (see that module's docstrings) -- a
progress callback that runs on the very same thread, synchronously, in the
middle of either of those, cannot safely open a second DB write against the
same row without risking a self-deadlock (a second SQLite connection
blocking forever on the file-level write lock the first, still-open
transaction holds, on the one thread that would have to finish that first
transaction to release it). GET /runs and GET /runs/{id} overlay the
in-memory value onto the persisted row for any run that is still RUNNING;
the persisted `progress` column itself is only ever written at the very
start (empty) and the very end (the last known value) of a run.

Single-active-run rule: `_run_lock` (a plain, per-process threading.Lock,
same idiom as import_routes.py's `_probe_semaphore`) makes the
"is a run already queued/running" check and the new BackupRun insert one
atomic step, so two concurrent POSTs can't both pass the check and both
start a run. It does not, and cannot, cover a second API process/replica --
this feature has no such deployment today (see the harness's own "no
Celery worker" note), so a DB-level advisory lock would be unused
complexity for a single-process admin operation.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import SessionLocal, get_db
from app.models.models import BackupRun, BackupRunKind, BackupRunStatus, User
from app.schemas.backup import BackupExportRequest, BackupRunListResponse, BackupRunResponse, TargetStateResponse
from app.services.backup import (
    BackupError,
    backups_dir,
    export_backup,
    import_backup,
    inspect_backup,
    target_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix='/api/v1/admin/backup',
    tags=['backup-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)

_run_lock = threading.Lock()

# See module docstring's "Progress is kept in-memory" section.
_progress_lock = threading.Lock()
_live_progress: dict[str, dict] = {}


def _active_run(db: Session) -> BackupRun | None:
    return db.scalar(
        select(BackupRun).where(BackupRun.status.in_([BackupRunStatus.QUEUED, BackupRunStatus.RUNNING]))
    )


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

def _run_export(run_id: str, passphrase: str) -> None:
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

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        out_path = backups_dir() / f'{timestamp}-{run_id[:8]}.weave-backup.tar.gz'
        manifest = export_backup(db, passphrase=passphrase, out_path=out_path, progress_cb=progress_cb)

        run = db.get(BackupRun, run_id)
        run.status = BackupRunStatus.FINISHED
        run.finished_at = datetime.now(timezone.utc)
        run.file_name = out_path.name
        run.size_bytes = out_path.stat().st_size
        run.report = {'tables': manifest.get('tables', {})}
        run.progress = _pop_live_progress(run_id) or {}
        db.commit()
    except Exception as exc:  # noqa: BLE001 -- any failure must still flip the run to FAILED
        logger.exception('backup export run %s failed', run_id)
        _fail_run(db, run_id, exc)
    finally:
        _pop_live_progress(run_id)
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

@router.post('/exports', status_code=status.HTTP_202_ACCEPTED, response_model=BackupRunResponse)
def create_export(
    payload: BackupExportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> BackupRun:
    with _run_lock:
        if _active_run(db) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Es läuft bereits eine Sicherung oder Wiederherstellung. Bitte warten Sie, bis dieser Vorgang abgeschlossen ist.',
            )
        run = BackupRun(kind=BackupRunKind.EXPORT, status=BackupRunStatus.QUEUED, created_by=user.id)
        db.add(run)
        db.commit()
        db.refresh(run)

    threading.Thread(target=_run_export, args=(run.id, payload.passphrase), daemon=True).start()
    return run


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


@router.get('/exports/{run_id}/download')
def download_export(run_id: str, db: Session = Depends(get_db)) -> FileResponse:
    run = db.get(BackupRun, run_id)
    if run is None or run.kind != BackupRunKind.EXPORT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sicherung nicht gefunden.')
    if run.status != BackupRunStatus.FINISHED or not run.file_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Die Sicherung ist noch nicht abgeschlossen.')
    file_path = backups_dir() / run.file_name
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Die Archivdatei fehlt auf dem Server.')
    # Contains a decrypt-with-passphrase-able copy of every secret in the
    # system -- never cacheable by an intermediary or the browser's own
    # disk cache.
    return FileResponse(
        file_path, media_type='application/gzip', filename=run.file_name, headers={'Cache-Control': 'no-store'}
    )


@router.delete('/exports/{run_id}', response_model=BackupRunResponse)
def delete_export(run_id: str, db: Session = Depends(get_db)) -> BackupRunResponse:
    run = db.get(BackupRun, run_id)
    if run is None or run.kind != BackupRunKind.EXPORT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sicherung nicht gefunden.')
    if run.status in (BackupRunStatus.QUEUED, BackupRunStatus.RUNNING):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Eine laufende Sicherung kann nicht gelöscht werden.')

    response = BackupRunResponse.model_validate(run)
    if run.file_name:
        (backups_dir() / run.file_name).unlink(missing_ok=True)
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

    with _run_lock:
        if not force:
            state = target_state(db)
            if not state['fresh']:
                incoming_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='Das Ziel enthält bereits Daten. Setzen Sie "Vorhandene Daten überschreiben", um fortzufahren.',
                )
        if _active_run(db) is not None:
            incoming_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Es läuft bereits eine Sicherung oder Wiederherstellung. Bitte warten Sie, bis dieser Vorgang abgeschlossen ist.',
            )

        run = BackupRun(
            kind=BackupRunKind.IMPORT,
            status=BackupRunStatus.QUEUED,
            created_by=user.id,
            file_name=incoming_path.name,
            size_bytes=total_bytes,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    threading.Thread(
        target=_run_import, args=(run.id, incoming_path, passphrase, user.id, force), daemon=True
    ).start()
    return run
