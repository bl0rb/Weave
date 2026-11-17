"""Tests for the optional Amazon S3 Vectors mirror (VECTOR_BACKEND=s3vectors,
app/services/vector_index.py), its call sites (index pipeline, supersede,
withdrawal, CLI reindex/reconcile) and the `python -m app.cli s3vectors`
commands.

No network: every test that enables the mirror swaps vector_index.client()
for FakeS3Vectors, an in-memory index that validates each request against
botocore's bundled `s3vectors` service model (so a wrong parameter name or
shape fails here exactly as it would against AWS) and records every call.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import app.cli as cli_module
from app.api import events as events_module
from app.core.config import Settings, settings
from app.models.models import Chunk, Document, DocumentStatus, IngestEvent
from app.services import vector_index
from app.workers import tasks as tasks_module
from app.workers.tasks import index_document
from tests.conftest import TestingSessionLocal, client

DIM = 8
MODEL = 'fake-embed'


class FakeClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {'Error': {'Code': code, 'Message': code}}


class FakeS3Vectors:
    """In-memory S3 Vectors index; requests are validated against botocore's
    real service model."""

    def __init__(self):
        import botocore.session
        from botocore.validate import ParamValidator

        self._model = botocore.session.get_session().get_service_model('s3vectors')
        self._validator = ParamValidator()
        self.index: dict | None = None
        self.vectors: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail_on: set[str] = set()

    def _call(self, operation: str, params: dict) -> None:
        report = self._validator.validate(params, self._model.operation_model(operation).input_shape)
        assert not report.has_errors(), report.generate_report()
        self.calls.append((operation, params))
        if operation in self.fail_on:
            raise FakeClientError('ServiceUnavailableException')

    def calls_of(self, operation: str) -> list[dict]:
        return [params for name, params in self.calls if name == operation]

    def put_vectors(self, **params):
        self._call('PutVectors', params)
        for vector in params['vectors']:
            self.vectors[vector['key']] = vector
        return {}

    def delete_vectors(self, **params):
        self._call('DeleteVectors', params)
        for key in params['keys']:
            self.vectors.pop(key, None)
        return {}

    def get_index(self, **params):
        self._call('GetIndex', params)
        if self.index is None:
            raise FakeClientError('NotFoundException')
        return {'index': self.index}

    def create_index(self, **params):
        self._call('CreateIndex', params)
        self.index = {
            key: params[key] for key in ('vectorBucketName', 'indexName', 'dataType', 'dimension', 'distanceMetric')
        }
        self.index['metadataConfiguration'] = params.get('metadataConfiguration')
        return {'indexArn': 'arn:aws:s3vectors:test'}

    def get_paginator(self, name):
        assert name == 'list_vectors'
        fake = self

        class _Paginator:
            def paginate(self, PaginationConfig=None, **params):
                page_size = (PaginationConfig or {}).get('PageSize', 1000)
                fake._call('ListVectors', {**params, 'maxResults': page_size})
                keys = list(fake.vectors)
                for start in range(0, max(len(keys), 1), page_size):
                    yield {'vectors': [{'key': key} for key in keys[start:start + page_size]]}

        return _Paginator()


@pytest.fixture
def fake(monkeypatch):
    fake = FakeS3Vectors()
    monkeypatch.setattr(settings, 'vector_backend', 's3vectors')
    monkeypatch.setattr(settings, 's3vectors_bucket', 'test-bucket')
    monkeypatch.setattr(settings, 's3vectors_index', 'test-index')
    monkeypatch.setattr(settings, 'embedding_dimension', DIM)
    monkeypatch.setattr(settings, 'embedding_model', MODEL)
    monkeypatch.setattr(vector_index, 'client', lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _test_sessions(monkeypatch):
    monkeypatch.setattr(tasks_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(cli_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(events_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(events_module.index_document, 'delay', MagicMock())


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = TestingSessionLocal()
    try:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.query(IngestEvent).delete()
        db.commit()
    finally:
        db.close()


def _make_document(db, **overrides) -> Document:
    frontmatter = dict(overrides.pop('frontmatter', {}))
    frontmatter.setdefault('_weave_release_id', str(uuid.uuid4()))
    frontmatter.setdefault('_weave_markdown_sha256', 'b' * 64)
    defaults = dict(
        source_job_id=str(uuid.uuid4()), content_sha256='a' * 64, engine='paddleocr', frontmatter=frontmatter,
        tags=[], processed_at=datetime.now(timezone.utc), status=DocumentStatus.PENDING,
    )
    defaults.update(overrides)
    document = Document(**defaults)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _add_chunks(db, document, count: int, *, model: str = MODEL, dim: int = DIM) -> list[int]:
    chunks = [
        Chunk(
            document_id=document.id, chunk_index=index, text=f'chunk {index}', heading_path=[], char_count=7,
            meta={}, embedding=[0.1] * dim, embedding_model=model,
        )
        for index in range(count)
    ]
    db.add_all(chunks)
    db.commit()
    return [chunk.id for chunk in chunks]


_MARKDOWN = '---\nteam: Support\n---\n\n# Titel\n\nErster Absatz.\n\n## Zwei\n\nZweiter Absatz.\n'


def _index(document_id, *, markdown: str = _MARKDOWN, reindex: bool = False) -> None:
    with patch('app.workers.tasks.ingest_client.fetch_released_markdown', return_value=markdown):
        index_document(str(document_id), reindex=reindex)


def _chunk_keys(document_id) -> set[str]:
    with TestingSessionLocal() as db:
        return {str(chunk_id) for (chunk_id,) in db.query(Chunk.id).filter(Chunk.document_id == document_id)}


# --- Settings ------------------------------------------------------------------


def test_settings_reject_unknown_backend():
    with pytest.raises(ValidationError, match='VECTOR_BACKEND must be one of'):
        Settings(vector_backend='qdrant')


def test_settings_require_bucket_for_s3vectors():
    with pytest.raises(ValidationError, match='S3VECTORS_BUCKET'):
        Settings(vector_backend='s3vectors')
    assert Settings(vector_backend='s3vectors', s3vectors_bucket='b').s3vectors_index == 'weave-chunks'


def test_default_backend_is_pgvector():
    assert Settings().vector_backend == 'pgvector'


@pytest.mark.parametrize('raw', ['', '   ', 'PGVECTOR', ' pgvector '])
def test_blank_or_cased_backend_means_pgvector(raw):
    # deploy files pass optional settings as `${VAR:-}` -> ''
    assert Settings(vector_backend=raw).vector_backend == 'pgvector'
    assert Settings(vector_backend=' S3Vectors ', s3vectors_bucket='b').vector_backend == 's3vectors'


def test_settings_reject_half_configured_static_keys():
    with pytest.raises(ValidationError, match='must be set together'):
        Settings(vector_backend='s3vectors', s3vectors_bucket='b', s3vectors_access_key_id='AKIA')
    with pytest.raises(ValidationError, match='must be set together'):
        Settings(vector_backend='s3vectors', s3vectors_bucket='b', s3vectors_secret_access_key='secret')
    Settings(vector_backend='s3vectors', s3vectors_bucket='b', s3vectors_access_key_id='A', s3vectors_secret_access_key='s')
    # never stops a pgvector deployment
    assert Settings(s3vectors_access_key_id='AKIA').vector_backend == 'pgvector'


# --- pgvector default: mirror fully inert ---------------------------------------


def test_pgvector_default_never_builds_a_client(monkeypatch):
    def _boom():
        raise AssertionError('S3 Vectors client must not be built in pgvector mode')

    monkeypatch.setattr(settings, 'vector_backend', 'pgvector')
    monkeypatch.setattr(vector_index, 'client', _boom)
    with TestingSessionLocal() as db:
        document = _make_document(db)
        assert vector_index.chunk_keys(db, document.id) == []
    _index(document.id)
    with TestingSessionLocal() as db:
        assert db.get(Document, document.id).status == DocumentStatus.INDEXED
    vector_index.forget_keys(['1'], document_id=document.id)


# --- client() --------------------------------------------------------------------


def test_client_uses_default_chain_unless_configured(monkeypatch):
    import boto3

    created = []

    class _Session:  # a dedicated Session per client, never boto3's shared default one
        def client(self, service, **kwargs):
            created.append((service, kwargs))
            return object()

    monkeypatch.setattr(boto3.session, 'Session', _Session)
    monkeypatch.setattr(boto3, 'client', lambda *a, **k: pytest.fail('default session must not be used'))
    monkeypatch.setattr(vector_index, '_client', None)
    monkeypatch.setattr(vector_index, '_client_key', None)
    vector_index.client()
    service, kwargs = created[-1]
    assert service == 's3vectors'
    assert set(kwargs) == {'config'}

    monkeypatch.setattr(settings, 's3vectors_region', 'eu-central-1')
    monkeypatch.setattr(settings, 's3vectors_endpoint_url', 'http://localhost:4566')
    monkeypatch.setattr(settings, 's3vectors_access_key_id', 'AKIA')
    monkeypatch.setattr(settings, 's3vectors_secret_access_key', 'secret')
    first = vector_index.client()
    assert vector_index.client() is first  # cached for an unchanged configuration
    _, kwargs = created[-1]
    assert len(created) == 2
    assert kwargs['region_name'] == 'eu-central-1'
    assert kwargs['endpoint_url'] == 'http://localhost:4566'
    assert kwargs['aws_access_key_id'] == 'AKIA'
    assert kwargs['aws_secret_access_key'] == 'secret'


# --- Records and batching ------------------------------------------------------


def test_build_record_shape():
    document_id = uuid.uuid4()
    with patch.object(settings, 'embedding_dimension', 3):
        record = vector_index.build_record((42, 3, [1, 2, 3], MODEL, document_id, None, None))
        assert record == {
            'key': '42',
            'data': {'float32': [1.0, 2.0, 3.0]},
            'metadata': {
                'collection': '__none__', 'team': '', 'document_id': str(document_id),
                'embedding_model': MODEL, 'chunk_index': 3,
            },
        }
        assert vector_index.build_record((1, 0, [1, 2], MODEL, document_id, 'hr', 'Support')) is None
        assert vector_index.build_record((1, 0, None, MODEL, document_id, 'hr', 'Support')) is None
        assert vector_index.build_record((1, 0, [1, 2, 3], MODEL, document_id, 'hr', 'Support'))['metadata'][
            'collection'] == 'hr'


def test_put_and_delete_are_batched_at_500(fake):
    records = [{'key': str(i), 'data': {'float32': [0.0] * DIM}, 'metadata': {}} for i in range(1201)]
    vector_index.put_records(records)
    assert [len(call['vectors']) for call in fake.calls_of('PutVectors')] == [500, 500, 201]
    assert all(call['vectorBucketName'] == 'test-bucket' and call['indexName'] == 'test-index'
               for call in fake.calls_of('PutVectors'))
    vector_index.delete_keys(str(i) for i in range(1201))
    assert [len(call['keys']) for call in fake.calls_of('DeleteVectors')] == [500, 500, 201]
    assert fake.vectors == {}


def test_client_creation_is_serialized(monkeypatch):
    import threading
    import time

    import boto3

    created = []

    class _Session:
        def client(self, service, **kwargs):
            time.sleep(0.05)  # widen the race window
            created.append(service)
            return object()

    monkeypatch.setattr(boto3.session, 'Session', _Session)
    monkeypatch.setattr(vector_index, '_client', None)
    monkeypatch.setattr(vector_index, '_client_key', None)
    results = []
    threads = [threading.Thread(target=lambda: results.append(vector_index.client())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(created) == 1
    assert all(result is results[0] for result in results)


@pytest.mark.parametrize('dimension, expected', [(DIM, 500), (1024, 500), (1536, 466), (3072, 239), (4096, 180)])
def test_put_batch_size_depends_on_dimension(monkeypatch, dimension, expected):
    monkeypatch.setattr(settings, 'embedding_dimension', dimension)
    assert vector_index.put_batch_size() == expected
    assert vector_index.put_batch_size(100) == min(100, expected)


def test_put_requests_stay_under_20_mib_at_4096_dimensions(fake, monkeypatch):
    import struct

    dim = 4096
    monkeypatch.setattr(settings, 'embedding_dimension', dim)
    # Worst-case JSON width: float32 values widened to Python floats with
    # the longest repr ('-1.2345678337905474e-05', 23 chars).
    value = struct.unpack('f', struct.pack('f', -1.2345678e-05))[0]
    assert len(repr(value)) == 23
    vector = [value] * dim
    document_id = str(uuid.uuid4())
    records = [
        {'key': str(i), 'data': {'float32': vector}, 'metadata': {
            'collection': 'c' * 63, 'team': 't' * 100, 'document_id': document_id,
            'embedding_model': 'm' * 100, 'chunk_index': i,
        }}
        for i in range(400)
    ]
    sizes = []
    real_put = fake.put_vectors

    def _measure(**params):
        sizes.append(len(json.dumps(params).encode()))  # botocore's rest-json body: json.dumps defaults
        return real_put(**params)

    monkeypatch.setattr(fake, 'put_vectors', _measure)
    assert vector_index.put_records(records) == 400
    assert [len(call['vectors']) for call in fake.calls_of('PutVectors')] == [180, 180, 40]
    assert max(sizes) < 20 * 1024 * 1024


def test_cli_sync_batch_is_capped_by_dimension(fake, monkeypatch):
    monkeypatch.setattr(vector_index, 'put_batch_size', lambda requested=500: min(requested, 2))
    fake.index = {'dataType': 'float32', 'dimension': DIM, 'distanceMetric': 'cosine'}
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        _add_chunks(db, document, 5)
    assert cli_module.main(['s3vectors', 'sync', '--batch', '500']) == 0
    assert [len(call['vectors']) for call in fake.calls_of('PutVectors')] == [2, 2, 1]


# --- Index pipeline call sites --------------------------------------------------


def test_index_mirrors_chunks_with_metadata(fake):
    with TestingSessionLocal() as db:
        document = _make_document(db, team='Support', collection_slug='handbuch')
    _index(document.id)
    keys = _chunk_keys(document.id)
    assert keys and set(fake.vectors) == keys
    vector = next(iter(fake.vectors.values()))
    assert len(vector['data']['float32']) == DIM
    assert vector['metadata']['collection'] == 'handbuch'
    assert vector['metadata']['team'] == 'Support'
    assert vector['metadata']['document_id'] == str(document.id)
    assert vector['metadata']['embedding_model'] == MODEL


def test_reindex_replaces_old_vectors(fake):
    with TestingSessionLocal() as db:
        document = _make_document(db)
    _index(document.id)
    old_keys = _chunk_keys(document.id)
    _index(document.id, markdown=_MARKDOWN + '\n## Drei\n\nDritter Absatz.\n', reindex=True)
    new_keys = _chunk_keys(document.id)
    assert set(fake.vectors) == new_keys
    deleted = {key for call in fake.calls_of('DeleteVectors') for key in call['keys']}
    assert deleted == old_keys - new_keys


def test_supersede_deletes_previous_version_vectors(fake):
    with TestingSessionLocal() as db:
        previous = _make_document(db, source_job_id='job-v1', status=DocumentStatus.INDEXED)
        previous_keys = {str(key) for key in _add_chunks(db, previous, 3)}
        for key in previous_keys:
            fake.vectors[key] = {'key': key}
        current = _make_document(db, source_job_id='job-v2', previous_job_id='job-v1')
    _index(current.id)
    with TestingSessionLocal() as db:
        assert db.get(Document, previous.id).status == DocumentStatus.SUPERSEDED
    current_keys = _chunk_keys(current.id)
    assert set(fake.vectors) == current_keys
    assert not (previous_keys - current_keys) & set(fake.vectors)


def test_s3_error_never_fails_indexing(fake, caplog):
    fake.fail_on = {'PutVectors', 'DeleteVectors'}
    with TestingSessionLocal() as db:
        document = _make_document(db)
    with caplog.at_level(logging.WARNING, logger='app.services.vector_index'):
        _index(document.id)
    with TestingSessionLocal() as db:
        stored = db.get(Document, document.id)
        assert stored.status == DocumentStatus.INDEXED
        assert stored.error is None
    assert any(str(document.id) in record.getMessage() for record in caplog.records)


def test_failed_index_run_deletes_no_vectors(fake):
    """A rolled-back rewrite keeps its chunks -- and therefore its vectors."""
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        keys = {str(key) for key in _add_chunks(db, document, 2)}
    for key in keys:
        fake.vectors[key] = {'key': key}
    with patch('app.workers.tasks.embed_chunks', side_effect=RuntimeError('boom')):
        _index(document.id, reindex=True)
    assert fake.calls_of('DeleteVectors') == []
    assert set(fake.vectors) == keys == _chunk_keys(document.id)


# --- Withdrawal -------------------------------------------------------------------


def _post_withdrawal(job_id: str):
    body = json.dumps({'event': 'document.withdrawn', 'job_id': job_id}).encode()
    signature = 'sha256=' + hmac.new(b'vector-test-secret', body, hashlib.sha256).hexdigest()
    return client.post(
        '/api/v1/events/ingest', content=body,
        headers={'Content-Type': 'application/json', events_module._SIGNATURE_HEADER: signature},
    )


@pytest.mark.parametrize('fail', [False, True])
def test_withdrawal_deletes_vectors_best_effort(fake, monkeypatch, fail):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'vector-test-secret')
    if fail:
        fake.fail_on = {'DeleteVectors'}
    enqueued = []
    monkeypatch.setattr(events_module.delete_vectors, 'delay', lambda *args: enqueued.append(args))
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        keys = {str(key) for key in _add_chunks(db, document, 3)}
    for key in keys:
        fake.vectors[key] = {'key': key}
    response = _post_withdrawal(document.source_job_id)
    assert response.status_code == 200 and response.json()['status'] == 'withdrawn'
    # No S3 call inside the webhook request: the keys go to the Celery task.
    assert fake.calls == []
    assert len(enqueued) == 1 and set(enqueued[0][0]) == keys and enqueued[0][1] == str(document.id)
    assert _chunk_keys(document.id) == set()

    tasks_module.delete_vectors(*enqueued[0])  # what the worker runs; never raises
    assert {key for call in fake.calls_of('DeleteVectors') for key in call['keys']} == keys
    assert (set(fake.vectors) == keys) is fail


def test_withdrawal_survives_enqueue_failure(fake, monkeypatch, caplog):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'vector-test-secret')
    monkeypatch.setattr(events_module.delete_vectors, 'delay', MagicMock(side_effect=ConnectionError('broker down')))
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        _add_chunks(db, document, 2)
    with caplog.at_level(logging.WARNING):
        response = _post_withdrawal(document.source_job_id)
    assert response.status_code == 200 and response.json()['status'] == 'withdrawn'
    assert _chunk_keys(document.id) == set()
    assert 'could not enqueue the delete' in caplog.text


def test_withdrawal_enqueues_nothing_with_pgvector(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'vector-test-secret')
    monkeypatch.setattr(settings, 'vector_backend', 'pgvector')
    delay = MagicMock()
    monkeypatch.setattr(events_module.delete_vectors, 'delay', delay)
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        _add_chunks(db, document, 2)
    assert _post_withdrawal(document.source_job_id).status_code == 200
    delay.assert_not_called()


# --- CLI ---------------------------------------------------------------------------


def test_cli_refuses_without_s3vectors_backend(monkeypatch):
    monkeypatch.setattr(settings, 'vector_backend', 'pgvector')
    monkeypatch.setattr(vector_index, 'client', lambda: pytest.fail('no client expected'))
    assert cli_module.main(['s3vectors', 'init']) == 2
    assert cli_module.main(['s3vectors', 'sync']) == 2
    assert cli_module.main(['s3vectors', 'status']) == 0


def test_cli_init_creates_then_verifies(fake):
    assert cli_module.main(['s3vectors', 'init']) == 0
    (create,) = fake.calls_of('CreateIndex')
    assert create['dataType'] == 'float32'
    assert create['dimension'] == DIM
    assert create['distanceMetric'] == 'cosine'
    assert create['metadataConfiguration'] == {'nonFilterableMetadataKeys': ['chunk_index']}
    assert cli_module.main(['s3vectors', 'init']) == 0
    assert len(fake.calls_of('CreateIndex')) == 1


def test_cli_init_fails_on_dimension_mismatch(fake):
    fake.index = {'dataType': 'float32', 'dimension': DIM + 1, 'distanceMetric': 'cosine'}
    assert cli_module.main(['s3vectors', 'init']) == 1
    assert fake.calls_of('CreateIndex') == []


def test_cli_sync_requires_index(fake):
    assert cli_module.main(['s3vectors', 'sync']) == 1
    assert fake.calls_of('PutVectors') == []


def test_cli_sync_writes_eligible_chunks_and_prunes(fake, capsys):
    fake.index = {'dataType': 'float32', 'dimension': DIM, 'distanceMetric': 'cosine'}
    with TestingSessionLocal() as db:
        indexed = _make_document(db, status=DocumentStatus.INDEXED, collection_slug='hr')
        eligible = {str(key) for key in _add_chunks(db, indexed, 5)}
        old_model = _make_document(db, status=DocumentStatus.INDEXED)
        old_model_keys = {str(key) for key in _add_chunks(db, old_model, 2, model='old-model')}
        blocked = _make_document(db, status=DocumentStatus.BLOCKED)
        blocked_keys = {str(key) for key in _add_chunks(db, blocked, 2)}
    for key in old_model_keys | blocked_keys | {'999999', 'not-a-chunk'}:
        fake.vectors[key] = {'key': key}

    assert cli_module.main(['s3vectors', 'sync', '--batch', '2']) == 0
    assert [len(call['vectors']) for call in fake.calls_of('PutVectors')] == [2, 2, 1]
    assert eligible <= set(fake.vectors)
    assert fake.vectors[min(eligible)]['metadata']['collection'] == 'hr'
    assert fake.calls_of('DeleteVectors') == []

    assert cli_module.main(['s3vectors', 'sync', '--prune']) == 0
    assert set(fake.vectors) == eligible

    assert cli_module.main(['s3vectors', 'status', '--count-vectors']) == 0
    out = capsys.readouterr().out
    assert 'eligible chunks (postgres, model fake-embed): 5' in out
    assert 'vectors in index: 5' in out


def test_cli_sync_prune_keeps_chunks_indexed_during_the_run(fake, monkeypatch):
    """A key absent from the sync snapshot but eligible by the time the prune
    runs (a document indexed concurrently) is re-checked and kept."""
    fake.index = {'dataType': 'float32', 'dimension': DIM, 'distanceMetric': 'cosine'}
    with TestingSessionLocal() as db:
        late = _make_document(db, status=DocumentStatus.INDEXED)
        late_keys = {str(key) for key in _add_chunks(db, late, 2)}
    for key in late_keys:
        fake.vectors[key] = {'key': key}
    monkeypatch.setattr(vector_index, 'eligible_rows', lambda db, **kwargs: iter(()))
    assert cli_module.main(['s3vectors', 'sync', '--prune']) == 0
    assert set(fake.vectors) == late_keys


def test_cli_sync_reports_failed_batches(fake):
    fake.index = {'dataType': 'float32', 'dimension': DIM, 'distanceMetric': 'cosine'}
    fake.fail_on = {'PutVectors'}
    with TestingSessionLocal() as db:
        _add_chunks(db, _make_document(db, status=DocumentStatus.INDEXED), 3)
    assert cli_module.main(['s3vectors', 'sync']) == 1


def test_cli_status_reports_missing_index(fake, capsys):
    assert cli_module.main(['s3vectors', 'status']) == 1
    assert 'index status: missing' in capsys.readouterr().out


def test_cli_rejects_batch_over_500(fake):
    with pytest.raises(SystemExit) as exc:
        cli_module.main(['s3vectors', 'sync', '--batch', '501'])
    assert exc.value.code == 2


def test_cli_reindex_overwrites_vectors(fake):
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED, embedding_model=MODEL)
        keys = {str(key) for key in _add_chunks(db, document, 2)}
    assert cli_module.reindex() == 0
    assert set(fake.vectors) == keys


def test_cli_reconcile_updates_collection_metadata(fake, monkeypatch):
    with TestingSessionLocal() as db:
        document = _make_document(db, status=DocumentStatus.INDEXED)
        keys = {str(key) for key in _add_chunks(db, document, 2)}
    monkeypatch.setattr(cli_module, 'fetch_job_collections', lambda job_ids: {
        document.source_job_id: ('handbuch', 'Handbuch'),
    })
    assert cli_module.reconcile_collections() == 0
    assert set(fake.vectors) == keys
    assert {vector['metadata']['collection'] for vector in fake.vectors.values()} == {'handbuch'}

    fake.vectors.clear()
    with TestingSessionLocal() as db:
        db.get(Document, document.id).collection_slug = None
        db.commit()
    assert cli_module.reconcile_collections(dry_run=True) == 0
    assert fake.vectors == {}
