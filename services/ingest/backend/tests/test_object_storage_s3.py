"""S3 backend of the object store, offloaded markdown, the db<->s3 migration
(app/services/storage_migration.py) and backups of S3 objects -- against an
in-memory stand-in for the boto3 client, on dedicated SQLite databases so
the per-backend counts are exact."""

import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.models import (
    Base,
    DocumentRelease,
    Job,
    JobMarkdownVersion,
    JobStatus,
    StoredObject,
    StoredObjectChunk,
    User,
    UserRole,
)
from app.services import backup, object_store, storage_migration
from app.services.security import hash_password

CHUNK = 64 * 1024
DATA = bytes(range(256)) * ((2 * CHUNK + 1000) // 256 + 1)
MARKDOWN = '# Ergebnis\n\nÄußerst wichtiger Inhalt.\n' * 50


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def iter_chunks(self, chunk_size: int = 1024):
        for offset in range(0, len(self._data), chunk_size):
            yield self._data[offset:offset + chunk_size]

    def close(self) -> None:
        pass


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None, datetime]] = {}
        self.gets = 0

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = (bytes(Body), ContentType, datetime.now(timezone.utc))

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None, Config=None):
        data = bytearray()
        while piece := Fileobj.read(7000):  # odd size: crosses chunk borders
            data.extend(piece)
        self.objects[Key] = (bytes(data), (ExtraArgs or {}).get('ContentType'), datetime.now(timezone.utc))

    def get_object(self, Bucket, Key, Range=None):
        self.gets += 1
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        data = self.objects[Key][0]
        if Range:
            data = data[int(Range.removeprefix('bytes=').rstrip('-')):]
        return {'Body': _Body(data)}

    def delete_objects(self, Bucket, Delete):
        for entry in Delete['Objects']:
            self.objects.pop(entry['Key'], None)

    def get_paginator(self, name):
        return self

    def paginate(self, Bucket, Prefix):
        yield {'Contents': [
            {'Key': key, 'LastModified': modified}
            for key, (_data, _type, modified) in self.objects.items() if key.startswith(Prefix)
        ]}

    def age(self, key: str, seconds: int) -> None:
        data, content_type, modified = self.objects[key]
        self.objects[key] = (data, content_type, modified - timedelta(seconds=seconds))


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(object_store, '_client', (os.getpid(), fake))
    monkeypatch.setattr(settings, 'object_chunk_bytes', CHUNK)
    monkeypatch.setattr(settings, 's3_bucket', 'test-bucket')
    monkeypatch.setattr(settings, 's3_key_prefix', 'weave-test/')
    monkeypatch.setattr(settings, 'object_storage_backend', 's3')
    return fake


@pytest.fixture
def Session(tmp_path: Path):
    engine = create_engine(f'sqlite:///{tmp_path / "storage.db"}', future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _add_document(db, job_id: str, upload_id: str) -> None:
    db.add(Job(
        id=job_id, original_filename='doc.pdf', upload_path=f'inbox/{job_id}.pdf',
        upload_object_id=upload_id, status=JobStatus.FINISHED, result_markdown=MARKDOWN,
    ))
    db.add(JobMarkdownVersion(job_id=job_id, version=1, content=MARKDOWN + 'v1'))
    db.add(DocumentRelease(
        job_id=job_id, markdown_snapshot=MARKDOWN + 'released',
        markdown_sha256=hashlib.sha256(MARKDOWN.encode()).hexdigest(), payload={},
    ))


def _assert_document_readable(Session, job_id: str, upload_id: str) -> None:
    with Session() as db:
        job = db.get(Job, job_id)
        assert job.result_markdown == MARKDOWN
        assert object_store.read_bytes(db, upload_id) == DATA
        version = db.scalar(select(JobMarkdownVersion).where(JobMarkdownVersion.job_id == job_id))
        assert version.content == MARKDOWN + 'v1'
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job_id))
        assert release.markdown_snapshot == MARKDOWN + 'released'


def test_objects_round_trip_through_s3(s3, Session, tmp_path) -> None:
    with Session() as db:
        from_bytes = object_store.put_bytes(db, DATA, content_type='application/pdf')
        from_file = object_store.put_file(
            db, io.BytesIO(DATA), sha256=hashlib.sha256(DATA).hexdigest(), size_bytes=len(DATA),
        )
        db.commit()
        for stored in (from_bytes, from_file):
            assert stored.backend == 's3'
            assert stored.storage_key.startswith('weave-test/objects/')
            assert s3.objects[stored.storage_key][0] == DATA
            assert db.scalar(select(func.count()).where(StoredObjectChunk.object_id == stored.id)) == 0
            assert object_store.read_bytes(db, stored.id) == DATA
        assert object_store.copy_to_path(db, from_file.id, tmp_path / 'copy.pdf').read_bytes() == DATA

        del s3.objects[from_bytes.storage_key]
        with pytest.raises(object_store.ObjectNotFoundError):
            object_store.read_bytes(db, from_bytes.id)


def test_markdown_is_offloaded_and_replaced_markdown_is_collected(s3, Session) -> None:
    with Session() as db:
        upload = object_store.put_bytes(db, DATA)
        _add_document(db, 'job-s3', upload.id)
        db.add(Job(id='job-empty', original_filename='e.pdf', upload_path='inbox/e.pdf', result_markdown=''))
        db.commit()

    with Session() as db:
        job = db.get(Job, 'job-s3')
        assert job._result_markdown is None and job.result_markdown_object_id
        first_object = job.result_markdown_object_id
        first_key = db.get(StoredObject, first_object).storage_key
        assert s3.objects[first_key][0] == MARKDOWN.encode()
        assert db.get(Job, 'job-empty')._result_markdown == ''  # empty text stays inline
        assert db.scalar(select(func.count()).select_from(Job).where(Job.markdown_nonempty())) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.markdown_present())) == 2

        job.result_markdown = '# Neu'
        db.commit()
        assert job.result_markdown == '# Neu'
        assert job.result_markdown_object_id != first_object

    with Session() as db:
        assert db.get(Job, 'job-s3').result_markdown == '# Neu'
        assert object_store.collect_garbage(db, min_age_seconds=0, limit=10_000) == 1
        assert db.get(StoredObject, first_object) is None
    assert first_key not in s3.objects


def test_migration_moves_everything_both_ways(s3, Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'object_storage_backend', 'db')
    with Session() as db:
        upload = object_store.put_bytes(db, DATA, content_type='application/pdf')
        _add_document(db, 'job-mig', upload.id)
        db.commit()
        upload_id = upload.id
    assert not s3.objects

    with Session() as db:
        with pytest.raises(storage_migration.MigrationRefused):
            storage_migration.migrate(db, to='s3', progress=lambda _m: None)

    # a -> b: PostgreSQL -> S3
    monkeypatch.setattr(settings, 'object_storage_backend', 's3')
    with Session() as db:
        result = storage_migration.migrate(db, to='s3', batch_size=2, progress=lambda _m: None)
        assert result == {'markdown_moved': 3, 'objects_moved': 1, 'skipped': 0}
        status = storage_migration.status(db)
        assert set(status['objects']) == {'s3'}
        assert all(counts == {'db': 0, 's3': 1} for counts in status['markdown'].values())
        assert db.scalar(select(func.count()).select_from(StoredObjectChunk)) == 0
        # Running it again is a no-op.
        assert storage_migration.migrate(db, to='s3', progress=lambda _m: None) == {
            'markdown_moved': 0, 'objects_moved': 0, 'skipped': 0,
        }
    _assert_document_readable(Session, 'job-mig', upload_id)
    assert len(s3.objects) == 4

    # b -> a: S3 -> PostgreSQL
    monkeypatch.setattr(settings, 'object_storage_backend', 'db')
    with Session() as db:
        result = storage_migration.migrate(db, to='db', batch_size=2, progress=lambda _m: None)
        assert result == {'markdown_moved': 3, 'objects_moved': 1, 'skipped': 0}
        status = storage_migration.status(db)
        assert set(status['objects']) == {'db'}
        assert status['objects']['db']['count'] == 1  # the released markdown objects are gone
        assert all(counts == {'db': 1, 's3': 0} for counts in status['markdown'].values())
        assert db.scalar(select(func.count()).select_from(StoredObjectChunk)) == 3
    _assert_document_readable(Session, 'job-mig', upload_id)
    assert not s3.objects


def test_sweep_deletes_only_old_unreferenced_keys(s3, Session) -> None:
    with Session() as db:
        kept_key = object_store.put_bytes(db, b'kept').storage_key
        db.commit()
        s3.put_object(Bucket='test-bucket', Key=object_store.s3_prefix() + 'orphan-old', Body=b'x')
        s3.put_object(Bucket='test-bucket', Key=object_store.s3_prefix() + 'orphan-fresh', Body=b'x')
        s3.put_object(Bucket='test-bucket', Key='other-app/objects/foreign', Body=b'x')
        for key in (kept_key, object_store.s3_prefix() + 'orphan-old', 'other-app/objects/foreign'):
            s3.age(key, 7200)

        assert storage_migration.sweep_s3_orphans(db) == 1
    assert set(s3.objects) == {
        kept_key, object_store.s3_prefix() + 'orphan-fresh', 'other-app/objects/foreign',
    }


@pytest.mark.parametrize('target_backend', ['db', 's3'])
def test_backup_carries_s3_objects_into_either_backend(s3, tmp_path, monkeypatch, target_backend) -> None:
    source_engine = create_engine(f'sqlite:///{tmp_path / "source.db"}', future=True)
    Base.metadata.create_all(bind=source_engine)
    SourceSession = sessionmaker(bind=source_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(settings, 'backup_dir', tmp_path / 'backups')
    with SourceSession() as db:
        upload = object_store.put_bytes(db, DATA, content_type='application/pdf')
        _add_document(db, 'job-backup', upload.id)
        db.commit()
        upload_id = upload.id
        source_keys = set(db.scalars(select(StoredObject.storage_key)))
        archive = tmp_path / 'export.weave-backup.tar.gz'
        backup.export_backup(db, passphrase='pw', out_path=archive)

    s3.objects.clear()  # the archive alone must be enough
    monkeypatch.setattr(settings, 'object_storage_backend', target_backend)
    target_engine = create_engine(f'sqlite:///{tmp_path / "target.db"}', future=True)
    Base.metadata.create_all(bind=target_engine)
    TargetSession = sessionmaker(bind=target_engine, autoflush=False, expire_on_commit=False)
    with TargetSession() as db:
        admin = User(username='admin', email='admin@example.com', password_hash=hash_password('Boots1'), role=UserRole.ADMIN)
        db.add(admin)
        db.commit()
        backup.import_backup(db, path=archive, passphrase='pw', importing_admin_id=admin.id)
        db.commit()
        assert {row.backend for row in db.scalars(select(StoredObject))} == {target_backend}
        # A restore never reuses the source instance's keys (shared buckets).
        assert not source_keys & set(db.scalars(select(StoredObject.storage_key)))

    _assert_document_readable(TargetSession, 'job-backup', upload_id)
    assert len(s3.objects) == (4 if target_backend == 's3' else 0)


@pytest.mark.parametrize('to', ['s3', 'db'])
def test_reader_follows_an_object_migrated_mid_read(s3, Session, monkeypatch, to) -> None:
    monkeypatch.setattr(settings, 'object_storage_backend', 'db' if to == 's3' else 's3')
    with Session() as db:
        object_id = object_store.put_bytes(db, DATA).id
        db.commit()

    monkeypatch.setattr(settings, 'object_storage_backend', to)
    with Session() as reader:
        chunks = object_store.iter_chunks(reader, object_id)
        first = next(chunks)
        with Session() as migrator:
            storage_migration.migrate_objects(migrator, to=to, progress=lambda _m: None)
        assert first + b''.join(chunks) == DATA


def test_markdown_with_lost_object_is_skipped_not_fatal(s3, Session, monkeypatch) -> None:
    with Session() as db:
        upload = object_store.put_bytes(db, DATA)
        _add_document(db, 'job-lost', upload.id)
        db.commit()
        lost = db.get(Job, 'job-lost').result_markdown_object_id
        del s3.objects[db.get(StoredObject, lost).storage_key]

    monkeypatch.setattr(settings, 'object_storage_backend', 'db')
    with Session() as db:
        result = storage_migration.migrate(db, to='db', progress=lambda _m: None)
        # The markdown row and its (still referenced) object are both skipped.
        assert result == {'markdown_moved': 2, 'objects_moved': 1, 'skipped': 2}
        assert db.get(Job, 'job-lost').result_markdown_object_id == lost  # left as it was


def test_release_needs_inline_or_offloaded_markdown(Session) -> None:
    from sqlalchemy.exc import IntegrityError

    with Session() as db:
        db.add(Job(id='job-check', original_filename='c.pdf', upload_path='inbox/c.pdf'))
        db.add(DocumentRelease(job_id='job-check', markdown_snapshot=None, markdown_sha256='x', payload={}))
        with pytest.raises(IntegrityError):
            db.commit()


def test_preload_fetches_offloaded_markdown_once_per_object(s3, Session) -> None:
    with Session() as db:
        for n in range(5):
            db.add(Job(id=f'job-pre-{n}', original_filename='p.pdf', upload_path='inbox/p.pdf', result_markdown=f'# {n}'))
        db.commit()
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.id.like('job-pre-%')).order_by(Job.id)).all()
        s3.gets = 0
        object_store.preload_offloaded(db, jobs)
        assert s3.gets == 5
        assert [job.result_markdown for job in jobs] == [f'# {n}' for n in range(5)]
        assert s3.gets == 5


def test_cli_storage_commands_report_missing_s3_config(monkeypatch, capsys) -> None:
    from app import cli

    monkeypatch.setattr(object_store, '_client', None)
    monkeypatch.setattr(settings, 's3_bucket', '')
    assert cli.main(['storage', 'sweep']) == 1
    assert 'S3 is not configured' in capsys.readouterr().err
