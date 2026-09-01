"""End-to-end tests for app/workers/tasks.py:index_document, with
app.services.ingest_client mocked at the seam so no real Weave-Ingest HTTP
call happens, and the real chunker/enrichment/FakeEmbeddingProvider stack
otherwise -- see contracts/events/document.processed.md for the envelope
this mirrors (frontmatter + `<!-- page:N/M -->` markers).

index_document is called directly (not via .delay()/.apply_async()) for the
same reason Weave-Ingest's own tests/test_webhook_tasks.py calls
deliver_webhook(delivery_id) directly: a Celery bound task's `self.app` is
available on a plain direct call, so the self-re-enqueue retry path
(self.app.send_task(...)) is fully exercisable and mockable without any
broker/worker at all.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import yaml

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from app.services import ingest_client
from app.services.embeddings import FakeEmbeddingProvider
from app.workers import tasks as tasks_module
from app.workers.tasks import index_document
from tests.conftest import TestingSessionLocal

SAMPLE_FRONTMATTER = {
    'source': 'v1.pdf',
    'original_filename': 'Handbuch.pdf',
    'pages': 2,
    'profile': 'PP-OCRv6 small det + rec',
    'profile_id': 'ppocrv6_small',
    'mode': 'single',
    'job_id': 'job-v1',
    'document_version': 1,
    'content_sha256': 'a' * 64,
    'processed_at': '2026-08-31T14:23:45Z',
    'engine': 'paddleocr',
    'team': 'Kundenservice',
    'department': 'Support',
    'tags': ['important'],
}


def _sample_markdown(frontmatter: dict) -> str:
    dumped = yaml.safe_dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return (
        f'---\n{dumped}---\n\n'
        '<!-- page:1/2 -->\n\n'
        '# Handbuch\n\n'
        'Erster Absatz mit Inhalt.\n\n'
        '## Installation\n\n'
        'Schritt eins.\n\n'
        '<!-- page:2/2 -->\n\n'
        '## Konfiguration\n\n'
        'Schritt zwei mit mehr Text.\n'
    )


@pytest.fixture(autouse=True)
def _use_test_session(monkeypatch):
    monkeypatch.setattr(tasks_module, 'SessionLocal', TestingSessionLocal)


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
        content_sha256='a' * 64,
        engine='paddleocr',
        frontmatter={},
        tags=[],
        processed_at=datetime.now(timezone.utc),
        markdown_url=f'{settings.weave_ingest_base_url}/api/v1/jobs/x/download',
        status=DocumentStatus.PENDING,
    )
    defaults.update(overrides)
    document = Document(**defaults)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


# --- Happy path: end-to-end fetch -> chunk -> embed -> persist -----------------


def test_index_document_end_to_end_success():
    db = TestingSessionLocal()
    try:
        document = _make_document(
            db, source_job_id='job-v1', frontmatter=SAMPLE_FRONTMATTER,
            team='Kundenservice', department='Support', tags=['important'],
        )
    finally:
        db.close()

    markdown = _sample_markdown(SAMPLE_FRONTMATTER)
    with patch('app.workers.tasks.ingest_client.fetch_markdown', return_value=markdown) as mock_fetch:
        index_document(str(document.id))

    # FINDING 3: the fetch is keyed off source_job_id, never markdown_url --
    # see test_index_document_never_fetches_the_stored_markdown_url_uses_job_id
    # below for an end-to-end test of why.
    mock_fetch.assert_called_once_with(document.source_job_id)

    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.INDEXED
        assert refreshed.markdown_body == markdown
        assert refreshed.indexed_at is not None
        assert refreshed.error is None
        assert refreshed.index_attempts == 0
        assert refreshed.embedding_model == provider.model_name

        chunks = db.query(Chunk).filter_by(document_id=document.id).order_by(Chunk.chunk_index).all()
        assert len(chunks) == 3
        assert refreshed.chunk_count == 3

        for chunk in chunks:
            assert chunk.embedding is not None
            assert len(chunk.embedding) == provider.dimension
            assert chunk.embedding_model == provider.model_name
            # Denormalized straight off the (freshly parsed) frontmatter.
            assert chunk.meta.get('team') == 'Kundenservice'
            assert chunk.meta.get('department') == 'Support'

        assert chunks[0].heading_path == ['Handbuch']
        assert chunks[0].page_start == 1
        assert chunks[0].page_end == 1
        assert chunks[-1].heading_path == ['Handbuch', 'Konfiguration']
        assert chunks[-1].page_start == 2
        assert chunks[-1].page_end == 2
    finally:
        db.close()


def test_index_document_reruns_cleanly_without_duplicate_chunks():
    """A redelivered task (acks_late) that reaches the pipeline a second
    time must not violate the (document_id, chunk_index) unique
    constraint -- the old chunk set is wiped before the new one is written.
    """
    db = TestingSessionLocal()
    try:
        document = _make_document(db, source_job_id='job-rerun', frontmatter=SAMPLE_FRONTMATTER)
    finally:
        db.close()

    markdown = _sample_markdown(SAMPLE_FRONTMATTER)
    with patch('app.workers.tasks.ingest_client.fetch_markdown', return_value=markdown):
        index_document(str(document.id))
        index_document(str(document.id))

    db = TestingSessionLocal()
    try:
        chunks = db.query(Chunk).filter_by(document_id=document.id).all()
        assert len(chunks) == 3
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.INDEXED
        assert refreshed.chunk_count == 3
    finally:
        db.close()


# --- Supersede ------------------------------------------------------------------


def test_index_document_supersedes_previous_version_in_same_transaction():
    db = TestingSessionLocal()
    try:
        v1 = _make_document(
            db, source_job_id='job-v1', frontmatter=SAMPLE_FRONTMATTER,
            status=DocumentStatus.INDEXED, chunk_count=2, indexed_at=datetime.now(timezone.utc),
        )
        db.add(Chunk(document_id=v1.id, chunk_index=0, text='old chunk 0', heading_path=[], char_count=11, meta={}))
        db.add(Chunk(document_id=v1.id, chunk_index=1, text='old chunk 1', heading_path=[], char_count=11, meta={}))
        db.commit()

        v2 = _make_document(
            db, source_job_id='job-v2', previous_job_id='job-v1', frontmatter=SAMPLE_FRONTMATTER,
        )
        v1_id, v2_id = v1.id, v2.id
    finally:
        db.close()

    markdown = _sample_markdown(SAMPLE_FRONTMATTER)
    with patch('app.workers.tasks.ingest_client.fetch_markdown', return_value=markdown):
        index_document(str(v2_id))

    db = TestingSessionLocal()
    try:
        refreshed_v1 = db.get(Document, v1_id)
        assert refreshed_v1.status == DocumentStatus.SUPERSEDED
        assert refreshed_v1.chunk_count == 0
        assert db.query(Chunk).filter_by(document_id=v1_id).count() == 0

        refreshed_v2 = db.get(Document, v2_id)
        assert refreshed_v2.status == DocumentStatus.INDEXED
        assert db.query(Chunk).filter_by(document_id=v2_id).count() == 3
    finally:
        db.close()


def test_index_document_with_unknown_previous_job_id_is_a_noop_for_supersede():
    db = TestingSessionLocal()
    try:
        document = _make_document(
            db, source_job_id='job-v2', previous_job_id='no-such-job', frontmatter=SAMPLE_FRONTMATTER,
        )
    finally:
        db.close()

    markdown = _sample_markdown(SAMPLE_FRONTMATTER)
    with patch('app.workers.tasks.ingest_client.fetch_markdown', return_value=markdown):
        index_document(str(document.id))  # must not raise

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.INDEXED
    finally:
        db.close()


# --- FINDING 3: fetch is keyed off source_job_id, never the stored markdown_url --


class _FakeIngestResponse:
    def __init__(self, status_code: int, text: str = '') -> None:
        self.status_code = status_code
        self.text = text


def test_index_document_never_fetches_the_stored_markdown_url_uses_job_id(monkeypatch):
    """A Document's markdown_url column is stored purely for audit/display
    (see app/models/models.py's docstring) -- even if it somehow ended up
    containing a foreign host or a path-traversal segment (e.g. a
    pre-FINDING-2 event, or any future bug upstream), index_document must
    still only ever fetch the URL THIS service builds from the document's
    own source_job_id (see app/services/ingest_client.py's
    fetch_markdown/build_markdown_url), never the stored markdown_url value
    itself. Unlike the old SSRF-guard test this replaces, this one mocks
    httpx.get directly (not fetch_markdown) so it actually observes which
    URL gets requested."""
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    job_id = str(uuid.uuid4())
    markdown = _sample_markdown(SAMPLE_FRONTMATTER)

    db = TestingSessionLocal()
    try:
        document = _make_document(
            db, source_job_id=job_id, frontmatter=SAMPLE_FRONTMATTER,
            markdown_url=f'https://weave.local/api/v1/jobs/{job_id}/download/../../../admin',
        )
    finally:
        db.close()

    with patch(
        'app.services.ingest_client.httpx.get', return_value=_FakeIngestResponse(200, markdown)
    ) as mock_get:
        index_document(str(document.id))

    mock_get.assert_called_once()
    requested_url = mock_get.call_args[0][0]
    assert requested_url == f'https://weave.local/api/v1/jobs/{job_id}/download'
    assert '..' not in requested_url

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.INDEXED
        assert refreshed.markdown_body == markdown
    finally:
        db.close()


def test_index_document_ssrf_guard_marks_failed_without_any_fetch(monkeypatch):
    """A job_id that doesn't resolve to a clean URL under
    settings.weave_ingest_base_url (defense-in-depth -- app/schemas/events.py's
    UUID pattern validation should already prevent this at webhook-receipt
    time, see FINDING 2) is rejected by _validate_markdown_url before any
    request is made."""
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    db = TestingSessionLocal()
    try:
        document = _make_document(db, source_job_id='../../../etc/passwd')
    finally:
        db.close()

    with patch('app.services.ingest_client.httpx.get') as mock_get:
        index_document(str(document.id))
    mock_get.assert_not_called()

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.FAILED
        assert refreshed.error
        assert db.query(Chunk).filter_by(document_id=document.id).count() == 0
    finally:
        db.close()


# --- Transient fetch error: retry with backoff ----------------------------------


def test_transient_fetch_error_reenqueues_with_backoff_and_bumps_attempts():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
    finally:
        db.close()

    with (
        patch(
            'app.workers.tasks.ingest_client.fetch_markdown',
            side_effect=ingest_client.TransientFetchError('boom'),
        ) as mock_fetch,
        patch.object(tasks_module.celery_app, 'send_task') as mock_send_task,
    ):
        index_document(str(document.id))

    mock_fetch.assert_called_once()
    mock_send_task.assert_called_once()
    args, kwargs = mock_send_task.call_args
    assert args[0] == tasks_module.INDEX_TASK_NAME
    assert kwargs['args'] == [str(document.id)]
    assert kwargs['countdown'] == tasks_module._BACKOFF_SECONDS[0]

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.PENDING
        assert refreshed.index_attempts == 1
        assert refreshed.chunk_count == 0
    finally:
        db.close()


def test_transient_fetch_error_backoff_grows_on_later_attempts():
    db = TestingSessionLocal()
    try:
        document = _make_document(db, index_attempts=1)
    finally:
        db.close()

    with (
        patch('app.workers.tasks.ingest_client.fetch_markdown', side_effect=ingest_client.TransientFetchError('boom')),
        patch.object(tasks_module.celery_app, 'send_task') as mock_send_task,
    ):
        index_document(str(document.id))

    args, kwargs = mock_send_task.call_args
    assert kwargs['countdown'] == tasks_module._BACKOFF_SECONDS[1]

    db = TestingSessionLocal()
    try:
        assert db.get(Document, document.id).index_attempts == 2
    finally:
        db.close()


def test_transient_fetch_error_exhausts_retries_and_marks_failed():
    db = TestingSessionLocal()
    try:
        document = _make_document(db, index_attempts=tasks_module._MAX_ATTEMPTS - 1)
    finally:
        db.close()

    with (
        patch(
            'app.workers.tasks.ingest_client.fetch_markdown',
            side_effect=ingest_client.TransientFetchError('still down'),
        ),
        patch.object(tasks_module.celery_app, 'send_task') as mock_send_task,
    ):
        index_document(str(document.id))

    mock_send_task.assert_not_called()

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.FAILED
        assert refreshed.index_attempts == tasks_module._MAX_ATTEMPTS
        assert 'still down' in refreshed.error
    finally:
        db.close()


# --- Permanent fetch error: no retry ---------------------------------------------


def test_permanent_fetch_error_marks_failed_immediately_no_retry():
    db = TestingSessionLocal()
    try:
        document = _make_document(db)
    finally:
        db.close()

    with (
        patch(
            'app.workers.tasks.ingest_client.fetch_markdown',
            side_effect=ingest_client.PermanentFetchError('bad token'),
        ),
        patch.object(tasks_module.celery_app, 'send_task') as mock_send_task,
    ):
        index_document(str(document.id))

    mock_send_task.assert_not_called()

    db = TestingSessionLocal()
    try:
        refreshed = db.get(Document, document.id)
        assert refreshed.status == DocumentStatus.FAILED
        assert 'bad token' in refreshed.error
    finally:
        db.close()


# --- Defensive: unknown document id ----------------------------------------------


def test_index_document_missing_document_is_a_noop():
    index_document(str(uuid.uuid4()))  # must not raise
