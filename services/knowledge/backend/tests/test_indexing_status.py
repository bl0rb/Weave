import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from tests.conftest import TestingSessionLocal, client

SECRET = 'isolated-status-contract-test'
CONTEXT = b'weave.indexing-status.v1\n'


@pytest.fixture
def corpus(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', SECRET)
    reference = {'job_id': str(uuid.uuid4()), 'release_id': str(uuid.uuid4()), 'markdown_sha256': 'a' * 64}
    with TestingSessionLocal() as db:
        doc = Document(
            source_job_id=reference['job_id'], content_sha256='b' * 64,
            engine='test', processed_at=datetime.now(timezone.utc),
            status=DocumentStatus.INDEXED, chunk_count=2, indexed_at=datetime.now(timezone.utc),
            frontmatter={'_weave_release_id': reference['release_id'], '_weave_markdown_sha256': reference['markdown_sha256'], 'private': 'not for the portal'},
            markdown_body='PRIVATE CONTENT', error='internal service credentials',
        )
        db.add(doc)
        db.flush()
        doc_id = doc.id
        for i in range(2):
            db.add(Chunk(document_id=doc.id, chunk_index=i, text='private text', char_count=12, embedding=[0.1, 0.2]))
        db.commit()
    yield reference, doc_id
    with TestingSessionLocal() as db:
        db.delete(db.get(Document, doc_id))
        db.commit()


def _post(items, *, timestamp=None, context=CONTEXT, mutate=False):
    body = json.dumps({'items': items}, separators=(',', ':')).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = 'sha256=' + hmac.new(SECRET.encode(), context + timestamp.encode() + b'\n' + body, hashlib.sha256).hexdigest()
    return client.post('/api/v1/indexing/status', content=body + (b' ' if mutate else b''), headers={
        'Content-Type': 'application/json', 'X-Weave-Status-Timestamp': timestamp, 'X-Weave-Status-Signature': signature,
    })


def test_confirms_exact_release_and_committed_embeddings_without_exposing_content(corpus):
    reference, _ = corpus
    response = _post([reference])
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item == {**reference, 'state': 'indexed', 'chunk_count': 2, 'indexed_at': item['indexed_at']}
    assert item['indexed_at'].endswith('Z')
    assert response.headers['cache-control'] == 'no-store'
    assert 'private' not in response.text.lower()
    assert 'credentials' not in response.text


@pytest.mark.parametrize('field,value', [('job_id', None), ('release_id', None), ('markdown_sha256', 'd' * 64)])
def test_never_confirms_another_job_release_or_snapshot(corpus, field, value):
    reference, _ = corpus
    response = _post([{**reference, field: value or str(uuid.uuid4())}])
    item = response.json()['items'][0]
    assert item['state'] == ('not_received' if field == 'job_id' else 'mismatch')
    assert item['chunk_count'] == 0 and item['indexed_at'] is None


@pytest.mark.parametrize('status', [DocumentStatus.PENDING, DocumentStatus.FAILED, DocumentStatus.BLOCKED, DocumentStatus.SUPERSEDED])
def test_only_active_indexed_status_is_ready_even_with_old_chunks_and_timestamp(corpus, status):
    reference, doc_id = corpus
    with TestingSessionLocal() as db:
        db.get(Document, doc_id).status = status
        db.commit()
    item = _post([reference]).json()['items'][0]
    assert item['state'] == status.value
    assert item['indexed_at'] is None and item['chunk_count'] == 0


@pytest.mark.parametrize('damage', ['missing_row', 'missing_embedding', 'empty_embedding', 'missing_timestamp', 'empty'])
def test_incomplete_or_empty_indexes_are_not_advertised_as_searchable(corpus, damage):
    reference, doc_id = corpus
    with TestingSessionLocal() as db:
        doc = db.get(Document, doc_id)
        if damage == 'missing_row':
            db.delete(doc.chunks[0])
        elif damage == 'missing_embedding':
            doc.chunks[0].embedding = None
        elif damage == 'empty_embedding':
            doc.chunks[0].embedding = []
        elif damage == 'missing_timestamp':
            doc.indexed_at = None
        else:
            doc.chunks.clear()
            doc.chunk_count = 0
        db.commit()
    item = _post([reference]).json()['items'][0]
    assert item['state'] == ('empty' if damage == 'empty' else 'incomplete')
    assert item['chunk_count'] == 0 and item['indexed_at'] is None


def test_signed_batch_preserves_order_and_does_not_enumerate_other_documents(corpus):
    reference, _ = corpus
    unknown = {**reference, 'job_id': str(uuid.uuid4())}
    items = _post([unknown, reference]).json()['items']
    assert [item['job_id'] for item in items] == [unknown['job_id'], reference['job_id']]
    assert [item['state'] for item in items] == ['not_received', 'indexed']


def test_service_bearer_without_status_signature_is_not_sufficient(corpus):
    reference, _ = corpus
    assert client.post('/api/v1/indexing/status', json={'items': [reference]}).status_code == 401


def test_missing_secret_fails_closed(corpus, monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', '')
    assert _post([corpus[0]]).status_code == 503


@pytest.mark.parametrize('options', [
    {'timestamp': int(time.time()) - 120}, {'timestamp': int(time.time()) + 120},
    {'context': b'wrong-signing-context\n'}, {'mutate': True},
])
def test_rejects_expired_future_wrong_context_and_tampered_requests(corpus, options):
    assert _post([corpus[0]], **options).status_code == 401


@pytest.mark.parametrize('size', [0, 51])
def test_batch_size_is_bounded(corpus, size):
    assert _post([corpus[0]] * size).status_code == 422
