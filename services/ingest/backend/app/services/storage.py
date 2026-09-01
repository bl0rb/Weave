import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.models.models import Job

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.png', '.jpg', '.jpeg', '.eml'}
ALLOWED_MIME_TYPES = {
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
    'image/png',
    'image/jpeg',
    'message/rfc822',
}

_EXTENSION_TO_MIME_TYPES: dict[str, set[str]] = {
    '.pdf': {'application/pdf'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    '.pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
    '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
    '.xls': {'application/vnd.ms-excel'},
    '.png': {'image/png'},
    '.jpg': {'image/jpeg'},
    '.jpeg': {'image/jpeg'},
    '.eml': {'message/rfc822'},
}

_GENERIC_MIME_TYPES = {'', 'application/octet-stream', 'binary/octet-stream'}


def ensure_storage_dirs() -> None:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.results_dir.mkdir(parents=True, exist_ok=True)


def ensure_folder(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported file extension')
    return suffix


def _validate_mime(file: UploadFile, suffix: str) -> None:
    """Reject a declared MIME type that contradicts the file extension.

    The extension is the actual gate -- `_safe_suffix` has already rejected
    anything outside ALLOWED_EXTENSIONS, and this function cannot add much on
    top of it, because the client picks both values. What it does do is catch
    the obvious mismatch (a .pdf announced as image/png).

    Clients routinely send nothing useful here: curl, browser drag/drop and
    sync clients all send application/octet-stream, so a generic or missing
    type is accepted. A type whose top-level category matches the extension
    (image/jpg for .jpg) is accepted too -- that is a client quirk, not a
    contradiction.

    Neither check says anything about the actual bytes. What has to cope with
    hostile input is the parser layer downstream (pypdf, xlrd, PaddleOCR).
    """
    declared = (file.content_type or '').strip().lower()
    expected = _EXTENSION_TO_MIME_TYPES.get(suffix, set())

    if not declared or declared in _GENERIC_MIME_TYPES:
        return
    if declared in expected:
        return
    if expected and declared.split('/')[0] in {entry.split('/')[0] for entry in expected}:
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail='MIME type does not match file extension',
    )


def save_upload(file: UploadFile, storage_folder: str, file_id: str) -> tuple[str, str, bytes, int]:
    ensure_storage_dirs()
    suffix = _safe_suffix(file.filename or '')
    _validate_mime(file, suffix)

    folder_path = ensure_folder((settings.uploads_dir / storage_folder).resolve())
    target_path = folder_path / f'{file_id}{suffix}'

    total_bytes = 0
    payload = bytearray()
    oversized = False
    try:
        with target_path.open('wb') as handle:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_bytes:
                    oversized = True
                    break
                handle.write(chunk)
                payload.extend(chunk)
    except Exception:
        # e.g. client disconnect mid-stream: the closed handle has already
        # committed a partial object (on Mountpoint-for-S3, close() commits),
        # so remove it before propagating.
        target_path.unlink(missing_ok=True)
        raise

    # On Mountpoint-for-S3, close() is what commits the object, so the handle
    # must be closed (via the `with` block above) before we unlink the target.
    # Unlinking while the handle is still open would race the close, either
    # raising on the unlink or leaving a partial object behind after close.
    if oversized:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail='File too large')

    return str(target_path.resolve()), file_id, bytes(payload), total_bytes


def build_result_path(storage_folder: str, file_id: str) -> Path:
    folder_path = ensure_folder((settings.results_dir / storage_folder).resolve())
    return (folder_path / f'{file_id}.md').resolve()


def build_edited_result_path(storage_folder: str, file_id: str, version: int) -> Path:
    edited_dir = ensure_folder((settings.results_dir / storage_folder / 'edited').resolve())
    return (edited_dir / f'{file_id}.v{version}.md').resolve()


# --- Orphaned-file audit (GET /auth/admin/storage/orphaned-files) ------------
#
# Report-only: this section only ever reads the filesystem/DB, never deletes
# or moves anything. Disk under uploads_dir/results_dir is a best-effort
# cache the worker rehydrates from the DB on demand (see
# app/workers/tasks.py's _resolve_upload_path/_resolve_result_path) -- the DB
# row (Job.upload_path/result_path) is the source of truth for what SHOULD
# exist, so "orphaned" here means exactly "on disk, but no Job row points at
# it any more" (e.g. a job whose upload_path/result_path was later
# reassigned, or a row that was deleted outright while its file survived).
#
# JobMarkdownVersion (editor save history) and JobArtifact (imported inline
# images/attachments) are deliberately NOT consulted below: both replaced an
# earlier on-disk representation and now live entirely in the DB as a
# text/blob column (see their class docstrings in app/models/models.py) --
# there is no disk path to protect for either any more. Any leftover file
# still sitting under the old on-disk layout those replaced (e.g.
# build_edited_result_path's `*.v{n}.md` files, unused since that migration)
# is correctly flagged as orphaned by this scan rather than silently
# skipped.


@dataclass
class OrphanedFile:
    kind: str  # 'upload' | 'result'
    # Relative to the storage root it was found under (uploads_dir /
    # results_dir) -- never the absolute host path, which is an
    # implementation detail the admin UI has no use for.
    path: str
    size_bytes: int
    modified_at: datetime  # aware, UTC (from the file's mtime)


def _referenced_paths(db: OrmSession) -> set[Path]:
    """Every on-disk path a Job row still points at, resolved
    (symlink-free, absolute) the same way a directory-walk entry is below --
    so the two can be compared by identity regardless of how either path was
    originally spelled (relative, trailing slash, ...)."""
    referenced: set[Path] = set()
    rows = db.execute(select(Job.upload_path, Job.result_path)).all()
    for upload_path, result_path in rows:
        for raw in (upload_path, result_path):
            if not raw:
                continue
            try:
                referenced.add(Path(raw).resolve())
            except OSError:
                # A malformed path column value can't match a real
                # directory-walk entry either way; skip rather than fail the
                # whole report over one bad row.
                continue
    return referenced


def _walk_storage_root(root: Path, kind: str, referenced: set[Path]) -> list[OrphanedFile]:
    if not root.exists():
        return []
    resolved_root = root.resolve()
    orphans: list[OrphanedFile] = []
    for entry in resolved_root.rglob('*'):
        if not entry.is_file():
            continue
        if entry in referenced:
            continue
        try:
            stat = entry.stat()
        except OSError:
            # Removed between the rglob listing and this stat -- nothing to
            # report.
            continue
        orphans.append(
            OrphanedFile(
                kind=kind,
                path=str(entry.relative_to(resolved_root)),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )
    return orphans


def find_orphaned_files(db: OrmSession) -> list[OrphanedFile]:
    """Every file under settings.uploads_dir/results_dir that no Job row's
    upload_path or result_path references. See the section docstring above
    for what "orphaned" means here; app/api/auth.py's
    GET /auth/admin/storage/orphaned-files is the only caller.
    """
    referenced = _referenced_paths(db)
    return [
        *_walk_storage_root(settings.uploads_dir, 'upload', referenced),
        *_walk_storage_root(settings.results_dir, 'result', referenced),
    ]
