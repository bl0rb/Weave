"""End-to-end tests for `python -m app.cli reindex` against SQLite.

app.cli.reindex() opens its own `SessionLocal()` from app.core.db, which is
bound to settings.database_url -- a different engine than the
TestingSessionLocal tests/conftest.py builds its tables on. Every test here
monkeypatches `app.cli.SessionLocal` to that same TestingSessionLocal, the
same swap-the-session-factory-for-tests approach conftest.py itself uses for
get_db on the FastAPI app.
"""

import uuid
from datetime import datetime, timezone

import pytest

import app.cli as cli_module
from app.cli import main, reindex
from app.models.models import Chunk, Document, DocumentStatus
from app.services.embeddings import FakeEmbeddingProvider
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


def _make_document(db, **overrides) -> Document:
    defaults = dict(
        source_job_id=str(uuid.uuid4()),
        content_sha256='e' * 64,
        engine='paddleocr',
        frontmatter={},
        tags=[],
        processed_at=datetime.now(timezone.utc),
        status=DocumentStatus.INDEXED,
        embedding_model='old-model-v1',
    )
    defaults.update(overrides)
    document = Document(**defaults)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _add_chunk(db, document, index: int, text: str, *, embedding_model: str = 'old-model-v1') -> Chunk:
    chunk = Chunk(
        document_id=document.id,
        chunk_index=index,
        text=text,
        heading_path=[],
        char_count=len(text),
        meta={},
        embedding=[0.1, 0.2, 0.3, 0.4],
        embedding_model=embedding_model,
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


def test_reindex_updates_embedding_and_model_for_indexed_documents():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        _add_chunk(db, document, 0, 'first chunk text')
        _add_chunk(db, document, 1, 'second chunk text')
    finally:
        db.close()

    failed = reindex()
    assert failed == 0

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        refreshed = db.get(Document, document.id)
        assert refreshed.embedding_model == provider.model_name

        chunks = db.query(Chunk).filter_by(document_id=document.id).order_by(Chunk.chunk_index).all()
        assert len(chunks) == 2
        for chunk in chunks:
            assert chunk.embedding_model == provider.model_name
            assert chunk.embedding != [0.1, 0.2, 0.3, 0.4]
            assert len(chunk.embedding) == provider.dimension
    finally:
        db.close()


def test_reindex_does_not_rechunk():
    """Chunk count and text must be untouched by a reindex run -- only
    embedding/embedding_model are allowed to change."""
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        _add_chunk(db, document, 0, 'unchanged chunk text')
    finally:
        db.close()

    reindex()

    db = TestingSessionLocal()
    try:
        db.expire_all()
        chunks = db.query(Chunk).filter_by(document_id=document.id).all()
        assert len(chunks) == 1
        assert chunks[0].text == 'unchanged chunk text'
        assert chunks[0].chunk_index == 0
    finally:
        db.close()


def test_reindex_skips_non_indexed_documents():
    db = TestingSessionLocal()
    try:
        document = _make_document(db, status=DocumentStatus.PENDING)
        _add_chunk(db, document, 0, 'pending doc chunk')
    finally:
        db.close()

    failed = reindex()
    assert failed == 0

    db = TestingSessionLocal()
    try:
        db.expire_all()
        refreshed = db.get(Document, document.id)
        assert refreshed.embedding_model == 'old-model-v1'
        chunk = db.query(Chunk).filter_by(document_id=document.id).first()
        assert chunk.embedding == [0.1, 0.2, 0.3, 0.4]
        assert chunk.embedding_model == 'old-model-v1'
    finally:
        db.close()


def test_reindex_only_model_filter():
    db = TestingSessionLocal()
    try:
        doc_a = _make_document(db, embedding_model='model-a')
        _add_chunk(db, doc_a, 0, 'doc a chunk', embedding_model='model-a')
        doc_b = _make_document(db, embedding_model='model-b')
        _add_chunk(db, doc_b, 0, 'doc b chunk', embedding_model='model-b')
    finally:
        db.close()

    failed = reindex(only_model='model-a')
    assert failed == 0

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        assert db.get(Document, doc_a.id).embedding_model == provider.model_name
        assert db.get(Document, doc_b.id).embedding_model == 'model-b'  # untouched
        assert db.query(Chunk).filter_by(document_id=doc_b.id).first().embedding_model == 'model-b'
    finally:
        db.close()


def test_reindex_document_id_filter():
    db = TestingSessionLocal()
    try:
        target = _make_document(db)
        _add_chunk(db, target, 0, 'target chunk')
        other = _make_document(db)
        _add_chunk(db, other, 0, 'other chunk')
    finally:
        db.close()

    failed = reindex(document_id=target.id)
    assert failed == 0

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        assert db.get(Document, target.id).embedding_model == provider.model_name
        assert db.get(Document, other.id).embedding_model == 'old-model-v1'  # untouched
    finally:
        db.close()


def test_reindex_one_failure_does_not_abort_the_rest(monkeypatch):
    db = TestingSessionLocal()
    try:
        bad = _make_document(db)
        _add_chunk(db, bad, 0, 'bad doc chunk')
        good = _make_document(db)
        _add_chunk(db, good, 0, 'good doc chunk')
    finally:
        db.close()

    original_embed_chunks = cli_module.embed_chunks

    def _flaky_embed_chunks(db_session, document, chunks, provider):
        if document.id == bad.id:
            raise RuntimeError('simulated embedding backend failure')
        return original_embed_chunks(db_session, document, chunks, provider)

    monkeypatch.setattr('app.cli.embed_chunks', _flaky_embed_chunks)

    failed = reindex()
    assert failed == 1

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        assert db.get(Document, bad.id).embedding_model == 'old-model-v1'
        assert db.get(Document, good.id).embedding_model == provider.model_name
    finally:
        db.close()


def test_main_returns_zero_on_success():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        _add_chunk(db, document, 0, 'chunk text')
    finally:
        db.close()

    assert main(['reindex']) == 0


def test_main_returns_nonzero_when_a_document_fails(monkeypatch):
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
        _add_chunk(db, document, 0, 'chunk text')
    finally:
        db.close()

    def _boom(*args, **kwargs):
        raise RuntimeError('embedding backend unreachable')

    monkeypatch.setattr('app.cli.embed_chunks', _boom)

    assert main(['reindex']) == 1


def test_main_document_id_flag():
    db = TestingSessionLocal()
    try:
        target = _make_document(db)
        _add_chunk(db, target, 0, 'target chunk')
        other = _make_document(db)
        _add_chunk(db, other, 0, 'other chunk')
    finally:
        db.close()

    assert main(['reindex', '--document-id', str(target.id)]) == 0

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        assert db.get(Document, target.id).embedding_model == provider.model_name
        assert db.get(Document, other.id).embedding_model == 'old-model-v1'
    finally:
        db.close()


def test_main_invalid_document_id_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        main(['reindex', '--document-id', 'not-a-uuid'])
    assert exc_info.value.code != 0


def test_main_only_model_flag():
    db = TestingSessionLocal()
    try:
        doc_a = _make_document(db, embedding_model='model-a')
        _add_chunk(db, doc_a, 0, 'doc a chunk', embedding_model='model-a')
        doc_b = _make_document(db, embedding_model='model-b')
        _add_chunk(db, doc_b, 0, 'doc b chunk', embedding_model='model-b')
    finally:
        db.close()

    assert main(['reindex', '--only-model', 'model-a']) == 0

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        db.expire_all()
        assert db.get(Document, doc_a.id).embedding_model == provider.model_name
        assert db.get(Document, doc_b.id).embedding_model == 'model-b'
    finally:
        db.close()
