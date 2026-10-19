"""Binary objects (original uploads, imported images and attachments, and
-- with the S3 backend -- generated markdown) in PostgreSQL or S3.

Every object is immutable. In PostgreSQL (backend 'db') it is split into
fixed-size chunks (settings.object_chunk_bytes), so neither an API pod nor a
worker ever holds a whole file in memory, and no pod needs a shared volume:
whichever replica handles a request or a task reads the bytes from the
database. With settings.object_storage_backend = 's3' new objects go to an
S3-compatible bucket instead (backend 's3', `storage_key`), streamed in
parts. Callers only deal in object ids; reads follow each object's own
backend, so both kinds coexist and app/services/storage_migration.py moves
them between backends.

Objects are never deleted directly. Deleting the last job or artifact that
references one leaves an orphan, which collect_garbage() removes (row, and
the S3 key with it).
"""

import hashlib
import io
import logging
import os
import threading
import uuid
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, exists, insert, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from app.core.config import settings
from app.models.models import (
    OFFLOADED_TEXT,
    Job,
    JobArtifact,
    StoredObject,
    StoredObjectChunk,
    cache_offloaded,
)

logger = logging.getLogger(__name__)

MARKDOWN_CONTENT_TYPE = 'text/markdown; charset=utf-8'
# S3 rejects multipart parts below 5 MiB (except the last one).
_MIN_S3_PART_BYTES = 8 * 1024 * 1024


class ObjectNotFoundError(LookupError):
    """The object (or one of its chunks) does not exist."""


class ObjectStorageConfigError(RuntimeError):
    """The S3 backend is used but not configured."""


def _chunk_bytes() -> int:
    return max(64 * 1024, int(settings.object_chunk_bytes))


# --- S3 client ----------------------------------------------------------

_client_lock = threading.Lock()
_client: tuple[int, object] | None = None  # (pid, client): never reuse a client across fork()


def s3_client():
    global _client
    pid = os.getpid()
    if _client is not None and _client[0] == pid:
        return _client[1]
    with _client_lock:
        if _client is None or _client[0] != pid:
            if not settings.s3_bucket:
                raise ObjectStorageConfigError('S3_BUCKET is not set')
            import boto3
            from botocore.config import Config

            kwargs: dict = {
                'config': Config(
                    s3={'addressing_style': settings.s3_addressing_style},
                    retries={'max_attempts': 5, 'mode': 'standard'},
                ),
            }
            if settings.s3_endpoint_url:
                kwargs['endpoint_url'] = settings.s3_endpoint_url
            if settings.s3_region:
                kwargs['region_name'] = settings.s3_region
            if settings.s3_access_key_id:
                kwargs['aws_access_key_id'] = settings.s3_access_key_id
                kwargs['aws_secret_access_key'] = settings.s3_secret_access_key
            _client = (pid, boto3.client('s3', **kwargs))
    return _client[1]


def s3_prefix() -> str:
    return f'{settings.s3_key_prefix}objects/'


def new_s3_key() -> str:
    """A fresh key per write, never derived from the object id: a restored
    backup or a migrated copy of another instance's data keeps the ids, and
    must still never share (and later delete) that instance's keys."""
    return f'{s3_prefix()}{uuid.uuid4()}'


# Exports (shared) vs. everything that deletes S3 keys of rows that still
# exist in some snapshot (exclusive): GC, s3->db moves, sweep.
_S3_DELETION_LOCK_KEY = 0x5745_4156_0001


@contextmanager
def s3_deletion_lock(db: Session, *, shared: bool = False, wait: bool = True) -> Iterator[bool]:
    """PostgreSQL advisory lock on its own connection, held for the block.
    An export holds it shared for its whole run, because S3 is not part of
    its REPEATABLE READ snapshot: a key deleted meanwhile would break it.
    Deleters take it exclusive -- GC without waiting (it then leaves S3
    objects for a later tick), migration and sweep waiting. Yields whether
    it is held; SQLite (tests, local development) has no concurrent
    exporter and always yields True."""
    bind = db.get_bind()
    if bind.dialect.name != 'postgresql':
        yield True
        return
    suffix = '_shared' if shared else ''
    with bind.connect() as conn:
        if wait:
            conn.execute(text(f'SELECT pg_advisory_lock{suffix}(:key)'), {'key': _S3_DELETION_LOCK_KEY})
            held = True
        else:
            held = bool(conn.scalar(text(f'SELECT pg_try_advisory_lock{suffix}(:key)'), {'key': _S3_DELETION_LOCK_KEY}))
        conn.commit()  # a session-level lock outlives the transaction
        try:
            yield held
        finally:
            if held:
                try:
                    conn.execute(text(f'SELECT pg_advisory_unlock{suffix}(:key)'), {'key': _S3_DELETION_LOCK_KEY})
                    conn.commit()
                except Exception:
                    conn.invalidate()  # never hand a locked connection back to the pool
                    raise


def _transfer_config():
    from boto3.s3.transfer import TransferConfig

    part = max(_chunk_bytes(), _MIN_S3_PART_BYTES)
    return TransferConfig(multipart_threshold=part, multipart_chunksize=part, max_concurrency=4)


def _extra_args(content_type: str | None) -> dict:
    return {'ContentType': content_type} if content_type else {}


def s3_upload_file(fileobj: BinaryIO, key: str, content_type: str | None) -> None:
    """Stream a readable file to `key` (multipart above one part)."""
    s3_client().upload_fileobj(
        fileobj, settings.s3_bucket, key, ExtraArgs=_extra_args(content_type), Config=_transfer_config()
    )


def _s3_put(key: str, data: bytes, content_type: str | None) -> None:
    s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, **_extra_args(content_type))


def s3_iter(key: str, chunk_bytes: int, offset: int = 0) -> Iterator[bytes]:
    from botocore.exceptions import ClientError

    extra = {'Range': f'bytes={offset}-'} if offset else {}
    try:
        body = s3_client().get_object(Bucket=settings.s3_bucket, Key=key, **extra)['Body']
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound'):
            raise ObjectNotFoundError(f'S3 key {key} is missing') from exc
        raise
    try:
        yield from body.iter_chunks(chunk_size=chunk_bytes)
    finally:
        body.close()


def delete_s3_keys(keys: Iterable[str]) -> None:
    """Best effort: a key that survives a failure here is found again by
    sweep_s3_orphans (app/services/storage_migration.py). Callers deleting
    keys of rows an export may still see hold s3_deletion_lock."""
    keys = [key for key in keys if key]
    for start in range(0, len(keys), 1000):
        batch = keys[start:start + 1000]
        try:
            s3_client().delete_objects(
                Bucket=settings.s3_bucket, Delete={'Objects': [{'Key': key} for key in batch], 'Quiet': True}
            )
        except Exception:
            logger.exception('deleting %d S3 objects failed; the orphan sweep removes them later', len(batch))


class IterReader(io.RawIOBase):
    """Read-only file over an iterator of byte chunks (for upload_fileobj)."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buffer = b''

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def rechunk(chunks: Iterator[bytes], size: int) -> Iterator[bytes]:
    """Exactly `size` bytes per chunk except the last (the 'db' layout)."""
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


# --- Writing --------------------------------------------------------------

def _use_s3() -> bool:
    return settings.object_storage_backend == 's3'


def _new_object(
    db: Session,
    *,
    sha256: str,
    size_bytes: int,
    content_type: str | None,
    backend: str = 'db',
    storage_key: str | None = None,
) -> StoredObject:
    stored = StoredObject(
        id=str(uuid.uuid4()),
        sha256=sha256,
        size_bytes=size_bytes,
        content_type=content_type,
        backend=backend,
        storage_key=storage_key,
        chunk_bytes=_chunk_bytes(),
    )
    db.add(stored)
    db.flush()  # the chunk rows below (and the caller's references) need it
    return stored


def insert_chunk(db: Session, object_id: str, seq: int, data: bytes) -> None:
    # Core insert, executed right away: an ORM object per chunk would keep
    # every chunk in the session until the next flush.
    db.execute(insert(StoredObjectChunk).values(object_id=object_id, seq=seq, data=data))


def put_bytes(db: Session, data: bytes, *, content_type: str | None = None) -> StoredObject:
    """Store `data` as a new object. Not committed: the caller commits it
    together with the row that references it."""
    sha256 = hashlib.sha256(data).hexdigest()
    if _use_s3():
        key = new_s3_key()
        _s3_put(key, data, content_type)
        return _new_object(
            db, sha256=sha256, size_bytes=len(data), content_type=content_type, backend='s3', storage_key=key,
        )
    stored = _new_object(db, sha256=sha256, size_bytes=len(data), content_type=content_type)
    view = memoryview(data)
    for seq, offset in enumerate(range(0, len(data), stored.chunk_bytes)):
        insert_chunk(db, stored.id, seq, bytes(view[offset:offset + stored.chunk_bytes]))
    return stored


def put_file(
    db: Session, fileobj: BinaryIO, *, sha256: str, size_bytes: int, content_type: str | None = None
) -> StoredObject:
    """Store a seekable file chunk by chunk. `sha256` and `size_bytes` come
    from the caller's validation pass over the same file (see
    app/services/storage.inspect_upload). Not committed."""
    fileobj.seek(0)
    if _use_s3():
        key = new_s3_key()
        s3_upload_file(fileobj, key, content_type)
        return _new_object(
            db, sha256=sha256, size_bytes=size_bytes, content_type=content_type, backend='s3', storage_key=key,
        )
    stored = _new_object(db, sha256=sha256, size_bytes=size_bytes, content_type=content_type)
    seq = 0
    while chunk := fileobj.read(stored.chunk_bytes):
        insert_chunk(db, stored.id, seq, chunk)
        seq += 1
    return stored


def put_text_s3_in_flush(db: Session, text: str) -> str:
    """Upload markdown to S3 and insert its StoredObject row with a Core
    statement on the flush's own connection -- usable from a before_flush
    hook, where Session.flush() must not be re-entered (see
    app/models/models.py: _offload_text). Returns the object id."""
    data = text.encode('utf-8')
    object_id = str(uuid.uuid4())
    key = new_s3_key()
    _s3_put(key, data, MARKDOWN_CONTENT_TYPE)
    db.connection().execute(StoredObject.__table__.insert().values(
        id=object_id,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        content_type=MARKDOWN_CONTENT_TYPE,
        backend='s3',
        storage_key=key,
        chunk_bytes=_chunk_bytes(),
        created_at=datetime.now(timezone.utc),
    ))
    return object_id


# --- Reading --------------------------------------------------------------

def _iter_backend(db: Session, stored: StoredObject, offset: int) -> Iterator[bytes]:
    if stored.backend == 's3':
        yield from s3_iter(stored.storage_key, stored.chunk_bytes, offset)
        return
    if stored.backend != 'db':
        raise ObjectNotFoundError(f'{stored.id}: unsupported backend {stored.backend!r}')
    chunk_count = -(-stored.size_bytes // stored.chunk_bytes)
    first_seq, skip = divmod(offset, stored.chunk_bytes)
    for seq in range(first_seq, chunk_count):
        data = db.scalar(
            select(StoredObjectChunk.data).where(
                StoredObjectChunk.object_id == stored.id, StoredObjectChunk.seq == seq
            )
        )
        if data is None:
            raise ObjectNotFoundError(f'{stored.id}: chunk {seq} is missing')
        yield data[skip:] if skip else data
        skip = 0


def iter_chunks(db: Session, object_id: str) -> Iterator[bytes]:
    """Yield the object's bytes in order, at most one chunk in memory at a
    time (one query per chunk in PostgreSQL, a streamed GET from S3). If the
    bytes vanish mid-read because a migration just moved the object to the
    other backend (app/services/storage_migration.py), it continues there at
    the same offset."""
    stored = db.get(StoredObject, object_id)
    if stored is None:
        raise ObjectNotFoundError(object_id)
    offset = 0
    while True:
        try:
            for chunk in _iter_backend(db, stored, offset):
                offset += len(chunk)
                yield chunk
            return
        except ObjectNotFoundError:
            backend = stored.backend
            try:
                db.refresh(stored)
            except ObjectDeletedError as exc:
                raise ObjectNotFoundError(object_id) from exc
            if stored.backend == backend:
                raise


def read_bytes(db: Session, object_id: str) -> bytes:
    """The whole object in memory -- only for small objects (artifacts,
    markdown)."""
    return b''.join(iter_chunks(db, object_id))


def read_text(db: Session, object_id: str) -> str:
    return read_bytes(db, object_id).decode('utf-8')


def preload_offloaded(db: Session, instances: Iterable) -> None:
    """Fetch the offloaded markdown of many rows concurrently (one GET per
    object, up to 8 at a time) into each row's cache, so a loop over
    `job.result_markdown` does not wait for one round trip per row."""
    wanted: dict[str, list[tuple[object, str]]] = {}
    for obj in instances:
        for _column, object_column, _name in OFFLOADED_TEXT.get(type(obj), ()):
            object_id = getattr(obj, object_column)
            if object_id and obj.__dict__.get(f'_offloaded_{object_column}', (None,))[0] != object_id:
                wanted.setdefault(object_id, []).append((obj, object_column))
    if not wanted:
        return
    rows = db.execute(
        select(StoredObject.id, StoredObject.backend, StoredObject.storage_key, StoredObject.chunk_bytes)
        .where(StoredObject.id.in_(list(wanted)))
    ).all()
    s3_rows = [row for row in rows if row.backend == 's3']

    def fetch(row) -> tuple[str, str]:
        return row.id, b''.join(s3_iter(row.storage_key, row.chunk_bytes)).decode('utf-8')

    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = dict(pool.map(fetch, s3_rows))
    for object_id, value in texts.items():
        for obj, object_column in wanted[object_id]:
            cache_offloaded(obj, object_column, object_id, value)


def copy_to_path(db: Session, object_id: str, path: Path) -> Path:
    """Write the object to a local file, e.g. into a worker's task dir."""
    with path.open('wb') as handle:
        for chunk in iter_chunks(db, object_id):
            handle.write(chunk)
    return path


# --- Garbage collection -----------------------------------------------------

def unreferenced_clauses() -> tuple:
    """WHERE clauses for a StoredObject nothing references any more. The
    markdown references come from OFFLOADED_TEXT, the one list of them."""
    clauses = [
        ~exists().where(Job.upload_object_id == StoredObject.id),
        ~exists().where(JobArtifact.object_id == StoredObject.id),
    ]
    for model, fields in OFFLOADED_TEXT.items():
        for _column, object_column, _name in fields:
            clauses.append(~exists().where(getattr(model, object_column) == StoredObject.id))
    return tuple(clauses)


def delete_unreferenced(db: Session, object_ids: list[str], *, wait: bool = True) -> int:
    """Delete those of `object_ids` that nothing references, re-checking
    the references in the DELETE itself. Commits; S3 keys of the deleted
    rows are removed after the commit, under s3_deletion_lock. Without
    `wait`, S3 objects are left for later while an export holds the lock."""
    if not object_ids:
        return 0
    with s3_deletion_lock(db, wait=wait) as may_delete_s3:
        clauses = unreferenced_clauses()
        if not may_delete_s3:
            clauses += (StoredObject.backend == 'db',)
        deleted = db.execute(
            delete(StoredObject)
            .where(StoredObject.id.in_(object_ids), *clauses)
            .returning(StoredObject.backend, StoredObject.storage_key)
            .execution_options(synchronize_session=False)
        ).all()
        db.commit()
        delete_s3_keys(key for backend, key in deleted if backend == 's3')
    return len(deleted)


def collect_garbage(db: Session, *, min_age_seconds: int = 3600, limit: int = 100) -> int:
    """Delete objects that nothing references any more (a deleted job or
    artifact, or replaced markdown, leaves its object behind). Objects
    younger than `min_age_seconds` are left alone so a write still in flight
    is never touched; the foreign keys block deleting an object that gained
    a reference meanwhile. Chunks go with their object (ON DELETE CASCADE).
    Commits."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
    candidate_ids = db.scalars(
        select(StoredObject.id).where(StoredObject.created_at < cutoff, *unreferenced_clauses()).limit(limit)
    ).all()
    return delete_unreferenced(db, list(candidate_ids), wait=False)
