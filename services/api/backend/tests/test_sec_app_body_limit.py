"""app/main.py's BodySizeLimitMiddleware: bodies over
MAX_REQUEST_BODY_BYTES get 413, whether declared by Content-Length or sent
chunked without one."""

import asyncio

import pytest

from app.core.config import settings
from app.main import BodySizeLimitMiddleware
from tests.conftest import client

LIMIT = 1000


@pytest.fixture(autouse=True)
def _small_limit(monkeypatch):
    monkeypatch.setattr(settings, 'max_request_body_bytes', LIMIT)


def test_default_limit_fits_a_maximal_chat_message():
    # Worst case JSON escaping: \\uXXXX (6 bytes) per character.
    assert type(settings).model_fields['max_request_body_bytes'].default >= 6 * settings.chat_message_max_length + 1024


def test_declared_content_length_over_limit_is_413():
    response = client.post('/v1/chat', content=b'x' * (LIMIT + 1), headers={'content-type': 'application/json'})
    assert response.status_code == 413


def test_chunked_body_over_limit_is_413():
    def _chunks():
        for _ in range(5):
            yield b'x' * 400

    response = client.post('/v1/chat', content=_chunks(), headers={'content-type': 'application/json'})
    assert 'content-length' not in response.request.headers
    assert response.status_code == 413


def test_body_within_limit_reaches_the_route():
    # Unauthenticated: the route itself answers (401), not the middleware.
    response = client.post('/v1/chat', json={'message': 'hi'})
    assert response.status_code == 401


def test_chunked_body_is_cut_off_while_streaming():
    """The wrapped app never receives more than the limit, even when it
    keeps reading chunk after chunk."""
    seen = []
    sent = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(len(message['body']))
            if not message.get('more_body'):
                break

    chunks = [{'type': 'http.request', 'body': b'x' * 400, 'more_body': True} for _ in range(10)]

    async def receive():
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}
    asyncio.run(BodySizeLimitMiddleware(app)(scope, receive, send))
    assert sum(seen) <= LIMIT
    assert sent[0]['type'] == 'http.response.start' and sent[0]['status'] == 413
