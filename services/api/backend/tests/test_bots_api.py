"""GET /v1/bots and GET /v1/bots/{id} -- proxy behaviour against a mocked
Weave-Runtime. Mocked at `app.services.runtime_client._client`, the one
seam that module exposes precisely so tests can substitute a fake
httpx.Client-shaped object with no real network I/O and no extra mocking
dependency (see that module's own docstring).
"""

import httpx
import pytest

from tests.conftest import auth_headers, client, make_user_with_token


class _FakeResponse:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpxClient:
    """Stands in for httpx.Client's `with _client() as client: client.get(path)`
    usage in app/services/runtime_client.py._get."""

    def __init__(self, get_impl) -> None:
        self._get_impl = get_impl

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, path: str):
        return self._get_impl(path)


@pytest.fixture
def caller(db_session):
    _, raw_token = make_user_with_token(db_session, username='bots-caller')
    return auth_headers(raw_token)


def test_list_bots_proxies_a_successful_runtime_response(monkeypatch, caller):
    bots_payload = [{'id': 'faq-bot', 'name': 'FAQ Bot'}]
    monkeypatch.setattr(
        'app.services.runtime_client._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(200, bots_payload)),
    )

    response = client.get('/v1/bots', headers=caller)
    assert response.status_code == 200
    assert response.json() == bots_payload


def test_get_bot_by_id_proxies_a_successful_runtime_response(monkeypatch, caller):
    bot_payload = {'id': 'faq-bot', 'name': 'FAQ Bot'}
    monkeypatch.setattr(
        'app.services.runtime_client._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(200, bot_payload)),
    )

    response = client.get('/v1/bots/faq-bot', headers=caller)
    assert response.status_code == 200
    assert response.json() == bot_payload


def test_list_bots_returns_502_when_runtime_is_unreachable(monkeypatch, caller):
    def _raise(path):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.services.runtime_client._client', lambda: _FakeHttpxClient(_raise))

    response = client.get('/v1/bots', headers=caller)
    assert response.status_code == 502
    assert 'unreachable' in response.json()['detail'].lower()


def test_list_bots_returns_502_when_runtime_returns_an_error_status(monkeypatch, caller):
    monkeypatch.setattr(
        'app.services.runtime_client._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(500, {'detail': 'boom'})),
    )

    response = client.get('/v1/bots', headers=caller)
    assert response.status_code == 502
    assert '500' in response.json()['detail']


def test_get_bot_returns_502_when_runtime_reports_not_found(monkeypatch, caller):
    monkeypatch.setattr(
        'app.services.runtime_client._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(404, {'detail': 'not found'})),
    )

    response = client.get('/v1/bots/unknown-bot', headers=caller)
    assert response.status_code == 502


def test_bots_route_requires_authentication():
    response = client.get('/v1/bots')
    assert response.status_code == 401
