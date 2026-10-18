"""POST /api/v1/memory (ADR-0009) -- above all its permission boundary: a
summary or overview reaches a caller exactly when the underlying documents
would reach the same caller through POST /api/v1/search."""

from datetime import datetime, timezone

import pytest

from app.models.models import Chunk, Collection, Document, DocumentStatus, KnowledgeMemory
from app.services import memory as memory_service
from tests.conftest import AUTH_HEADERS, TestingSessionLocal, client, make_chunk, make_collection, make_document

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with TestingSessionLocal() as db:
        db.query(KnowledgeMemory).delete()
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.query(Collection).delete()
        db.commit()


def _summarized(db, slug, filename, title, *, status=DocumentStatus.INDEXED, chunk_text=None):
    document = make_document(
        original_filename=filename, collection_slug=slug, status=status,
        frontmatter={'source': filename, 'source_kind': 'upload'},
    )
    db.add(document)
    db.flush()
    db.add(KnowledgeMemory(
        kind='document', collection_slug=slug, document_id=document.id, title=title, document_type='Richtlinie',
        summary=f'Zusammenfassung von {title}.', key_points=['Punkt'], generator='extractive', built_at=NOW,
    ))
    if chunk_text:
        db.add(make_chunk(document, text=chunk_text, meta={'collection': slug}))
    return document


def _overview(db, slug, documents, summary='Alles über Personal.'):
    db.add(KnowledgeMemory(
        kind='collection', collection_slug=slug, title=slug, summary=summary, topics=['Urlaub'],
        source_document_ids=[str(d.id) for d in documents], generator='extractive', built_at=NOW,
    ))


@pytest.fixture
def corpus():
    with TestingSessionLocal() as db:
        db.add_all([
            make_collection(slug='personal', name='Personal', description='HR'),
            make_collection(slug='vorstand', name='Vorstand', visibility='restricted', read_teams=['board']),
        ])
        urlaub = _summarized(db, 'personal', 'urlaubsrichtlinie.pdf', 'Urlaubsrichtlinie')
        reise = _summarized(db, 'personal', 'reisekosten.pdf', 'Reisekosten')
        alt = _summarized(db, 'personal', 'alt.pdf', 'Alte Richtlinie', status=DocumentStatus.SUPERSEDED)
        geheim = _summarized(db, 'vorstand', 'gehaelter.pdf', 'Gehälter Vorstand')
        _overview(db, 'personal', [urlaub, reise])
        _overview(db, 'vorstand', [geheim], summary='Vertrauliche Vorstandsunterlagen.')
        db.commit()
        return {'urlaub': urlaub, 'reise': reise, 'alt': alt, 'geheim': geheim}


def _memory(**body):
    body.setdefault('query', 'Was ist im Wissensbereich?')
    return client.post('/api/v1/memory', json=body, headers=AUTH_HEADERS)


def test_requires_the_service_token():
    assert client.post('/api/v1/memory', json={'query': 'x', 'allowed_collections': []}).status_code == 401


@pytest.mark.parametrize('body', [{'query': 'x'}, {'query': 'x', 'allowed_collections': None}])
def test_unrestricted_callers_are_refused(body):
    assert client.post('/api/v1/memory', json=body, headers=AUTH_HEADERS).status_code == 422


def test_only_allowed_collections_and_indexed_documents_are_returned(corpus):
    body = _memory(query='Gehälter Vorstand Urlaubsrichtlinie', allowed_collections=['personal']).json()
    assert [c['collection'] for c in body['collections']] == ['personal']
    # Both named in the question, but only the readable one comes back.
    assert [d['title'] for d in body['documents']] == ['Urlaubsrichtlinie']
    assert 'Vertrauliche' not in str(body)


def test_reader_of_both_sees_both_and_a_named_collection_narrows_overviews(corpus):
    body = _memory(query='Was ist im Bereich Vorstand?', allowed_collections=['personal', 'vorstand']).json()
    assert [c['collection'] for c in body['collections']] == ['vorstand']
    assert body['collections'][0]['summary'] == 'Vertrauliche Vorstandsunterlagen.'


def test_collection_filter_is_a_refinement_never_a_widening(corpus):
    body = _memory(allowed_collections=['personal'], collection='vorstand').json()
    assert body == {'collections': [], 'documents': []}


def test_no_grant_means_nothing(corpus):
    assert _memory(allowed_collections=[]).json() == {'collections': [], 'documents': []}
    assert _memory(allowed_collections=['__none__']).json() == {'collections': [], 'documents': []}


def test_overview_lists_live_documents_with_count(corpus):
    overview = _memory(allowed_collections=['personal']).json()['collections'][0]
    assert overview['summary'] == 'Alles über Personal.' and overview['topics'] == ['Urlaub']
    assert overview['stale'] is False and overview['document_count'] == 2
    assert {d['title'] for d in overview['documents']} == {'Urlaubsrichtlinie', 'Reisekosten'}


def test_overview_text_is_withheld_once_a_source_document_is_gone(corpus):
    with TestingSessionLocal() as db:
        db.get(Document, corpus['reise'].id).status = DocumentStatus.BLOCKED
        db.commit()
    overview = _memory(allowed_collections=['personal']).json()['collections'][0]
    assert overview['stale'] is True and overview['summary'] is None and overview['topics'] == []
    assert [d['title'] for d in overview['documents']] == ['Urlaubsrichtlinie']


def test_documents_follow_the_chunk_search_ranking(corpus, monkeypatch):
    ranked = [str(corpus['geheim'].id), str(corpus['alt'].id), str(corpus['reise'].id)]
    monkeypatch.setattr(memory_service, '_ranked_document_ids', lambda db, request: ranked)
    documents = _memory(query='Wie rechne ich ab?', allowed_collections=['personal']).json()['documents']
    # A ranked but unreadable (restricted, superseded) document never appears.
    assert [d['title'] for d in documents] == ['Reisekosten']
    assert documents[0]['source_kind'] == 'upload' and documents[0]['original_filename'] == 'reisekosten.pdf'


def test_nothing_built_skips_the_chunk_search(monkeypatch):
    """Runtime runs the chunk search itself when the memory is empty, so an
    empty memory must not embed and search a second time."""
    def _boom(*args, **kwargs):
        raise AssertionError('search must not run without summaries')

    monkeypatch.setattr(memory_service, 'search', _boom)
    with TestingSessionLocal() as db:
        db.add(make_collection(slug='personal', name='Personal'))
        db.commit()
    body = _memory(query='Welche Dokumente gibt es zu Spesen?', allowed_collections=['personal']).json()
    assert body == {'collections': [], 'documents': []}


def test_search_failure_keeps_name_matches(corpus, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError('embeddings down')

    monkeypatch.setattr(memory_service, 'search', _boom)
    documents = _memory(query='Worum geht es in reisekosten.pdf?', allowed_collections=['personal']).json()['documents']
    assert [d['title'] for d in documents] == ['Reisekosten']


def test_real_chunk_search_ranks_summaries_by_content():
    with TestingSessionLocal() as db:
        db.add(make_collection(slug='personal', name='Personal'))
        _summarized(db, 'personal', 'a.pdf', 'Reisekosten', chunk_text='Spesen werden monatlich abgerechnet.')
        _summarized(db, 'personal', 'b.pdf', 'Urlaub', chunk_text='Urlaub beantragen im Portal.')
        db.commit()
    documents = _memory(query='Welche Dokumente gibt es zu Spesen?', allowed_collections=['personal']).json()['documents']
    assert documents[0]['title'] == 'Reisekosten'
