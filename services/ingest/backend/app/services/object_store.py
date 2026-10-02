"""Binary objects (original uploads, imported images and attachments) in
PostgreSQL.

Every object is immutable and split into fixed-size chunks
(settings.object_chunk_bytes), so neither an API pod nor a worker ever holds
a whole file in memory, and no pod needs a shared volume: whichever replica
handles a request or a task reads the bytes from the database. Callers only
deal in object ids; `StoredObject.backend` leaves room for an object-storage
backend (S3) later without touching callers or migrating existing objects.

Objects are never deleted directly. Deleting the last job or artifact that
references one leaves an orphan, which collect_garbage() removes.
"""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, exists, insert, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Job, JobArtifact, StoredObject, StoredObjectChunk


class ObjectNotFoundError(LookupError):
    """The object (or one of its chunks) does not exist."""


def _chunk_bytes() -> int:
    return max(64 * 1024, int(settings.object_chunk_bytes))


def _new_object(db: Session, *, sha256: str, size_bytes: int, content_type: str | None) -> StoredObject:
    stored = StoredObject(
        id=str(uuid.uuid4()),
        sha256=sha256,
        size_bytes=size_bytes,
        content_type=content_type,
        chunk_bytes=_chunk_bytes(),
    )
    db.add(stored)
    db.flush()  # the chunk rows below reference it
    return stored


def _insert_chunk(db: Session, object_id: str, seq: int, data: bytes) -> None:
    # Core insert, executed right away: an ORM object per chunk would keep
    # every chunk in the session until the next flush.
    db.execute(insert(StoredObjectChunk).values(object_id=object_id, seq=seq, data=data))


def put_bytes(db: Session, data: bytes, *, content_type: str | None = None) -> StoredObject:
    """Store `data` as a new object. Not committed: the caller commits it
    together with the row that references it."""
    stored = _new_object(db, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), content_type=content_type)
    view = memoryview(data)
    for seq, offset in enumerate(range(0, len(data), stored.chunk_bytes)):
        _insert_chunk(db, stored.id, seq, bytes(view[offset:offset + stored.chunk_bytes]))
    return stored


def put_file(
    db: Session, fileobj: BinaryIO, *, sha256: str, size_bytes: int, content_type: str | None = None
) -> StoredObject:
    """Store a seekable file chunk by chunk. `sha256` and `size_bytes` come
    from the caller's validation pass over the same file (see
    app/services/storage.inspect_upload). Not committed."""
    stored = _new_object(db, sha256=sha256, size_bytes=size_bytes, content_type=content_type)
    fileobj.seek(0)
    seq = 0
    while chunk := fileobj.read(stored.chunk_bytes):
        _insert_chunk(db, stored.id, seq, chunk)
        seq += 1
    return stored


def iter_chunks(db: Session, object_id: str) -> Iterator[bytes]:
    """Yield the object's bytes in order, one query per chunk, so at most one
    chunk is in memory at a time."""
    stored = db.get(StoredObject, object_id)
    if stored is None:
        raise ObjectNotFoundError(object_id)
    if stored.backend != 'db':
        raise ObjectNotFoundError(f'{object_id}: unsupported backend {stored.backend!r}')
    chunk_count = -(-stored.size_bytes // stored.chunk_bytes)
    for seq in range(chunk_count):
        data = db.scalar(
            select(StoredObjectChunk.data).where(
                StoredObjectChunk.object_id == object_id, StoredObjectChunk.seq == seq
            )
        )
        if data is None:
            raise ObjectNotFoundError(f'{object_id}: chunk {seq} is missing')
        yield data


def read_bytes(db: Session, object_id: str) -> bytes:
    """The whole object in memory -- only for small objects (artifacts)."""
    return b''.join(iter_chunks(db, object_id))


def copy_to_path(db: Session, object_id: str, path: Path) -> Path:
    """Write the object to a local file, e.g. into a worker's task dir."""
    with path.open('wb') as handle:
        for chunk in iter_chunks(db, object_id):
            handle.write(chunk)
    return path


def collect_garbage(db: Session, *, min_age_seconds: int = 3600, limit: int = 100) -> int:
    """Delete objects that nothing references any more (a deleted job or
    artifact leaves its object behind). Objects younger than
    `min_age_seconds` are left alone so a write still in flight is never
    touched; the reference checks are repeated in the DELETE itself, and the
    foreign keys block deleting an object that gained a reference meanwhile.
    Chunks go with their object (ON DELETE CASCADE). Commits."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
    unreferenced = (
        ~exists().where(Job.upload_object_id == StoredObject.id),
        ~exists().where(JobArtifact.object_id == StoredObject.id),
    )
    candidate_ids = db.scalars(
        select(StoredObject.id).where(StoredObject.created_at < cutoff, *unreferenced).limit(limit)
    ).all()
    if not candidate_ids:
        return 0
    result = db.execute(delete(StoredObject).where(StoredObject.id.in_(candidate_ids), *unreferenced))
    db.commit()
    return result.rowcount or 0
