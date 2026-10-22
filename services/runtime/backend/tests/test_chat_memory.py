"""Knowledge memory in Runtime (ADR-0009): the 'overview' intent, the
overview turn against Weave-Retrieval's POST /api/v1/memory, its sources,
and every fallback to the regular knowledge turn. Weave-Retrieval is mocked
at the httpx seam, like tests/test_chat_e2e.py."""

import pytest

from app.core.config import settings
from app.services import router as router_service
from app.services.botconfig import load_bot as real_load_bot
from tests.conftest import AUTH_HEADERS, client
from tests.test_chat_e2e import _FakeResponse, _chunk, _readable_collections_response, _search_response

OVERVIEW_QUESTION = 'Was ist im Wissensbereich Vertraege?'
BODY = {'bot_id': 'legal-support', 'message': OVERVIEW_QUESTION, 'user': {'id': 'u-1', 'team': 'legal'}}

MEMORY = {
    'collections': [{
        'collection': 'vertraege', 'name': 'Vertraege', 'description': 'Arbeitsverträge',
        'summary': 'Muster und Regeln für Arbeitsverträge.', 'topics': ['Kündigung'], 'stale': False,
        'document_count': 1, 'documents': [{'document_id': 'doc-1', 'title': 'Arbeitsvertrag', 'document_type': 'Vertrag'}],
    }],
    'documents': [{
        'document_id': 'doc-1', 'collection': 'vertraege', 'title': 'Arbeitsvertrag', 'document_type': 'Vertrag',
        'summary': 'Regelt Probezeit und Kündigung.', 'key_points': ['Kündigungsfrist drei Monate'],
        'original_filename': 'arbeitsvertrag.pdf', 'document_version': 2, 'source': 'arbeitsvertrag.pdf',
        'source_kind': 'upload',
    }],
}


def _bot_without_filters(bot_id):
    bot = real_load_bot(bot_id)
    return bot.model_copy(update={'retrieval': bot.retrieval.model_copy(update={'filters': type(bot.retrieval.filters)()})})


@pytest.fixture
def memory_on(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'llm')
    monkeypatch.setattr('app.services.chat.load_bot', _bot_without_filters)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())


def _retrieval(monkeypatch, memory_response):
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append((url.rsplit('/', 1)[-1], json))
        if url.endswith('/memory'):
            return memory_response
        return _FakeResponse(200, _search_response(_chunk()))

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _post)
    return calls


# --- router -------------------------------------------------------------------


@pytest.mark.parametrize('message', [
    'Was weißt du?',
    'Was ist im Wissensbereich Personal?',
    'Welche Dokumente gibt es zum Thema Urlaub?',
    'Worum geht es in der Reisekostenrichtlinie?',
    'Which documents do you have about travel?',
])
def test_overview_questions_route_to_overview_only_with_memory(message):
    bot = real_load_bot('legal-support')
    assert router_service.route(message, bot, memory=True).intent == 'overview'
    assert router_service.route(message, bot).intent in ('conversational', 'knowledge')


@pytest.mark.parametrize('message', [
    'Hallo, was weißt du?',
    'Wie lang ist die Kündigungsfrist im Arbeitsvertrag?',
    # Detail questions a summary cannot answer: they keep the chunk search and its guard.
    'Welche Dokumente sind für den Visumsantrag nötig?',
    'Was steht im Dokument zur Kündigungsfrist?',
    'Worum geht es im Abschnitt 3 des Arbeitsvertrags?',
    'Which documents do I need to submit for reimbursement?',
    'What files are allowed for upload?',
])
def test_other_questions_keep_their_intent_with_memory(message):
    bot = real_load_bot('legal-support')
    assert router_service.route(message, bot, memory=True) == router_service.route(message, bot)


def test_llm_router_knows_overview_only_with_memory():
    bot = real_load_bot('legal-support')

    def answer(messages, model):
        return '{"intent": "overview", "confidence": 0.8}'

    decision = router_service.route('x', bot, 'llm', answer, memory=True)
    assert (decision.intent, decision.needs_retrieval, decision.router_fallback) == ('overview', True, False)
    assert router_service.route('x', bot, 'llm', answer).router_fallback is True
    assert 'overview:' in router_service._LLM_MEMORY_SYSTEM_PROMPT and 'six intents' in router_service._LLM_MEMORY_SYSTEM_PROMPT


# --- overview turn ------------------------------------------------------------


def test_overview_turn_answers_from_memory_with_sources(memory_on, monkeypatch):
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    resp = client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert [name for name, _ in calls] == ['memory']  # no chunk search needed
    payload = calls[0][1]
    assert payload['allowed_collections'] == ['vertraege', '__none__']  # the same scope a knowledge turn gets
    assert payload['query'] == OVERVIEW_QUESTION

    assert '[context:2]' in body['answer']
    overview, document = body['sources']
    assert overview['memory_kind'] == 'collection' and overview['document_id'] == 'collection:vertraege'
    assert overview['original_filename'] == 'Übersicht: Vertraege' and overview['chunk_id'] == 0
    assert document['memory_kind'] == 'document' and document['document_id'] == 'doc-1'
    assert document['source_kind'] == 'upload' and document['document_version'] == 2
    trace = body['trace']
    assert trace['intent'] == 'overview' and trace['guard'] is None
    assert trace['retrieval']['collections'] == ['vertraege', '__none__']
    assert 'memory_ms' in trace['timings_ms']


@pytest.mark.parametrize('memory_response', [
    _FakeResponse(200, {'collections': [], 'documents': []}),  # nothing built yet
    _FakeResponse(404, {'detail': 'Not Found'}),  # an older Weave-Retrieval
    _FakeResponse(503, {'detail': 'down'}),
])
def test_overview_turn_falls_back_to_the_chunk_search(memory_on, monkeypatch, memory_response):
    calls = _retrieval(monkeypatch, memory_response)
    body = client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS).json()
    assert [name for name, _ in calls] == ['memory', 'search']
    assert body['trace']['intent'] == 'overview'
    assert body['sources'][0]['memory_kind'] is None and body['sources'][0]['chunk_id'] == 1


def test_empty_memory_still_answers_what_do_you_know_from_the_bot_overview(memory_on, monkeypatch):
    """ADR-0009 §7: with nothing built, 'Was weißt du?' gets the same answer
    as without the memory (the overview block), never the guard reply."""
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append(url.rsplit('/', 1)[-1])
        return _FakeResponse(200, {'collections': [], 'documents': []} if url.endswith('/memory') else _search_response())

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _post)
    body = client.post('/internal/chat', json={**BODY, 'message': 'Was weißt du?'}, headers=AUTH_HEADERS).json()
    assert calls == ['memory']
    assert body['trace']['intent'] == 'conversational' and body['trace']['guard'] is None
    assert body['answer'] != real_load_bot('legal-support').guard.no_context_reply


@pytest.mark.parametrize('mode', ['Off', 'disabled', ''])
def test_an_unknown_mode_keeps_the_memory_off(monkeypatch, mode):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', mode)
    monkeypatch.setattr('app.services.chat.load_bot', _bot_without_filters)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    assert client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS).json()['trace']['intent'] == 'knowledge'
    assert [name for name, _ in calls] == ['search']


def test_bot_filters_the_memory_cannot_apply_skip_it(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'llm')
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS)  # legal-support filters department: legal
    assert [name for name, _ in calls] == ['search']


def test_memory_off_leaves_routing_and_retrieval_unchanged(monkeypatch):
    monkeypatch.setattr('app.services.chat.load_bot', _bot_without_filters)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    body = client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS).json()
    assert [name for name, _ in calls] == ['search']
    assert body['trace']['intent'] == 'knowledge'


# --- knowledge turn: orientation before the chunk search (ADR-0009 §10a) ----

KNOWLEDGE_QUESTION = 'Wie lang ist die Kündigungsfrist im Arbeitsvertrag?'
KNOWLEDGE_BODY = {**BODY, 'message': KNOWLEDGE_QUESTION}


def test_knowledge_turn_focuses_the_chunk_search_on_the_memory_documents(memory_on, monkeypatch):
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()

    assert [name for name, _ in calls] == ['memory', 'search']
    memory_payload, search_payload = calls[0][1], calls[1][1]
    # Same question, same scope and team axis for both calls -- the memory
    # is read with exactly the chunk search's permission inputs.
    assert memory_payload['query'] == search_payload['query'] == KNOWLEDGE_QUESTION
    assert memory_payload['allowed_collections'] == search_payload['allowed_collections'] == ['vertraege', '__none__']
    assert memory_payload['allowed_teams'] == search_payload['allowed_teams']
    assert memory_payload['limit'] == 5
    assert search_payload['filters'] == {'document_ids': ['doc-1']}

    trace = body['trace']
    assert trace['intent'] == 'knowledge' and trace['guard'] is None
    assert trace['retrieval']['focus_documents'] == ['doc-1']
    assert 'memory_ms' in trace['timings_ms'] and 'retrieval_ms' in trace['timings_ms']
    # Sources are the chunks only, never the summaries.
    assert [source['memory_kind'] for source in body['sources']] == [None]
    assert body['sources'][0]['chunk_id'] == 1
    assert '[context:1]' in body['answer']


def test_focused_search_without_hits_retries_unfocused(memory_on, monkeypatch):
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append((url.rsplit('/', 1)[-1], json))
        if url.endswith('/memory'):
            return _FakeResponse(200, MEMORY)
        return _FakeResponse(200, _search_response() if json['filters'] else _search_response(_chunk(document_id='doc-9')))

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _post)
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()
    assert [name for name, _ in calls] == ['memory', 'search', 'search']
    assert calls[1][1]['filters'] == {'document_ids': ['doc-1']} and calls[2][1]['filters'] is None
    assert body['trace']['retrieval']['focus_documents'] is None
    assert body['sources'][0]['document_id'] == 'doc-9' and body['trace']['guard'] is None


@pytest.mark.parametrize('memory_response', [
    _FakeResponse(200, {'collections': [], 'documents': []}),
    _FakeResponse(404, {'detail': 'Not Found'}),
    _FakeResponse(503, {'detail': 'down'}),
])
def test_empty_or_unavailable_memory_searches_exactly_as_before(memory_on, monkeypatch, memory_response):
    calls = _retrieval(monkeypatch, memory_response)
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()
    assert [name for name, _ in calls] == ['memory', 'search']
    assert calls[1][1]['filters'] is None
    assert body['trace']['retrieval']['focus_documents'] is None and body['trace']['guard'] is None


def test_guard_stays_when_the_focused_and_unfocused_search_find_nothing(memory_on, monkeypatch):
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append(url.rsplit('/', 1)[-1])
        return _FakeResponse(200, MEMORY if url.endswith('/memory') else _search_response())

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _post)
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()
    assert calls == ['memory', 'search', 'search']
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context', 'topic': None}
    assert body['answer'] == real_load_bot('legal-support').guard.no_context_reply and body['sources'] == []


def test_knowledge_turn_without_a_scope_never_reads_the_memory(memory_on, monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response(
        {'slug': 'other', 'name': 'Other', 'description': None, 'public': True},
    ))
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()
    assert calls == []
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_collections', 'topic': None}


def test_knowledge_turn_with_memory_off_or_bot_filters_is_unchanged(monkeypatch):
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())
    monkeypatch.setattr('app.services.chat.load_bot', _bot_without_filters)
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    body = client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS).json()  # mode off
    assert [name for name, _ in calls] == ['search'] and calls[0][1]['filters'] is None
    assert body['trace']['retrieval']['focus_documents'] is None and 'memory_ms' not in body['trace']['timings_ms']

    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'llm')
    monkeypatch.setattr('app.services.chat.load_bot', real_load_bot)  # legal-support filters department: legal
    calls.clear()
    client.post('/internal/chat', json=KNOWLEDGE_BODY, headers=AUTH_HEADERS)
    assert [name for name, _ in calls] == ['search'] and calls[0][1]['filters'] == {'department': 'legal'}


def test_mode_saved_in_ingest_wins_over_the_environment(monkeypatch):
    from app.services.chat_config_client import ChatProviderSnapshot

    monkeypatch.setattr('app.services.chat.load_bot', _bot_without_filters)
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', lambda *a, **k: _readable_collections_response())
    calls = _retrieval(monkeypatch, _FakeResponse(200, MEMORY))
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda endpoint: ChatProviderSnapshot(enabled=False, knowledge_memory_mode='extractive'),
    )
    assert client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS).json()['trace']['intent'] == 'overview'
    assert [name for name, _ in calls] == ['memory']

    monkeypatch.setattr(settings, 'knowledge_memory_mode', 'llm')
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda endpoint: ChatProviderSnapshot(enabled=False, knowledge_memory_mode='off'),
    )
    assert client.post('/internal/chat', json=BODY, headers=AUTH_HEADERS).json()['trace']['intent'] == 'knowledge'
