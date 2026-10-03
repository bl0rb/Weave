"""Tests for GET /api/v1/internal/documents (app/api/inventory.py) -- the
paged identity listing Weave-Ingest's orphan cleanup reads."""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from tests.conftest import TestingSessionLocal, client

SECRET = 'inventory-secret'
URL = '/api/v1/internal/documents'
HEADERS = {'X-Weave-Reindex-Token': SECRET}


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


def _make_document(job_id: str, collection_slug: str | None, status=DocumentStatus.INDEXED) -> None:
    db = TestingSessionLocal()
    try:
        db.add(Document(
            source_job_id=job_id, content_sha256='e' * 64, engine='paddleocr', frontmatter={'title': 'secret'},
            tags=[], processed_at=datetime.now(timezone.utc), status=status, collection_slug=collection_slug,
        ))
        db.commit()
    finally:
        db.close()


def test_inventory_requires_token(monkeypatch):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={'X-Weave-Reindex-Token': 'wrong'}).status_code == 401
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', '')
    assert client.get(URL, headers=HEADERS).status_code == 503


def test_inventory_pages_by_job_id_and_exposes_identity_only():
    job_ids = sorted(str(uuid.uuid4()) for _ in range(3))
    _make_document(job_ids[0], 'hr')
    _make_document(job_ids[1], None, DocumentStatus.BLOCKED)
    _make_document(job_ids[2], 'it')

    first = client.get(URL, params={'limit': 2}, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json() == {
        'items': [
            {'job_id': job_ids[0], 'collection_slug': 'hr', 'status': 'indexed'},
            {'job_id': job_ids[1], 'collection_slug': None, 'status': 'blocked'},
        ],
        'next_after': job_ids[1],
    }

    second = client.get(URL, params={'limit': 2, 'after': job_ids[1]}, headers=HEADERS)
    assert second.json() == {
        'items': [{'job_id': job_ids[2], 'collection_slug': 'it', 'status': 'indexed'}],
        'next_after': None,
    }


def test_inventory_rejects_oversized_pages():
    assert client.get(URL, params={'limit': 1001}, headers=HEADERS).status_code == 422
