"""Security-review regressions for the chat path (POST /v1/chat,
POST /v1/chat/stream, POST /v1/chat/completions) and
app/services/runtime_client.py:

- No pooled DB connection may stay checked out (idle in transaction) while
  a request waits on Weave-Runtime -- a blocking turn can take up to
  `chat_read_timeout_seconds`, a stream as long as the answer takes, so a
  handful of slow chats would otherwise drain the whole pool and fail every
  other request.
- chat_stream() waits for Runtime's response headers (held until retrieval
  finishes) with the same long read timeout chat() uses.
- A transport error never leaks into the client-visible `detail`.
- A malformed SSE data line ends the stream with an in-band `error` event
  instead of aborting it.
"""

import json
import logging

import httpx
import pytest

from app.core.db import engine
from app.models.models import Conversation, Message, MessageRole
from app.services import runtime_client
from tests.conftest import auth_headers, client, make_user_with_token

TRACE_EVENT = {'type': 'trace', 'trace': {'bot_id': 'legal-support'}}
DONE_EVENT = {'type': 'done'}


@pytest.fixture
def caller(db_session):
    user, raw_token = make_user_with_token(db_session, username='sec-chat-caller', team='legal')
    db_session.commit()
    return user, auth_headers(raw_token)


def _checked_out() -> int:
    return engine.pool.checkedout()


# --- (1) DB connection released while waiting on Weave-Runtime -------------


def test_chat_releases_db_connection_during_runtime_call(monkeypatch, caller, db_session):
    user, headers = caller
    seen: list[int] = []

    def fake_chat(**kwargs):
        seen.append(_checked_out())
        return {'answer': 'Antwort', 'sources': [], 'trace': None}

    def fake_title(**kwargs):
        seen.append(_checked_out())
        return 'Titel'

    monkeypatch.setattr(runtime_client, 'chat', fake_chat)
    monkeypatch.setattr(runtime_client, 'conversation_title', fake_title)

    response = client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=headers)
    assert response.status_code == 200
    assert seen == [0, 0]

    # The assistant turn is still persisted after the connection was released.
    conversation = db_session.query(Conversation).filter(Conversation.user_id == user.id).one()
    assert [m.role for m in conversation.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert conversation.messages[1].content == 'Antwort'
    assert conversation.title == 'Titel'


def test_chat_continuing_a_conversation_releases_db_connection(monkeypatch, caller, db_session):
    """An existing conversation is resolved with a SELECT and its history is
    lazy-loaded -- both open a read transaction that must also end."""
    user, headers = caller
    seen: list[int] = []
    histories: list[list] = []

    def fake_chat(**kwargs):
        seen.append(_checked_out())
        histories.append(kwargs['history'])
        return {'answer': 'Antwort', 'sources': [], 'trace': None}

    monkeypatch.setattr(runtime_client, 'chat', fake_chat)
    monkeypatch.setattr(runtime_client, 'conversation_title', lambda **kwargs: 'Titel')

    first = client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'Eins'}, headers=headers)
    conversation_id = first.json()['conversation_id']
    second = client.post(
        '/v1/chat',
        json={'bot_id': 'legal-support', 'message': 'Zwei', 'conversation_id': conversation_id},
        headers=headers,
    )
    assert second.status_code == 200
    assert seen == [0, 0]
    assert histories[1] == [{'role': 'user', 'content': 'Eins'}, {'role': 'assistant', 'content': 'Antwort'}]
    assert db_session.query(Message).count() == 4


def test_chat_stream_releases_db_connection_while_streaming(monkeypatch, caller, db_session):
    user, headers = caller
    seen: list[int] = []

    def fake_events():
        for event in (TRACE_EVENT, {'type': 'delta', 'text': 'Hal'}, {'type': 'delta', 'text': 'lo'}):
            seen.append(_checked_out())
            yield event
        seen.append(_checked_out())
        yield {'type': 'sources', 'sources': []}
        yield DONE_EVENT

    def fake_chat_stream(**kwargs):
        seen.append(_checked_out())
        return fake_events()

    def fake_title(**kwargs):
        seen.append(_checked_out())
        return 'Titel'

    monkeypatch.setattr(runtime_client, 'chat_stream', fake_chat_stream)
    monkeypatch.setattr(runtime_client, 'conversation_title', fake_title)

    response = client.post('/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=headers)
    assert response.status_code == 200
    assert 'data: {"type": "done"}' in response.text
    assert seen == [0, 0, 0, 0, 0, 0]

    conversation = db_session.query(Conversation).filter(Conversation.user_id == user.id).one()
    assert [m.role for m in conversation.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert conversation.messages[1].content == 'Hallo'
    assert conversation.title == 'Titel'


@pytest.mark.parametrize('stream', [False, True])
def test_openai_completions_releases_db_connection_during_runtime_call(monkeypatch, caller, stream):
    _, headers = caller
    seen: list[int] = []

    def fake_chat(**kwargs):
        seen.append(_checked_out())
        return {'answer': 'Antwort', 'sources': [], 'trace': None}

    def fake_events():
        seen.append(_checked_out())
        yield TRACE_EVENT
        yield {'type': 'delta', 'text': 'Antwort'}
        seen.append(_checked_out())
        yield DONE_EVENT

    def fake_chat_stream(**kwargs):
        seen.append(_checked_out())
        return fake_events()

    monkeypatch.setattr(runtime_client, 'chat', fake_chat)
    monkeypatch.setattr(runtime_client, 'chat_stream', fake_chat_stream)

    response = client.post(
        '/v1/chat/completions',
        json={'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': stream},
        headers=headers,
    )
    assert response.status_code == 200
    assert seen and all(count == 0 for count in seen)


# --- runtime_client transport behaviour --------------------------------------


@pytest.fixture
def transport(monkeypatch):
    """Route every httpx.Client runtime_client builds through `handler`,
    recording each client's timeout."""

    class _Transport:
        handler = None
        timeouts: list[httpx.Timeout] = []

    real_client_cls = httpx.Client

    def _patched_client_cls(*args, **kwargs):
        _Transport.timeouts.append(kwargs.get('timeout'))
        kwargs.setdefault('transport', httpx.MockTransport(lambda request: _Transport.handler(request)))
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(runtime_client.httpx, 'Client', _patched_client_cls)
    _Transport.timeouts = []
    return _Transport


def test_chat_stream_uses_the_chat_read_timeout(monkeypatch, transport):
    """Runtime holds the stream's response headers until retrieval is done,
    so the default 10s read timeout would cut off slow-but-healthy turns."""
    from app.core.config import settings

    monkeypatch.setattr(settings, 'chat_read_timeout_seconds', 12345.0)
    transport.handler = lambda request: httpx.Response(
        200, content=f'data: {json.dumps(DONE_EVENT)}\n\n'.encode(), headers={'content-type': 'text/event-stream'}
    )

    events = list(runtime_client.chat_stream(bot_id='b', message='Hi', history=[], user={'id': 'u'}))
    assert events == [DONE_EVENT]
    assert [t.read for t in transport.timeouts] == [12345.0]


@pytest.mark.parametrize('call', ['chat', 'chat_stream', 'list_bots'])
def test_transport_error_detail_is_generic_and_logged(transport, caplog, call):
    def handler(request):
        raise httpx.ConnectError('[Errno 111] Connection refused to runtime.internal:8000')

    transport.handler = handler
    caplog.set_level(logging.WARNING, logger='app.services.runtime_client')

    with pytest.raises(runtime_client.RuntimeUnavailable) as exc_info:
        if call == 'list_bots':
            runtime_client.list_bots()
        else:
            getattr(runtime_client, call)(bot_id='b', message='Hi', history=[], user={'id': 'u'})

    assert str(exc_info.value) == 'Weave-Runtime unreachable'
    assert 'Errno 111' in caplog.text


def test_chat_route_does_not_leak_transport_error(transport, caller):
    _, headers = caller

    def handler(request):
        raise httpx.ConnectError('[Errno 111] Connection refused')

    transport.handler = handler

    response = client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=headers)
    assert response.status_code == 502
    assert response.json() == {'detail': 'Weave-Runtime unreachable'}


@pytest.mark.parametrize('bad_line', ['data: {not json', 'data: [1, 2]', 'data: "text"'])
def test_malformed_sse_line_raises_runtime_unavailable(transport, bad_line):
    body = f'data: {json.dumps(TRACE_EVENT)}\n\n{bad_line}\n\n'.encode()
    transport.handler = lambda request: httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    events = runtime_client.chat_stream(bot_id='b', message='Hi', history=[], user={'id': 'u'})
    assert next(events) == TRACE_EVENT
    with pytest.raises(runtime_client.RuntimeUnavailable):
        next(events)


def test_malformed_sse_line_becomes_in_band_error_event(transport, caller, db_session):
    user, headers = caller
    body = f'data: {json.dumps(TRACE_EVENT)}\n\ndata: {{broken\n\n'.encode()
    transport.handler = lambda request: httpx.Response(200, content=body, headers={'content-type': 'text/event-stream'})

    response = client.post('/v1/chat/stream', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=headers)
    assert response.status_code == 200
    events = [json.loads(line[len('data: '):]) for line in response.text.split('\n') if line.startswith('data: ')]
    assert events[0] == TRACE_EVENT
    assert events[-1]['type'] == 'error'
    # No assistant message for a stream that never reached `done`.
    roles = [m.role for m in db_session.query(Message).join(Conversation).filter(Conversation.user_id == user.id)]
    assert roles == [MessageRole.USER]
