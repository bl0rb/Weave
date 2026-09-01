import uuid
from datetime import datetime, timezone

from app.models.models import Collection, Document, DocumentStatus
from tests.conftest import TestingSessionLocal, client


def _persist_document(**overrides) -> Document:
    db = TestingSessionLocal()
    try:
        defaults = dict(
            source_job_id=str(uuid.uuid4()),
            content_sha256='e' * 64,
            engine='paddleocr',
            frontmatter={'source': 'report.pdf'},
            tags=['important'],
            team='Kundenservice',
            department='Support',
            processed_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        doc = Document(**defaults)
        db.add(doc)
        db.commit()
        db.refresh(doc)
        db.expunge(doc)
        return doc
    finally:
        db.close()


def _cleanup():
    db = TestingSessionLocal()
    try:
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


def test_list_documents_returns_persisted_rows():
    _cleanup()
    try:
        _persist_document()
        resp = client.get('/api/v1/documents')
        assert resp.status_code == 200
        body = resp.json()
        assert body['total'] == 1
        assert len(body['items']) == 1
        assert body['items'][0]['team'] == 'Kundenservice'
    finally:
        _cleanup()


def test_list_documents_filters_by_status_and_team():
    _cleanup()
    try:
        _persist_document(team='Kundenservice', status=DocumentStatus.INDEXED)
        _persist_document(source_job_id=str(uuid.uuid4()), team='Engineering', status=DocumentStatus.PENDING)

        resp = client.get('/api/v1/documents', params={'status': 'indexed'})
        assert resp.status_code == 200
        body = resp.json()
        assert body['total'] == 1
        assert body['items'][0]['team'] == 'Kundenservice'

        resp = client.get('/api/v1/documents', params={'team': 'Engineering'})
        assert resp.status_code == 200
        body = resp.json()
        assert body['total'] == 1
        assert body['items'][0]['status'] == 'pending'
    finally:
        _cleanup()


def test_get_document_includes_chunk_count():
    _cleanup()
    try:
        doc = _persist_document(chunk_count=3)
        resp = client.get(f'/api/v1/documents/{doc.id}')
        assert resp.status_code == 200
        body = resp.json()
        assert body['chunk_count'] == 3
        assert body['frontmatter'] == {'source': 'report.pdf'}
    finally:
        _cleanup()


def test_get_document_404_for_unknown_id():
    resp = client.get(f'/api/v1/documents/{uuid.uuid4()}')
    assert resp.status_code == 404


# --- GET /api/v1/collections -----------------------------------------------------


def _cleanup_collections():
    db = TestingSessionLocal()
    try:
        db.query(Collection).delete()
        db.commit()
    finally:
        db.close()


def test_list_collections_returns_registry_rows_with_zero_document_count():
    _cleanup_collections()
    try:
        db = TestingSessionLocal()
        try:
            db.add(Collection(slug='handbuch', name='Handbuch', description='Interne Doku', read_teams=['support']))
            db.commit()
        finally:
            db.close()

        resp = client.get('/api/v1/collections')
        assert resp.status_code == 200
        body = resp.json()
        assert body['total'] == 1
        item = body['items'][0]
        assert item['slug'] == 'handbuch'
        assert item['name'] == 'Handbuch'
        assert item['description'] == 'Interne Doku'
        assert item['read_teams'] == ['support']
        assert item['document_count'] == 0
    finally:
        _cleanup_collections()


def test_list_collections_counts_documents_per_collection():
    _cleanup_collections()
    _cleanup()
    try:
        db = TestingSessionLocal()
        try:
            db.add(Collection(slug='handbuch', name='Handbuch'))
            db.add(Collection(slug='empty-collection', name='Empty'))
            db.commit()
        finally:
            db.close()

        _persist_document(collection_slug='handbuch')
        _persist_document(source_job_id=str(uuid.uuid4()), collection_slug='handbuch')
        _persist_document(source_job_id=str(uuid.uuid4()), collection_slug=None)

        resp = client.get('/api/v1/collections')
        assert resp.status_code == 200
        by_slug = {item['slug']: item for item in resp.json()['items']}
        assert by_slug['handbuch']['document_count'] == 2
        assert by_slug['empty-collection']['document_count'] == 0
    finally:
        _cleanup_collections()
        _cleanup()


def test_list_collections_empty_registry_returns_empty_list():
    _cleanup_collections()
    resp = client.get('/api/v1/collections')
    assert resp.status_code == 200
    assert resp.json() == {'items': [], 'total': 0}
