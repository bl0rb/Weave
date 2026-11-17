"""Tests for app/services/contradictions.py (the contradiction-candidate
heuristic), app/services/ingest_client.py's report_contradictions, and the
best-effort hook at the end of app/workers/tasks.py's index pipeline.

SQLite path only: embeddings are crafted directly as tiny vectors (the
sqlite column is plain JSON, so any length works) and compared by the
module's in-Python cosine fallback.
"""

import math
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from app.services import contradictions, ingest_client
from app.services.contradictions import conflicting_facts, extract_facts, find_candidates
from app.workers import tasks as tasks_module
from app.workers.tasks import index_document
from tests.conftest import TestingSessionLocal

MODEL = 'test-model'
SLUG = 'personal'
NEAR = [1.0, 0.1, 0.0, 0.0]  # cosine with BASE ~= 0.995
BASE = [1.0, 0.0, 0.0, 0.0]
FAR = [0.0, 1.0, 0.0, 0.0]  # cosine with BASE == 0


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


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _doc(db, *, texts, vectors=None, collection_slug=SLUG, status=DocumentStatus.INDEXED,
         embedding_model=MODEL, previous_job_id=None, source_job_id=None, heading_path=None):
    vectors = vectors or [BASE] * len(texts)
    document = Document(
        source_job_id=source_job_id or str(uuid.uuid4()),
        previous_job_id=previous_job_id,
        content_sha256='a' * 64,
        engine='paddleocr',
        frontmatter={},
        tags=[],
        processed_at=datetime.now(timezone.utc),
        status=status,
        collection_slug=collection_slug,
        embedding_model=embedding_model,
        chunk_count=len(texts),
    )
    db.add(document)
    db.flush()
    for index, (text, vector) in enumerate(zip(texts, vectors)):
        db.add(Chunk(
            document_id=document.id, chunk_index=index, text=text, heading_path=heading_path or ['Regeln'],
            char_count=len(text), meta={}, embedding=vector, embedding_model=embedding_model,
        ))
    db.commit()
    db.refresh(document)
    return document


FRIST_30 = 'Die Kündigungsfrist für Mitarbeitende beträgt 30 Tage zum Monatsende.'
FRIST_14 = 'Die Kündigungsfrist für Mitarbeitende beträgt 14 Tage zum Monatsende.'


# --- extract_facts --------------------------------------------------------------


def _values(text):
    return [(f.kind, f.unit, f.value) for f in extract_facts(text)]


def test_extract_facts_dates_normalize_across_formats():
    assert _values('ab 1.4.2026') == [('date', '', '2026-04-01')]
    assert _values('ab 01.04.26') == [('date', '', '2026-04-01')]
    assert _values('ab 2026-04-01') == [('date', '', '2026-04-01')]


def test_extract_facts_date_at_sentence_end():
    assert _values('Die Regelung gilt ab dem 01.04.2026.') == [('date', '', '2026-04-01')]
    assert _values('gilt ab 1.4.26.') == [('date', '', '2026-04-01')]
    assert _values('bis 31.12.2026. Danach nicht mehr.') == [('date', '', '2026-12-31')]
    (fact,) = extract_facts('ab dem 01.04.2026.')
    assert fact.text == '01.04.2026'


def test_extract_facts_date_followed_by_dot_digit_is_not_a_date():
    # a period followed by a digit continues the token: section/version number
    assert _values('Abschnitt 1.4.2026.3') == []


def test_extract_facts_thousands_number_at_sentence_end():
    assert _values('Das Budget beträgt 1.000.000.') == [('number', '', '1000000')]
    assert _values('Das Budget beträgt 1.000.000 €.') == [('money', 'eur', '1000000')]
    # section numbers at the end of a sentence stay ignored
    assert _values('Siehe Abschnitt 3.2.1.') == []


def test_extract_facts_rejects_impossible_dates():
    assert _values('am 31.13.2026') == []


def test_extract_facts_units_money_percent():
    assert _values('30 Tage') == [('duration', 'tag', '30')]
    assert _values('2 days') == [('duration', 'tag', '2')]
    assert _values('6 Wochen') == [('duration', 'woche', '6')]
    assert _values('EUR 1.200,50') == [('money', 'eur', '1200.5')]
    assert _values('50 €') == [('money', 'eur', '50')]
    assert _values('€50') == [('money', 'eur', '50')]
    assert _values('19%') == [('percent', '%', '19')]
    assert _values('19 Prozent') == [('percent', '%', '19')]
    assert _values('10 GB') == [('measure', 'gb', '10')]
    assert _values('3 Arbeitstage') == [('duration', 'arbeitstag', '3')]


def test_extract_facts_ignores_noise():
    # single digits, list markers, dotted section numbers, version strings
    assert _values('1. Schritt eins, Abschnitt 3.2.1, Version v2.1, 3 Personen') == []
    # bare numbers with two or more digits count
    assert _values('Raum 12') == [('number', '', '12')]


def test_extract_facts_large_numbers_keep_precision():
    assert _values('1.234.567 €') == [('money', 'eur', '1234567')]


def test_extract_facts_context_words_are_stemmed_and_filtered():
    (fact,) = extract_facts(FRIST_30)
    assert fact.text == '30 Tage'
    assert 'kuendigungsfrist' in fact.context
    # stop words and unit words never count as context
    assert 'die' not in fact.context and 'tage' not in fact.context


def test_extract_facts_empty_and_none():
    assert extract_facts('') == []
    assert extract_facts(None) == []


# --- conflicting_facts ------------------------------------------------------------


def test_conflicting_facts_detects_different_value_same_context():
    pairs = conflicting_facts(extract_facts(FRIST_30), extract_facts(FRIST_14))
    assert [(a.text, b.text) for a, b in pairs] == [('30 Tage', '14 Tage')]


def test_conflicting_facts_same_facts_is_not_a_conflict():
    assert conflicting_facts(extract_facts(FRIST_30), extract_facts(FRIST_30.replace('Die ', 'Eine '))) == []


def test_conflicting_facts_equivalent_date_formats_agree():
    a = extract_facts('Die Regelung gilt ab 1.4.2026 für alle Standorte.')
    b = extract_facts('Die Regelung gilt ab 2026-04-01 für alle Standorte.')
    assert conflicting_facts(a, b) == []


def test_conflicting_facts_requires_shared_context():
    a = extract_facts('Die Lieferzeit für Ersatzteile beträgt 3 Tage.')
    b = extract_facts('Die Kündigungsfrist für Mitarbeitende beträgt 14 Tage.')
    # "für" is a stop word, "Mitarbeitende"/"Ersatzteile" differ
    assert conflicting_facts(a, b) == []


def test_conflicting_facts_requires_same_kind_and_unit():
    a = extract_facts('Die Kündigungsfrist beträgt 30 Tage.')
    b = extract_facts('Die Kündigungsfrist beträgt 4 Wochen.')
    assert conflicting_facts(a, b) == []


def test_conflicting_facts_value_present_on_other_side_is_skipped():
    a = extract_facts('Kündigungsfrist 30 Tage.')
    b = extract_facts('Kündigungsfrist 30 Tage, in der Probezeit Kündigungsfrist 14 Tage.')
    # 30 occurs on both sides; 14 is additional information, not a conflict
    assert conflicting_facts(a, b) == []


# --- find_candidates -------------------------------------------------------------


def test_find_candidates_happy_path(db):
    other = _doc(db, texts=[FRIST_14], vectors=[BASE], heading_path=['Personal', 'Kündigung'])
    new = _doc(db, texts=['Einleitung ohne Zahlen.', FRIST_30], vectors=[BASE, NEAR])

    reports = find_candidates(db, new)

    assert len(reports) == 1
    report = reports[0]
    assert set(report) == {
        'collection_slug', 'source_job_id', 'other_source_job_id', 'similarity', 'heading_path',
        'other_heading_path', 'excerpt', 'other_excerpt', 'facts', 'other_facts',
    }
    assert report['collection_slug'] == SLUG
    assert report['source_job_id'] == new.source_job_id
    assert report['other_source_job_id'] == other.source_job_id
    expected = round(1.0 / math.sqrt(1.01), 4)
    assert report['similarity'] == expected
    assert report['heading_path'] == ['Regeln']
    assert report['other_heading_path'] == ['Personal', 'Kündigung']
    assert '30 Tage' in report['excerpt'] and '14 Tage' in report['other_excerpt']
    assert report['facts'] == ['30 Tage']
    assert report['other_facts'] == ['14 Tage']


def test_find_candidates_excerpt_is_trimmed_around_fact(db):
    long_text = ('Vorwort ' * 60) + FRIST_30 + (' Nachwort' * 60)
    _doc(db, texts=[FRIST_14])
    new = _doc(db, texts=[long_text])
    (report,) = find_candidates(db, new)
    assert report['excerpt'].startswith('…') and report['excerpt'].endswith('…')
    assert '30 Tage' in report['excerpt']
    assert len(report['excerpt']) <= 2 * 160 + 20


def test_find_candidates_below_similarity_threshold(db):
    _doc(db, texts=[FRIST_14], vectors=[FAR])
    new = _doc(db, texts=[FRIST_30], vectors=[BASE])
    assert find_candidates(db, new) == []


def test_find_candidates_threshold_is_configurable(db, monkeypatch):
    _doc(db, texts=[FRIST_14], vectors=[[1.0, 1.0, 0.0, 0.0]])  # cosine ~0.707
    new = _doc(db, texts=[FRIST_30], vectors=[BASE])
    assert find_candidates(db, new) == []
    monkeypatch.setattr(settings, 'contradiction_min_similarity', 0.7)
    assert len(find_candidates(db, new)) == 1


def test_find_candidates_identical_facts_not_reported(db):
    _doc(db, texts=[FRIST_30])
    new = _doc(db, texts=[FRIST_30])
    assert find_candidates(db, new) == []


def test_find_candidates_without_facts(db):
    _doc(db, texts=['Die Kündigungsfrist ist im Tarifvertrag geregelt.'])
    new = _doc(db, texts=['Die Kündigungsfrist ist im Arbeitsvertrag geregelt.'])
    assert find_candidates(db, new) == []


def test_find_candidates_other_collection_ignored(db):
    _doc(db, texts=[FRIST_14], collection_slug='andere')
    new = _doc(db, texts=[FRIST_30])
    assert find_candidates(db, new) == []


def test_find_candidates_document_without_collection_skipped(db):
    _doc(db, texts=[FRIST_14], collection_slug=None)
    new = _doc(db, texts=[FRIST_30], collection_slug=None)
    assert find_candidates(db, new) == []


def test_find_candidates_skips_own_lineage(db):
    v1 = _doc(db, texts=[FRIST_14])
    v2 = _doc(db, texts=['Kündigungsfrist für Mitarbeitende: 21 Tage zum Monatsende.'],
              previous_job_id=v1.source_job_id)
    v3 = _doc(db, texts=[FRIST_30], previous_job_id=v2.source_job_id)
    # neither the direct predecessor nor the one before it is a contradiction
    assert find_candidates(db, v3) == []
    # ... and from the middle version, the newer one is lineage too
    assert find_candidates(db, v2) == []


def test_find_candidates_unrelated_document_reported_despite_lineage(db):
    v1 = _doc(db, texts=[FRIST_14])
    unrelated = _doc(db, texts=['Die Kündigungsfrist für Mitarbeitende beträgt 60 Tage zum Monatsende.'])
    v2 = _doc(db, texts=[FRIST_30], previous_job_id=v1.source_job_id)
    reports = find_candidates(db, v2)
    assert [r['other_source_job_id'] for r in reports] == [unrelated.source_job_id]


@pytest.mark.parametrize('status', [DocumentStatus.PENDING, DocumentStatus.SUPERSEDED, DocumentStatus.FAILED,
                                    DocumentStatus.BLOCKED])
def test_find_candidates_ignores_non_indexed_documents(db, status):
    _doc(db, texts=[FRIST_14], status=status)
    new = _doc(db, texts=[FRIST_30])
    assert find_candidates(db, new) == []


def test_find_candidates_new_document_not_indexed(db):
    _doc(db, texts=[FRIST_14])
    new = _doc(db, texts=[FRIST_30], status=DocumentStatus.PENDING)
    assert find_candidates(db, new) == []


def test_find_candidates_ignores_other_embedding_model(db):
    _doc(db, texts=[FRIST_14], embedding_model='old-model')
    new = _doc(db, texts=[FRIST_30])
    assert find_candidates(db, new) == []


def test_find_candidates_respects_report_cap_and_orders_by_similarity(db, monkeypatch):
    monkeypatch.setattr(settings, 'contradiction_max_reports_per_document', 2)
    vectors = [[1.0, 0.05 * i, 0.0, 0.0] for i in range(1, 5)]
    others = [
        _doc(db, texts=[f'Die Kündigungsfrist für Mitarbeitende beträgt {10 + i} Tage zum Monatsende.'],
             vectors=[vector])
        for i, vector in enumerate(vectors)
    ]
    new = _doc(db, texts=[FRIST_30])
    reports = find_candidates(db, new)
    assert len(reports) == 2
    assert [r['other_source_job_id'] for r in reports] == [others[0].source_job_id, others[1].source_job_id]
    assert reports[0]['similarity'] >= reports[1]['similarity']


def test_find_candidates_cap_is_spread_across_other_documents(db, monkeypatch):
    # a near-copy with many conflicting chunks must not use up the whole cap
    monkeypatch.setattr(settings, 'contradiction_max_reports_per_document', 10)
    monkeypatch.setattr(contradictions, '_TOP_K_PER_CHUNK', 20)
    topics = [f'Regel{i}' for i in range(8)]
    copy = _doc(db, texts=[f'Die {t} für Mitarbeitende beträgt 14 Tage.' for t in topics],
                vectors=[[1.0, 0.01, 0.0, 0.0]] * len(topics))
    other = _doc(db, texts=['Die Regel0 für Mitarbeitende beträgt 21 Tage.'],
                 vectors=[[1.0, 0.3, 0.0, 0.0]])
    new = _doc(db, texts=[f'Die {t} für Mitarbeitende beträgt 30 Tage.' for t in topics])
    reports = find_candidates(db, new)
    by_other = [r['other_source_job_id'] for r in reports]
    assert by_other.count(copy.source_job_id) == contradictions._MAX_REPORTS_PER_OTHER_DOCUMENT
    assert other.source_job_id in by_other
    assert [r['similarity'] for r in reports] == sorted((r['similarity'] for r in reports), reverse=True)


def test_find_candidates_per_document_cap_respects_global_cap(db, monkeypatch):
    monkeypatch.setattr(settings, 'contradiction_max_reports_per_document', 3)
    monkeypatch.setattr(contradictions, '_TOP_K_PER_CHUNK', 20)
    topics = [f'Regel{i}' for i in range(8)]
    _doc(db, texts=[f'Die {t} für Mitarbeitende beträgt 14 Tage.' for t in topics])
    new = _doc(db, texts=[f'Die {t} für Mitarbeitende beträgt 30 Tage.' for t in topics])
    assert len(find_candidates(db, new)) == 3


def test_find_candidates_date_conflict_at_sentence_end(db):
    other = _doc(db, texts=['Das Tagegeld für Reisen beträgt 24 € ab dem 01.04.2025.'])
    new = _doc(db, texts=['Das Tagegeld für Reisen beträgt 28 € ab dem 01.04.2026.'])
    (report,) = find_candidates(db, new)
    assert report['other_source_job_id'] == other.source_job_id
    assert '01.04.2026' in report['facts'] and '01.04.2025' in report['other_facts']


def test_find_candidates_cap_zero_disables(db, monkeypatch):
    monkeypatch.setattr(settings, 'contradiction_max_reports_per_document', 0)
    _doc(db, texts=[FRIST_14])
    new = _doc(db, texts=[FRIST_30])
    assert find_candidates(db, new) == []


def test_find_candidates_only_top_k_neighbours_per_chunk(db):
    # three near-identical agreeing chunks outrank the conflicting one
    for _ in range(3):
        _doc(db, texts=[FRIST_30], vectors=[BASE])
    _doc(db, texts=[FRIST_14], vectors=[[1.0, 0.3, 0.0, 0.0]])
    new = _doc(db, texts=[FRIST_30], vectors=[BASE])
    assert find_candidates(db, new) == []


def test_find_candidates_date_conflict(db):
    other = _doc(db, texts=['Die neue Reisekostenregelung gilt ab 01.05.2026 für alle Standorte.'])
    new = _doc(db, texts=['Die neue Reisekostenregelung gilt ab 1.4.2026 für alle Standorte.'])
    (report,) = find_candidates(db, new)
    assert report['other_source_job_id'] == other.source_job_id
    assert report['facts'] == ['1.4.2026'] and report['other_facts'] == ['01.05.2026']


def test_find_candidates_postgres_branch_uses_vector_query(db, monkeypatch):
    """On Postgres the per-chunk pgvector query replaces the Python scan;
    only chunks carrying fact tokens are queried."""
    other = _doc(db, texts=[FRIST_14])
    new = _doc(db, texts=['Einleitung ohne Zahlen.', FRIST_30])
    other_chunk = db.query(Chunk).filter(Chunk.document_id == other.id).one()
    calls = []

    def fake_neighbours(_db, document, lineage, chunk):
        calls.append(chunk.text)
        assert new.source_job_id in lineage
        return [(other_chunk, other.source_job_id, 0.9)]

    monkeypatch.setattr(contradictions, '_is_postgres', lambda _db: True)
    monkeypatch.setattr(contradictions, '_neighbours_postgres', fake_neighbours)
    (report,) = find_candidates(db, new)
    assert calls == [FRIST_30]
    assert report['similarity'] == 0.9


# --- report_contradictions ----------------------------------------------------------


REPORT = {'collection_slug': SLUG, 'source_job_id': 'a', 'other_source_job_id': 'b', 'similarity': 0.9}


@pytest.fixture
def ingest_token(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_api_token', 'svc-token')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'http://ingest.local/')


def test_report_contradictions_posts_reports(ingest_token):
    response = MagicMock(status_code=200)
    with patch('app.services.ingest_client.httpx.post', return_value=response) as mock_post:
        assert ingest_client.report_contradictions([REPORT]) is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == 'http://ingest.local/api/v1/quality/contradictions'
    assert kwargs['json'] == {'reports': [REPORT]}
    assert kwargs['headers'] == {'Authorization': 'Bearer svc-token'}
    assert kwargs['follow_redirects'] is False


def test_report_contradictions_noop_without_token(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_api_token', '')
    with patch('app.services.ingest_client.httpx.post') as mock_post:
        assert ingest_client.report_contradictions([REPORT]) is False
    mock_post.assert_not_called()


def test_report_contradictions_noop_without_reports(ingest_token):
    with patch('app.services.ingest_client.httpx.post') as mock_post:
        assert ingest_client.report_contradictions([]) is False
    mock_post.assert_not_called()


@pytest.mark.parametrize('status_code', [401, 404, 422, 500, 503, 302])
def test_report_contradictions_non_2xx_returns_false(ingest_token, status_code):
    with patch('app.services.ingest_client.httpx.post', return_value=MagicMock(status_code=status_code)):
        assert ingest_client.report_contradictions([REPORT]) is False


def test_report_contradictions_transport_error_never_raises(ingest_token):
    with patch('app.services.ingest_client.httpx.post', side_effect=httpx.ConnectError('down')):
        assert ingest_client.report_contradictions([REPORT]) is False


# --- index pipeline hook ---------------------------------------------------------------


class _ConstantProvider:
    """Every text embeds to BASE -- similarity 1.0 against existing chunks."""

    model_name = MODEL
    dimension = 4

    def embed_batch(self, texts):
        return [list(BASE) for _ in texts]


def _pending_doc(db):
    document = Document(
        source_job_id=str(uuid.uuid4()),
        content_sha256='a' * 64,
        engine='paddleocr',
        frontmatter={'_weave_release_id': str(uuid.uuid4()), '_weave_markdown_sha256': 'b' * 64},
        tags=[],
        processed_at=datetime.now(timezone.utc),
        status=DocumentStatus.PENDING,
        collection_slug=SLUG,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


MARKDOWN = f'---\ncollection: {SLUG}\n---\n\n# Regeln\n\n{FRIST_30}\n'


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(tasks_module, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(tasks_module, 'get_provider', lambda: _ConstantProvider())
    monkeypatch.setattr(tasks_module.ingest_client, 'fetch_released_markdown', lambda *a, **k: MARKDOWN)


def _status(document_id):
    session = TestingSessionLocal()
    try:
        return session.get(Document, document_id).status
    finally:
        session.close()


def test_index_document_reports_candidates(db, pipeline):
    other = _doc(db, texts=[FRIST_14])
    new = _pending_doc(db)
    with patch.object(tasks_module.ingest_client, 'report_contradictions', return_value=True) as mock_report:
        index_document(str(new.id))
    assert _status(new.id) == DocumentStatus.INDEXED
    mock_report.assert_called_once()
    (reports,) = mock_report.call_args.args
    assert [(r['source_job_id'], r['other_source_job_id']) for r in reports] == [
        (new.source_job_id, other.source_job_id)
    ]


def test_index_document_no_report_without_candidates(db, pipeline):
    _doc(db, texts=[FRIST_30])
    new = _pending_doc(db)
    with patch.object(tasks_module.ingest_client, 'report_contradictions') as mock_report:
        index_document(str(new.id))
    assert _status(new.id) == DocumentStatus.INDEXED
    mock_report.assert_not_called()


def test_index_document_check_disabled(db, pipeline, monkeypatch):
    monkeypatch.setattr(settings, 'contradiction_check_enabled', False)
    _doc(db, texts=[FRIST_14])
    new = _pending_doc(db)
    with patch.object(contradictions, 'find_candidates') as mock_find, \
            patch.object(tasks_module.ingest_client, 'report_contradictions') as mock_report:
        index_document(str(new.id))
    assert _status(new.id) == DocumentStatus.INDEXED
    mock_find.assert_not_called()
    mock_report.assert_not_called()


def test_index_document_check_failure_never_fails_indexing(db, pipeline):
    new = _pending_doc(db)
    with patch.object(contradictions, 'find_candidates', side_effect=RuntimeError('boom')), \
            patch.object(tasks_module.ingest_client, 'report_contradictions') as mock_report:
        index_document(str(new.id))
    assert _status(new.id) == DocumentStatus.INDEXED
    mock_report.assert_not_called()


def test_index_document_report_failure_never_fails_indexing(db, pipeline, monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_api_token', 'svc-token')
    _doc(db, texts=[FRIST_14])
    new = _pending_doc(db)
    with patch('app.services.ingest_client.httpx.post', side_effect=httpx.ConnectError('down')) as mock_post:
        index_document(str(new.id))
    assert _status(new.id) == DocumentStatus.INDEXED
    mock_post.assert_called_once()
