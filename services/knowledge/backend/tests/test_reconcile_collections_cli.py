"""Tests for `python -m app.cli reconcile-collections` (audit finding F41,
ADR 0008 addendum) against SQLite. Weave-Ingest's job -> space lookup is
mocked at the `app.cli.fetch_job_collections` seam; the session factory is
swapped like tests/test_reindex_cli.py does.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.cli import main, reconcile_collections
from app.models.models import Chunk, Document, DocumentStatus
from app.services.collection_sync import CollectionSyncError
from tests.conftest import TestingSessionLocal


@pytest.fixture(autouse=True)
def _use_test_session(monkeypatch):
    monkeypatch.setattr('app.cli.SessionLocal', TestingSessionLocal)


@pytest.fixture(autouse=True)
def _cleanup_tables():
    yield
    db = TestingSessionLocal()
    try:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


def _make_document(*, collection_slug=None, status=DocumentStatus.INDEXED) -> tuple[str, uuid.UUID]:
    db = TestingSessionLocal()
    try:
        document = Document(
            source_job_id=str(uuid.uuid4()), content_sha256='e' * 64, engine='paddleocr',
            frontmatter={'title': 'Altbestand', 'team': 'support'}, tags=[], team='support',
            collection_slug=collection_slug, processed_at=datetime.now(timezone.utc), status=status,
        )
        db.add(document)
        db.flush()
        db.add(Chunk(document_id=document.id, chunk_index=0, text='text', heading_path=[], char_count=4,
                     meta={'team': 'support'}))
        db.commit()
        return document.source_job_id, document.id
    finally:
        db.close()


def _load(document_id):
    db = TestingSessionLocal()
    try:
        document = db.get(Document, document_id)
        chunk = db.query(Chunk).filter_by(document_id=document_id).one()
        return document.collection_slug, dict(document.frontmatter), dict(chunk.meta)
    finally:
        db.close()


def test_assigns_the_space_of_the_ingest_job_to_a_legacy_document(monkeypatch):
    job_id, document_id = _make_document()
    calls = []
    monkeypatch.setattr('app.cli.fetch_job_collections',
                        lambda job_ids: calls.append(job_ids) or {job_id: ('handbuch', 'Handbuch')})

    assert reconcile_collections() == 0

    slug, frontmatter, meta = _load(document_id)
    assert slug == 'handbuch'
    assert frontmatter == {'title': 'Altbestand', 'team': 'support', 'collection': 'handbuch', 'collection_name': 'Handbuch'}
    assert meta == {'team': 'support', 'collection': 'handbuch'}
    assert calls == [[job_id]]


def test_leaves_space_less_and_already_scoped_documents_alone(monkeypatch):
    spaceless_job, spaceless_id = _make_document(status=DocumentStatus.PENDING)
    scoped_job, scoped_id = _make_document(collection_slug='vertrieb')
    calls = []
    # Even a (stale) answer for an already scoped row must not move it.
    monkeypatch.setattr('app.cli.fetch_job_collections',
                        lambda job_ids: calls.append(job_ids) or {scoped_job: ('handbuch', 'Handbuch')})

    assert reconcile_collections() == 0

    assert calls == [[spaceless_job]]
    assert _load(spaceless_id)[0] is None
    assert _load(scoped_id)[0] == 'vertrieb'


def test_is_idempotent(monkeypatch):
    job_id, document_id = _make_document()
    calls = []
    monkeypatch.setattr('app.cli.fetch_job_collections',
                        lambda job_ids: calls.append(job_ids) or {job_id: ('handbuch', 'Handbuch')})

    assert reconcile_collections() == 0
    first = _load(document_id)
    assert reconcile_collections() == 0

    assert _load(document_id) == first
    assert calls == [[job_id]]  # nothing left to look up on the second run


def test_dry_run_writes_nothing(monkeypatch):
    job_id, document_id = _make_document()
    monkeypatch.setattr('app.cli.fetch_job_collections', lambda job_ids: {job_id: ('handbuch', 'Handbuch')})

    assert main(['reconcile-collections', '--dry-run']) == 0

    assert _load(document_id)[0] is None


def test_batches_lookups_and_keeps_earlier_batches_when_ingest_fails(monkeypatch):
    first_job, first_id = _make_document()
    _make_document()

    def fake_fetch(job_ids):
        if job_ids == [first_job]:
            return {first_job: ('handbuch', 'Handbuch')}
        raise CollectionSyncError('ingest down')

    monkeypatch.setattr('app.cli.fetch_job_collections', fake_fetch)

    assert reconcile_collections(batch_size=1) == 1
    assert _load(first_id)[0] == 'handbuch'
