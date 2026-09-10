"""GET /v1/models, `"stream": true` on POST /v1/chat/completions, and
POST /v1/chat/stream -- all three against a mocked Weave-Runtime at the
actual HTTP TRANSPORT (`httpx.MockTransport`), the same level
tests/test_chat_e2e.py already mocks at (see that module's own docstring
for why: this proves runtime_client's real request-building, not just that
some inner function was called with the right arguments). The `runtime`
fixture and `_request_json` helper below are a deliberate, small
duplication of test_chat_e2e.py's own -- kept local rather than hoisted
into conftest.py so this module stays self-contained and that module's own
already-green tests are left untouched.
"""

import json
import uuid

import httpx
import pytest

from app.core import ratelimit
from app.core.ratelimit import FixedWindowRateLimiter
from app.models.models import Conversation, Message, MessageRole
from app.services import runtime_client
from tests.conftest import auth_headers, client, make_user_with_token


@pytest.fixture
def runtime(monkeypatch):
    """Install a MockTransport-backed httpx.Client for every client
    runtime_client._client() builds during this test -- see
    tests/test_chat_e2e.py's own `runtime` fixture, which this mirrors."""

    class _Runtime:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []

        def handle(self, handler) -> None:
            def _recording_handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(request)
                return handler(request)

            transport = httpx.MockTransport(_recording_handler)
            real_client_cls = httpx.Client

            def _patched_client_cls(*args, **kwargs):
                kwargs.setdefault('transport', transport)
                return real_client_cls(*args, **kwargs)

            monkeypatch.setattr(runtime_client.httpx, 'Client', _patched_client_cls)

    return _Runtime()


def _request_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _sse_body(events: list[dict]) -> bytes:
    """Weave-Runtime's own SSE framing (contracts/internal-chat.md): one
    `data: <json>\\n\\n` line per event, nothing else."""
    return ''.join(f'data: {json.dumps(event)}\n\n' for event in events).encode('utf-8')


def _parse_sse_events(body: str) -> list[dict | str]:
    """The inverse of `_sse_body` above, for asserting on what a route
    actually sent -- `'[DONE]'` (OpenAI's own non-JSON sentinel line) is
    returned as the literal string `'[DONE]'` rather than attempted as
    JSON."""
    events: list[dict | str] = []
    for line in body.split('\n'):
        if not line.startswith('data: '):
            continue
        payload = line[len('data: ') :]
        events.append('[DONE]' if payload == '[DONE]' else json.loads(payload))
    return events


TRACE_EVENT = {
    'type': 'trace',
    'trace': {
        'intent': 'conversational',
        'confidence': 0.9,
        'needs_retrieval': False,
        'needs_tool': False,
        'retrieval': None,
        'model': 'fake-chat',
        'router_mode': 'rules',
        'timings_ms': {'router_ms': 0.5},
        'guard': None,
    },
}
SOURCES_EVENT = {'sources': [{'source': 'confluence', 'document_id': 'doc-1', 'chunk_id': 1}], 'type': 'sources'}
DONE_EVENT = {'type': 'done'}


# --- GET /v1/models ----------------------------------------------------------


def test_list_models_requires_authentication():
    response = client.get('/v1/models')
    assert response.status_code == 401


def test_list_models_lists_the_bots_from_the_runtime_registry(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='models-caller')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == 'GET'
        assert request.url.path == '/internal/bots'
        bots = [{'id': 'faq-bot', 'name': 'FAQ Bot'}, {'id': 'legal-support', 'name': 'Legal'}]
        return httpx.Response(200, json=bots)

    runtime.handle(handler)

    response = client.get('/v1/models', headers=auth_headers(raw_token))
    assert response.status_code == 200
    body = response.json()
    assert body['object'] == 'list'
    assert [entry['id'] for entry in body['data']] == ['faq-bot', 'legal-support']
    for entry in body['data']:
        assert entry['object'] == 'model'
        assert entry['owned_by'] == 'weave'
        assert isinstance(entry['created'], int)


def test_list_models_is_rate_limited(db_session, monkeypatch, runtime):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))
    _, raw_token = make_user_with_token(db_session, username='models-ratelimited')
    db_session.commit()

    runtime.handle(lambda request: httpx.Response(200, json=[]))
    headers = auth_headers(raw_token)

    assert client.get('/v1/models', headers=headers).status_code == 200
    assert client.get('/v1/models', headers=headers).status_code == 429


# --- POST /v1/chat/completions, "stream": true --------------------------------


def test_chat_completions_stream_emits_openai_chunks_with_role_and_done(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='shim-stream', team='legal')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/internal/chat/stream'
        body = _sse_body(
            [
                TRACE_EVENT,
                {'type': 'delta', 'text': 'Hallo '},
                {'type': 'delta', 'text': 'Welt'},
                SOURCES_EVENT,
                DONE_EVENT,
            ]
        )
        return httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/completions',
        json={'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': True},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')

    events = _parse_sse_events(response.text)
    assert events[-1] == '[DONE]'
    chunks = events[:-1]

    # Every chunk shares the SAME id/model (a real OpenAI stream never
    # changes these mid-stream) and is a chat.completion.chunk.
    assert len({c['id'] for c in chunks}) == 1
    assert all(c['object'] == 'chat.completion.chunk' for c in chunks)
    assert all(c['model'] == 'legal-support' for c in chunks)

    # First chunk carries delta.role == 'assistant' (what OpenAI clients
    # key off to start a new assistant turn) -- no later chunk repeats it.
    assert chunks[0]['choices'][0]['delta']['role'] == 'assistant'
    assert chunks[0]['choices'][0]['delta']['content'] == 'Hallo '
    assert all('role' not in c['choices'][0]['delta'] for c in chunks[1:])

    # The two 'delta' events' text, concatenated in order, is the full
    # answer; finish_reason is null throughout except the final chunk.
    content_chunks = [c for c in chunks if c['choices'][0].get('finish_reason') is None]
    content_pieces = [c['choices'][0]['delta'].get('content') for c in content_chunks]
    assert ''.join(content_pieces) == 'Hallo Welt'
    assert chunks[-1]['choices'][0]['finish_reason'] == 'stop'


def test_chat_completions_stream_stops_without_done_on_an_upstream_error_event(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='shim-stream-error')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse_body([TRACE_EVENT, {'type': 'delta', 'text': 'Teil'}, {'type': 'error', 'detail': 'LLM failed'}])
        return httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/completions',
        json={'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': True},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    # No [DONE], no finish_reason='stop' chunk -- the stream just ends,
    # exactly like a real OpenAI backend dropping the connection would.
    # (finish_reason is None on every non-final chunk, so it's dropped
    # from the wire entirely by exclude_none=True -- see _sse_chunk.)
    assert '[DONE]' not in events
    assert all(e['choices'][0].get('finish_reason') is None for e in events)


def test_chat_completions_without_stream_is_unchanged(db_session, runtime):
    """Locks in that adding `stream` to OpenAIChatCompletionRequest didn't
    touch the existing one-shot JSON response for a request that omits it
    (the default, `stream=False`) -- same shape test_chat_e2e.py's own
    round-trip test already asserts on."""
    _, raw_token = make_user_with_token(db_session, username='shim-no-stream')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/internal/chat'  # NOT /internal/chat/stream
        return httpx.Response(
            200,
            json={
                'answer': 'Normale Antwort.',
                'sources': [],
                'trace': {
                    'intent': 'conversational',
                    'confidence': 0.9,
                    'needs_retrieval': False,
                    'needs_tool': False,
                    'retrieval': None,
                    'model': 'fake-chat',
                    'router_mode': 'rules',
                    'timings_ms': {'total_ms': 1.0},
                    'guard': None,
                },
            },
        )

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/completions',
        json={'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}]},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/json')
    body = response.json()
    assert body['object'] == 'chat.completion'
    assert body['choices'][0]['message'] == {'role': 'assistant', 'content': 'Normale Antwort.'}
    assert body['choices'][0]['finish_reason'] == 'stop'


def test_chat_completions_stream_is_rate_limited(db_session, monkeypatch, runtime):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))
    _, raw_token = make_user_with_token(db_session, username='shim-stream-ratelimited')
    db_session.commit()

    runtime.handle(lambda request: httpx.Response(200, content=_sse_body([TRACE_EVENT, DONE_EVENT])))
    headers = auth_headers(raw_token)
    payload = {'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': True}

    first = client.post('/v1/chat/completions', json=payload, headers=headers)
    assert first.status_code == 200

    second = client.post('/v1/chat/completions', json=payload, headers=headers)
    assert second.status_code == 429


# --- POST /v1/chat/stream (own UI) --------------------------------------------


def test_chat_stream_forwards_events_and_persists_the_assistant_message_once_at_done(db_session, runtime):
    user, raw_token = make_user_with_token(db_session, username='own-stream', team='legal')
    db_session.commit()

    sent_events = [
        TRACE_EVENT,
        {'type': 'delta', 'text': 'Hallo '},
        {'type': 'delta', 'text': 'Welt'},
        SOURCES_EVENT,
        DONE_EVENT,
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        # See test_chat_e2e.py::test_chat_sends_the_real_internal_chat_request_shape
        # for why /internal/conversation-title needs its own stubbed branch.
        if request.url.path == '/internal/conversation-title':
            return httpx.Response(200, json={'title': 'Titel'})
        assert request.url.path == '/internal/chat/stream'
        return httpx.Response(200, content=_sse_body(sent_events), headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    conversation_id = uuid.UUID(response.headers['x-conversation-id'])

    # Every runtime event is forwarded, unchanged, in order.
    assert _parse_sse_events(response.text) == sent_events

    messages = (
        db_session.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id)
        .all()
    )
    assert len(messages) == 2
    assert messages[0].role == MessageRole.USER
    assert messages[0].content == 'Hi'
    assert messages[1].role == MessageRole.ASSISTANT
    assert messages[1].content == 'Hallo Welt'
    assert messages[1].sources == SOURCES_EVENT['sources']
    assert messages[1].trace == TRACE_EVENT['trace']

    conversation = db_session.get(Conversation, conversation_id)
    assert conversation.user_id == user.id


def test_chat_stream_forwards_a_collections_filter_in_the_real_request_body(db_session, runtime):
    """Same ChatRequest.collections contract as POST /v1/chat (see
    tests/test_chat_e2e.py's own version of this assertion) -- must reach
    Weave-Runtime's `/internal/chat/stream` request body exactly the same
    way on the streaming path."""
    user, raw_token = make_user_with_token(db_session, username='own-stream-with-filter', team='legal')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/internal/conversation-title':
            return httpx.Response(200, json={'title': 'Titel'})
        assert request.url.path == '/internal/chat/stream'
        assert json.loads(request.content)['collections'] == ['legal-internal']
        return httpx.Response(200, content=_sse_body([DONE_EVENT]), headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/stream',
        json={'bot_id': 'legal-support', 'message': 'Hi', 'collections': ['legal-internal']},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 200


def test_chat_stream_aborted_mid_stream_persists_no_assistant_message(db_session, runtime):
    """The runtime connection ends after a `trace` and one `delta` --
    neither `done` nor `error` ever arrives, exactly the shape a genuinely
    dropped connection has (see app/api/chat.py's `_stream_and_persist` own
    docstring, case 3). The already-persisted user message must survive;
    no assistant message may ever appear for this turn."""
    user, raw_token = make_user_with_token(db_session, username='own-stream-abort')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse_body([TRACE_EVENT, {'type': 'delta', 'text': 'Angefangene Antwort'}])
        return httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events == [TRACE_EVENT, {'type': 'delta', 'text': 'Angefangene Antwort'}]

    messages = db_session.query(Message).join(Conversation).filter(Conversation.user_id == user.id).all()
    assert len(messages) == 1
    assert messages[0].role == MessageRole.USER
    assert messages[0].content == 'Hi'


def test_chat_stream_upstream_error_event_persists_no_assistant_message(db_session, runtime):
    user, raw_token = make_user_with_token(db_session, username='own-stream-error')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse_body([TRACE_EVENT, {'type': 'delta', 'text': 'Teil'}, {'type': 'error', 'detail': 'LLM failed'}])
        return httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    runtime.handle(handler)

    response = client.post(
        '/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=auth_headers(raw_token)
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[-1] == {'type': 'error', 'detail': 'LLM failed'}

    messages = db_session.query(Message).join(Conversation).filter(Conversation.user_id == user.id).all()
    assert len(messages) == 1
    assert messages[0].role == MessageRole.USER


def test_chat_stream_returns_404_before_streaming_for_an_unknown_conversation(db_session, runtime):
    """A pre-stream failure (conversation resolution here; the same holds
    for a RuntimeUnavailable/RuntimeRejected before any SSE byte is sent)
    must be a normal HTTP status, never an in-band SSE event -- Weave-Runtime
    never gets called at all."""
    _, raw_token = make_user_with_token(db_session, username='own-stream-404')
    db_session.commit()

    def _unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError('Weave-Runtime must never be called when conversation resolution fails')

    runtime.handle(_unexpected)

    response = client.post(
        '/v1/chat/stream',
        json={'bot_id': 'legal-support', 'message': 'Hi', 'conversation_id': str(uuid.uuid4())},
        headers=auth_headers(raw_token),
    )
    assert response.status_code == 404
    assert not response.headers['content-type'].startswith('text/event-stream')


def test_chat_stream_route_is_rate_limited(db_session, monkeypatch, runtime):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1))
    _, raw_token = make_user_with_token(db_session, username='own-stream-ratelimited')
    db_session.commit()

    runtime.handle(lambda request: httpx.Response(200, content=_sse_body([TRACE_EVENT, DONE_EVENT])))
    headers = auth_headers(raw_token)

    first = client.post('/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'eins'}, headers=headers)
    assert first.status_code == 200

    second = client.post('/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'zwei'}, headers=headers)
    assert second.status_code == 429


def test_chat_stream_requires_authentication():
    response = client.post('/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'hi'})
    assert response.status_code == 401
