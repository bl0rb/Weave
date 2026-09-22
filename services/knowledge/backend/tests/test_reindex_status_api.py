"""Tests for GET /api/v1/internal/reindex/status (app/api/reindex.py) --
the progress projection Weave-Ingest's 'Vektoren neu berechnen' admin action
polls through its own reindex-status proxy."""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from tests.conftest import TestingSessionLocal, client

SECRET = 'reindex-status-secret'


@pytest.fixture(autouse=True)
def _use_secret(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', SECRET)


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
    )
    defaults.update(overrides)
    document = Document(**defaults)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def test_reindex_status_requires_token():
    response = client.get('/api/v1/internal/reindex/status')
    assert response.status_code == 401

    response = client.get('/api/v1/internal/reindex/status', headers={'X-Weave-Reindex-Token': 'wrong'})
    assert response.status_code == 401


def test_reindex_status_unconfigured_service_token(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', '')
    response = client.get('/api/v1/internal/reindex/status', headers={'X-Weave-Reindex-Token': SECRET})
    assert response.status_code == 503


def test_reindex_status_counts_documents_by_status():
    db = TestingSessionLocal()
    try:
        _make_document(db, status=DocumentStatus.INDEXED)
        _make_document(db, status=DocumentStatus.INDEXED)
        _make_document(db, status=DocumentStatus.PENDING)
        _make_document(db, status=DocumentStatus.FAILED)
    finally:
        db.close()

    response = client.get('/api/v1/internal/reindex/status', headers={'X-Weave-Reindex-Token': SECRET})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 4
    assert body['by_status']['indexed'] == 2
    assert body['by_status']['pending'] == 1
    assert body['by_status']['failed'] == 1
    assert body['newest_updated_at'] is not None
