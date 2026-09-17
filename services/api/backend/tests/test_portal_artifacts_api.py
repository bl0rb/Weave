"""GET /v1/portal/releases/{release_id}/artifacts/{filename} -- proxy
behaviour against a mocked Weave-Ingest. Mocked at
`app.api.portal_artifacts._client`, the same seam pattern as
app/services/runtime_client.py (see tests/test_bots_api.py).
"""

import httpx
import pytest

from app.core.config import settings
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


@pytest.fixture(autouse=True)
def _configure_ingest(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest.internal')
    monkeypatch.setattr(settings, 'ingest_service_token', 'ingest-service-token')


def test_proxies_image_bytes_and_content_type(monkeypatch, caller):
    monkeypatch.setattr(
        'app.api.portal_artifacts._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(200, b'fake-png', {'content-type': 'image/png'})),
    )
    response = client.get('/v1/portal/releases/r1/artifacts/diagram.png', headers=caller)
    assert response.status_code == 200
    assert response.content == b'fake-png'
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_returns_404_when_ingest_reports_not_found(monkeypatch, caller):
    monkeypatch.setattr(
        'app.api.portal_artifacts._client',
        lambda: _FakeHttpxClient(lambda path: _FakeResponse(404)),
    )
    response = client.get('/v1/portal/releases/r1/artifacts/missing.png', headers=caller)
    assert response.status_code == 404


def test_returns_502_when_ingest_is_unreachable(monkeypatch, caller):
    def _raise(path):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_raise))
    response = client.get('/v1/portal/releases/r1/artifacts/diagram.png', headers=caller)
    assert response.status_code == 502


def test_route_requires_authentication():
    response = client.get('/v1/portal/releases/r1/artifacts/diagram.png')
    assert response.status_code == 401


def test_returns_503_when_ingest_not_configured(monkeypatch, caller):
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    response = client.get('/v1/portal/releases/r1/artifacts/diagram.png', headers=caller)
    assert response.status_code == 503
