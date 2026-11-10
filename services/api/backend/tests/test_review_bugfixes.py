"""Regressions for the non-security bugs of the weave-api review:

- A new turn makes its conversation the most recently active one
  (GET /v1/conversations orders by updated_at).
- Weave-Runtime refusing OUR service token (401) is a gateway failure (502),
  not the end user's 401 -- the chat would otherwise loop users to /login.
"""

import time

import httpx
import pytest

from app.services import runtime_client
from tests.conftest import auth_headers, client, make_user_with_token


@pytest.fixture
def runtime(monkeypatch):
    class _Runtime:
        handler = None

    real_client_cls = httpx.Client

    def _patched_client_cls(*args, **kwargs):
        kwargs.setdefault('transport', httpx.MockTransport(lambda request: _Runtime.handler(request)))
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(runtime_client.httpx, 'Client', _patched_client_cls)
    return _Runtime


@pytest.fixture
def caller(db_session):
    _, raw_token = make_user_with_token(db_session, username='review-bugs', team='legal')
    db_session.commit()
    return auth_headers(raw_token)


def _answering_runtime(request: httpx.Request) -> httpx.Response:
    if request.url.path == '/internal/conversation-title':
        return httpx.Response(200, json={'title': 'Titel'})
    return httpx.Response(200, json={'answer': 'ok', 'sources': [], 'trace': {}})


def test_a_new_turn_moves_its_conversation_to_the_top(runtime, caller):
    runtime.handler = _answering_runtime

    first = client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'A'}, headers=caller).json()
    time.sleep(0.01)
    client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'B'}, headers=caller)
    time.sleep(0.01)
    client.post(
        '/v1/chat',
        json={'bot_id': 'legal-support', 'message': 'A again', 'conversation_id': first['conversation_id']},
        headers=caller,
    )

    listed = client.get('/v1/conversations', headers=caller).json()
    assert listed['items'][0]['id'] == first['conversation_id']


@pytest.mark.parametrize('path', ['/v1/chat', '/v1/chat/completions'])
def test_runtime_refusing_the_service_token_is_a_502(runtime, caller, path):
    runtime.handler = lambda request: httpx.Response(401, json={'detail': 'invalid service token'})

    if path == '/v1/chat':
        body = {'bot_id': 'legal-support', 'message': 'Hi'}
    else:
        body = {'model': 'legal-support', 'messages': [{'role': 'user', 'content': 'Hi'}]}
    response = client.post(path, json=body, headers=caller)

    assert response.status_code == 502


def test_runtime_403_is_still_passed_through(runtime, caller):
    runtime.handler = lambda request: httpx.Response(403, json={'detail': 'no access to this bot'})

    response = client.post('/v1/chat', json={'bot_id': 'legal-support', 'message': 'Hi'}, headers=caller)

    assert response.status_code == 403
