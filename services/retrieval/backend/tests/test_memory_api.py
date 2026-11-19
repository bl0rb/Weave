"""POST /api/v1/memory (ADR-0009) -- above all its permission boundary: a
summary or overview reaches a caller exactly when the underlying documents
would reach the same caller through POST /api/v1/search."""

from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Chunk, Collection, Document, DocumentStatus, KnowledgeCard, KnowledgeMemory
from app.services import memory as memory_service
from app.services import search as search_module
from app.services.embeddings import FakeEmbeddingProvider
from tests.conftest import AUTH_HEADERS, TestingSessionLocal, client, make_chunk, make_collection, make_document

NOW = datetime.now(timezone.utc)
EMPTY = {'collections': [], 'documents': [], 'cards': [], 'search': None, 'focus_documents': None}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with TestingSessionLocal() as db:
        db.query(KnowledgeCard).delete()
        db.query(KnowledgeMemory).delete()
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.query(Collection).delete()
        db.commit()


def _summarized(db, slug, filename, title, *, status=DocumentStatus.INDEXED, chunk_text=None, embedded=False):
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
        embedding = FakeEmbeddingProvider().embed_query(chunk_text) if embedded else None
        db.add(make_chunk(document, text=chunk_text, meta={'collection': slug}, embedding=embedding))
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
    assert body == EMPTY


def test_no_grant_means_nothing(corpus):
    assert _memory(allowed_collections=[]).json() == EMPTY
    assert _memory(allowed_collections=['__none__']).json() == EMPTY


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
    monkeypatch.setattr(memory_service, '_ranked_document_ids', lambda db, request, query_vec=None: ranked)
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
    assert body == EMPTY


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


def test_ranking_search_is_small_unreranked_and_does_not_refresh(corpus, monkeypatch):
    calls = []

    def _recorder(db, request, *, refresh=True, query_vec=None):
        calls.append((request, refresh, query_vec))
        return [], None

    monkeypatch.setattr(memory_service, 'search', _recorder)
    _memory(query='Wie rechne ich ab?', allowed_collections=['personal'])
    [(request, refresh, query_vec)] = calls
    assert request.rerank is False and request.top_k == 20 and request.final_k == 20
    assert refresh is False and query_vec is None


# --- include_chunks: one call for memory + chunk search ------------------------------

_SPESEN = 'Spesen werden monatlich abgerechnet.'


@pytest.fixture
def embeddings(monkeypatch):
    """Counts query embeddings wherever the memory path could embed."""
    calls = []
    real = memory_service.embed_query

    def _counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(memory_service, 'embed_query', _counting)
    monkeypatch.setattr(search_module, 'embed_query', _counting)
    return calls


def _seed_focus_corpus(*, summarized_chunks=True):
    with TestingSessionLocal() as db:
        db.add(make_collection(slug='personal', name='Personal'))
        text = _SPESEN if summarized_chunks else None
        reise = _summarized(db, 'personal', 'a.pdf', 'Reisekosten', chunk_text=text, embedded=True)
        urlaub = _summarized(db, 'personal', 'b.pdf', 'Urlaub', chunk_text=text and 'Urlaub beantragen.', embedded=True)
        # Same matching text, but no summary: never part of the focus.
        loose = make_document(original_filename='c.pdf', collection_slug='personal', status=DocumentStatus.INDEXED)
        db.add(loose)
        db.flush()
        db.add(make_chunk(loose, text=_SPESEN, embedding=FakeEmbeddingProvider().embed_query(_SPESEN)))
        db.commit()
        return str(reise.id), str(urlaub.id), str(loose.id)


def test_include_chunks_searches_the_focus_documents_with_one_embedding(embeddings):
    reise, urlaub, loose = _seed_focus_corpus()
    body = _memory(
        query=_SPESEN, allowed_collections=['personal'], include_chunks=True, top_k=10, final_k=5, rerank=False,
    ).json()
    assert body['focus_documents'] == [reise, urlaub]
    results = body['search']['results']
    assert results and {r['document_id'] for r in results} <= {reise, urlaub}
    assert results[0]['document_id'] == reise
    assert body['search']['query'] == _SPESEN
    assert body['search']['trace']['top_k'] == 10 and body['search']['trace']['final_k'] == 5
    assert body['search']['trace']['rerank_requested'] is False
    assert embeddings == [_SPESEN]


def test_include_chunks_falls_back_to_the_plain_search_on_a_focused_miss(embeddings):
    reise, urlaub, loose = _seed_focus_corpus(summarized_chunks=False)
    query = f'Reisekosten und Urlaub: {_SPESEN}'  # both summaries named, but they have no chunks
    body = _memory(query=query, allowed_collections=['personal'], include_chunks=True).json()
    assert {d['document_id'] for d in body['documents']} == {reise, urlaub}
    assert body['focus_documents'] is None
    assert [r['document_id'] for r in body['search']['results']] == [loose]
    assert embeddings == [query]


def test_include_chunks_without_any_summary_runs_the_plain_search(embeddings):
    with TestingSessionLocal() as db:
        db.add(make_collection(slug='personal', name='Personal'))
        document = make_document(original_filename='c.pdf', collection_slug='personal', status=DocumentStatus.INDEXED)
        db.add(document)
        db.flush()
        db.add(make_chunk(document, text=_SPESEN, embedding=FakeEmbeddingProvider().embed_query(_SPESEN)))
        db.commit()
    body = _memory(query=_SPESEN, allowed_collections=['personal'], include_chunks=True).json()
    assert body['documents'] == [] and body['focus_documents'] is None
    assert [r['document_id'] for r in body['search']['results']] == [str(document.id)]
    assert embeddings == [_SPESEN]


def test_include_chunks_rejects_final_k_above_top_k():
    response = _memory(allowed_collections=['personal'], include_chunks=True, top_k=3, final_k=5)
    assert response.status_code == 400


def test_without_include_chunks_no_search_is_returned(corpus):
    body = _memory(allowed_collections=['personal'], top_k=3, final_k=5).json()
    assert body['search'] is None and body['focus_documents'] is None and body['cards'] == []


# --- include_cards: knowledge cards ----------------------------------------------------

_CARD_QUERY = 'Wie viele Urlaubstage habe ich?'


def _card(db, document, question, *, sources=None, embedded_text=None, slug=None):
    card = KnowledgeCard(
        collection_slug=slug or document.collection_slug, document_id=document.id,
        source_document_ids=[str(d.id) for d in (sources or [document])],
        card_sha256=f'{question:x<64}'[:64], question=question, answer=f'Antwort: {question}',
        keywords=['urlaub'],
        embedding=FakeEmbeddingProvider().embed_query(embedded_text or _CARD_QUERY),
        embedding_model=settings.embedding_model, generator='llm', built_at=NOW,
    )
    db.add(card)
    return card


def test_cards_respect_space_switch_permissions_sources_and_threshold():
    with TestingSessionLocal() as db:
        db.add_all([
            make_collection(slug='personal', name='Personal', memory_cards_enabled=True),
            make_collection(slug='archiv', name='Archiv'),  # cards switched off
            make_collection(slug='vorstand', name='Vorstand', visibility='restricted', memory_cards_enabled=True),
        ])
        urlaub = _summarized(db, 'personal', 'urlaub.pdf', 'Urlaub')
        reise = _summarized(db, 'personal', 'reise.pdf', 'Reise', chunk_text='Reisekosten erstatten.', embedded=True)
        alt = _summarized(db, 'personal', 'alt.pdf', 'Alt', status=DocumentStatus.SUPERSEDED)
        archiv = _summarized(db, 'archiv', 'archiv.pdf', 'Archiv')
        geheim = _summarized(db, 'vorstand', 'geheim.pdf', 'Geheim')
        _card(db, urlaub, 'Q-visible', sources=[urlaub, reise])
        _card(db, archiv, 'Q-space-off')
        _card(db, geheim, 'Q-not-permitted')
        _card(db, reise, 'Q-source-not-permitted', sources=[reise, geheim])
        _card(db, reise, 'Q-source-not-indexed', sources=[reise, alt])
        _card(db, reise, 'Q-below-threshold', embedded_text='Kantine Speiseplan Mittwoch')
        _card(db, urlaub, 'Q-moved-space', slug='vorstand')  # card of another space than its document
        db.commit()
        ids = {'urlaub': str(urlaub.id), 'reise': str(reise.id)}

    body = _memory(
        query=_CARD_QUERY, allowed_collections=['personal', 'archiv'], include_cards=True, card_limit=10,
    ).json()
    assert [card['question'] for card in body['cards']] == ['Q-visible']
    card = body['cards'][0]
    assert card['collection'] == 'personal' and card['document_id'] == ids['urlaub']
    assert card['source_document_ids'] == [ids['urlaub'], ids['reise']]
    assert card['score'] == pytest.approx(1.0, abs=1e-6) and card['keywords'] == ['urlaub']
    assert body['search'] is None

    # include_chunks: the card's sources join the focus ('reise' is no
    # memory document here, only a card source, and holds the only chunk).
    body = _memory(
        query=_CARD_QUERY, allowed_collections=['personal'], include_cards=True, include_chunks=True, limit=1,
    ).json()
    assert [d['document_id'] for d in body['documents']] == [ids['urlaub']]
    assert body['focus_documents'] == [ids['urlaub'], ids['reise']]
    assert [r['document_id'] for r in body['search']['results']] == [ids['reise']]


def test_cards_are_ordered_limited_refined_and_need_the_current_embedding_model(monkeypatch):
    # Fake embeddings of different texts are near-orthogonal: keep them all.
    monkeypatch.setattr(settings, 'memory_card_min_similarity', -1.0)
    with TestingSessionLocal() as db:
        db.add_all([
            make_collection(slug='personal', name='Personal', memory_cards_enabled=True),
            make_collection(slug='reise', name='Reise', memory_cards_enabled=True),
        ])
        urlaub = _summarized(db, 'personal', 'urlaub.pdf', 'Urlaub')
        reise = _summarized(db, 'reise', 'reise.pdf', 'Reise')
        exact_b = _card(db, urlaub, 'Q-exact-b')
        exact_a = _card(db, urlaub, 'Q-exact-a')
        _card(db, urlaub, 'Q-other-text', embedded_text='Kantine Speiseplan Mittwoch')
        _card(db, reise, 'Q-other-space')
        _card(db, urlaub, 'Q-old-model').embedding_model = 'retired-model'
        db.commit()
        exact_ids = sorted([exact_b.id, exact_a.id])

    def _questions(**kwargs):
        body = _memory(query=_CARD_QUERY, include_cards=True, **kwargs).json()
        return [card['question'] for card in body['cards']], body['cards']

    questions, cards = _questions(allowed_collections=['personal', 'reise'], card_limit=10)
    assert 'Q-old-model' not in questions
    assert set(questions) == {'Q-exact-a', 'Q-exact-b', 'Q-other-space', 'Q-other-text'}
    scores = [card['score'] for card in cards]
    assert scores == sorted(scores, reverse=True) and questions[-1] == 'Q-other-text'

    # Equal scores tie-break by id; card_limit cuts after ordering.
    questions, cards = _questions(allowed_collections=['personal'], card_limit=2)
    assert [card['card_id'] for card in cards] == exact_ids
    # `collection` is a refinement within allowed_collections, as for /search.
    questions, _ = _questions(allowed_collections=['personal', 'reise'], collection='reise')
    assert questions == ['Q-other-space']
    questions, _ = _questions(allowed_collections=['personal'], collection='reise')
    assert questions == []


def test_a_failing_card_search_serves_no_cards_but_keeps_the_rest(embeddings, monkeypatch):
    """E.g. Knowledge migration 0008 not applied yet: the card query fails
    inside its savepoint, the request still answers with its chunk search."""
    from sqlalchemy.exc import OperationalError

    with TestingSessionLocal() as db:
        db.add(make_collection(slug='personal', name='Personal', memory_cards_enabled=True))
        _summarized(db, 'personal', 'spesen.pdf', 'Spesen', chunk_text=_SPESEN, embedded=True)
        db.commit()

    def _broken(db, request, query_vec):
        raise OperationalError('SELECT knowledge_cards', {}, Exception('no such table: knowledge_cards'))

    monkeypatch.setattr(memory_service, '_card_candidates', _broken)
    response = _memory(query=_SPESEN, allowed_collections=['personal'], include_cards=True, include_chunks=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['cards'] == [] and len(body['documents']) == 1
    assert [r['text'] for r in body['search']['results']] == [_SPESEN]
