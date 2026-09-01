"""Outbound webhook delivery: signature correctness and payload shape
(app/services/webhooks.py), the deliver_webhook worker task's happy path /
final-4xx / retried-5xx paths (app/workers/webhook_tasks.py), and the
job-completion dispatch hook (app/workers/tasks.py's process_job) creating a
delivery only when the job/run was itself configured with a
webhook_connection_id -- never a fan-out to every connection subscribed to
the event -- and never breaking job completion on a webhook failure.

Also covers 'document.processed' (contracts/events/document.processed.md):
its payload shape (frontmatter parsed back out of result_markdown, the real
quality_gate signals, markdown_url instead of inline markdown), that it rides
the exact same dispatch/delivery/signature machinery as job.finished, and
that it is excluded for benchmark variant jobs the same way job.finished
already is (per-task opt-in -- see dispatch_job_event's docstring).

Also covers 'collection.updated' (contracts/events/collection.updated.md):
its thin payload shape (slug/name/description/read_teams/updated_at), that
it rides the same delivery/signature/retry machinery as every other event,
and its deliberately different dispatch shape -- a fan-out to every enabled
connection subscribed to it, across every owner, triggered from
app/api/routes.py's collection create/update rather than a job/run
completion hook (see dispatch_collection_event's own docstring for why this
is the one intentional exception to the "never a fan-out" rule above).

Drives deliver_webhook directly against the shared sqlite test DB
(SessionLocal monkeypatched to conftest's TestingSessionLocal), same pattern
as test_openwebui_tasks.py; send_webhook_request is mocked at the
app.workers.webhook_tasks seam so no real network is needed.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import yaml

from app.models.models import Collection, ImportRun, Job, JobStatus, WebhookConnection, WebhookDelivery
from app.services import security
from app.services.webhooks import (
    build_collection_updated_payload,
    build_document_processed_payload,
    build_job_payload,
    build_run_payload,
    send_webhook_request,
)
from app.workers import webhook_tasks
from app.workers.webhook_tasks import deliver_webhook
from tests.conftest import TestingSessionLocal, create_test_user


@pytest.fixture()
def db_session(monkeypatch):
    monkeypatch.setattr(webhook_tasks, 'SessionLocal', TestingSessionLocal)
    return TestingSessionLocal()


def _make_connection(db, owner_id: str, *, events=('job.finished', 'job.failed'), secret: str | None = None, enabled: bool = True) -> WebhookConnection:
    connection = WebhookConnection(
        owner_id=owner_id,
        name='n8n',
        url='https://n8n.example.com/webhook/abc',
        events=list(events),
        enabled=enabled,
        secret_encrypted=security.encrypt_webhook_secret(secret) if secret else None,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _make_job(
    db, owner_id: str | None, *, status=JobStatus.FINISHED, filename='report.pdf', markdown='hello',
    error_message=None, tags=None, webhook_connection_id: str | None = None,
) -> Job:
    settings_info = {'profile_id': 'ppocrv6_small', 'folder': 'inbox', 'subfolder': 'q3'}
    if webhook_connection_id:
        settings_info['webhook_connection_id'] = webhook_connection_id
    job = Job(
        original_filename=filename,
        upload_path=f'/tmp/{filename}',
        status=status,
        result_markdown=markdown if status == JobStatus.FINISHED else None,
        error_message=error_message,
        owner_id=owner_id,
        document_version=1,
        content_sha256='deadbeef' * 8,
        processing_info={'settings': settings_info},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _quality_gate(grade: str = 'A', recommendation: str = 'allow', **signal_overrides) -> dict:
    """A realistic app/services/quality_gate.evaluate_document_quality
    return value -- the real 'signals' keys (ocr_confidence,
    confidence_sample_size, structure_quality, noise_penalty, text_quality,
    field_validation), not the placeholder shape an earlier contract draft
    had (text_extraction_confidence/layout_preservation/language_detection)."""
    signals = {
        'ocr_confidence': 0.95,
        'confidence_sample_size': 120,
        'structure_quality': 0.9,
        'noise_penalty': 0.02,
        'text_quality': 0.98,
        'field_validation': {},
    }
    signals.update(signal_overrides)
    return {'grade': grade, 'score': 0.95, 'recommendation': recommendation, 'issues': [], 'signals': signals}


def _frontmatter_markdown(data: dict, body: str = '# Report\n\nhello world') -> str:
    """A markdown result exactly as app/services/paddle_service.py's
    _build_rag_frontmatter / _prepend_frontmatter write one:
    '---\\n<yaml>---\\n\\n<body>'."""
    dumped = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f'---\n{dumped}---\n\n{body}'


def _make_run(db, owner_id: str | None, *, webhook_connection_id: str | None = None, pages_imported: int = 0, pages_failed: int = 0) -> ImportRun:
    options: dict = {}
    if webhook_connection_id:
        options['webhook_connection_id'] = webhook_connection_id
    run = ImportRun(
        owner_id=owner_id, kind='confluence', scope_type='space', scope_value='ENG',
        options=options, state={'frontier': [], 'visited': {}, 'errors': []},
        pages_imported=pages_imported, pages_failed=pages_failed,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_delivery(
    db, connection: WebhookConnection, *,
    job_id: str | None = None, import_run_id: str | None = None, collection_id: str | None = None,
    event: str = 'job.finished',
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        connection_id=connection.id,
        connection_name=connection.name,
        owner_id=connection.owner_id,
        event=event,
        job_id=job_id,
        import_run_id=import_run_id,
        collection_id=collection_id,
        status='pending',
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def _make_collection(
    db, owner_id: str | None, *, name: str = 'Kundenservice', read_teams=('kundenservice',), description=None,
) -> Collection:
    collection = Collection(
        owner_id=owner_id,
        slug=f'coll-{uuid.uuid4().hex[:8]}',
        name=name,
        description=description,
        read_teams=list(read_teams),
    )
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return collection


def _quiet_other_collection_updated_connections(db) -> None:
    """dispatch_collection_event fans out to EVERY enabled connection
    subscribed to 'collection.updated', system-wide (see its own docstring)
    -- unlike every other dispatch_*_event test in this file, which only
    ever touches connections/deliveries scoped to one job/run id it created
    itself, a fan-out test's "no delivery"/"exactly N deliveries" assertion
    is vulnerable to a 'collection.updated'-subscribed connection some
    OTHER test already committed to this suite's one shared, never-reset
    sqlite database (see conftest.py). Disabling every such pre-existing
    connection first makes each fan-out test deterministic regardless of
    what ran before it."""
    for connection in db.query(WebhookConnection).filter(WebhookConnection.enabled.is_(True)).all():
        if 'collection.updated' in (connection.events or []):
            connection.enabled = False
    db.commit()


# --- send_webhook_request: signature correctness -----------------------------

def test_send_webhook_request_signs_body_with_known_secret() -> None:
    payload = {'event': 'job.finished', 'timestamp': '2026-08-25T00:00:00+00:00'}
    secret = 'shh-its-a-secret'

    captured = {}

    class _FakeResponse:
        status_code = 200
        body = b'{}'

    def _fake_safe_fetch(url, *, method, headers, body, timeout, max_bytes, allowed_private_hosts):
        captured['headers'] = headers
        captured['body'] = body
        return _FakeResponse()

    with patch('app.services.webhooks.safe_fetch', side_effect=_fake_safe_fetch):
        http_status, error_message = send_webhook_request(
            'https://example.com/hook', payload, secret, allowed_private_hosts=frozenset()
        )

    assert http_status == 200
    assert error_message is None
    assert captured['headers']['X-Weave-Ingest-Event'] == 'job.finished'
    expected_hex = hmac.new(secret.encode('utf-8'), captured['body'], hashlib.sha256).hexdigest()
    assert captured['headers']['X-Weave-Ingest-Signature'] == f'sha256={expected_hex}'


def test_send_webhook_request_no_secret_sends_no_signature_header() -> None:
    payload = {'event': 'job.failed'}
    captured = {}

    class _FakeResponse:
        status_code = 200
        body = b'{}'

    def _fake_safe_fetch(url, *, method, headers, body, timeout, max_bytes, allowed_private_hosts):
        captured['headers'] = headers
        return _FakeResponse()

    with patch('app.services.webhooks.safe_fetch', side_effect=_fake_safe_fetch):
        send_webhook_request('https://example.com/hook', payload, None, allowed_private_hosts=frozenset())

    assert 'X-Weave-Ingest-Signature' not in captured['headers']


def test_send_webhook_request_transport_failure_returns_zero_status() -> None:
    from app.services.safe_fetch import SafeFetchError

    with patch('app.services.webhooks.safe_fetch', side_effect=SafeFetchError('blocked: private address')):
        http_status, error_message = send_webhook_request(
            'https://example.com/hook', {'event': 'job.finished'}, None, allowed_private_hosts=frozenset()
        )
    assert http_status == 0
    assert 'blocked' in error_message


def test_send_webhook_request_4xx_returns_status_and_detail() -> None:
    class _FakeResponse:
        status_code = 422
        body = b'bad payload'

    with patch('app.services.webhooks.safe_fetch', return_value=_FakeResponse()):
        http_status, error_message = send_webhook_request(
            'https://example.com/hook', {'event': 'job.finished'}, None, allowed_private_hosts=frozenset()
        )
    assert http_status == 422
    assert error_message == 'bad payload'


# --- Payload builders ---------------------------------------------------------

def test_build_job_payload_shape_finished_includes_markdown() -> None:
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_payload_user', email='webhook_payload_user@example.com')
        job = _make_job(db, user.id, markdown='# hi')
        payload = build_job_payload(db, job, 'job.finished', include_markdown=True)

        assert payload['event'] == 'job.finished'
        assert payload['markdown'] == '# hi'
        assert payload['error_message'] is None
        assert payload['job']['id'] == job.id
        assert payload['job']['filename'] == 'report.pdf'
        assert payload['job']['status'] == 'FINISHED'
        assert payload['job']['folder'] == 'inbox'
        assert payload['job']['subfolder'] == 'q3'
        assert payload['job']['profile_id'] == 'ppocrv6_small'
        assert payload['job']['document_version'] == 1
        assert payload['job']['content_sha256'] == job.content_sha256
        assert payload['download_url'].endswith(f'/api/v1/jobs/{job.id}/download')
        datetime.fromisoformat(payload['timestamp'])  # parses without raising
    finally:
        db.close()


def test_build_job_payload_failed_has_null_markdown_and_error_message() -> None:
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_payload_user2', email='webhook_payload_user2@example.com')
        job = _make_job(db, user.id, status=JobStatus.FAILED, error_message='OCR timed out')
        payload = build_job_payload(db, job, 'job.failed', include_markdown=False)

        assert payload['markdown'] is None
        assert payload['error_message'] == 'OCR timed out'
        assert payload['job']['status'] == 'FAILED'
    finally:
        db.close()


def test_build_run_payload_shape() -> None:
    run = ImportRun(
        source_id=None,
        owner_id=None,
        kind='confluence',
        scope_type='space',
        scope_value='ENG',
        options={},
        state={'frontier': [], 'visited': {}, 'errors': []},
        pages_imported=7,
        pages_failed=2,
    )
    run.id = 'run-1'
    payload = build_run_payload(run)
    assert payload == {
        'event': 'import_run.finished',
        'timestamp': payload['timestamp'],
        'run': {
            'id': 'run-1',
            'scope_type': 'space',
            'scope_value': 'ENG',
            'pages_imported': 7,
            'pages_failed': 2,
        },
    }
    datetime.fromisoformat(payload['timestamp'])


# --- build_collection_updated_payload ------------------------------------------

def test_build_collection_updated_payload_shape() -> None:
    """Deliberately thin (contracts/events/collection.updated.md): exactly
    the four registry fields plus updated_at, nothing job-/document-shaped."""
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_coll_payload_user', email='webhook_coll_payload_user@example.com')
        collection = _make_collection(
            db, user.id, name='Kundenservice 2026', read_teams=['kundenservice', 'qm'], description='Q3/Q4 Anfragen',
        )
        payload = build_collection_updated_payload(collection)

        assert payload == {
            'event': 'collection.updated',
            'timestamp': payload['timestamp'],
            'slug': collection.slug,
            'name': 'Kundenservice 2026',
            'description': 'Q3/Q4 Anfragen',
            'read_teams': ['kundenservice', 'qm'],
            'updated_at': collection.updated_at.isoformat(),
        }
        datetime.fromisoformat(payload['timestamp'])
    finally:
        db.close()


def test_build_collection_updated_payload_null_description_and_empty_read_teams() -> None:
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_coll_payload_user2', email='webhook_coll_payload_user2@example.com')
        collection = _make_collection(db, user.id, read_teams=[], description=None)
        payload = build_collection_updated_payload(collection)

        assert payload['description'] is None
        assert payload['read_teams'] == []
    finally:
        db.close()


# --- build_document_processed_payload -----------------------------------------

def test_build_document_processed_payload_shape() -> None:
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_docproc_user', email='webhook_docproc_user@example.com')
        job_id = 'job-doc-proc-1'
        frontmatter_data = {
            'source': f'{job_id}.pdf',
            'original_filename': 'report.pdf',
            'pages': 3,
            'profile': 'Standard OCR',
            'profile_id': 'ppocrv6_small',
            'mode': 'single',
            'job_id': job_id,
            'document_version': 1,
            'content_sha256': 'a' * 64,
            'processed_at': '2026-08-31T14:23:45Z',
            'engine': 'paddleocr',
        }
        job = Job(
            id=job_id,
            original_filename='report.pdf',
            upload_path=f'/tmp/{job_id}.pdf',
            status=JobStatus.FINISHED,
            result_markdown=_frontmatter_markdown(frontmatter_data),
            owner_id=user.id,
            document_version=1,
            content_sha256='a' * 64,
            processing_info={
                'settings': {'profile_id': 'ppocrv6_small', 'mode': 'single'},
                'execution': {
                    'status': 'finished',
                    'engine': 'paddleocr',
                    'finished_at': '2026-08-31T14:23:45.500000+00:00',
                    'quality_gate': _quality_gate(),
                },
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = build_document_processed_payload(job)

        assert payload['event'] == 'document.processed'
        assert payload['job_id'] == job_id
        assert payload['document_version'] == 1
        assert payload['previous_job_id'] is None
        assert payload['content_sha256'] == 'a' * 64
        assert payload['original_filename'] == 'report.pdf'
        assert payload['markdown_url'].endswith(f'/api/v1/jobs/{job_id}/download')
        assert payload['frontmatter']['source'] == frontmatter_data['source']
        assert payload['frontmatter']['pages'] == 3
        assert payload['quality'] == {
            'grade': 'A',
            'recommendation': 'allow',
            'signals': _quality_gate()['signals'],
        }
        assert payload['engine'] == 'paddleocr'
        # engine/processed_at come from the parsed frontmatter, not a
        # separately-derived value -- guarantees they match what a consumer
        # sees when it actually fetches markdown_url.
        assert payload['processed_at'] == '2026-08-31T14:23:45Z'
        datetime.fromisoformat(payload['timestamp'])  # parses without raising
    finally:
        db.close()


def test_build_document_processed_payload_carries_version_lineage() -> None:
    """document_version/previous_job_id come straight off the Job row, and
    quality.grade/recommendation are the quality_gate's own values verbatim
    (not re-derived) -- exercised here with a non-default grade/recommendation
    pair and a real reprocessing lineage."""
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_docproc_user2', email='webhook_docproc_user2@example.com')
        # previous_job_id is a real FK to jobs.id -- the predecessor row must
        # exist first.
        predecessor = Job(
            id='job-v1', original_filename='report-v1.pdf', upload_path='/tmp/report-v1.pdf',
            status=JobStatus.FINISHED, owner_id=user.id, content_sha256='b' * 64,
        )
        db.add(predecessor)
        db.commit()
        job = Job(
            id='job-v2',
            original_filename='report-v2.pdf',
            upload_path='/tmp/report-v2.pdf',
            status=JobStatus.FINISHED,
            result_markdown=_frontmatter_markdown({'engine': 'pypdf-fallback', 'processed_at': '2026-08-31T00:00:00Z'}),
            owner_id=user.id,
            document_version=2,
            previous_job_id='job-v1',
            content_sha256='b' * 64,
            processing_info={
                'execution': {'engine': 'pypdf-fallback', 'quality_gate': _quality_gate(grade='B', recommendation='warn')},
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = build_document_processed_payload(job)
        assert payload['document_version'] == 2
        assert payload['previous_job_id'] == 'job-v1'
        assert payload['quality']['grade'] == 'B'
        assert payload['quality']['recommendation'] == 'warn'
        assert payload['engine'] == 'pypdf-fallback'
    finally:
        db.close()


def test_build_document_processed_payload_falls_back_without_frontmatter() -> None:
    """A result with no recognizable frontmatter block (malformed/legacy)
    must never crash payload building -- frontmatter degrades to {}, and
    engine/processed_at fall back to job.processing_info['execution']."""
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_docproc_user3', email='webhook_docproc_user3@example.com')
        job = Job(
            id='job-no-fm',
            original_filename='weird.eml',
            upload_path='/tmp/weird.eml',
            status=JobStatus.FINISHED,
            result_markdown='no frontmatter at all, just body text',
            owner_id=user.id,
            document_version=1,
            content_sha256='c' * 64,
            processing_info={
                'execution': {
                    'engine': 'mail-eml',
                    'finished_at': '2026-08-31T10:00:00+00:00',
                    'quality_gate': _quality_gate(grade='C', recommendation='block'),
                },
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = build_document_processed_payload(job)
        assert payload['frontmatter'] == {}
        assert payload['engine'] == 'mail-eml'
        assert payload['processed_at'] == '2026-08-31T10:00:00+00:00'
        assert payload['quality']['grade'] == 'C'
        assert payload['quality']['recommendation'] == 'block'
    finally:
        db.close()


def test_build_document_processed_payload_missing_quality_gate_defaults_gracefully() -> None:
    """Defensive: an execution dict with no quality_gate at all (e.g. a test
    double, or a future code path) must not raise -- quality degrades to
    None/None/{} rather than crashing the whole dispatch hook."""
    db = TestingSessionLocal()
    try:
        user = create_test_user(username='webhook_docproc_user4', email='webhook_docproc_user4@example.com')
        job = Job(
            id='job-no-quality',
            original_filename='plain.pdf',
            upload_path='/tmp/plain.pdf',
            status=JobStatus.FINISHED,
            result_markdown=_frontmatter_markdown({'engine': 'paddleocr'}),
            owner_id=user.id,
            document_version=1,
            content_sha256='d' * 64,
            processing_info={'execution': {'engine': 'paddleocr'}},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = build_document_processed_payload(job)
        assert payload['quality'] == {'grade': None, 'recommendation': None, 'signals': {}}
    finally:
        db.close()


# --- deliver_webhook worker task ---------------------------------------------

def test_deliver_webhook_happy_path_marks_sent(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user', email='webhook_task_user@example.com')
    connection = _make_connection(db, user.id, secret='sekret')
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request', return_value=(200, None)) as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == connection.url
    assert args[1]['event'] == 'job.finished'
    assert args[1]['markdown'] == job.result_markdown  # job.finished -> include_markdown=True
    assert args[2] == 'sekret'

    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'sent'
    assert refreshed.http_status == 200
    assert refreshed.error_message is None
    assert refreshed.attempts == 1


def test_deliver_webhook_import_run_payload(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user_run', email='webhook_task_user_run@example.com')
    connection = _make_connection(db, user.id, events=('import_run.finished',))
    run = ImportRun(
        owner_id=user.id, kind='confluence', scope_type='space', scope_value='ENG',
        options={}, state={'frontier': [], 'visited': {}, 'errors': []},
        pages_imported=3, pages_failed=1,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    delivery = _make_delivery(db, connection, import_run_id=run.id, event='import_run.finished')
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request', return_value=(200, None)) as mock_send:
        deliver_webhook(delivery_id)

    payload = mock_send.call_args[0][1]
    assert payload['event'] == 'import_run.finished'
    assert payload['run']['id'] == run.id
    assert payload['run']['pages_imported'] == 3

    db.expire_all()
    assert db.get(WebhookDelivery, delivery_id).status == 'sent'


def test_deliver_webhook_collection_updated_payload(db_session) -> None:
    """deliver_webhook must branch on delivery.collection_id the same way it
    already branches on job_id/import_run_id, building
    build_collection_updated_payload's thin registry-nudge shape."""
    db = db_session
    user = create_test_user(username='webhook_task_coll', email='webhook_task_coll@example.com')
    connection = _make_connection(db, user.id, events=('collection.updated',), secret='sekret')
    collection = _make_collection(db, user.id, name='Kundenservice', read_teams=['kundenservice'])
    delivery = _make_delivery(db, connection, collection_id=collection.id, event='collection.updated')
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request', return_value=(200, None)) as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == connection.url
    payload = args[1]
    assert payload['event'] == 'collection.updated'
    assert payload['slug'] == collection.slug
    assert payload['name'] == 'Kundenservice'
    assert payload['read_teams'] == ['kundenservice']
    assert args[2] == 'sekret'

    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'sent'
    assert refreshed.http_status == 200


def test_deliver_webhook_collection_deleted_before_delivery_runs_fails_gracefully(db_session) -> None:
    """A collection deleted before its pending delivery is processed: unlike
    test_deliver_webhook_connection_deleted_marks_failed (where the delivery
    row keeps referencing the now-nonexistent connection_id, so
    deliver_webhook's own `db.get()` returns None), the collections.id ->
    webhook_deliveries.collection_id FK is ondelete='SET NULL' and this
    suite's sqlite runs with PRAGMA foreign_keys=ON (see conftest.py) -- so
    the SET NULL genuinely fires at the DB level on delete, and by the time
    deliver_webhook re-fetches the row, delivery.collection_id is already
    NULL rather than a dangling id (same reachability caveat already true,
    untested, for job_id/import_run_id on this exact FK-enforcing setup).
    What this exercises instead: that outcome still lands the delivery
    'failed' via the shared fallback branch, rather than crashing or leaving
    it stuck 'pending' forever."""
    db = db_session
    user = create_test_user(username='webhook_task_coll_deleted', email='webhook_task_coll_deleted@example.com')
    connection = _make_connection(db, user.id, events=('collection.updated',))
    collection = _make_collection(db, user.id)
    delivery = _make_delivery(db, connection, collection_id=collection.id, event='collection.updated')
    delivery_id = delivery.id

    db.delete(collection)
    db.commit()

    with patch('app.workers.webhook_tasks.send_webhook_request') as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_not_called()
    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'failed'
    assert refreshed.collection_id is None  # the FK's SET NULL already applied
    assert refreshed.error_message == 'delivery has neither a job, an import run, nor a collection to build a payload from'


def test_deliver_webhook_document_processed_uses_document_payload(db_session) -> None:
    """deliver_webhook must branch on delivery.event: 'document.processed'
    gets build_document_processed_payload (markdown_url, frontmatter,
    quality), never build_job_payload's inline 'markdown' field."""
    db = db_session
    user = create_test_user(username='webhook_task_docproc', email='webhook_task_docproc@example.com')
    connection = _make_connection(db, user.id, events=('document.processed',), secret='sekret')
    job = Job(
        original_filename='report.pdf',
        upload_path='/tmp/report.pdf',
        status=JobStatus.FINISHED,
        result_markdown=_frontmatter_markdown({'engine': 'paddleocr', 'processed_at': '2026-08-31T09:00:00Z'}),
        owner_id=user.id,
        document_version=1,
        content_sha256='e' * 64,
        processing_info={'execution': {'engine': 'paddleocr', 'quality_gate': _quality_gate()}},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    delivery = _make_delivery(db, connection, job_id=job.id, event='document.processed')
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request', return_value=(200, None)) as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == connection.url
    payload = args[1]
    assert payload['event'] == 'document.processed'
    assert 'markdown' not in payload  # unlike job.finished -- markdown_url instead of inline markdown
    assert payload['markdown_url'].endswith(f'/api/v1/jobs/{job.id}/download')
    assert payload['quality']['grade'] == 'A'
    assert payload['quality']['recommendation'] == 'allow'
    assert args[2] == 'sekret'

    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'sent'
    assert refreshed.http_status == 200


def test_deliver_webhook_document_processed_signs_body_like_every_other_event(db_session) -> None:
    """End-to-end through the real send_webhook_request (only safe_fetch
    mocked): document.processed gets the exact same X-Weave-Ingest-Event /
    X-Weave-Ingest-Signature headers, computed over the real serialized
    document.processed payload."""
    db = db_session
    user = create_test_user(username='webhook_task_docproc_sig', email='webhook_task_docproc_sig@example.com')
    connection = _make_connection(db, user.id, events=('document.processed',), secret='top-secret')
    job = Job(
        original_filename='signed.pdf',
        upload_path='/tmp/signed.pdf',
        status=JobStatus.FINISHED,
        result_markdown=_frontmatter_markdown({'engine': 'paddleocr', 'processed_at': '2026-08-31T09:00:00Z'}),
        owner_id=user.id,
        document_version=1,
        content_sha256='f' * 64,
        processing_info={'execution': {'engine': 'paddleocr', 'quality_gate': _quality_gate()}},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    delivery = _make_delivery(db, connection, job_id=job.id, event='document.processed')
    delivery_id = delivery.id

    captured = {}

    class _FakeResponse:
        status_code = 200
        body = b'{}'

    def _fake_safe_fetch(url, *, method, headers, body, timeout, max_bytes, allowed_private_hosts):
        captured['headers'] = headers
        captured['body'] = body
        return _FakeResponse()

    with patch('app.services.webhooks.safe_fetch', side_effect=_fake_safe_fetch):
        deliver_webhook(delivery_id)

    assert captured['headers']['X-Weave-Ingest-Event'] == 'document.processed'
    expected_hex = hmac.new(b'top-secret', captured['body'], hashlib.sha256).hexdigest()
    assert captured['headers']['X-Weave-Ingest-Signature'] == f'sha256={expected_hex}'
    sent_body = json.loads(captured['body'])
    assert sent_body['event'] == 'document.processed'
    assert sent_body['job_id'] == job.id
    assert sent_body['quality']['grade'] == 'A'

    db.expire_all()
    assert db.get(WebhookDelivery, delivery_id).status == 'sent'


def test_deliver_webhook_4xx_is_final_no_retry_enqueued(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user3', email='webhook_task_user3@example.com')
    connection = _make_connection(db, user.id)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery_id = delivery.id

    with (
        patch('app.workers.webhook_tasks.send_webhook_request', return_value=(422, 'bad payload')),
        patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task,
    ):
        deliver_webhook(delivery_id)

    mock_send_task.assert_not_called()
    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'failed'
    assert refreshed.http_status == 422
    assert refreshed.error_message == 'bad payload'
    assert refreshed.attempts == 1


def test_deliver_webhook_5xx_stays_pending_and_reenqueues_with_backoff(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user4', email='webhook_task_user4@example.com')
    connection = _make_connection(db, user.id)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery_id = delivery.id

    with (
        patch('app.workers.webhook_tasks.send_webhook_request', return_value=(503, 'Service Unavailable')),
        patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task,
    ):
        deliver_webhook(delivery_id)

    mock_send_task.assert_called_once()
    args, kwargs = mock_send_task.call_args
    assert args[0] == webhook_tasks.DELIVER_TASK_NAME
    assert kwargs['args'] == [delivery_id]
    assert kwargs['countdown'] == webhook_tasks._BACKOFF_SECONDS[0]

    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'pending'  # not terminal yet
    assert refreshed.http_status == 503
    assert refreshed.attempts == 1


def test_deliver_webhook_exhausts_retries_then_fails(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user5', email='webhook_task_user5@example.com')
    connection = _make_connection(db, user.id)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery.attempts = webhook_tasks._MAX_ATTEMPTS - 1  # this call is the last allowed attempt
    db.commit()
    delivery_id = delivery.id

    with (
        patch('app.workers.webhook_tasks.send_webhook_request', return_value=(0, 'connection refused')),
        patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task,
    ):
        deliver_webhook(delivery_id)

    mock_send_task.assert_not_called()
    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'failed'
    assert refreshed.attempts == webhook_tasks._MAX_ATTEMPTS


def test_deliver_webhook_connection_deleted_marks_failed(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user6', email='webhook_task_user6@example.com')
    connection = _make_connection(db, user.id)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery_id = delivery.id

    db.delete(connection)
    db.commit()

    with patch('app.workers.webhook_tasks.send_webhook_request') as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_not_called()
    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'failed'
    assert 'deleted' in refreshed.error_message


def test_deliver_webhook_disabled_connection_marks_failed(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user7', email='webhook_task_user7@example.com')
    connection = _make_connection(db, user.id, enabled=False)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request') as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_not_called()
    db.expire_all()
    refreshed = db.get(WebhookDelivery, delivery_id)
    assert refreshed.status == 'failed'
    assert 'disabled' in refreshed.error_message


def test_deliver_webhook_non_pending_is_a_noop(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_task_user8', email='webhook_task_user8@example.com')
    connection = _make_connection(db, user.id)
    job = _make_job(db, user.id)
    delivery = _make_delivery(db, connection, job_id=job.id, event='job.finished')
    delivery.status = 'sent'
    db.commit()
    delivery_id = delivery.id

    with patch('app.workers.webhook_tasks.send_webhook_request') as mock_send:
        deliver_webhook(delivery_id)

    mock_send.assert_not_called()


# --- Job-completion dispatch hook (app/workers/tasks.py) --------------------
#
# Regression coverage for the opt-in rewrite: a delivery now only ever
# happens when the job/run itself carries a webhook_connection_id, never
# just because the owner happens to have a matching subscribed connection.

def test_dispatch_job_event_noop_without_configured_connection(db_session) -> None:
    """Regression (the opt-in core): owner has an enabled connection
    subscribed to job.finished, but the job itself was never configured
    with a webhook_connection_id -- must be a complete no-op."""
    db = db_session
    user = create_test_user(username='webhook_hook_user', email='webhook_hook_user@example.com')
    _make_connection(db, user.id, events=('job.finished',))
    job = _make_job(db, user.id)  # no webhook_connection_id in settings

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


def test_dispatch_job_event_delivers_only_to_configured_connection(db_session) -> None:
    """Regression: the job is configured with one connection; a second
    enabled connection also subscribed to job.finished exists for the same
    owner but was never selected on the job -- exactly one delivery, for
    the configured connection, must be created."""
    db = db_session
    user = create_test_user(username='webhook_hook_user2', email='webhook_hook_user2@example.com')
    configured = _make_connection(db, user.id, events=('job.finished',))
    _make_connection(db, user.id, events=('job.finished',))  # second subscribed connection, not configured
    job = _make_job(db, user.id, webhook_connection_id=configured.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_called_once()
    db.expire_all()
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].connection_id == configured.id
    assert deliveries[0].event == 'job.finished'
    assert deliveries[0].status == 'pending'


def test_dispatch_job_event_document_processed_delivers_when_configured(db_session) -> None:
    """document.processed is dispatched through the exact same
    dispatch_job_event call as job.finished/job.failed (just a different
    event string) -- a connection subscribed to both receives two separate
    deliveries for one job completion."""
    db = db_session
    user = create_test_user(username='webhook_hook_docproc', email='webhook_hook_docproc@example.com')
    configured = _make_connection(db, user.id, events=('job.finished', 'document.processed'))
    job = _make_job(db, user.id, webhook_connection_id=configured.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')
        webhook_tasks.dispatch_job_event(db, job, 'document.processed')

    assert mock_send_task.call_count == 2
    db.expire_all()
    events = sorted(d.event for d in db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).all())
    assert events == ['document.processed', 'job.finished']


def test_dispatch_job_event_document_processed_noop_when_not_subscribed(db_session) -> None:
    """A connection subscribed only to job.finished must not also pick up
    document.processed just because both are dispatched for the same job."""
    db = db_session
    user = create_test_user(username='webhook_hook_docproc2', email='webhook_hook_docproc2@example.com')
    connection = _make_connection(db, user.id, events=('job.finished',))
    job = _make_job(db, user.id, webhook_connection_id=connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')
        webhook_tasks.dispatch_job_event(db, job, 'document.processed')

    mock_send_task.assert_called_once()  # job.finished only
    db.expire_all()
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].event == 'job.finished'


def test_dispatch_document_processed_noop_for_benchmark_job(db_session) -> None:
    """Benchmark variant jobs (app/api/benchmarks.py's create_benchmark) never
    stamp a webhook_connection_id into settings -- the exact same exclusion
    job.finished already relies on (see
    test_dispatch_job_event_noop_without_configured_connection above) -- so a
    benchmark job must never produce a document.processed delivery either,
    even when the owner has a connection subscribed to it."""
    db = db_session
    user = create_test_user(username='webhook_benchmark_user', email='webhook_benchmark_user@example.com')
    _make_connection(db, user.id, events=('job.finished', 'document.processed'))
    job = _make_job(db, user.id)  # no webhook_connection_id -- mirrors a benchmark variant job
    job.processing_info = {
        'settings': {'mode': 'benchmark', 'benchmark_run_id': 'run-1', 'variant_kind': 'ocr'},
    }
    db.commit()

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')
        webhook_tasks.dispatch_job_event(db, job, 'document.processed')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


def test_dispatch_job_event_configured_connection_not_subscribed_is_noop(db_session) -> None:
    """Regression: the configured connection exists, is enabled, and is
    owned by the job's owner, but its events list doesn't include this
    event -- still zero deliveries."""
    db = db_session
    user = create_test_user(username='webhook_hook_user3', email='webhook_hook_user3@example.com')
    connection = _make_connection(db, user.id, events=('job.failed',))  # not subscribed to job.finished
    job = _make_job(db, user.id, webhook_connection_id=connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


def test_dispatch_job_event_configured_connection_disabled_is_noop(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_hook_user_disabled_conn', email='webhook_hook_user_disabled_conn@example.com')
    connection = _make_connection(db, user.id, events=('job.finished',), enabled=False)
    job = _make_job(db, user.id, webhook_connection_id=connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


def test_dispatch_job_event_configured_connection_owned_by_other_user_is_noop(db_session) -> None:
    """Regression: the connection id stored on the job belongs to a
    different owner (stale/foreign id) -- must not be usable."""
    db = db_session
    owner = create_test_user(username='webhook_hook_owner', email='webhook_hook_owner@example.com')
    other = create_test_user(username='webhook_hook_other', email='webhook_hook_other@example.com')
    foreign_connection = _make_connection(db, other.id, events=('job.finished',))
    job = _make_job(db, owner.id, webhook_connection_id=foreign_connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


def test_dispatch_job_event_noop_when_webhooks_disabled(db_session, monkeypatch) -> None:
    db = db_session
    monkeypatch.setattr(webhook_tasks.settings, 'webhooks_enabled', False)
    user = create_test_user(username='webhook_hook_user_wh_disabled', email='webhook_hook_user_wh_disabled@example.com')
    connection = _make_connection(db, user.id, events=('job.finished',))
    job = _make_job(db, user.id, webhook_connection_id=connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')

    mock_send_task.assert_not_called()


def test_dispatch_job_event_respects_pending_cap(db_session, monkeypatch) -> None:
    db = db_session
    monkeypatch.setattr(webhook_tasks.settings, 'webhook_max_pending_deliveries_per_user', 0)
    user = create_test_user(username='webhook_hook_user6', email='webhook_hook_user6@example.com')
    connection = _make_connection(db, user.id, events=('job.finished',))
    job = _make_job(db, user.id, webhook_connection_id=connection.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_job_event(db, job, 'job.finished')  # must not raise

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.job_id == job.id).count() == 0


# --- Import-run dispatch hook (app/workers/import_tasks.py) -----------------
#
# Same opt-in contract as dispatch_job_event above, but reading
# webhook_connection_id from run.options instead of processing_info.

def test_dispatch_run_event_delivers_when_configured(db_session) -> None:
    db = db_session
    user = create_test_user(username='webhook_run_user', email='webhook_run_user@example.com')
    connection = _make_connection(db, user.id, events=('import_run.finished',))
    run = _make_run(db, user.id, webhook_connection_id=connection.id, pages_imported=3)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_run_event(db, run)

    mock_send_task.assert_called_once()
    db.expire_all()
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.import_run_id == run.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].connection_id == connection.id
    assert deliveries[0].event == 'import_run.finished'
    assert deliveries[0].status == 'pending'


def test_dispatch_run_event_noop_without_configured_connection(db_session) -> None:
    """Regression: owner has an enabled connection subscribed to
    import_run.finished, but the run itself has no webhook_connection_id in
    its options -- must be a complete no-op."""
    db = db_session
    user = create_test_user(username='webhook_run_user2', email='webhook_run_user2@example.com')
    _make_connection(db, user.id, events=('import_run.finished',))
    run = _make_run(db, user.id)  # no webhook_connection_id in options

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_run_event(db, run)

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.import_run_id == run.id).count() == 0


# --- Collection dispatch (app/api/routes.py's create_collection/update_collection) --
#
# Unlike dispatch_job_event/dispatch_run_event above, this is a fan-out, not
# a per-task opt-in -- see dispatch_collection_event's own module comment for
# why a collection has no equivalent of a single "the configured connection".

def test_dispatch_collection_event_fans_out_to_every_subscribed_connection_across_owners(db_session) -> None:
    """The core of the fan-out contract: two DIFFERENT owners each have an
    enabled connection subscribed to collection.updated -- both get their
    own delivery for the same collection.updated call, even though neither
    owns (or was ever configured on) the collection itself."""
    db = db_session
    _quiet_other_collection_updated_connections(db)
    collection_owner = create_test_user(username='webhook_coll_owner', email='webhook_coll_owner@example.com')
    other_owner1 = create_test_user(username='webhook_coll_sub1', email='webhook_coll_sub1@example.com')
    other_owner2 = create_test_user(username='webhook_coll_sub2', email='webhook_coll_sub2@example.com')
    connection1 = _make_connection(db, other_owner1.id, events=('collection.updated',))
    connection2 = _make_connection(db, other_owner2.id, events=('collection.updated', 'job.finished'))
    collection = _make_collection(db, collection_owner.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')

    assert mock_send_task.call_count == 2
    db.expire_all()
    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).all()
    assert len(deliveries) == 2
    assert {d.connection_id for d in deliveries} == {connection1.id, connection2.id}
    assert {d.owner_id for d in deliveries} == {other_owner1.id, other_owner2.id}
    assert all(d.event == 'collection.updated' and d.status == 'pending' for d in deliveries)


def test_dispatch_collection_event_skips_unsubscribed_and_disabled_connections(db_session) -> None:
    db = db_session
    _quiet_other_collection_updated_connections(db)
    owner = create_test_user(username='webhook_coll_skip_owner', email='webhook_coll_skip_owner@example.com')
    _make_connection(db, owner.id, events=('job.finished',))  # not subscribed
    _make_connection(db, owner.id, events=('collection.updated',), enabled=False)  # subscribed but disabled
    collection = _make_collection(db, owner.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).count() == 0


def test_dispatch_collection_event_no_delivery_when_no_connection_configured(db_session) -> None:
    """'kein Feuern wenn keine Webhook-Verbindung konfiguriert ist': with no
    webhook connections at all in the system, create/update must not raise
    and must not create any delivery."""
    db = db_session
    _quiet_other_collection_updated_connections(db)
    owner = create_test_user(username='webhook_coll_none_owner', email='webhook_coll_none_owner@example.com')
    collection = _make_collection(db, owner.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')  # must not raise

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).count() == 0


def test_dispatch_collection_event_noop_when_webhooks_disabled(db_session, monkeypatch) -> None:
    db = db_session
    _quiet_other_collection_updated_connections(db)
    monkeypatch.setattr(webhook_tasks.settings, 'webhooks_enabled', False)
    owner = create_test_user(username='webhook_coll_disabled_owner', email='webhook_coll_disabled_owner@example.com')
    _make_connection(db, owner.id, events=('collection.updated',))
    collection = _make_collection(db, owner.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).count() == 0


def test_dispatch_collection_event_respects_pending_cap_per_connection_owner(db_session, monkeypatch) -> None:
    db = db_session
    _quiet_other_collection_updated_connections(db)
    monkeypatch.setattr(webhook_tasks.settings, 'webhook_max_pending_deliveries_per_user', 0)
    owner = create_test_user(username='webhook_coll_cap_owner', email='webhook_coll_cap_owner@example.com')
    _make_connection(db, owner.id, events=('collection.updated',))
    collection = _make_collection(db, owner.id)

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')  # must not raise

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).count() == 0


def test_dispatch_collection_event_ignores_connection_without_owner(db_session) -> None:
    """A connection whose owner was deleted (owner_id SET NULL) is skipped --
    there is no owner left to attribute a pending-cap or a GET
    /webhooks/deliveries listing to."""
    db = db_session
    _quiet_other_collection_updated_connections(db)
    owner = create_test_user(username='webhook_coll_orphan_owner', email='webhook_coll_orphan_owner@example.com')
    connection = _make_connection(db, owner.id, events=('collection.updated',))
    collection = _make_collection(db, owner.id)
    connection.owner_id = None
    db.commit()

    with patch.object(webhook_tasks.celery_app, 'send_task') as mock_send_task:
        webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')

    mock_send_task.assert_not_called()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.collection_id == collection.id).count() == 0


def test_process_job_completion_hook_swallows_webhook_dispatch_errors(monkeypatch, tmp_path) -> None:
    """Drives the real app/workers/tasks.process_job task end to end with
    webhook_tasks.dispatch_job_event patched to raise: the job must still
    land FINISHED (the try/except around the hook in tasks.py must swallow
    the error), not FAILED and not propagate the exception out of the task.
    """
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(
        tasks, 'convert_to_markdown_with_details',
        lambda *args, **kwargs: ('# hook-test result', {'page_count': 1}),
    )
    monkeypatch.setattr(tasks.webhook_tasks, 'dispatch_job_event', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    upload_path = settings.uploads_dir / 'inbox' / 'hook-job.pdf'
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b'%PDF-1.4 fake upload content')

    db = TestingSessionLocal()
    user = create_test_user(username='webhook_hook_user4', email='webhook_hook_user4@example.com')
    db.add(Job(
        id='hook-job',
        original_filename='hook-job.pdf',
        upload_path=str(upload_path),
        status=JobStatus.PENDING,
        owner_id=user.id,
        processing_info={'settings': {'storage_folder': 'inbox'}},
    ))
    db.commit()
    db.close()

    tasks.process_job('hook-job')  # must not raise despite the dispatch hook throwing

    db = TestingSessionLocal()
    job = db.get(Job, 'hook-job')
    assert job.status == JobStatus.FINISHED
    assert job.error_message is None
    db.close()


def test_process_job_completion_dispatches_document_processed_alongside_job_finished(monkeypatch, tmp_path) -> None:
    """End-to-end through the real app/workers/tasks.process_job completion
    hook: a successful run must call dispatch_job_event for BOTH
    'job.finished' and 'document.processed', in that order, as two separate
    calls (never a single fan-out inside dispatch_job_event itself)."""
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(
        tasks, 'convert_to_markdown_with_details',
        lambda *args, **kwargs: (
            '# hook-test result',
            {'page_count': 1, 'engine': 'paddleocr', 'quality_gate': _quality_gate()},
        ),
    )
    dispatched_events: list[str] = []
    monkeypatch.setattr(
        tasks.webhook_tasks, 'dispatch_job_event',
        lambda db, job, event: dispatched_events.append(event),
    )
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    upload_path = settings.uploads_dir / 'inbox' / 'both-events-job.pdf'
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b'%PDF-1.4 fake upload content')

    db = TestingSessionLocal()
    user = create_test_user(username='webhook_hook_user_both', email='webhook_hook_user_both@example.com')
    db.add(Job(
        id='both-events-job',
        original_filename='both-events-job.pdf',
        upload_path=str(upload_path),
        status=JobStatus.PENDING,
        owner_id=user.id,
        processing_info={'settings': {'storage_folder': 'inbox'}},
    ))
    db.commit()
    db.close()

    tasks.process_job('both-events-job')

    assert dispatched_events == ['job.finished', 'document.processed']


def test_process_job_completion_swallows_document_processed_dispatch_error_only(monkeypatch, tmp_path) -> None:
    """The document.processed dispatch has its own try/except, separate from
    job.finished's: a failure raised only on the document.processed call must
    still land the job FINISHED (job.finished itself already succeeded), not
    FAILED, and not propagate out of the task."""
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(
        tasks, 'convert_to_markdown_with_details',
        lambda *args, **kwargs: ('# result', {'page_count': 1}),
    )

    def _dispatch(db, job, event):
        if event == 'document.processed':
            raise RuntimeError('document.processed dispatch boom')

    monkeypatch.setattr(tasks.webhook_tasks, 'dispatch_job_event', _dispatch)
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    upload_path = settings.uploads_dir / 'inbox' / 'docproc-only-fail-job.pdf'
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b'%PDF-1.4 fake upload content')

    db = TestingSessionLocal()
    user = create_test_user(
        username='webhook_hook_user_docproc_fail', email='webhook_hook_user_docproc_fail@example.com'
    )
    db.add(Job(
        id='docproc-only-fail-job',
        original_filename='docproc-only-fail-job.pdf',
        upload_path=str(upload_path),
        status=JobStatus.PENDING,
        owner_id=user.id,
        processing_info={'settings': {'storage_folder': 'inbox'}},
    ))
    db.commit()
    db.close()

    tasks.process_job('docproc-only-fail-job')  # must not raise

    db = TestingSessionLocal()
    job = db.get(Job, 'docproc-only-fail-job')
    assert job.status == JobStatus.FINISHED
    assert job.error_message is None
    db.close()


def test_process_job_failed_path_swallows_webhook_dispatch_errors(monkeypatch, tmp_path) -> None:
    """Companion to the FINISHED-path swallow test above: drives process_job
    into a FAILED terminal state (converter raises) with dispatch_job_event
    ALSO raising -- the job must still land FAILED with its real error
    message, not crash the task or mask the conversion failure with the
    webhook error.
    """
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(
        tasks, 'convert_to_markdown_with_details',
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('conversion exploded')),
    )
    monkeypatch.setattr(tasks.webhook_tasks, 'dispatch_job_event', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('webhook boom')))
    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'

    upload_path = settings.uploads_dir / 'inbox' / 'hook-fail-job.pdf'
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b'%PDF-1.4 fake upload content')

    db = TestingSessionLocal()
    user = create_test_user(username='webhook_hook_user5', email='webhook_hook_user5@example.com')
    db.add(Job(
        id='hook-fail-job',
        original_filename='hook-fail-job.pdf',
        upload_path=str(upload_path),
        status=JobStatus.PENDING,
        owner_id=user.id,
        processing_info={'settings': {'storage_folder': 'inbox'}},
    ))
    db.commit()
    db.close()

    tasks.process_job('hook-fail-job')  # must not raise

    db = TestingSessionLocal()
    job = db.get(Job, 'hook-fail-job')
    assert job.status == JobStatus.FAILED
    assert 'conversion exploded' in (job.error_message or '')
