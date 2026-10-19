"""Move stored objects and generated markdown between PostgreSQL ('db') and
S3 ('s3'), in both directions, while the services keep running.

Reads always follow each row's own backend (app/services/object_store.py,
app/models/models.py: _offloaded_text), so a half-migrated instance is
fully usable and a migration can be interrupted and simply started again:
every object and every markdown row is moved and committed on its own.

Order of operations (both directions):
  1. set OBJECT_STORAGE_BACKEND to the target and restart API and workers,
     so no new data lands in the old backend (migrate() refuses otherwise);
  2. `python -m app.cli storage migrate --to <target>`;
  3. `python -m app.cli storage status` shows nothing left in the old
     backend. Keep the S3 settings until a migration to 'db' has finished.
"""

import io
import logging
from collections.abc import Callable

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import OFFLOADED_TEXT, StoredObject, StoredObjectChunk
from app.services import object_store

logger = logging.getLogger(__name__)

BACKENDS = ('db', 's3')


class MigrationRefused(RuntimeError):
    pass


# --- Binary objects -----------------------------------------------------------

def _object_to_s3(db: Session, row) -> bool:
    key = object_store.new_s3_key()
    object_store.s3_upload_file(
        io.BufferedReader(object_store.IterReader(object_store.iter_chunks(db, row.id))), key, row.content_type
    )
    moved = db.execute(
        update(StoredObject)
        .where(StoredObject.id == row.id, StoredObject.backend == 'db')
        .values(backend='s3', storage_key=key)
        .execution_options(synchronize_session=False)
    ).rowcount
    if moved:
        db.execute(delete(StoredObjectChunk).where(StoredObjectChunk.object_id == row.id))
    db.commit()
    if not moved:  # collected or moved meanwhile
        object_store.delete_s3_keys([key])
    return bool(moved)


def _object_to_db(db: Session, row) -> bool:
    # Deletes the S3 key of a row a running export may still see as 's3'.
    with object_store.s3_deletion_lock(db):
        return _object_to_db_locked(db, row)


def _object_to_db_locked(db: Session, row) -> bool:
    try:
        chunks = object_store.iter_chunks(db, row.id)
        for seq, chunk in enumerate(object_store.rechunk(chunks, row.chunk_bytes)):
            object_store.insert_chunk(db, row.id, seq, chunk)
        moved = db.execute(
            update(StoredObject)
            .where(StoredObject.id == row.id, StoredObject.backend == 's3')
            .values(backend='db', storage_key=None)
            .execution_options(synchronize_session=False)
        ).rowcount
    except IntegrityError:  # collected meanwhile
        moved = 0
    if not moved:
        db.rollback()
        return False
    db.commit()
    object_store.delete_s3_keys([row.storage_key])
    return True


def migrate_objects(
    db: Session, *, to: str, batch_size: int = 100, progress: Callable[[str], None] = print
) -> tuple[int, int]:
    """Returns (moved, skipped): skipped objects have lost their bytes."""
    source = 's3' if to == 'db' else 'db'
    mover = _object_to_s3 if to == 's3' else _object_to_db
    moved = skipped = 0
    failed: set[str] = set()
    while True:
        query = select(
            StoredObject.id, StoredObject.content_type, StoredObject.storage_key, StoredObject.chunk_bytes
        ).where(StoredObject.backend == source)
        if failed:
            query = query.where(StoredObject.id.not_in(failed))
        batch = db.execute(query.order_by(StoredObject.created_at).limit(batch_size)).all()
        if not batch:
            return moved, skipped
        for row in batch:
            db.expunge_all()  # core updates below leave loaded StoredObjects stale
            try:
                if mover(db, row):
                    moved += 1
                else:
                    failed.add(row.id)  # gone or moved by someone else: do not pick it up again
            except object_store.ObjectNotFoundError as exc:
                db.rollback()
                failed.add(row.id)
                skipped += 1
                progress(f'object {row.id}: skipped, {exc}')
        progress(f'objects -> {to}: {moved} moved')


# --- Markdown ---------------------------------------------------------------------

def _markdown_columns():
    for model, fields in OFFLOADED_TEXT.items():
        for column, object_column, name in fields:
            yield model, getattr(model, column), getattr(model, object_column), f'{model.__tablename__}.{name}'


def _markdown_to_s3(db: Session, model, column, object_column, batch_size: int, progress) -> tuple[int, int]:
    pk = model.__mapper__.primary_key[0]
    moved = 0
    while True:
        rows = db.execute(
            select(pk, column)
            .where(object_column.is_(None), func.length(func.coalesce(column, '')) > 0)
            .limit(batch_size)
        ).all()
        if not rows:
            return moved, 0
        for row_id, text in rows:
            object_id = object_store.put_text_s3_in_flush(db, text)
            # Only if nobody edited the row meanwhile; otherwise the new
            # object is an orphan for collect_garbage and the row is retried.
            moved += db.execute(
                update(model)
                .where(pk == row_id, object_column.is_(None), column == text)
                .values({column: None, object_column: object_id})
                .execution_options(synchronize_session=False)
            ).rowcount
        db.commit()


def _markdown_to_db(db: Session, model, column, object_column, batch_size: int, progress) -> tuple[int, int]:
    pk = model.__mapper__.primary_key[0]
    moved = 0
    failed: set = set()  # rows whose object lost its bytes: reported, left as they are
    while True:
        query = select(pk, object_column).where(object_column.is_not(None))
        if failed:
            query = query.where(pk.not_in(failed))
        rows = db.execute(query.limit(batch_size)).all()
        if not rows:
            return moved, len(failed)
        released = []
        for row_id, object_id in rows:
            try:
                text = object_store.read_text(db, object_id)
            except object_store.ObjectNotFoundError as exc:
                db.rollback()
                failed.add(row_id)
                progress(f'{model.__tablename__} {row_id}: skipped, {exc}')
                continue
            moved += db.execute(
                update(model)
                .where(pk == row_id, object_column == object_id)
                .values({column: text, object_column: None})
                .execution_options(synchronize_session=False)
            ).rowcount
            released.append(object_id)
        db.commit()
        object_store.delete_unreferenced(db, released)


def migrate_markdown(
    db: Session, *, to: str, batch_size: int = 100, progress: Callable[[str], None] = print
) -> tuple[int, int]:
    """Returns (moved, skipped) over all markdown columns."""
    mover = _markdown_to_s3 if to == 's3' else _markdown_to_db
    total = total_skipped = 0
    for model, column, object_column, label in _markdown_columns():
        moved, skipped = mover(db, model, column, object_column, batch_size, progress)
        progress(f'{label} -> {to}: {moved} moved, {skipped} skipped')
        total += moved
        total_skipped += skipped
    return total, total_skipped


def migrate(db: Session, *, to: str, batch_size: int = 100, progress: Callable[[str], None] = print) -> dict:
    if to not in BACKENDS:
        raise MigrationRefused(f'unknown backend {to!r}')
    if settings.object_storage_backend != to:
        raise MigrationRefused(
            f'OBJECT_STORAGE_BACKEND is {settings.object_storage_backend!r}: set it to {to!r} and restart '
            'API and workers first, or new data keeps landing in the old backend.'
        )
    if not settings.s3_bucket:
        raise MigrationRefused('S3_BUCKET is not set; both directions need the S3 settings.')
    # Markdown first: moving it to 'db' releases its objects, which then need
    # no move of their own.
    markdown, markdown_skipped = migrate_markdown(db, to=to, batch_size=batch_size, progress=progress)
    objects, objects_skipped = migrate_objects(db, to=to, batch_size=batch_size, progress=progress)
    # Skipped rows point at objects whose bytes are gone (see the progress
    # lines); they stay where they are and show up in status().
    return {
        'markdown_moved': markdown,
        'objects_moved': objects,
        'skipped': markdown_skipped + objects_skipped,
    }


# --- Status and orphan sweep --------------------------------------------------------

def status(db: Session) -> dict:
    objects = {
        backend: {'count': count, 'bytes': int(size or 0)}
        for backend, count, size in db.execute(
            select(StoredObject.backend, func.count(), func.sum(StoredObject.size_bytes)).group_by(StoredObject.backend)
        ).all()
    }
    markdown = {}
    for _model, column, object_column, label in _markdown_columns():
        markdown[label] = {
            'db': db.scalar(select(func.count()).where(func.length(func.coalesce(column, '')) > 0)) or 0,
            's3': db.scalar(select(func.count()).where(object_column.is_not(None))) or 0,
        }
    return {'configured_backend': settings.object_storage_backend, 'objects': objects, 'markdown': markdown}


def sweep_s3_orphans(db: Session, *, min_age_seconds: int = 3600) -> int:
    """Delete S3 keys under the object prefix that no stored_objects row
    points to -- left behind by a rolled-back transaction, an interrupted
    migration or a backup import that replaced the database. Keys younger
    than `min_age_seconds` may belong to a write still in flight; exports and
    imports block it through object_store.s3_deletion_lock. The prefix must
    belong to this instance alone: another instance's keys look like
    orphans from here."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
    paginator = object_store.s3_client().get_paginator('list_objects_v2')
    deleted = 0
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=object_store.s3_prefix()):
        candidates = [entry['Key'] for entry in page.get('Contents', []) if entry['LastModified'] < cutoff]
        if not candidates:
            continue
        with object_store.s3_deletion_lock(db):
            known = set(db.scalars(select(StoredObject.storage_key).where(StoredObject.storage_key.in_(candidates))))
            db.rollback()  # end the read; the next page sees fresh commits
            orphans = [key for key in candidates if key not in known]
            object_store.delete_s3_keys(orphans)
        deleted += len(orphans)
    return deleted
