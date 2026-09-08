"""GET /v1/collections (app/api/collections.py) -- proxy behaviour against a
mocked Weave-Retrieval. Mocked at `app.services.retrieval_client._client`,
the same seam app/services/runtime_client.py exposes for
tests/test_bots_api.py, for the same reason (no real network I/O, no extra
mocking dependency).
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
    """Stands in for httpx.Client's `with _client() as client: client.get(path,
    params=params)` usage in app/services/retrieval_client.list_collections.
    Records every `(path, params)` pair it's called with, so a test can
    assert exactly what was forwarded to Weave-Retrieval."""

    def __init__(self, get_impl) -> None:
        self._get_impl = get_impl
        self.calls: list[tuple[str, dict | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, path: str, params=None):
        self.calls.append((path, params))
        return self._get_impl(path, params)


@pytest.fixture
def fake_retrieval(monkeypatch):
    """Installs a `_FakeHttpxClient` built from `get_impl` as
    `retrieval_client._client`, and returns the fake client instance itself
    so a test can inspect `.calls` afterwards."""

    def _install(get_impl):
        fake = _FakeHttpxClient(get_impl)
        monkeypatch.setattr('app.services.retrieval_client._client', lambda: fake)
        return fake

    return _install


@pytest.fixture
def caller(db_session):
    _, raw_token = make_user_with_token(db_session, username='collections-caller', team='Legal')
    return auth_headers(raw_token)


def test_get_collections_proxies_a_successful_retrieval_response(fake_retrieval, caller):
    payload = [{'slug': 'legal-2026', 'name': 'Legal 2026', 'description': None, 'public': False}]
    fake_retrieval(lambda path, params: _FakeResponse(200, payload))

    response = client.get('/v1/collections', headers=caller)

    assert response.status_code == 200
    assert response.json() == payload


def test_get_collections_forwards_the_callers_own_team(fake_retrieval, caller):
    fake = fake_retrieval(lambda path, params: _FakeResponse(200, []))

    response = client.get('/v1/collections', headers=caller)

    assert response.status_code == 200
    assert fake.calls == [('/api/v1/collections', {'teams': ['Legal']})]


def test_get_collections_omits_team_param_for_a_user_with_no_team(fake_retrieval, db_session):
    _, raw_token = make_user_with_token(db_session, username='collections-no-team', team=None)
    fake = fake_retrieval(lambda path, params: _FakeResponse(200, []))

    response = client.get('/v1/collections', headers=auth_headers(raw_token))

    assert response.status_code == 200
    assert fake.calls == [('/api/v1/collections', {'teams': []})]


def test_a_foreign_team_query_param_cannot_override_the_callers_own_team(fake_retrieval, caller):
    """The route accepts no `team` parameter of its own -- an extra query
    string is simply ignored by FastAPI, so the caller's OWN team (off
    their authenticated User row) is always what gets forwarded, never
    whatever a request happens to also carry."""
    fake = fake_retrieval(lambda path, params: _FakeResponse(200, []))

    response = client.get('/v1/collections?team=some-other-team', headers=caller)

    assert response.status_code == 200
    assert fake.calls == [('/api/v1/collections', {'teams': ['Legal']})]


def test_get_collections_returns_502_when_retrieval_is_unreachable(fake_retrieval, caller):
    def _raise(path, params):
        raise httpx.ConnectError('connection refused')

    fake_retrieval(_raise)

    response = client.get('/v1/collections', headers=caller)

    assert response.status_code == 502
    assert 'unreachable' in response.json()['detail'].lower()


def test_get_collections_returns_502_when_retrieval_returns_an_error_status(fake_retrieval, caller):
    fake_retrieval(lambda path, params: _FakeResponse(401, {'detail': 'invalid service token'}))

    response = client.get('/v1/collections', headers=caller)

    assert response.status_code == 502
    assert '401' in response.json()['detail']


def test_get_collections_returns_502_on_a_non_json_response(fake_retrieval, caller):
    class _BrokenResponse(_FakeResponse):
        def json(self):
            raise ValueError('not json')

    fake_retrieval(lambda path, params: _BrokenResponse(200))

    response = client.get('/v1/collections', headers=caller)

    assert response.status_code == 502


def test_get_collections_requires_authentication():
    response = client.get('/v1/collections')
    assert response.status_code == 401
