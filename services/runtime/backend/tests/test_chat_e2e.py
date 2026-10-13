"""End-to-end tests for the real POST /internal/chat pipeline
(app/services/chat.py) -- full HTTP round trips through the FastAPI app
against the two real bots under bots/ (general-assistant: no retrieval;
legal-support: retrieval enabled, team-restricted, require_sources,
collection-bound to 'vertraege'), with FakeLLM as the (default,
dependency-free) LLM provider and Weave-Retrieval's own HTTP calls mocked at
`httpx.post`/`httpx.get` -- the exact seam app/services/retrieval_client.py
itself talks to, no client/session object in between to substitute instead
(mirrors this module's own unit tests, tests/test_retrieval_client.py's
`_FakeResponse` pattern).

Every legal-support knowledge/complex-fallback turn below now also needs
`httpx.get` mocked for `GET /api/v1/collections`, since
`resolve_collection_scope` (app/services/chat.py) resolves the caller's
readable collections BEFORE ever calling `retrieval_client.search()` --
`_readable_collections_response` provides a 'legal' team readable set that
always includes 'vertraege', legal-support's own configured collection
(bots/legal-support.yaml), so these tests keep exercising the knowledge path
itself rather than the new Collections guard (see test_chat_service.py and
the dedicated 'no_collections' tests below for that guard on its own).

Router mode is the default ('rules', settings.router_mode) throughout --
'llm' mode is router.py's own concern (tests/test_router.py already covers
both modes at the unit level); this file only needs ONE router mode to
exercise the pipeline stages around it.
"""

import pytest

from app.services.chat import NO_COLLECTION_SENTINEL
from tests.conftest import AUTH_HEADERS, client


class _FakeResponse:
    """Just enough of an httpx.Response to exercise retrieval_client's own
    parsing -- mirrors tests/test_retrieval_client.py's identical helper."""

    def __init__(self, status_code: int, json_data=None, text: str = '') -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError('no json body on this fake response')
        return self._json_data


def _collection(**overrides) -> dict:
    collection = {'slug': 'vertraege', 'name': 'Vertraege', 'description': None, 'public': False}
    collection.update(overrides)
    return collection


def _readable_collections_response(*collections: dict):
    """A `GET /api/v1/collections` 200 response -- defaults to exactly the
    one collection legal-support.yaml itself is bound to, so a test that
    doesn't care about Collections scoping specifically can mock this once
    and still reach the ordinary knowledge/guard/complex-fallback paths."""
    return _FakeResponse(200, list(collections) or [_collection()])


def _chunk(**overrides) -> dict:
    chunk = {
        'chunk_id': 1,
        'document_id': 'doc-1',
        'text': 'Die Kündigungsfrist beträgt drei Monate zum Quartalsende.',
        'heading_path': ['Arbeitsvertrag', 'Kündigung'],
        'page_start': 4,
        'page_end': 4,
        'document_version': 1,
        'source': 'confluence',
        'original_filename': 'arbeitsvertrag.pdf',
        'team': 'legal',
        'department': 'legal',
        'scores': {'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.95},
        'embedding_model': 'fake-embed',
    }
    chunk.update(overrides)
    return chunk


def _search_response(*results: dict) -> dict:
    return {
        'query': 'query text',
        'results': list(results),
        'trace': {'vector_candidates': 0, 'fulltext_candidates': 0, 'fused': 0, 'reranked': 0},
    }


_KNOWLEDGE_QUESTION = 'Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?'
_LEGAL_BODY = {'bot_id': 'legal-support', 'message': _KNOWLEDGE_QUESTION, 'user': {'id': 'u-1', 'team': 'legal'}}


# --- conversational flow -----------------------------------------------------


def test_conversational_flow_answers_via_the_llm_with_no_retrieval_and_no_sources(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a conversational turn must never call out to Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'general-assistant', 'message': 'Hallo!'},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body['answer'] == '[fake-llm] Hallo!'
    assert body['sources'] == []
    trace = body['trace']
    assert trace['intent'] == 'conversational'
    assert trace['needs_retrieval'] is False
    assert trace['retrieval'] is None
    assert trace['guard'] is None
    assert trace['model'] == 'fake-chat'
    assert trace['router_mode'] == 'rules'
    assert set(trace['timings_ms']) >= {'router_ms', 'llm_ms', 'total_ms'}


# --- knowledge flow with sources ---------------------------------------------


def test_knowledge_flow_passes_context_to_the_llm_and_returns_sources(monkeypatch):
    sent_requests = []
    sent_collection_requests = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        sent_collection_requests.append({'url': url, 'headers': headers, 'params': params, 'timeout': timeout})
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        sent_requests.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
        payload = _search_response(
            _chunk(chunk_id=1, document_id='doc-1', text='erster Auszug'),
            _chunk(chunk_id=2, document_id='doc-2', text='zweiter Auszug', source='sharepoint', scores={
                'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.4, 'rerank': None,
            }),
        )
        return _FakeResponse(200, payload)

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    # FakeLLM's own '[context:N]' marker (app/services/llm.py) only appears
    # when a system-role message actually carried N '[source ...]' lines --
    # this is the proof that retrieval's chunks really reached the LLM call,
    # without needing a real LLM in the loop to inspect the raw messages.
    assert '[context:2]' in body['answer']

    sources = body['sources']
    assert len(sources) == 2
    assert sources[0]['document_id'] == 'doc-1'
    assert sources[0]['chunk_id'] == 1
    assert sources[0]['score'] == 0.95  # rerank present -> preferred over rrf
    assert sources[1]['document_id'] == 'doc-2'
    assert sources[1]['score'] == 0.4  # rerank is None here -> falls back to rrf

    trace = body['trace']
    assert trace['intent'] == 'knowledge'
    assert trace['needs_retrieval'] is True
    # legal-support.yaml leaves `retrieval.include_uncollected` at its
    # default (True), so NO_COLLECTION_SENTINEL ('__none__') is appended
    # alongside the real 'vertraege' slug -- both here (the trace) and in
    # the outgoing `allowed_collections` asserted further down. See
    # test_chat_service.py's own `resolve_collection_scope`/
    # `include_uncollected` tests for that function's logic in isolation.
    assert trace['retrieval'] == {
        'candidates': 2, 'used': 2, 'collections': ['vertraege', '__none__'], 'requested_collections': None, 'rewritten_query': None,
    }
    assert trace['guard'] is None
    assert 'retrieval_ms' in trace['timings_ms']

    # resolve_collection_scope's own GET call: the CALLING USER's team, so
    # Weave-Retrieval can answer "what may 'legal' actually read".
    assert len(sent_collection_requests) == 1
    assert sent_collection_requests[0]['params'] == {'team': 'legal'}

    # The outgoing Weave-Retrieval search request itself: legal-support's
    # own filters, allowed_teams scoped to the CALLING USER's team (see
    # app/services/chat.py's _allowed_teams docstring for why that's
    # preferred over the bot's own permissions.teams here), and
    # allowed_collections as resolved above -- legal-support.yaml's own
    # 'vertraege' intersected with what 'legal' may read (here: everything
    # _readable_collections_response returns), plus the Altbestand sentinel.
    assert len(sent_requests) == 1
    sent_json = sent_requests[0]['json']
    assert sent_json['query'] == _KNOWLEDGE_QUESTION
    assert sent_json['filters'] == {'department': 'legal'}
    assert sent_json['allowed_teams'] == ['legal']
    assert sent_json['allowed_collections'] == ['vertraege', '__none__']
    assert sent_json['top_k'] == 20
    assert sent_json['final_k'] == 5


# --- guard flow: retrieval finds nothing -------------------------------------


def test_guard_flow_returns_no_context_reply_when_retrieval_finds_nothing(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _empty_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response())

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _empty_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert body['answer'] == 'Ich habe dazu keine belegten Informationen in den Rechtsdokumenten gefunden.'
    assert body['sources'] == []

    trace = body['trace']
    assert trace['intent'] == 'knowledge'
    # A collection scope WAS resolved (the search call itself ran, with
    # 'vertraege' plus the Altbestand sentinel as its allowed_collections,
    # since legal-support.yaml leaves include_uncollected at its True
    # default) -- this is the 'no_context' guard (search ran but found
    # nothing), not 'no_collections' (search never ran at all, see the
    # dedicated test for that below).
    assert trace['retrieval'] == {
        'candidates': 0, 'used': 0, 'collections': ['vertraege', '__none__'], 'requested_collections': None, 'rewritten_query': None,
    }
    assert trace['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert trace['model'] is None  # the guard short-circuits before any LLM call


# --- uncertainty instruction after the context block -------------------------


def _spy_fake_llm(monkeypatch):
    from app.services.llm import FakeLLM

    seen: list[list[dict[str, str]]] = []
    original = FakeLLM.chat

    def _chat(self, messages, model=None, temperature=None):
        seen.append(messages)
        return original(self, messages, model=model, temperature=temperature)

    monkeypatch.setattr(FakeLLM, 'chat', _chat)
    return seen


def _guard_bot(monkeypatch, **guard):
    from app.services import chat as chat_module
    from app.services.botconfig import load_bot

    bot = load_bot('legal-support')
    monkeypatch.setattr(
        chat_module, 'load_bot', lambda bot_id: bot.model_copy(update={'guard': bot.guard.model_copy(update=guard)})
    )


def _single_hit(monkeypatch, rerank):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(
            _chunk(scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': rerank}),
        ))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)


def test_require_sources_bot_gets_the_uncertainty_instruction_after_the_context(monkeypatch):
    from app.services.chat import _UNCERTAINTY_INSTRUCTION, _WEAK_CONTEXT_INSTRUCTION

    seen = _spy_fake_llm(monkeypatch)
    _single_hit(monkeypatch, rerank=0.95)

    body = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS).json()
    assert '[context:1]' in body['answer']  # the source count is unaffected by the extra message

    messages = seen[-1]
    assert [turn['role'] for turn in messages] == ['system', 'system', 'system', 'user']
    assert messages[1]['content'].startswith('Keep any relevant image markdown')
    assert messages[2]['content'] == _UNCERTAINTY_INSTRUCTION
    assert _WEAK_CONTEXT_INSTRUCTION not in messages[2]['content']
    assert messages[3] == {'role': 'user', 'content': _KNOWLEDGE_QUESTION}


@pytest.mark.parametrize(('rerank', 'weak_expected'), [(0.3, True), (0.5, False), (None, False)])
def test_weak_context_hint_follows_the_best_rerank_score(monkeypatch, rerank, weak_expected):
    from app.services.chat import _UNCERTAINTY_INSTRUCTION, _WEAK_CONTEXT_INSTRUCTION

    seen = _spy_fake_llm(monkeypatch)
    _guard_bot(monkeypatch, weak_context_below=0.5)
    _single_hit(monkeypatch, rerank=rerank)

    client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    instruction = seen[-1][2]['content']
    assert instruction.startswith(_UNCERTAINTY_INSTRUCTION)
    assert (_WEAK_CONTEXT_INSTRUCTION in instruction) is weak_expected


@pytest.mark.parametrize('guard', [{'uncertainty_hint': False}, {'require_sources': False}])
def test_uncertainty_instruction_is_omitted_when_switched_off(monkeypatch, guard):
    seen = _spy_fake_llm(monkeypatch)
    _guard_bot(monkeypatch, **guard)
    _single_hit(monkeypatch, rerank=0.95)

    client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert [turn['role'] for turn in seen[-1]] == ['system', 'system', 'user']


# --- clarify guard: weak hits become a clarifying question -------------------


def _clarify_bot(monkeypatch, **guard):
    from app.services import chat as chat_module
    from app.services.botconfig import load_bot

    bot = load_bot('legal-support')
    patched = bot.model_copy(update={'guard': bot.guard.model_copy(update={'clarify_below': 0.4, **guard})})
    monkeypatch.setattr(chat_module, 'load_bot', lambda bot_id: patched)
    return patched


def test_clarify_guard_asks_instead_of_answering_when_every_hit_is_weak(monkeypatch):
    _clarify_bot(monkeypatch)

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(
            _chunk(chunk_id=1, document_id='doc-1', heading_path=['Arbeitsvertrag', 'Kündigung'],
                   scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': 0.21}),
            _chunk(chunk_id=2, document_id='doc-2', heading_path=[], original_filename='betriebsvereinbarung.pdf',
                   scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': 0.35}),
            _chunk(chunk_id=3, document_id='doc-1', heading_path=['Arbeitsvertrag', 'Probezeit'],
                   scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': 0.30}),
        ))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert body['answer'] == 'Ich bin mir nicht sicher, worauf sich deine Frage bezieht. Meinst du eines dieser Dokumente?'
    assert body['sources'] == []
    trace = body['trace']
    assert trace['guard'] == {'triggered': True, 'reason': 'clarify'}
    assert trace['retrieval']['candidates'] == 3
    assert trace['retrieval']['used'] == 0
    assert trace['model'] is None
    # Best score first, one entry per document, label from heading / file name.
    assert trace['clarification'] == {
        'question': body['answer'],
        'options': [
            {
                'label': 'betriebsvereinbarung',
                'message': f'{_KNOWLEDGE_QUESTION} (Dokument: betriebsvereinbarung)',
                'document_id': 'doc-2',
                'collection': None,
            },
            {
                'label': 'Arbeitsvertrag',
                'message': f'{_KNOWLEDGE_QUESTION} (Dokument: Arbeitsvertrag)',
                'document_id': 'doc-1',
                'collection': None,
            },
        ],
    }


def test_clarify_guard_stays_silent_when_one_hit_is_strong_enough(monkeypatch):
    _clarify_bot(monkeypatch)

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(
            _chunk(chunk_id=1, document_id='doc-1', scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': 0.4}),
            _chunk(chunk_id=2, document_id='doc-2', scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': 0.1}),
        ))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    body = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS).json()
    assert '[context:2]' in body['answer']
    assert body['trace']['guard'] is None
    assert body['trace']['clarification'] is None


def test_clarify_guard_never_fires_without_rerank_scores_or_hits(monkeypatch):
    _clarify_bot(monkeypatch)

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    responses = iter([
        _search_response(_chunk(scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.5, 'rerank': None})),
        _search_response(),
    ])
    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr(
        'app.services.retrieval_client.httpx.post',
        lambda url, headers=None, json=None, timeout=None: _FakeResponse(200, next(responses)),
    )

    unreranked = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS).json()
    assert '[context:1]' in unreranked['answer']
    assert unreranked['trace']['clarification'] is None

    empty = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS).json()
    assert empty['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert empty['trace']['clarification'] is None


def test_clarify_options_are_capped_and_use_the_document_id_as_last_resort(monkeypatch):
    bot = _clarify_bot(monkeypatch, clarify_max_options=2)
    from app.services.chat import _build_clarification
    from app.services.retrieval_client import RetrievedChunk, RetrievedChunkScores

    def _hit(document_id, rerank):
        return RetrievedChunk(
            chunk_id=1, document_id=document_id, text='t', source='upload', original_filename=None,
            page_start=None, page_end=None, document_version=1, heading_path=[],
            scores=RetrievedChunkScores(rerank=rerank), collection='vertraege',
        )

    clarification = _build_clarification(bot, '  Frage?  ', [_hit('a', 0.1), _hit('b', 0.3), _hit('c', 0.2)])
    assert [option.label for option in clarification.options] == ['b', 'c']
    assert clarification.options[0].message == 'Frage? (Dokument: b)'
    assert clarification.options[0].collection == 'vertraege'


# --- history-aware query rewrite ---------------------------------------------


def test_query_rewrite_searches_the_rewritten_query_and_reports_it(monkeypatch):
    sent_requests = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        sent_requests.append(json)
        return _FakeResponse(200, _search_response(_chunk()))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)
    monkeypatch.setattr('app.services.chat.settings.retrieval_query_rewrite_default', True)
    seen = {}

    def _fake_rewrite(bot, message, history, llm_provider):
        seen['message'], seen['history'], seen['provider'] = message, history, llm_provider
        return 'Kündigungsfrist bei Teilzeit laut Arbeitsvertrag'

    monkeypatch.setattr('app.services.chat._rewrite_query', _fake_rewrite)

    body = {
        **_LEGAL_BODY,
        'message': 'Und bei Teilzeit?',
        'history': [
            {'role': 'user', 'content': _KNOWLEDGE_QUESTION},
            {'role': 'assistant', 'content': 'Drei Monate zum Quartalsende.'},
        ],
    }
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    payload = resp.json()

    assert sent_requests[0]['query'] == 'Kündigungsfrist bei Teilzeit laut Arbeitsvertrag'
    assert payload['trace']['retrieval']['rewritten_query'] == 'Kündigungsfrist bei Teilzeit laut Arbeitsvertrag'
    # The answering LLM still sees the ORIGINAL message, not the rewrite.
    assert payload['answer'].startswith('[fake-llm] Und bei Teilzeit?')
    assert seen['message'] == 'Und bei Teilzeit?'
    assert [turn['role'] for turn in seen['history']] == ['user', 'assistant']
    assert seen['provider'] is not None


class _StubProvider:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, messages, model=None, temperature=None):
        from app.services.llm import LLMResult, LLMUsage

        self.calls.append({'messages': messages, 'model': model, 'temperature': temperature})
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResult(content=self.reply, model=model or 'stub', usage=LLMUsage(prompt_tokens=1, completion_tokens=1))


def _rewrite_bot(enabled: bool | None):
    from app.services.botconfig import load_bot

    bot = load_bot('legal-support')
    return bot.model_copy(update={'retrieval': bot.retrieval.model_copy(update={'query_rewrite': enabled})})


_HISTORY = [{'role': 'user', 'content': 'Welche Kündigungsfrist gilt?'}, {'role': 'assistant', 'content': 'Drei Monate.'}]


@pytest.mark.parametrize(
    ('reply', 'expected'),
    [
        ('"Kündigungsfrist bei Teilzeit"', 'Kündigungsfrist bei Teilzeit'),  # quotes + whitespace cleaned
        ('Und bei Teilzeit?', None),  # identical to the message -> raw message is searched
        ('   ', None),
        ('x' * 600, None),  # implausibly long
        (RuntimeError('provider down'), None),  # failure never breaks the turn
    ],
)
def test_rewrite_query_cleans_or_rejects_the_llm_reply(reply, expected):
    from app.services.chat import _rewrite_query

    provider = _StubProvider(reply)
    assert _rewrite_query(_rewrite_bot(True), 'Und bei Teilzeit?', _HISTORY, provider) == expected
    assert provider.calls[0]['temperature'] == 0.0
    assert provider.calls[0]['model'] == 'fake-chat'
    prompt = provider.calls[0]['messages'][1]['content']
    assert 'Nutzer: Welche Kündigungsfrist gilt?' in prompt
    assert 'Assistent: Drei Monate.' in prompt
    assert prompt.endswith('Letzte Nutzernachricht:\nUnd bei Teilzeit?')


def test_rewrite_query_is_skipped_without_history_provider_or_opt_in(monkeypatch):
    from app.services.chat import _rewrite_query

    provider = _StubProvider('Eigenständige Frage')
    assert _rewrite_query(_rewrite_bot(True), 'Frage?', [], provider) is None
    assert _rewrite_query(_rewrite_bot(True), 'Frage?', _HISTORY, None) is None
    assert _rewrite_query(_rewrite_bot(False), 'Frage?', _HISTORY, provider) is None
    # None on the bot defers to the runtime default (off unless configured).
    monkeypatch.setattr('app.services.chat.settings.retrieval_query_rewrite_default', False)
    assert _rewrite_query(_rewrite_bot(None), 'Frage?', _HISTORY, provider) is None
    monkeypatch.setattr('app.services.chat.settings.retrieval_query_rewrite_default', True)
    assert _rewrite_query(_rewrite_bot(None), 'Frage?', _HISTORY, provider) == 'Eigenständige Frage'
    assert len(provider.calls) == 1


def test_rewrite_prompt_only_carries_the_most_recent_turns(monkeypatch):
    from app.services.chat import _query_rewrite_messages

    monkeypatch.setattr('app.services.chat.settings.retrieval_query_rewrite_history_turns', 2)
    history = [{'role': 'user', 'content': f'Frage {index}'} for index in range(5)]
    prompt = _query_rewrite_messages('Nachfrage', history)[1]['content']
    assert 'Frage 3' in prompt and 'Frage 4' in prompt
    assert 'Frage 2' not in prompt


# --- guard flow: min_score drops weak rerank hits ---------------------------


def test_min_score_drops_weak_rerank_hits_but_keeps_unreranked_ones(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(
            _chunk(chunk_id=1, document_id='doc-1', scores={'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.95}),
            _chunk(chunk_id=2, document_id='doc-2', scores={'vector': 0.6, 'fulltext': 0.4, 'rrf': 0.5, 'rerank': 0.12}),
            # No rerank score (reranker off/failed): must pass regardless.
            _chunk(chunk_id=3, document_id='doc-3', scores={'vector': 0.5, 'fulltext': 0.3, 'rrf': 0.4, 'rerank': None}),
        ))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)
    # legal-support.yaml leaves guard.min_score unset -> runtime default applies.
    monkeypatch.setattr('app.services.chat.settings.guard_default_min_score', 0.5)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert '[context:2]' in body['answer']
    assert [source['document_id'] for source in body['sources']] == ['doc-1', 'doc-3']
    assert body['trace']['retrieval']['candidates'] == 3
    assert body['trace']['retrieval']['used'] == 2
    assert body['trace']['guard'] is None  # guard did not fire


def test_min_score_dropping_every_hit_fires_the_no_context_guard(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(
            _chunk(chunk_id=1, document_id='doc-1', scores={'vector': 0.7, 'fulltext': 0.4, 'rrf': 0.6, 'rerank': 0.2}),
        ))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)
    monkeypatch.setattr('app.services.chat.settings.guard_default_min_score', 0.5)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert body['answer'] == 'Ich habe dazu keine belegten Informationen in den Rechtsdokumenten gefunden.'
    assert body['sources'] == []
    assert body['trace']['retrieval']['candidates'] == 1
    assert body['trace']['retrieval']['used'] == 0
    assert body['trace']['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert body['trace']['model'] is None


# --- guard flow: no readable/allowed collection at all -----------------------


def test_guard_flow_returns_no_collections_reply_when_the_user_cannot_read_the_bots_collection(monkeypatch):
    # legal-support.yaml is bound to 'vertraege' -- a caller whose team may
    # only read a DIFFERENT collection must never reach Weave-Retrieval's
    # own search at all (Collections contract point 5/backend/app/services/
    # chat.py's own docstring, step 5a). This must hold DESPITE
    # `retrieval.include_uncollected` defaulting to True: legal-support.yaml
    # names its own Collections restriction (['vertraege']), so the one
    # include_uncollected exception (a pure Altbestand-only search) never
    # applies here -- an empty REAL intersection for a Collections-bound bot
    # always guards, sentinel notwithstanding (see
    # resolve_collection_scope's own docstring).
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response(_collection(slug='handbuch', name='Mitarbeiterhandbuch', public=True))

    def _unexpected_post(*args, **kwargs):
        raise AssertionError('resolve_collection_scope found nothing shared -- search() must never be called')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert body['answer'] == 'Ich habe dazu keine belegten Informationen in den Rechtsdokumenten gefunden.'
    assert body['sources'] == []

    trace = body['trace']
    assert trace['intent'] == 'knowledge'
    assert trace['retrieval'] == {'candidates': 0, 'used': 0, 'collections': [], 'requested_collections': None, 'rewritten_query': None}
    assert trace['guard'] == {'triggered': True, 'reason': 'no_collections'}
    assert trace['model'] is None


# --- per-request Collections filter ("Collection-Filter pro Anfrage") --------
#
# `ChatRequest.collections` -- a caller-supplied filter, applied strictly
# AFTER legal-support's own rights scope (bot.retrieval.collections=
# ['vertraege'] intersected with whatever 'legal' may read, plus the
# Altbestand sentinel per include_uncollected's True default) has already
# been resolved. See test_chat_service.py for that filter's pure-function
# behaviour (`resolve_collection_scope`/`_apply_collections_filter`) in
# isolation; these exercise it through the full HTTP pipeline instead,
# alongside the new `requested_collections` trace field and the dedicated
# `'filter_excluded_all'` guard reason.


def test_collections_filter_restricts_the_effective_search_scope(monkeypatch):
    # Unfiltered, legal-support's own scope would be ['vertraege',
    # '__none__'] (see test_knowledge_flow_... above). Filtering the
    # request down to exactly 'vertraege' drops the Altbestand sentinel
    # too -- the caller asked for this one real Collection, not legacy
    # documents alongside it.
    sent_requests = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        sent_requests.append(json)
        return _FakeResponse(200, _search_response(_chunk(chunk_id=1, document_id='doc-1')))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    body_with_filter = {**_LEGAL_BODY, 'collections': ['vertraege']}
    resp = client.post('/internal/chat', json=body_with_filter, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    trace = body['trace']
    assert trace['retrieval'] == {
        'candidates': 1, 'used': 1, 'collections': ['vertraege'], 'requested_collections': ['vertraege'], 'rewritten_query': None,
    }
    assert trace['guard'] is None
    assert sent_requests[0]['allowed_collections'] == ['vertraege']


def test_collections_filter_with_a_foreign_slug_is_dropped_silently_and_the_rest_searches_normally(monkeypatch):
    # 'geheimprojekt' is not a Collection this caller/bot combination was
    # ever entitled to (never on legal-support.yaml's own list, never in
    # what 'legal' may read) -- it must be dropped without a trace, while
    # the rest of the filter (naming a slug that IS already in scope)
    # resolves exactly as if 'geheimprojekt' had never been mentioned.
    sent_requests = []

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        sent_requests.append(json)
        return _FakeResponse(200, _search_response(_chunk(chunk_id=1, document_id='doc-1')))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    # Naming the sentinel too (alongside 'vertraege') reproduces this
    # scope's own unfiltered result -- proof that 'geheimprojekt' contributed
    # nothing at all, neither adding itself nor disturbing the rest.
    body_with_filter = {**_LEGAL_BODY, 'collections': ['vertraege', NO_COLLECTION_SENTINEL, 'geheimprojekt']}
    resp = client.post('/internal/chat', json=body_with_filter, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    trace = body['trace']
    assert trace['retrieval']['collections'] == ['vertraege', '__none__']
    assert trace['retrieval']['requested_collections'] == ['vertraege', '__none__', 'geheimprojekt']
    assert trace['guard'] is None
    assert sent_requests[0]['allowed_collections'] == ['vertraege', '__none__']


def test_collections_filter_naming_only_foreign_slugs_triggers_its_own_guard_reason(monkeypatch):
    # legal-support's own scope IS non-empty here (readable includes
    # 'vertraege', legal-support's own configured collection) -- unlike
    # test_guard_flow_returns_no_collections_reply_..., this caller has
    # real read-authority. It is the caller's OWN filter, naming only a
    # Collection outside that authority, that empties the result -- a
    # DIFFERENT, distinguishable guard reason from 'no_collections'.
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _unexpected_post(*args, **kwargs):
        raise AssertionError('the filter excluded every collection this scope had -- search() must never be called')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    body_with_filter = {**_LEGAL_BODY, 'collections': ['geheimprojekt']}
    resp = client.post('/internal/chat', json=body_with_filter, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    trace = body['trace']
    assert trace['guard'] == {'triggered': True, 'reason': 'filter_excluded_all'}
    assert trace['retrieval'] == {
        'candidates': 0, 'used': 0, 'collections': [], 'requested_collections': ['geheimprojekt'], 'rewritten_query': None,
    }
    assert trace['model'] is None
    assert body['sources'] == []
    # A DIFFERENT fixed reply from the ordinary no_collections/no_context
    # text ("Ich habe dazu keine belegten Informationen ...") -- this
    # caller has read-authority, its OWN filter is what excluded everything,
    # a materially different, caller-fixable condition (see GuardTrace's own
    # docstring, app/schemas/chat.py).
    assert body['answer'] != 'Ich habe dazu keine belegten Informationen in den Rechtsdokumenten gefunden.'
    assert 'Auswahl' in body['answer']


def test_no_collections_filter_is_byte_for_byte_the_pre_filter_behaviour(monkeypatch):
    # Regression test: a request that omits `collections` entirely (the
    # ChatRequest default, `None`) must resolve identically to how this
    # exact body behaved before the filter existed at all -- mirrors
    # test_knowledge_flow_passes_context_to_the_llm_and_returns_sources
    # above, with the new trace field's own value asserted explicitly.
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response(_chunk(chunk_id=1, document_id='doc-1')))

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    trace = body['trace']
    assert trace['retrieval'] == {
        'candidates': 1, 'used': 1, 'collections': ['vertraege', '__none__'], 'requested_collections': None, 'rewritten_query': None,
    }
    assert trace['guard'] is None


# --- permissions --------------------------------------------------------------


def test_permission_denied_for_a_team_not_on_the_bots_allowlist(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a denied request must never reach Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    body = {**_LEGAL_BODY, 'user': {'id': 'u-1', 'team': 'sales'}}
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 403


def test_permission_allows_a_team_on_the_bots_allowlist():
    # 'management' is on legal-support's allowlist too, not just 'legal' --
    # combined with the retrieval-flow tests above (team 'legal'), this
    # covers both permitted teams, not just one.
    body = {**_LEGAL_BODY, 'message': 'Hallo!', 'user': {'id': 'u-1', 'team': 'management'}}
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    # 'Hallo!' routes to 'conversational' (matched before the retrieval-only
    # 'knowledge' default), so no retrieval mock is even needed here.
    assert resp.status_code == 200
    assert resp.json()['trace']['intent'] == 'conversational'


# --- document/action: not yet supported in V1 --------------------------------


def test_document_intent_returns_the_polite_v1_placeholder_with_no_llm_call(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a V1-unsupported intent must never call out to Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'general-assistant', 'message': 'Fass dieses PDF zusammen.'},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert 'V2' in body['answer'] or 'V3' in body['answer']
    assert body['sources'] == []
    trace = body['trace']
    assert trace['intent'] == 'document'
    assert trace['retrieval'] is None
    assert trace['model'] is None  # no LLM call was made for this turn
    assert 'llm_ms' not in trace['timings_ms']


def test_action_intent_returns_the_polite_v1_placeholder():
    resp = client.post(
        '/internal/chat',
        json={'bot_id': 'general-assistant', 'message': 'Buche mir einen Flug nach München.'},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['trace']['intent'] == 'action'
    assert body['trace']['needs_tool'] is True
    assert body['sources'] == []


# --- RetrievalUnavailable -> 503 ----------------------------------------------


def test_retrieval_unavailable_surfaces_as_503_with_a_clear_detail(monkeypatch):
    import httpx

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _connection_refused(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _connection_refused)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()['detail']  # a non-empty, human-readable detail message


def test_retrieval_unavailable_from_the_collections_call_also_surfaces_as_503(monkeypatch):
    # resolve_collection_scope's own GET call (app/services/chat.py) can
    # fail exactly like search()'s POST -- see this module's own docstring
    # note on why app/api/internal.py maps RetrievalUnavailable from either
    # one to the same 503, and this pipeline's own module docstring.
    import httpx

    def _connection_refused(url, headers=None, params=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a failed collections lookup must never reach search()')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _connection_refused)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post('/internal/chat', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()['detail']


# --- complex intent: best-effort knowledge fallback --------------------------


def test_complex_intent_with_retrieval_enabled_runs_the_knowledge_path(monkeypatch):
    # Regression: 'complex' used to short-circuit into the V1 placeholder
    # even when the router flagged needs_retrieval and the bot has retrieval
    # enabled -- contracts/internal-chat.md promises a best-effort knowledge
    # turn for exactly that case.
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        payload = _search_response(
            _chunk(chunk_id=7, document_id='doc-7', text='Regelung aus Vertrag A'),
        )
        return _FakeResponse(200, payload)

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    body = dict(_LEGAL_BODY, message='Vergleiche die Regelungen aus Vertrag A und Vertrag B.')
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data['trace']['intent'] == 'complex'
    assert data['trace']['needs_retrieval'] is True
    assert '[context:1]' in data['answer']
    assert len(data['sources']) == 1
    assert data['sources'][0]['document_id'] == 'doc-7'


def test_complex_intent_on_general_assistant_requires_retrieval_service():
    # general-assistant now searches approved knowledge. A missing Retrieval
    # service is therefore surfaced as an unavailable dependency, not hidden
    # behind the old no-retrieval placeholder.
    body = {
        'bot_id': 'general-assistant',
        'message': 'Vergleiche die Regelungen aus Vertrag A und Vertrag B.',
        'user': {'id': 'u-1', 'team': 'legal'},
    }
    resp = client.post('/internal/chat', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 503
