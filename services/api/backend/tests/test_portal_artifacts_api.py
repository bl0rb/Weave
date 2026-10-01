"""GET /v1/portal/releases/{release_id}/artifacts/{filename} -- proxy
behaviour against a mocked Weave-Ingest. Mocked at
`app.api.portal_artifacts._client`, the same seam pattern as
app/services/runtime_client.py (see tests/test_bots_api.py).
"""

import httpx
import pytest

from app.core.config import settings
from app.services.ingest_identity import IngestIdentity
from app.services.retrieval_client import RetrievalClientError
from tests.conftest import auth_headers, client, make_user_with_token


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b'', headers: dict | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class _FakeHttpxClient:
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
    _, raw_token = make_user_with_token(db_session, username='portal-artifacts-caller')
    return auth_headers(raw_token)


_RELEASE = '0b6c1f9e-2a4d-4a7e-9a55-6f0f3c2d1e10'


@pytest.fixture(autouse=True)
def _configure_ingest(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest.internal')
    monkeypatch.setattr(settings, 'ingest_service_token', 'ingest-service-token')


@pytest.fixture(autouse=True)
def readable(monkeypatch):
    """The release lies in space 'handbuch', which the caller may read --
    tests of the read check change either side. Records every
    list_collections call."""
    state = {'slug': 'handbuch', 'readable': [{'slug': 'handbuch'}], 'calls': []}

    def _list_collections(*, team, user=None):
        state['calls'].append({'team': team, 'user': user})
        return state['readable']

    monkeypatch.setattr('app.api.portal_artifacts.release_collection', lambda release_id: state['slug'])
    monkeypatch.setattr('app.api.portal_artifacts.list_collections', _list_collections)
    return state


def _ingest_must_not_be_called(path):
    raise AssertionError(f'artifact proxied without read access: {path}')


def test_proxies_image_bytes_and_content_type(monkeypatch, caller):
    monkeypatch.setattr(
        'app.api.portal_artifacts._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(200, b'fake-png', {'content-type': 'image/png'})),
    )
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 200
    assert response.content == b'fake-png'
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['x-content-type-options'] == 'nosniff'
    # No max-age: a cached copy would outlive a revoked grant (ADR 0008).
    assert response.headers['cache-control'] == 'private, no-cache'


def test_returns_404_when_ingest_reports_not_found(monkeypatch, caller):
    monkeypatch.setattr(
        'app.api.portal_artifacts._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(404)),
    )
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/missing.png', headers=caller)
    assert response.status_code == 404


def test_returns_502_when_ingest_is_unreachable(monkeypatch, caller):
    def _raise(path):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_raise))
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 502


def test_route_requires_authentication():
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png')
    assert response.status_code == 401


def test_returns_503_when_ingest_not_configured(monkeypatch, caller):
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 503


# --- per-space read check (ADR 0008) ----------------------------------------


def test_revoked_or_missing_grant_on_the_space_is_404_and_nothing_is_proxied(monkeypatch, caller, readable):
    readable['readable'] = [{'slug': 'other-space'}]
    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_ingest_must_not_be_called))

    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 404


@pytest.mark.parametrize('release_id', [_RELEASE, 'not-a-uuid'])
def test_unknown_release_or_one_without_a_space_is_404(monkeypatch, caller, readable, release_id):
    readable['slug'] = None
    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_ingest_must_not_be_called))

    response = client.get(f'/v1/portal/releases/{release_id}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 404


def test_read_check_uses_the_callers_teams_and_person_grants(monkeypatch, db_session, readable):
    monkeypatch.setattr(
        'app.core.auth.fetch_identity',
        lambda subject: IngestIdentity(subject=subject, username='bob', email='', team='Technik', is_admin=False),
    )
    _, raw_token = make_user_with_token(db_session, username='artifact-bob', oidc_subject='weave-ingest:42')
    monkeypatch.setattr(
        'app.api.portal_artifacts._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(200, b'fake-png', {'content-type': 'image/png'})),
    )

    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=auth_headers(raw_token))
    assert response.status_code == 200
    assert readable['calls'] == [{'team': ['Technik'], 'user': '42'}]


def test_returns_502_when_retrieval_is_unreachable(monkeypatch, caller):
    def _raise(release_id):
        raise RetrievalClientError('Weave-Retrieval unreachable')

    monkeypatch.setattr('app.api.portal_artifacts.release_collection', _raise)
    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_ingest_must_not_be_called))
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 502
