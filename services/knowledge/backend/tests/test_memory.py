"""Knowledge memory (ADR-0009): app/services/memory.py and
app/workers/memory_tasks.py -- extractive and LLM summaries, the per-collection
debounce, the rebuild after every change, and the graceful fallback when the
central model is missing or answers garbage. The central chat provider and
the model itself are mocked at the httpx seam."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.api import events as events_module
from app.core.config import settings
from app.models.models import Collection, Document, DocumentStatus, IngestEvent, KnowledgeMemory
from app.services import memory
from app.services.collection_sync import sync_collections
from app.workers import memory_tasks
from tests.conftest import TestingSessionLocal
from tests.test_events_api import _post

SLUG = 'personal'

HANDBOOK = (
    '---\ntitle: Urlaubsrichtlinie\n---\n\n<!-- page:1/1 -->\n\n# Urlaubsrichtlinie\n\n'
    'Diese Richtlinie regelt, wie Mitarbeitende Urlaub beantragen und genehmigen lassen.\n\n'
    '## Antrag\n\nÜber das Portal.\n\n## Genehmigung\n\nDurch die Teamleitung.\n'
)


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'extractive')
    monkeypatch.setattr(settings, 'memory_debounce_seconds', 120)
    monkeypatch.setattr(memory_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(events_module, 'SessionLocal', TestingSessionLocal)
    yield
    with TestingSessionLocal() as db:
        db.query(KnowledgeMemory).delete()
        db.query(Document).delete()
        db.query(Collection).delete()
        db.query(IngestEvent).delete()
        db.commit()


def _doc(db, *, slug=SLUG, name='urlaub.pdf', body=HANDBOOK, status=DocumentStatus.INDEXED, sha='b' * 64, tags=None):
    document = Document(
        source_job_id=str(uuid.uuid4()), content_sha256='a' * 64, original_filename=name, engine='paddleocr',
        frontmatter={'_weave_markdown_sha256': sha}, markdown_body=body, collection_slug=slug,
        processed_at=datetime.now(timezone.utc), status=status, tags=tags or [],
    )
    db.add(document)
    db.commit()
    return document


def _rows(db, kind):
    return db.query(KnowledgeMemory).filter_by(kind=kind).all()


def _llm_answer(content: str) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {'choices': [{'message': {'content': content}}]}
    return response


def _provider(enabled=True) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {'enabled': enabled, 'base_url': 'https://llm.local/v1', 'model': 'm-1'}
    return response


# --- extractive --------------------------------------------------------------


def test_extractive_summary_uses_title_type_first_paragraph_and_headings():
    with TestingSessionLocal() as db:
        summary = memory.extractive_document_summary(_doc(db))
    assert summary.title == 'Urlaubsrichtlinie'
    assert summary.document_type == 'PDF-Dokument'
    assert summary.summary.startswith('Diese Richtlinie regelt')
    assert summary.key_points == ['Antrag', 'Genehmigung']
    assert summary.generator == 'extractive'


# --- triggers and debounce ---------------------------------------------------


def test_touch_is_a_no_op_while_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'off')
    with TestingSessionLocal() as db:
        memory.touch(db, SLUG)
        _doc(db)
        assert memory.claim_due_collections(db) == []
        assert db.query(KnowledgeMemory).count() == 0


def test_changes_are_debounced_per_collection_and_claimed_once():
    with TestingSessionLocal() as db:
        for _ in range(3):
            memory.touch(db, SLUG)
        assert memory.claim_due_collections(db) == []  # still inside the debounce window
        state = _rows(db, 'collection')[0]
        state.dirty_since = datetime.now(timezone.utc) - timedelta(seconds=121)
        db.commit()
        assert memory.claim_due_collections(db) == [SLUG]
        assert memory.claim_due_collections(db) == []  # claimed, not enqueued twice


def test_backfill_marks_collections_with_unsummarized_documents():
    with TestingSessionLocal() as db:
        _doc(db)
        _doc(db, slug=None)  # legacy, no collection: no memory in v1
        memory.claim_due_collections(db)
        assert [row.collection_slug for row in _rows(db, 'collection')] == [SLUG]


def test_schedule_enqueues_one_build_per_due_collection():
    with TestingSessionLocal() as db:
        memory.touch(db, SLUG)
        _rows(db, 'collection')[0].dirty_since = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    with patch.object(memory_tasks.celery_app, 'send_task') as send:
        assert memory_tasks.schedule_memory_builds() == 1
    send.assert_called_once_with(memory_tasks.BUILD_TASK_NAME, args=[SLUG])


# --- build ---------------------------------------------------------------------


def test_build_creates_document_summaries_and_overview():
    with TestingSessionLocal() as db:
        db.add(Collection(slug=SLUG, name='Personal', description='HR-Themen', synced_at=datetime.now(timezone.utc)))
        first = _doc(db, tags=['urlaub'])
        second = _doc(db, name='reise.docx', body='# Reisekosten\n\nSo werden Reisekosten abgerechnet und erstattet.\n')
        _doc(db, name='alt.pdf', status=DocumentStatus.SUPERSEDED)
        memory.touch(db, SLUG)
        memory_tasks.build_memory(SLUG)

        documents = {row.document_id: row for row in _rows(db, 'document')}
        assert set(documents) == {first.id, second.id}
        assert documents[second.id].document_type == 'Word-Dokument'
        overview = _rows(db, 'collection')[0]
        assert overview.title == 'Personal'
        assert 'enthält 2 Dokumente' in overview.summary and 'HR-Themen' in overview.summary
        assert set(overview.source_document_ids) == {str(first.id), str(second.id)}
        assert 'urlaub' in overview.topics
        assert overview.dirty_since is None and overview.building_since is None and overview.built_at


def test_rebuild_follows_every_change_and_drops_removed_documents():
    with TestingSessionLocal() as db:
        kept = _doc(db)
        gone = _doc(db, name='weg.pdf')
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)

        gone.status = DocumentStatus.SUPERSEDED
        kept.frontmatter = {'_weave_markdown_sha256': 'c' * 64}  # re-released content
        kept.markdown_body = '# Neu\n\nVöllig neuer Inhalt der Urlaubsrichtlinie für das kommende Jahr.\n'
        db.commit()
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)

        rows = _rows(db, 'document')
        assert [row.document_id for row in rows] == [kept.id]
        assert rows[0].title == 'Neu' and rows[0].source_sha256 == 'c' * 64
        assert _rows(db, 'collection')[0].source_document_ids == [str(kept.id)]


def test_large_collections_continue_on_the_next_tick(monkeypatch):
    monkeypatch.setattr(settings, 'memory_max_documents_per_run', 2)
    with TestingSessionLocal() as db:
        for index in range(3):
            _doc(db, name=f'd{index}.pdf')
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        assert len(_rows(db, 'document')) == 2
        assert memory.claim_due_collections(db) == [SLUG]  # due right away, no extra debounce
        memory.build_collection_memory(db, SLUG)
        assert len(_rows(db, 'document')) == 3
        assert len(_rows(db, 'collection')[0].source_document_ids) == 3


# --- LLM mode and graceful degradation ----------------------------------------


@pytest.fixture
def _llm_mode(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'llm')
    monkeypatch.setattr(settings, 'chat_config_base_url', 'https://ingest.local')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'svc')


def test_llm_mode_uses_the_central_model(_llm_mode):
    answers = [
        _llm_answer('```json\n' + json.dumps({
            'title': 'Urlaub', 'document_type': 'Richtlinie', 'summary': 'Regelt Urlaubsanträge.',
            'key_points': ['Antrag im Portal'],
        }) + '\n```'),
        _llm_answer(json.dumps({'summary': 'Alles zu Personalthemen.', 'topics': ['Urlaub']})),
    ]
    with TestingSessionLocal() as db, patch('app.services.memory.httpx.get', return_value=_provider()) as get, \
            patch('app.services.memory.httpx.post', side_effect=answers) as post:
        _doc(db)
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        row = _rows(db, 'document')[0]
        assert (row.title, row.document_type, row.generator, row.model) == ('Urlaub', 'Richtlinie', 'llm', 'm-1')
        overview = _rows(db, 'collection')[0]
        assert (overview.summary, overview.topics, overview.generator) == ('Alles zu Personalthemen.', ['Urlaub'], 'llm')
        assert get.call_args.args[0] == 'https://ingest.local/api/v1/internal/chat-provider'
        assert post.call_args.args[0] == 'https://llm.local/v1/chat/completions'

        # Nothing changed: a rebuild costs no further model call.
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        assert post.call_count == 2


def test_placeholder_model_answer_degrades_to_extractive(_llm_mode):
    with TestingSessionLocal() as db, patch('app.services.memory.httpx.get', return_value=_provider()), \
            patch('app.services.memory.httpx.post', return_value=_llm_answer('Ich bin ein Platzhalter.')):
        document = _doc(db)
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        row = _rows(db, 'document')[0]
        assert row.generator == 'extractive' and row.title == 'Urlaubsrichtlinie'
        assert _rows(db, 'collection')[0].generator == 'extractive'
        db.refresh(document)
        assert document.status == DocumentStatus.INDEXED


@pytest.mark.parametrize('provider', [None, 'disabled', 'unreachable'])
def test_missing_central_model_degrades_to_extractive(_llm_mode, monkeypatch, provider):
    if provider is None:
        monkeypatch.setattr(settings, 'chat_config_base_url', '')
    get = MagicMock(return_value=_provider(enabled=False))
    if provider == 'unreachable':
        import httpx
        get.side_effect = httpx.ConnectError('down')
    with TestingSessionLocal() as db, patch('app.services.memory.httpx.get', get), \
            patch('app.services.memory.httpx.post') as post:
        _doc(db)
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        assert _rows(db, 'document')[0].generator == 'extractive'
        post.assert_not_called()


# --- triggers from the real change paths --------------------------------------


def test_withdrawal_removes_the_summary_and_marks_the_collection(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_webhook_secret', 'memory-test-secret')
    with TestingSessionLocal() as db:
        document = _doc(db)
        memory.touch(db, SLUG)
        memory.build_collection_memory(db, SLUG)
        job_id = document.source_job_id
    assert _post({'event': 'document.withdrawn', 'job_id': job_id}).json()['status'] == 'withdrawn'
    with TestingSessionLocal() as db:
        assert _rows(db, 'document') == []
        assert _rows(db, 'collection')[0].dirty_since is not None


def test_registry_sync_marks_renamed_and_forgets_deleted_collections(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    monkeypatch.setattr(settings, 'weave_ingest_api_token', 'svc')
    with TestingSessionLocal() as db:
        now = datetime.now(timezone.utc)
        db.add_all([Collection(slug=SLUG, name='Personal', synced_at=now), Collection(slug='alt', name='Alt', synced_at=now)])
        db.add(KnowledgeMemory(kind='collection', collection_slug='alt', summary='alt'))
        db.commit()
        registry = MagicMock(status_code=200)
        registry.json.return_value = [{'slug': SLUG, 'name': 'Personal & HR'}]
        with patch('app.services.collection_sync.httpx.get', return_value=registry):
            sync_collections(db)
        rows = _rows(db, 'collection')
        assert [(row.collection_slug, row.dirty_since is not None) for row in rows] == [(SLUG, True)]
