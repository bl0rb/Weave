"""Binary objects in PostgreSQL chunks (app/services/object_store.py)."""

import hashlib
import io
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.models import Job, JobStatus, StoredObject, StoredObjectChunk
from app.services import object_store
from conftest import TestingSessionLocal

# 64 KiB is the smallest chunk size object_store allows; this spans 3 chunks.
CHUNK = 64 * 1024
DATA = bytes(range(256)) * ((2 * CHUNK + 1000) // 256 + 1)


@pytest.fixture(autouse=True)
def _small_chunks(monkeypatch):
    monkeypatch.setattr(settings, 'object_chunk_bytes', CHUNK)


def _chunk_count(db, object_id: str) -> int:
    return db.query(StoredObjectChunk).filter(StoredObjectChunk.object_id == object_id).count()


def test_bytes_and_files_round_trip_in_chunks(tmp_path) -> None:
    with TestingSessionLocal() as db:
        from_bytes = object_store.put_bytes(db, DATA, content_type='application/pdf')
        from_file = object_store.put_file(
            db, io.BytesIO(DATA), sha256=hashlib.sha256(DATA).hexdigest(), size_bytes=len(DATA),
        )
        db.commit()

        for stored in (from_bytes, from_file):
            assert stored.sha256 == hashlib.sha256(DATA).hexdigest()
            assert stored.size_bytes == len(DATA)
            assert _chunk_count(db, stored.id) == 3
            assert object_store.read_bytes(db, stored.id) == DATA
            assert max(len(chunk) for chunk in object_store.iter_chunks(db, stored.id)) == CHUNK

        target = object_store.copy_to_path(db, from_file.id, tmp_path / 'copy.pdf')
        assert target.read_bytes() == DATA


def test_empty_object_has_no_chunks() -> None:
    with TestingSessionLocal() as db:
        stored = object_store.put_bytes(db, b'')
        db.commit()
        assert _chunk_count(db, stored.id) == 0
        assert object_store.read_bytes(db, stored.id) == b''


def test_missing_object_or_chunk_is_an_error() -> None:
    with TestingSessionLocal() as db:
        with pytest.raises(object_store.ObjectNotFoundError):
            object_store.read_bytes(db, 'does-not-exist')

        stored = object_store.put_bytes(db, DATA)
        db.commit()
        db.query(StoredObjectChunk).filter(
            StoredObjectChunk.object_id == stored.id, StoredObjectChunk.seq == 1
        ).delete()
        db.commit()
        with pytest.raises(object_store.ObjectNotFoundError):
            object_store.read_bytes(db, stored.id)


def test_garbage_collection_removes_only_old_unreferenced_objects() -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    with TestingSessionLocal() as db:
        orphan = object_store.put_bytes(db, DATA)
        referenced = object_store.put_bytes(db, b'%PDF referenced')
        fresh_orphan = object_store.put_bytes(db, b'%PDF just written')
        orphan.created_at = old
        referenced.created_at = old
        db.add(Job(
            id='gc-referencing-job', original_filename='gc.pdf', upload_path='inbox/gc/gc.pdf',
            upload_object_id=referenced.id, status=JobStatus.FINISHED,
        ))
        db.commit()
        ids = (orphan.id, referenced.id, fresh_orphan.id)

    with TestingSessionLocal() as db:
        object_store.collect_garbage(db, limit=10_000)

    with TestingSessionLocal() as db:
        assert db.get(StoredObject, ids[0]) is None
        assert _chunk_count(db, ids[0]) == 0  # chunks go with their object
        assert db.get(StoredObject, ids[1]) is not None
        assert db.get(StoredObject, ids[2]) is not None
        db.query(Job).filter(Job.id == 'gc-referencing-job').delete()
        db.commit()
