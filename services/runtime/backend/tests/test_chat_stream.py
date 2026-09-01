"""End-to-end tests for POST /internal/chat/stream (app/api/internal.py,
app/services/chat.py's `handle_chat_stream`/`_stream_prepared_turn`) -- the
streaming counterpart to tests/test_chat_e2e.py. Mirrors that module's own
style (real bots under bots/, FakeLLM as the LLM provider, Weave-Retrieval's
own HTTP calls mocked at `httpx.get`/`httpx.post`), but reads the response as
a sequence of SSE `data: <json>` events instead of one JSON body, and asserts
the event-ordering guarantee contracts/internal-chat.md's stream section
promises: `trace` once, before any `delta`; `delta` zero or more times;
then either `sources`+`done`, or a single terminal `error` in place of both.
"""

import json

from tests.conftest import AUTH_HEADERS, client


class _FakeResponse:
    """Mirrors tests/test_chat_e2e.py's identical helper -- just enough of
    an httpx.Response to exercise retrieval_client's own parsing."""

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


def _parse_sse_events(text: str) -> list[dict]:
    """Split a `text/event-stream` body back into its individual JSON
    events, in emission order -- the inverse of app/api/internal.py's own
    `_sse_event` ('data: <json>\\n\\n' per event)."""
    events = []
    for block in text.split('\n\n'):
        block = block.strip('\n')
        if not block:
            continue
        assert block.startswith('data: '), f'not an SSE data line: {block!r}'
        events.append(json.loads(block[len('data: '):]))
    return events


# --- conversational flow: ordering + multiple deltas --------------------------


def test_stream_conversational_flow_emits_trace_then_multiple_deltas_then_sources_then_done(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a conversational turn must never call out to Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post(
        '/internal/chat/stream',
        json={'bot_id': 'general-assistant', 'message': 'Hallo mein Freund, wie geht es dir heute?'},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.headers['content-type'].startswith('text/event-stream')

    events = _parse_sse_events(resp.text)
    types = [event['type'] for event in events]

    # trace first, done last, sources immediately before done -- and more
    # than one delta in between (FakeLLM's own multi-delta chat_stream).
    assert types[0] == 'trace'
    assert types[-1] == 'done'
    assert types[-2] == 'sources'
    delta_types = types[1:-2]
    assert delta_types, 'expected at least one delta event'
    assert all(t == 'delta' for t in delta_types)
    assert len(delta_types) > 1, 'FakeLLM.chat_stream must split its reply into multiple deltas'

    trace_event = events[0]
    assert trace_event['trace']['intent'] == 'conversational'
    assert trace_event['trace']['model'] == 'fake-chat'
    assert trace_event['trace']['guard'] is None
    # llm_ms/total_ms cannot be known before the first delta -- see
    # ChatStreamTraceEvent's own docstring.
    assert 'router_ms' in trace_event['trace']['timings_ms']
    assert 'llm_ms' not in trace_event['trace']['timings_ms']
    assert 'total_ms' not in trace_event['trace']['timings_ms']

    reconstructed = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert reconstructed == '[fake-llm] Hallo mein Freund, wie geht es dir heute?'

    sources_event = events[-2]
    assert sources_event['sources'] == []


# --- knowledge flow with sources ----------------------------------------------


def test_stream_knowledge_flow_reconstructs_the_context_marked_answer_and_sends_sources_after_deltas(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _fake_post(url, headers=None, json=None, timeout=None):
        payload = _search_response(
            _chunk(chunk_id=1, document_id='doc-1', text='erster Auszug'),
            _chunk(chunk_id=2, document_id='doc-2', text='zweiter Auszug', source='sharepoint'),
        )
        return _FakeResponse(200, payload)

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _fake_post)

    resp = client.post('/internal/chat/stream', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    assert events[0]['type'] == 'trace'
    trace = events[0]['trace']
    assert trace['intent'] == 'knowledge'
    assert trace['retrieval'] == {
        'candidates': 2, 'used': 2, 'collections': ['vertraege', '__none__'], 'requested_collections': None,
    }
    assert 'retrieval_ms' in trace['timings_ms']

    reconstructed = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert '[context:2]' in reconstructed

    sources_event = next(event for event in events if event['type'] == 'sources')
    assert len(sources_event['sources']) == 2
    assert sources_event['sources'][0]['document_id'] == 'doc-1'

    # sources strictly after every delta, done strictly last.
    sources_index = events.index(sources_event)
    assert all(events[i]['type'] == 'delta' for i in range(1, sources_index))
    assert events[-1]['type'] == 'done'


# --- guard flow: retrieval finds nothing --------------------------------------


def test_stream_guard_flow_streams_the_guard_text_with_empty_sources_no_context(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _empty_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(200, _search_response())

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _empty_post)

    resp = client.post('/internal/chat/stream', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    trace = events[0]['trace']
    assert trace['guard'] == {'triggered': True, 'reason': 'no_context'}
    assert trace['model'] is None  # no LLM call happens for a guard-triggered turn

    reconstructed = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert reconstructed == 'Ich habe dazu keine belegten Informationen in den Rechtsdokumenten gefunden.'
    assert len(reconstructed.split()) > 1  # actually split into multiple word-deltas, not one blob

    sources_event = next(event for event in events if event['type'] == 'sources')
    assert sources_event['sources'] == []
    assert events[-1]['type'] == 'done'


def test_stream_guard_flow_streams_the_guard_text_with_empty_sources_no_collections(monkeypatch):
    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response(_collection(slug='handbuch', name='Mitarbeiterhandbuch', public=True))

    def _unexpected_post(*args, **kwargs):
        raise AssertionError('resolve_collection_scope found nothing shared -- search() must never be called')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post('/internal/chat/stream', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    trace = events[0]['trace']
    assert trace['guard'] == {'triggered': True, 'reason': 'no_collections'}
    assert trace['retrieval'] == {'candidates': 0, 'used': 0, 'collections': [], 'requested_collections': None}

    sources_event = next(event for event in events if event['type'] == 'sources')
    assert sources_event['sources'] == []


# --- V1-unsupported intent: streamed placeholder, no LLM call -----------------


def test_stream_document_intent_streams_the_v1_placeholder_with_no_llm_call(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a V1-unsupported intent must never call out to Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    resp = client.post(
        '/internal/chat/stream',
        json={'bot_id': 'general-assistant', 'message': 'Fass dieses PDF zusammen.'},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    trace = events[0]['trace']
    assert trace['intent'] == 'document'
    assert trace['retrieval'] is None
    assert trace['model'] is None

    reconstructed = ''.join(event['text'] for event in events if event['type'] == 'delta')
    assert 'V2' in reconstructed or 'V3' in reconstructed
    assert next(event for event in events if event['type'] == 'sources')['sources'] == []


# --- errors BEFORE the stream: normal HTTP status, no SSE body ---------------


def test_stream_permission_denied_returns_403_without_any_sse_body(monkeypatch):
    def _unexpected_post(*args, **kwargs):
        raise AssertionError('a denied request must never reach Weave-Retrieval')

    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _unexpected_post)

    body = {**_LEGAL_BODY, 'user': {'id': 'u-1', 'team': 'sales'}}
    resp = client.post('/internal/chat/stream', json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 403
    assert not resp.headers['content-type'].startswith('text/event-stream')
    assert resp.json()['detail']


def test_stream_unknown_bot_id_returns_404_without_any_sse_body():
    resp = client.post(
        '/internal/chat/stream', json={'bot_id': 'no-such-bot', 'message': 'Hallo!'}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 404
    assert not resp.headers['content-type'].startswith('text/event-stream')


def test_stream_retrieval_unavailable_surfaces_as_503_without_any_sse_body(monkeypatch):
    import httpx

    def _fake_get(url, headers=None, params=None, timeout=None):
        return _readable_collections_response()

    def _connection_refused(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.retrieval_client.httpx.get', _fake_get)
    monkeypatch.setattr('app.services.retrieval_client.httpx.post', _connection_refused)

    resp = client.post('/internal/chat/stream', json=_LEGAL_BODY, headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert not resp.headers['content-type'].startswith('text/event-stream')
    assert resp.json()['detail']


def test_stream_missing_auth_returns_401_without_any_sse_body():
    resp = client.post('/internal/chat/stream', json={'bot_id': 'general-assistant', 'message': 'Hallo!'})
    assert resp.status_code == 401


# --- errors MID-stream: an in-band error event, HTTP status stays 200 --------


def test_stream_mid_generation_llm_failure_yields_an_error_event_after_partial_deltas(monkeypatch):
    import app.services.llm as llm_service

    class _BrokenMidStreamProvider:
        def chat(self, messages, model=None, temperature=None):
            raise AssertionError('a streaming turn must call chat_stream(), never chat()')

        def chat_stream(self, messages, model=None, temperature=None):
            yield 'partial '
            yield 'answer '
            raise llm_service.LLMError('upstream exploded mid-stream', transient=True)

    monkeypatch.setattr('app.services.llm.get_llm', lambda provider_name=None: _BrokenMidStreamProvider())

    resp = client.post(
        '/internal/chat/stream', json={'bot_id': 'general-assistant', 'message': 'Hallo!'}, headers=AUTH_HEADERS
    )
    # The response already committed to 200/text-event-stream before
    # generation failed -- this is NOT a 5xx, by design (see
    # app/api/internal.py's own docstring and contracts/internal-chat.md).
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    assert events[0]['type'] == 'trace'
    assert events[1] == {'type': 'delta', 'text': 'partial '}
    assert events[2] == {'type': 'delta', 'text': 'answer '}
    assert events[3] == {'type': 'error', 'detail': 'upstream exploded mid-stream'}
    # Nothing follows the error -- no sources/done event afterward.
    assert len(events) == 4


def test_stream_llm_failure_before_any_delta_yields_only_trace_then_error(monkeypatch):
    import app.services.llm as llm_service

    class _BrokenFromTheStartProvider:
        def chat(self, messages, model=None, temperature=None):
            raise AssertionError('a streaming turn must call chat_stream(), never chat()')

        def chat_stream(self, messages, model=None, temperature=None):
            raise llm_service.LLMError('could not even connect', transient=True)
            yield  # pragma: no cover -- makes this a generator function

    monkeypatch.setattr('app.services.llm.get_llm', lambda provider_name=None: _BrokenFromTheStartProvider())

    resp = client.post(
        '/internal/chat/stream', json={'bot_id': 'general-assistant', 'message': 'Hallo!'}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    assert [event['type'] for event in events] == ['trace', 'error']
    assert events[1]['detail'] == 'could not even connect'


# --- parity with the non-streaming pipeline ------------------------------------


def test_stream_and_non_stream_pipelines_agree_on_answer_text_and_sources():
    """Regression for the shared-preparation refactor (`_prepare_turn`,
    app/services/chat.py): `handle_chat` and `handle_chat_stream` must
    describe the exact same turn for an identical request -- the streamed
    answer's deltas, concatenated, must equal the non-streaming `answer`
    string, and both must agree on `sources`/`intent`."""
    from app.schemas.chat import ChatRequest
    from app.services import chat as chat_service

    request = ChatRequest.model_validate({'bot_id': 'general-assistant', 'message': 'Hallo, wie geht es dir?'})

    blocking = chat_service.handle_chat(request)
    events = list(chat_service.handle_chat_stream(request))

    reconstructed = ''.join(event.text for event in events if event.type == 'delta')
    assert reconstructed == blocking.answer

    sources_event = next(event for event in events if event.type == 'sources')
    assert [s.model_dump() for s in sources_event.sources] == [s.model_dump() for s in blocking.sources]

    trace_event = next(event for event in events if event.type == 'trace')
    assert trace_event.trace.intent == blocking.trace.intent
    assert trace_event.trace.needs_retrieval == blocking.trace.needs_retrieval
