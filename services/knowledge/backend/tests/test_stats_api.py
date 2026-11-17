"""Tests for GET /api/v1/internal/stats (app/api/stats.py) -- the aggregate
corpus statistics Weave-Ingest's admin overview proxies."""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Chunk, Collection, Document, DocumentStatus, KnowledgeMemory
from tests.conftest import TestingSessionLocal, client

SECRET = 'stats-secret'
URL = '/api/v1/internal/stats'
HEADERS = {'X-Weave-Reindex-Token': SECRET}


@pytest.fixture(autouse=True)
def _use_secret(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', SECRET)
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'off')


@pytest.fixture(autouse=True)
def _cleanup_tables():
    yield
    with TestingSessionLocal() as db:
        db.query(KnowledgeMemory).delete()
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.query(Collection).delete()
        db.commit()


def _document(db, status=DocumentStatus.INDEXED, slug: str | None = 'hr') -> Document:
    document = Document(
        source_job_id=str(uuid.uuid4()), content_sha256='e' * 64, engine='paddleocr', frontmatter={}, tags=[],
        processed_at=datetime.now(timezone.utc), status=status, collection_slug=slug,
    )
    db.add(document)
    db.flush()
    return document


def _chunk(db, document: Document, index: int, chars: int, model: str | None, embedded: bool = True) -> None:
    db.add(Chunk(
        document_id=document.id, chunk_index=index, text='x' * chars, char_count=chars, heading_path=[], meta={},
        embedding=[0.1] * settings.embedding_dimension if embedded else None, embedding_model=model,
    ))


def _seed() -> None:
    with TestingSessionLocal() as db:
        db.add_all([
            Collection(slug='hr', name='HR', visibility='public'),
            Collection(slug='it', name='IT'),
            Collection(slug='legal', name='Recht'),
        ])
        hr = _document(db, slug='hr')
        hr_second = _document(db, slug='hr')
        it_pending = _document(db, DocumentStatus.PENDING, slug='it')
        _document(db, slug='unknown')
        _document(db, DocumentStatus.FAILED, slug='legal')
        superseded = _document(db, DocumentStatus.SUPERSEDED, slug='legal')
        _chunk(db, hr, 0, 100, 'bge-m3')
        _chunk(db, hr, 1, 200, 'bge-m3')
        _chunk(db, hr_second, 0, 300, 'e5-large')
        _chunk(db, it_pending, 0, 400, None, embedded=False)
        _chunk(db, hr_second, 1, 600, None, embedded=False)
        _chunk(db, superseded, 0, 500, 'old-model')
        now = datetime.now(timezone.utc)
        db.add_all([
            KnowledgeMemory(kind='document', collection_slug='hr', document_id=hr.id, built_at=now),
            KnowledgeMemory(kind='document', collection_slug='hr', document_id=hr_second.id, built_at=now),
            # Left over from a superseded version: not a current summary.
            KnowledgeMemory(kind='document', collection_slug='legal', document_id=superseded.id, built_at=now),
            KnowledgeMemory(kind='collection', collection_slug='hr', built_at=now),
            # Only marked for a rebuild (memory.touch): no overview yet.
            KnowledgeMemory(kind='collection', collection_slug='it', dirty_since=now),
        ])
        db.commit()


def test_stats_requires_token(monkeypatch):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={'X-Weave-Reindex-Token': 'wrong'}).status_code == 401
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', '')
    assert client.get(URL, headers=HEADERS).status_code == 503


def test_stats_on_an_empty_corpus():
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['documents'] == {
        'total': 0, 'by_status': {'pending': 0, 'indexed': 0, 'blocked': 0, 'superseded': 0, 'failed': 0},
    }
    assert body['chunks'] == {'total': 0, 'embedded': 0, 'avg_chars': None}
    assert body['embedding_models'] == []
    assert body['collections'] == {'total': 0, 'with_documents': 0}
    assert body['memory'] == {'documents': 0, 'collections': 0}
    assert body['vector_backend'] == settings.vector_backend
    assert body['storage'] is None
    assert body['generated_at']


def test_stats_aggregates_documents_chunks_collections_and_models():
    _seed()
    body = client.get(URL, headers=HEADERS).json()
    assert body['documents'] == {
        'total': 6, 'by_status': {'pending': 1, 'indexed': 3, 'blocked': 0, 'superseded': 1, 'failed': 1},
    }
    # Only chunks of indexed documents count (pending/superseded ones are
    # not in the search index); most-used model first.
    assert body['chunks'] == {'total': 4, 'embedded': 3, 'avg_chars': 300.0}
    assert body['embedding_models'] == [{'model': 'bge-m3', 'chunks': 2}, {'model': 'e5-large', 'chunks': 1}]
    # 'unknown' has an indexed document but no registry entry; 'it' and
    # 'legal' have none indexed.
    assert body['collections'] == {'total': 3, 'with_documents': 1}
    # Knowledge memory is off: its rows are not reported.
    assert body['memory'] == {'documents': 0, 'collections': 0}
    assert body['storage'] is None


def test_stats_reports_built_summaries_when_the_feature_is_on(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'extractive')
    _seed()
    body = client.get(URL, headers=HEADERS).json()
    # Same base as memory_status: summaries of indexed documents and built
    # collection overviews; the dirty-only 'it' row and the superseded
    # document's summary do not count.
    assert body['memory'] == {'documents': 2, 'collections': 1}


def test_stats_skips_the_settings_callback_without_memory_rows(monkeypatch):
    calls = []
    monkeypatch.setattr('app.services.memory.refresh_settings', lambda: calls.append(1))
    assert client.get(URL, headers=HEADERS).json()['memory'] == {'documents': 0, 'collections': 0}
    assert calls == []
    _seed()
    client.get(URL, headers=HEADERS)
    assert calls == [1]
