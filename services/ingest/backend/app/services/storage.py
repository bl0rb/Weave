"""Upload validation. The bytes themselves go to app/services/object_store.py;
nothing is written to a local or shared filesystem."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

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


@dataclass
class InspectedUpload:
    file: BinaryIO
    suffix: str
    size_bytes: int
    sha256: str


def inspect_upload(file: UploadFile) -> InspectedUpload:
    """Validate an upload (extension, declared MIME type, size limit) and
    hash it in one streaming pass over the body Starlette has already
    spooled to the pod's temp directory -- the bytes are never all in
    memory. Leaves the file rewound for object_store.put_file."""
    suffix = _safe_suffix(file.filename or '')
    _validate_mime(file, suffix)

    digest = hashlib.sha256()
    total_bytes = 0
    file.file.seek(0)
    while chunk := file.file.read(1024 * 1024):
        total_bytes += len(chunk)
        if total_bytes > settings.max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail='File too large')
        digest.update(chunk)
    file.file.seek(0)
    return InspectedUpload(file=file.file, suffix=suffix, size_bytes=total_bytes, sha256=digest.hexdigest())
