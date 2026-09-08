"""Tests for POST /api/v1/events/ingest (app/api/events.py): signature
verification, event-type filtering, idempotent dedup on (job_id,
content_sha256), the block-recommendation branch, and the null-quality
warn-semantics branch.

`index_document.delay` is mocked at the app.api.events seam for every test
here (autouse fixture below) -- no real Celery/Redis is needed, mirroring
how Weave-Ingest's own tests/test_webhook_api.py mocks
app.api.webhook_routes.celery_app.send_task.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.api import events as events_module
from app.core.config import settings
from app.models.models import Chunk, Collection, Document, DocumentStatus, IngestEvent
from app.services.collection_sync import CollectionSyncError
from tests.conftest import TestingSessionLocal, client

_URL = '/api/v1/events/ingest'


@pytest.fixture(autouse=True)
def _mock_index_document_delay(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(events_module.index_document, 'delay', mock)
    return mock


@pytest.fixture(autouse=True)
def _use_test_db_for_lazy_collection_sync(monkeypatch):
    # _ensure_collection_known opens its OWN session via app.core.db's
    # module-level SessionLocal (see that function's docstring on why it
    # can't reuse the request-scoped `db`) -- that SessionLocal is bound to
    # settings.database_url, NOT the TestClient's overridden get_db, so
    # every test touching a `collection` frontmatter field needs it
    # repointed at the same TestingSessionLocal/test.db everything else
    # here uses, same as tests/test_index_task.py does for
    # app.workers.tasks.SessionLocal.
    monkeypatch.setattr(events_module, 'SessionLocal', TestingSessionLocal)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = TestingSessionLocal()
    try:
        db.query(Document).delete()
        db.query(IngestEvent).delete()
        db.query(Collection).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize('override,expected_status', [(False, DocumentStatus.BLOCKED), (True, DocumentStatus.PENDING)])
def test_grade_c_requires_signed_override(override, expected_status, _mock_index_document_delay, monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'quality-test-secret')
    event = _event_payload(quality={'grade': 'C', 'recommendation': 'block'}, quality_override=override)
    response = _post(event)
    assert response.status_code in (200, 202), response.text
    with TestingSessionLocal() as db:
        document = db.query(Document).filter_by(source_job_id=event['job_id']).one()
        assert document.status == expected_status
        assert document.quality_grade == 'C'
        assert document.quality_recommendation == 'block'
    assert _mock_index_document_delay.called is override


def test_withdrawal_removes_document_and_prevents_late_release(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'withdrawal-test-secret')
    event = _event_payload()
    assert _post(event).status_code == 202
    with TestingSessionLocal() as db:
        document = db.query(Document).filter_by(source_job_id=event['job_id']).one()
        document_id = document.id
        document.markdown_body = 'obsolete content'
        db.add(Chunk(document_id=document.id, chunk_index=0, text='obsolete content', char_count=16))
        db.commit()
    withdrawal = {'event': 'document.withdrawn', 'job_id': event['job_id']}
    assert _post(withdrawal, signature=None).status_code == 401
    for _attempt in range(2):
        assert _post(withdrawal).json()['status'] == 'withdrawn'
    assert _post(event).json()['status'] == 'withdrawn'
    with TestingSessionLocal() as db:
        assert db.query(Document).filter_by(source_job_id=event['job_id']).first() is None
        assert db.query(Chunk).filter_by(document_id=document_id).count() == 0


def test_withdrawal_before_release_prevents_publication(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'withdrawal-test-secret')
    event = _event_payload()
    assert _post({'event': 'document.withdrawn', 'job_id': event['job_id']}).status_code == 200
    assert _post(event).json()['status'] == 'withdrawn'
    assert _post({'event': 'document.withdrawn', 'job_id': '../invalid'}).status_code == 400


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={mac}'


def _event_payload(**overrides) -> dict:
    job_id = overrides.pop('job_id', None) or str(uuid.uuid4())
    release_id = overrides.pop('release_id', None) or str(uuid.uuid4())
    content_sha256 = overrides.pop('content_sha256', None) or 'a' * 64
    markdown_sha256 = overrides.pop('markdown_sha256', None) or 'b' * 64
    quality = overrides.pop(
        'quality',
        {
            'grade': 'A',
            'recommendation': 'allow',
            'signals': {
                'ocr_confidence': 0.95, 'confidence_sample_size': 340, 'structure_quality': 0.91,
                'noise_penalty': 0.03, 'text_quality': 0.97, 'field_validation': {},
            },
        },
    )
    frontmatter = overrides.pop(
        'frontmatter',
        {
            'source': f'{job_id}.pdf', 'original_filename': 'report.pdf', 'pages': 3,
            'profile': 'PP-OCRv6 small det + rec', 'profile_id': 'ppocrv6_small', 'mode': 'single',
            'job_id': job_id, 'document_version': 1, 'content_sha256': content_sha256,
            'uploaded_by': 'mathias', 'team': 'Kundenservice', 'department': 'Support',
            'tags': ['section-1', 'important'], 'processed_at': '2026-08-31T14:23:45Z', 'engine': 'paddleocr',
        },
    )
    payload = {
        'event': 'document.released',
        'timestamp': '2026-08-31T14:23:45.123456+00:00',
        'job_id': job_id,
        'document_version': 1,
        'previous_job_id': None,
        'content_sha256': content_sha256,
        'original_filename': 'report.pdf',
        'markdown_url': f'/api/v1/portal/releases/{release_id}/download',
        'frontmatter': frontmatter,
        'quality': quality,
        'engine': 'paddleocr',
        'processed_at': '2026-08-31T14:23:45Z',
        'release_id': release_id,
        'markdown_sha256': markdown_sha256,
    }
    payload.update(overrides)
    return payload


def _post(payload: dict, *, secret: str | None = None, signature: str | None = '__auto__'):
    body = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if signature == '__auto__':
        # Sign with whatever secret is configured unless the caller named one
        # explicitly. The route refuses unsigned events outright now, so an
        # unsigned default would only ever exercise the 503 path.
        effective = secret if secret is not None else settings.weave_ingest_webhook_secret
        if effective:
            headers[events_module._SIGNATURE_HEADER] = _sign(body, effective)
    elif signature is not None:
        headers[events_module._SIGNATURE_HEADER] = signature
    return client.post(_URL, content=body, headers=headers)


def _get_document(source_job_id: str) -> Document | None:
    db = TestingSessionLocal()
    try:
        return db.query(Document).filter_by(source_job_id=source_job_id).one_or_none()
    finally:
        db.close()


# --- Signature verification ---------------------------------------------------


def test_missing_signature_header_returns_401_when_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(), secret=None, signature=None)
    assert resp.status_code == 401


def test_wrong_signature_returns_401(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(), signature='sha256=' + '0' * 64)
    assert resp.status_code == 401


def test_malformed_signature_header_returns_401(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(), signature='not-a-valid-header')
    assert resp.status_code == 401


def test_correct_signature_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()
    resp = _post(payload, secret='top-secret')
    assert resp.status_code == 202
    assert resp.json()['status'] == 'accepted'


def test_unsigned_event_is_rejected(monkeypatch):
    # A configured secret means an unsigned event is never accepted.
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(), secret=None, signature=None)
    assert resp.status_code == 401


# --- Event-type filtering ------------------------------------------------------


def test_non_document_processed_event_is_ignored(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post({'event': 'job.finished', 'job': {'id': 'x'}})
    assert resp.status_code == 204
    _mock_index_document_delay.assert_not_called()
    db = TestingSessionLocal()
    try:
        assert db.query(Document).count() == 0
        assert db.query(IngestEvent).count() == 0
    finally:
        db.close()


# --- FIX 2: collection.updated -> immediate resync ------------------------------
#
# An access revocation (read_teams shrinking) at Weave-Ingest must take
# effect immediately, not wait out the periodic collection_sync_tick (see
# app/api/events.py's _handle_collection_updated docstring and
# app/core/config.py's collection_sync_tick_seconds).


def test_collection_updated_event_triggers_an_immediate_sync(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock()
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    resp = _post({'event': 'collection.updated'})

    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok'}
    sync_mock.assert_called_once()
    # Never touches the document.processed pipeline.
    _mock_index_document_delay.assert_not_called()


def test_collection_updated_event_requires_a_valid_signature(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock()
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    resp = _post({'event': 'collection.updated'}, secret=None, signature=None)

    assert resp.status_code == 401
    sync_mock.assert_not_called()


def test_collection_updated_event_actually_updates_the_local_mirror(monkeypatch):
    """End-to-end (no mocked sync_collections this time): a real registry
    fetch is triggered synchronously by the event, so a shrunk `read_teams`
    is reflected in the local `collections` mirror by the time this request
    returns -- no waiting for a tick."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    db = TestingSessionLocal()
    try:
        db.add(Collection(slug='handbuch', name='Handbuch', read_teams=['support', 'sales']))
        db.commit()
    finally:
        db.close()

    # Same canonical {'items': [...]} envelope as Weave-Ingest's real
    # CollectionRegistryResponse (see test_collection_sync.py) -- read_teams
    # has shrunk to just 'support', simulating a revoked 'sales' access.
    registry_payload = {
        'items': [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'read_teams': ['support']}]
    }
    with patch('app.services.collection_sync.httpx.get', return_value=MagicMock(
        status_code=200, json=lambda: registry_payload,
    )):
        resp = _post({'event': 'collection.updated'})

    assert resp.status_code == 200

    db = TestingSessionLocal()
    try:
        handbuch = db.get(Collection, 'handbuch')
        assert handbuch is not None
        assert handbuch.read_teams == ['support']
    finally:
        db.close()


def test_collection_updated_event_failed_sync_still_returns_200(monkeypatch, caplog):
    """A failing immediate sync (Weave-Ingest unreachable) must not push
    Weave-Ingest's webhook delivery into a retry loop -- only logged, still
    200 (see _handle_collection_updated's docstring)."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock(side_effect=CollectionSyncError('weave-ingest unreachable'))
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    with caplog.at_level('WARNING'):
        resp = _post({'event': 'collection.updated'})

    assert resp.status_code == 200
    sync_mock.assert_called_once()
    assert any('collection.updated' in record.getMessage() for record in caplog.records)


def test_collection_updated_event_does_not_write_an_ingest_event_row(monkeypatch):
    """No idempotency ledger entry for this event type -- it carries no
    job_id/content_sha256 to key one on, and a duplicate sync is harmless
    (see _handle_collection_updated's docstring)."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock()
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    _post({'event': 'collection.updated'})
    _post({'event': 'collection.updated'})

    assert sync_mock.call_count == 2  # both delivered, neither deduped
    db = TestingSessionLocal()
    try:
        assert db.query(IngestEvent).count() == 0
    finally:
        db.close()


def test_invalid_json_body_returns_400(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    body = b'not json at all'
    resp = client.post(
        _URL,
        content=body,
        headers={
            'Content-Type': 'application/json',
            events_module._SIGNATURE_HEADER: _sign(body, 'top-secret'),
        },
    )
    assert resp.status_code == 400


def test_missing_required_field_returns_400(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()
    del payload['job_id']
    resp = _post(payload)
    assert resp.status_code == 400


# --- Idempotency ---------------------------------------------------------------


def test_processed_event_awaits_release_without_persistence(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = {'event': 'document.processed'}

    resp = _post(payload)

    assert resp.status_code == 200
    assert resp.json() == {'status': 'awaiting_release'}
    _mock_index_document_delay.assert_not_called()
    db = TestingSessionLocal()
    try:
        assert db.query(Document).count() == 0
        assert db.query(IngestEvent).count() == 0
    finally:
        db.close()


def test_released_event_owns_reserved_frontmatter_provenance(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload(frontmatter={
        'source': 'report.pdf',
        '_weave_release_id': str(uuid.uuid4()),
        '_weave_markdown_sha256': '0' * 64,
    })

    resp = _post(payload)

    assert resp.status_code == 202
    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.frontmatter['_weave_release_id'] == payload['release_id']
    assert document.frontmatter['_weave_markdown_sha256'] == payload['markdown_sha256']
    assert document.frontmatter['source'] == 'report.pdf'


def test_different_release_for_same_job_is_rejected_without_overwrite(
    monkeypatch, _mock_index_document_delay,
):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    first = _event_payload()
    assert _post(first).status_code == 202

    second = _event_payload(
        job_id=first['job_id'], release_id=str(uuid.uuid4()), markdown_sha256='c' * 64,
        content_sha256='d' * 64,
    )
    resp = _post(second)

    assert resp.status_code == 409
    document = _get_document(first['job_id'])
    assert document is not None
    assert document.frontmatter['_weave_release_id'] == first['release_id']
    assert document.content_sha256 == first['content_sha256']
    _mock_index_document_delay.assert_called_once()
    db = TestingSessionLocal()
    try:
        assert db.query(IngestEvent).filter_by(event_key=f"release:{second['release_id']}").count() == 0
    finally:
        db.close()


def test_duplicate_event_is_not_double_processed(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()

    first = _post(payload)
    assert first.status_code == 202
    assert first.json()['status'] == 'accepted'

    second = _post(payload)
    assert second.status_code == 200
    assert second.json() == {'status': 'duplicate'}

    # The first delivery queued the document; the duplicate must also
    # re-offer it because the first enqueue may have been lost after commit.
    assert _mock_index_document_delay.call_count == 2

    db = TestingSessionLocal()
    try:
        assert db.query(Document).filter_by(source_job_id=payload['job_id']).count() == 1
        assert db.query(IngestEvent).filter_by(event_key=f"release:{payload['release_id']}").count() == 1
    finally:
        db.close()


def test_enqueue_failure_after_commit_is_recovered_by_duplicate_delivery(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()
    enqueue = MagicMock(side_effect=[RuntimeError('redis down'), None])
    monkeypatch.setattr(events_module.index_document, 'delay', enqueue)

    # TestClient surfaces the server exception; in production this is the
    # required HTTP 500 that causes the outbox to redeliver.
    with pytest.raises(RuntimeError, match='redis down'):
        _post(payload)

    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.status == DocumentStatus.PENDING

    retry = _post(payload)
    assert retry.status_code == 200
    assert retry.json() == {'status': 'duplicate'}
    assert enqueue.call_count == 2


def test_duplicate_indexed_release_never_reenqueues(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()
    assert _post(payload).status_code == 202

    db = TestingSessionLocal()
    try:
        document = db.query(Document).filter_by(source_job_id=payload['job_id']).one()
        document.status = DocumentStatus.INDEXED
        db.commit()
    finally:
        db.close()

    retry = _post(payload)
    assert retry.status_code == 200
    assert retry.json() == {'status': 'duplicate'}
    _mock_index_document_delay.assert_called_once()


def test_duplicate_detection_keys_on_job_id_and_content_sha256_not_timestamp(monkeypatch):
    """A redelivery of the same (job_id, content_sha256) with a freshly
    rebuilt `timestamp` (exactly what Weave-Ingest's own retry does -- see
    build_document_processed_payload rebuilding it on every delivery
    attempt) must still be recognized as the same event."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()
    _post(payload)

    retried = dict(payload)
    retried['timestamp'] = '2026-08-31T15:00:00.000000+00:00'
    resp = _post(retried)
    assert resp.status_code == 200
    assert resp.json() == {'status': 'duplicate'}


# --- FINDING 1: source_job_id unique-constraint race ----------------------------


def test_concurrent_same_job_id_different_releases_returns_409(monkeypatch, _mock_index_document_delay):
    """Two released events for the SAME job_id but DIFFERENT releases can
    both pass event dedup before either inserts its Document. The loser of
    the source_job_id race must then be rejected conservatively, without
    overwriting the winning release.
    documents.source_job_id's own unique constraint. This simulates the
    loser's exact failure point: a colliding row committed by a concurrent
    session in the gap between this request's own SELECT and its own
    commit (see app/api/events.py's module docstring, FINDING 1)."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    job_id = str(uuid.uuid4())
    payload = _event_payload(job_id=job_id, content_sha256='a' * 64)
    winning_release_id = str(uuid.uuid4())

    real_commit = OrmSession.commit
    state = {'raced': False}

    def _racy_commit(self, *args, **kwargs):
        if not state['raced']:
            state['raced'] = True
            # This session already holds SQLite's single writer lock (from
            # the IngestEvent db.flush() earlier in the request) -- release
            # it first so the "concurrent" session below can actually write,
            # same as two truly separate connections/processes would not
            # block each other on a real database. Then simulate the
            # concurrent request winning the source_job_id race by
            # committing its own Document row for the same job_id, and
            # raise the exact failure this session's own commit would have
            # hit had it reached the database first (a real UNIQUE
            # violation, mocked here purely to sidestep SQLite's
            # same-process single-writer limitation).
            self.rollback()
            other = TestingSessionLocal()
            try:
                other.add(Document(
                    source_job_id=job_id, content_sha256='c' * 64, engine='paddleocr',
                    frontmatter={
                        '_weave_release_id': winning_release_id,
                        '_weave_markdown_sha256': 'c' * 64,
                    }, tags=[], processed_at=datetime.now(timezone.utc),
                ))
                real_commit(other)
            finally:
                other.close()
            raise IntegrityError('INSERT INTO documents (...)', {}, Exception('UNIQUE constraint failed: documents.source_job_id'))
        return real_commit(self, *args, **kwargs)

    monkeypatch.setattr(OrmSession, 'commit', _racy_commit)

    resp = _post(payload)

    assert resp.status_code == 409

    db = TestingSessionLocal()
    try:
        documents = db.query(Document).filter_by(source_job_id=job_id).all()
        assert len(documents) == 1
        assert documents[0].content_sha256 == 'c' * 64
        assert documents[0].frontmatter['_weave_release_id'] == winning_release_id

        assert db.query(IngestEvent).filter_by(event_key=f"release:{payload['release_id']}").count() == 0
    finally:
        db.close()

    _mock_index_document_delay.assert_not_called()


# --- Block recommendation -------------------------------------------------------


def test_block_recommendation_creates_blocked_document_without_task(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload(quality={'grade': 'C', 'recommendation': 'block', 'signals': {}})

    resp = _post(payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'blocked'
    assert 'document_id' in body

    _mock_index_document_delay.assert_not_called()

    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.status == DocumentStatus.BLOCKED
    assert document.quality_recommendation == 'block'


# --- Null quality (warn semantics) ----------------------------------------------


def test_null_quality_is_indexed_like_warn(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload(quality={'grade': None, 'recommendation': None, 'signals': {}})

    resp = _post(payload)

    assert resp.status_code == 202
    assert resp.json()['status'] == 'accepted'

    _mock_index_document_delay.assert_called_once()

    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.status == DocumentStatus.PENDING
    # Raw wire value preserved verbatim -- None, not silently rewritten to
    # 'warn' -- only the *routing decision* (indexed vs. blocked) treats it
    # like 'warn'.
    assert document.quality_grade is None
    assert document.quality_recommendation is None


# --- Document upsert field mapping ----------------------------------------------


def test_accepted_document_denormalizes_frontmatter_fields(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload()

    resp = _post(payload)
    assert resp.status_code == 202
    document_id = resp.json()['document_id']

    document = _get_document(payload['job_id'])
    assert document is not None
    assert str(document.id) == document_id
    assert document.team == 'Kundenservice'
    assert document.department == 'Support'
    assert document.tags == ['section-1', 'important']
    assert document.engine == 'paddleocr'
    assert document.content_sha256 == payload['content_sha256']
    assert document.markdown_url == payload['markdown_url']
    assert document.frontmatter == {
        **payload['frontmatter'],
        '_weave_release_id': payload['release_id'],
        '_weave_markdown_sha256': payload['markdown_sha256'],
    }
    assert document.previous_job_id is None

    _mock_index_document_delay.assert_called_once_with(document_id)


# --- FINDING 2: job_id/content_sha256 pattern validation -------------------------
#
# app/api/events.py converts every DocumentReleasedEvent ValidationError to
# 400 (not FastAPI's default 422) -- see that module's manual
# `except ValidationError: raise HTTPException(400, ...)` and the existing
# test_missing_required_field_returns_400 above, which this mirrors for
# consistency with this route's own established convention.


def test_job_id_must_be_a_uuid(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(job_id='not-a-uuid'))
    assert resp.status_code == 400


def test_content_sha256_must_be_64_lowercase_hex_chars(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(content_sha256='not-hex'))
    assert resp.status_code == 400


def test_content_sha256_rejects_uppercase_hex(monkeypatch):
    """The contract's sha256 is lowercase hex; an uppercase-hex value of the
    right length would otherwise silently be treated as a DIFFERENT
    dedup key than its lowercase form for the same underlying content."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    resp = _post(_event_payload(content_sha256='A' * 64))
    assert resp.status_code == 400


def test_release_id_and_markdown_sha256_are_validated(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    assert _post(_event_payload(release_id='not-a-uuid')).status_code == 400
    assert _post(_event_payload(markdown_sha256='A' * 64)).status_code == 400


def test_colliding_delimiter_shifted_payloads_are_both_rejected(monkeypatch):
    """job_id='X:Y' + content_sha256='Z' would build the exact same
    event_key string (f'{job_id}:{content_sha256}') as job_id='X' +
    content_sha256='Y:Z' if job_id/content_sha256 were unvalidated
    free-form strings -- silently merging two distinct documents'
    idempotency ledger entries. Requiring job_id to be UUID-shaped and
    content_sha256 to be exactly 64 hex chars makes every operand of that
    collision structurally invalid, so both variants are rejected long
    before an event_key is ever built."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    base_uuid = '11111111-1111-1111-1111-111111111111'
    colliding_a = _event_payload(job_id=f'{base_uuid}:aa', content_sha256='b' * 64)
    colliding_b = _event_payload(job_id=base_uuid, content_sha256='aa:' + 'b' * 64)

    assert _post(colliding_a).status_code == 400
    assert _post(colliding_b).status_code == 400


def test_valid_uuid_job_id_and_hex_sha256_are_still_accepted(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    payload = _event_payload(job_id=str(uuid.uuid4()), content_sha256='f' * 64)

    resp = _post(payload)

    assert resp.status_code == 202
    assert resp.json()['status'] == 'accepted'


# --- Collections (contract point 3): denormalization + lazy registry reload -----


def test_event_without_collection_leaves_collection_slug_none(monkeypatch, _mock_index_document_delay):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock()
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    payload = _event_payload()  # default frontmatter carries no 'collection' key
    resp = _post(payload)

    assert resp.status_code == 202
    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.collection_slug is None
    sync_mock.assert_not_called()


def test_event_with_already_known_collection_denormalizes_slug_without_syncing(
    monkeypatch, _mock_index_document_delay,
):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    db = TestingSessionLocal()
    try:
        db.add(Collection(slug='handbuch', name='Handbuch'))
        db.commit()
    finally:
        db.close()

    sync_mock = MagicMock()
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    payload = _event_payload()
    payload['frontmatter'] = dict(payload['frontmatter'], collection='handbuch', collection_name='Handbuch')
    resp = _post(payload)

    assert resp.status_code == 202
    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.collection_slug == 'handbuch'
    # Already in the local registry mirror -- no lazy reload needed.
    sync_mock.assert_not_called()


def test_event_with_unknown_collection_triggers_one_lazy_sync_and_still_indexes(
    monkeypatch, _mock_index_document_delay,
):
    """contract point 3: an unknown slug gets exactly one lazy-reload
    attempt; a document is still indexed regardless of the outcome. Here the
    lazy reload 'succeeds' (simulated by the fake sync itself inserting the
    row the caller is looking for), which is the common case: the new
    collection has just been created upstream and this is its first
    document."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')

    def _fake_sync(sync_db):
        sync_db.add(Collection(slug='brand-new', name='Brand New'))
        sync_db.commit()

    sync_mock = MagicMock(side_effect=_fake_sync)
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    payload = _event_payload()
    payload['frontmatter'] = dict(payload['frontmatter'], collection='brand-new')
    resp = _post(payload)

    assert resp.status_code == 202
    assert resp.json()['status'] == 'accepted'
    _mock_index_document_delay.assert_called_once()

    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.collection_slug == 'brand-new'
    sync_mock.assert_called_once()

    db = TestingSessionLocal()
    try:
        assert db.get(Collection, 'brand-new') is not None
    finally:
        db.close()


def test_event_with_unknown_collection_and_failed_lazy_sync_still_indexes(
    monkeypatch, _mock_index_document_delay, caplog,
):
    """contract point 3, the unhappy path: the lazy reload itself fails
    (Weave-Ingest unreachable, say) -- the document must STILL be indexed,
    only a warning gets logged. Search-time filtering over this collection
    is Weave-Retrieval's problem once the registry does eventually catch
    up, not something this webhook may block on."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    sync_mock = MagicMock(side_effect=CollectionSyncError('weave-ingest unreachable'))
    monkeypatch.setattr(events_module, 'sync_collections', sync_mock)

    payload = _event_payload()
    payload['frontmatter'] = dict(payload['frontmatter'], collection='still-unknown')

    with caplog.at_level('WARNING'):
        resp = _post(payload)

    assert resp.status_code == 202
    assert resp.json()['status'] == 'accepted'
    _mock_index_document_delay.assert_called_once()

    document = _get_document(payload['job_id'])
    assert document is not None
    # The slug is still recorded on the document even though the registry
    # doesn't (yet) know it -- see Document.collection_slug's docstring on
    # why there is no FK enforcing the opposite.
    assert document.collection_slug == 'still-unknown'
    sync_mock.assert_called_once()
    assert any('still-unknown' in record.getMessage() for record in caplog.records)


def test_blocked_document_still_gets_its_collection_slug(monkeypatch, _mock_index_document_delay):
    """A blocked document isn't indexed now, but it should still carry its
    collection for whenever it is eventually re-processed and accepted --
    see this module's own docstring on why step 5's denormalization doesn't
    branch on the block/accept outcome."""
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'top-secret')
    db = TestingSessionLocal()
    try:
        db.add(Collection(slug='handbuch', name='Handbuch'))
        db.commit()
    finally:
        db.close()

    payload = _event_payload(quality={'grade': 'C', 'recommendation': 'block', 'signals': {}})
    payload['frontmatter'] = dict(payload['frontmatter'], collection='handbuch')

    resp = _post(payload)

    assert resp.status_code == 200
    assert resp.json()['status'] == 'blocked'
    document = _get_document(payload['job_id'])
    assert document is not None
    assert document.status == DocumentStatus.BLOCKED
    assert document.collection_slug == 'handbuch'
    _mock_index_document_delay.assert_not_called()


def test_unset_webhook_secret_refuses_events(monkeypatch):
    # Regression: an empty secret used to skip verification entirely, leaving
    # the route that writes into the index open to anyone who could reach it.
    from app.core.config import settings

    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', '')
    resp = client.post(
        '/api/v1/events/ingest',
        content=b'{"event":"document.processed"}',
        headers={'Content-Type': 'application/json'},
    )
    assert resp.status_code == 503
    assert 'misconfigured' in resp.json()['detail']
