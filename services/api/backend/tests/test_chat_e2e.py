"""End-to-end tests for POST /v1/chat and POST /v1/chat/completions against
a mocked Weave-Runtime -- mocked at the actual HTTP TRANSPORT
(`httpx.MockTransport`), not at `runtime_client._client()` (as
tests/test_bots_api.py does) or at `runtime_client.chat()` itself (as
tests/test_chat_api.py does). Concretely: `app/services/runtime_client.py`'s
own `_client()` factory runs completely for real here -- it still builds a
genuine `httpx.Client` with the real `base_url`/`Authorization` header/
timeout from `settings` -- only the one `httpx.Client` CLASS is patched so
every instance it constructs is wired to a `MockTransport` instead of a real
socket. That means these tests observe (and assert on) the exact bytes
Weave-Runtime would receive: method, path, headers, JSON body -- proving
`_client()`'s own construction is correct, not just that `chat()` was CALLED
with the right keyword arguments (test_chat_api.py already covers that,
against the OLD placeholder-era Weave-Runtime contract).
"""

import json

import httpx
import pytest

from app.models.models import Conversation, Message
from app.services import runtime_client
from tests.conftest import auth_headers, client, make_user_with_token


@pytest.fixture
def runtime(monkeypatch):
    """Install a MockTransport-backed httpx.Client for every client
    runtime_client._client() builds during this test, and return an object
    exposing the requests it received (`.requests`) once a handler is
    installed via `.handle(handler)`. `handler(request) -> httpx.Response`
    -- exactly a MockTransport handler, see this module's own docstring.
    """

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


def _runtime_chat_response(**overrides) -> dict:
    body = {
        'answer': 'Antwort von Weave-Runtime.',
        'sources': [],
        'trace': {
            'intent': 'conversational',
            'confidence': 0.9,
            'needs_retrieval': False,
            'needs_tool': False,
            'retrieval': None,
            'model': 'fake-chat',
            'router_mode': 'rules',
            'timings_ms': {'total_ms': 1.2},
            'guard': None,
        },
    }
    body.update(overrides)
    return body


def _request_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


# --- POST /v1/chat against the real Weave-Runtime request/response shape ----


def test_chat_sends_the_real_internal_chat_request_shape(db_session, runtime):
    user, raw_token = make_user_with_token(db_session, username='e2e-chat', team='legal')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        # The first assistant turn also triggers a follow-up
        # POST /internal/conversation-title (see app/api/chat.py) -- stub
        # it separately so this test can still assert on the /internal/chat
        # request shape below without it being the request seen last.
        if request.url.path == '/internal/conversation-title':
            return httpx.Response(200, json={'title': 'Titel'})
        assert request.method == 'POST'
        assert request.url.path == '/internal/chat'
        return httpx.Response(200, json=_runtime_chat_response(answer='Hallo zurück!'))

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat', json={'bot_id': 'legal-support', 'message': 'Hallo!'}, headers=auth_headers(raw_token)
    )
    assert resp.status_code == 200
    assert resp.json()['answer'] == 'Hallo zurück!'

    assert len(runtime.requests) == 2
    sent = runtime.requests[0]
    assert sent.headers['authorization'].startswith('Bearer ')
    body = _request_json(sent)
    assert body == {
        'bot_id': 'legal-support',
        'message': 'Hallo!',
        'history': [],
        'user': {'id': str(user.id), 'team': 'legal', 'teams': ['legal']},
    }


def test_chat_forwards_a_collections_filter_in_the_real_request_body(db_session, runtime):
    """ChatRequest.collections (app/schemas/chat.py) reaches Weave-Runtime's
    own `/internal/chat` as a `collections` key in the real JSON body --
    the wire-level counterpart to tests/test_chat_api.py's mocked-at-
    runtime_client.chat() version of the same assertion."""
    user, raw_token = make_user_with_token(db_session, username='e2e-chat-with-filter', team='legal')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_runtime_chat_response())

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat',
        json={'bot_id': 'legal-support', 'message': 'Hallo!', 'collections': ['legal-internal']},
        headers=auth_headers(raw_token),
    )
    assert resp.status_code == 200

    body = _request_json(runtime.requests[0])
    assert body == {
        'bot_id': 'legal-support',
        'message': 'Hallo!',
        'history': [],
        'user': {'id': str(user.id), 'team': 'legal', 'teams': ['legal']},
        'collections': ['legal-internal'],
    }


def test_chat_passes_through_runtime_404_for_an_unknown_bot(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='e2e-chat-404')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'detail': "unknown bot_id: 'no-such-bot'"})

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat', json={'bot_id': 'no-such-bot', 'message': 'Hallo?'}, headers=auth_headers(raw_token)
    )
    assert resp.status_code == 404
    assert resp.json()['detail'] == "unknown bot_id: 'no-such-bot'"


def test_chat_passes_through_runtime_403_for_a_denied_team(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='e2e-chat-403', team='sales')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={'detail': "bot 'legal-support' is restricted to teams ['legal']"})

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat', json={'bot_id': 'legal-support', 'message': 'Hallo?'}, headers=auth_headers(raw_token)
    )
    assert resp.status_code == 403


def test_chat_maps_runtime_503_retrieval_unavailable_to_502(db_session, runtime):
    # Weave-Runtime's own RetrievalUnavailable -> 503 (see that service's
    # app/api/internal.py); this gateway's runtime_client classifies ANY 5xx
    # as RuntimeUnavailable, and app/api/chat.py maps that to 502 -- proving
    # this end-to-end (real HTTP status code in, real HTTP status code out)
    # rather than asserting it only at the exception-type level.
    _, raw_token = make_user_with_token(db_session, username='e2e-chat-503')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={'detail': 'Weave-Retrieval unreachable'})

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat', json={'bot_id': 'legal-support', 'message': 'Kündigungsfrist?'}, headers=auth_headers(raw_token)
    )
    assert resp.status_code == 502

    # The user's own message still went to disk -- only the assistant turn
    # never happened (same discipline as test_chat_api.py's own 502 test).
    messages = db_session.query(Message).join(Conversation).all()
    assert len(messages) == 1


def test_chat_forwards_conversation_history_in_the_real_shape(db_session, runtime):
    from app.models.models import MessageRole

    user, raw_token = make_user_with_token(db_session, username='e2e-chat-history')
    conversation = Conversation(user_id=user.id, bot_id='legal-support')
    db_session.add(conversation)
    db_session.flush()
    db_session.add_all(
        [
            Message(conversation_id=conversation.id, role=MessageRole.USER, content='Erste Frage'),
            Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content='Erste Antwort'),
        ]
    )
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_runtime_chat_response())

    runtime.handle(handler)

    resp = client.post(
        '/v1/chat',
        json={'bot_id': 'legal-support', 'message': 'Zweite Frage', 'conversation_id': str(conversation.id)},
        headers=auth_headers(raw_token),
    )
    assert resp.status_code == 200
    sent = _request_json(runtime.requests[0])
    assert sent['history'] == [
        {'role': 'user', 'content': 'Erste Frage'},
        {'role': 'assistant', 'content': 'Erste Antwort'},
    ]
    assert sent['message'] == 'Zweite Frage'


# --- POST /v1/chat/completions: the OpenAI-compatible shim -------------------


def test_chat_completions_openai_round_trip_with_no_conversation_persisted(db_session, runtime):
    user, raw_token = make_user_with_token(db_session, username='e2e-shim', team='legal')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/internal/chat'
        body = _request_json(request)
        assert body['bot_id'] == 'legal-support'
        assert body['message'] == 'Und heute?'
        return httpx.Response(200, json=_runtime_chat_response(answer='Heute scheint die Sonne.'))

    runtime.handle(handler)

    openai_request = {
        'model': 'legal-support',
        'messages': [
            {'role': 'system', 'content': 'You are unused here.'},
            {'role': 'user', 'content': 'Wie war das Wetter gestern?'},
            {'role': 'assistant', 'content': 'Gestern war es sonnig.'},
            {'role': 'user', 'content': 'Und heute?'},
        ],
    }
    resp = client.post('/v1/chat/completions', json=openai_request, headers=auth_headers(raw_token))
    assert resp.status_code == 200
    body = resp.json()

    assert body['object'] == 'chat.completion'
    assert body['model'] == 'legal-support'
    assert body['id'].startswith('chatcmpl-')
    assert isinstance(body['created'], int)
    assert body['choices'][0]['message'] == {'role': 'assistant', 'content': 'Heute scheint die Sonne.'}
    assert body['choices'][0]['finish_reason'] == 'stop'
    assert body.get('usage') is None

    # The system turn was dropped (Weave-Runtime's ChatMessage has no
    # 'system' role -- see app/api/openai_compat.py's own docstring), and
    # only the two turns BEFORE the final user message became history.
    sent = _request_json(runtime.requests[0])
    assert sent['message'] == 'Und heute?'
    assert sent['history'] == [
        {'role': 'user', 'content': 'Wie war das Wetter gestern?'},
        {'role': 'assistant', 'content': 'Gestern war es sonnig.'},
    ]
    assert sent['user'] == {'id': str(user.id), 'team': 'legal', 'teams': ['legal']}

    # Nothing was ever persisted -- no Conversation, no Message row, for
    # ANY user, not just this one (the shim is stateless by construction).
    assert db_session.query(Conversation).count() == 0
    assert db_session.query(Message).count() == 0


def test_chat_completions_requires_at_least_one_user_message(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='e2e-shim-no-user-msg')
    db_session.commit()

    def _unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError('Weave-Runtime must never be called for an invalid shim request')

    runtime.handle(_unexpected)

    openai_request = {'model': 'legal-support', 'messages': [{'role': 'system', 'content': 'only a system turn'}]}
    resp = client.post('/v1/chat/completions', json=openai_request, headers=auth_headers(raw_token))
    assert resp.status_code == 400


def test_chat_completions_passes_through_runtime_error_status(db_session, runtime):
    _, raw_token = make_user_with_token(db_session, username='e2e-shim-error')
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'detail': "unknown bot_id: 'no-such-bot'"})

    runtime.handle(handler)

    openai_request = {'model': 'no-such-bot', 'messages': [{'role': 'user', 'content': 'Hallo?'}]}
    resp = client.post('/v1/chat/completions', json=openai_request, headers=auth_headers(raw_token))
    assert resp.status_code == 404


def test_chat_completions_requires_authentication():
    resp = client.post(
        '/v1/chat/completions', json={'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'hi'}]}
    )
    assert resp.status_code == 401
