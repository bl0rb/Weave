"""Security hardening of GET /v1/portal/releases/{id}/artifacts/{filename}:
only passive raster images render inline from this gateway's origin (an
SVG or HTML artifact would otherwise be stored XSS next to the session
cookie), the filename stays one plain path segment, and transport errors
never reach the client verbatim."""

import httpx
import pytest

from app.core.config import settings
from tests.conftest import auth_headers, client, make_user_with_token
from tests.test_portal_artifacts_api import _RELEASE, _FakeHttpxClient, _FakeResponse, _ingest_must_not_be_called


@pytest.fixture
def caller(db_session):
    _, raw_token = make_user_with_token(db_session, username='sec-app-artifacts-caller')
    return auth_headers(raw_token)


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest.internal')
    monkeypatch.setattr(settings, 'ingest_service_token', 'ingest-service-token')
    monkeypatch.setattr('app.api.portal_artifacts.release_collection', lambda release_id: 'handbuch')
    monkeypatch.setattr('app.api.portal_artifacts.list_collections', lambda *, team, user=None: [{'slug': 'handbuch'}])


def _serve(monkeypatch, content_type: str, disposition: str | None = None, seen: list | None = None) -> None:
    headers = {'content-type': content_type}
    if disposition is not None:
        headers['content-disposition'] = disposition

    def _get(path):
        if seen is not None:
            seen.append(path)
        return _FakeResponse(200, b'<svg onload="alert(1)"/>', headers)

    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_get))


@pytest.mark.parametrize('content_type', ['image/svg+xml', 'text/html', 'application/xhtml+xml', 'application/pdf'])
def test_active_content_types_are_attachments_with_sandbox_csp(monkeypatch, caller, content_type):
    _serve(monkeypatch, content_type, 'inline; filename="evil"')
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/evil.svg', headers=caller)
    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'filename="evil.svg"' in response.headers['content-disposition']
    assert response.headers['content-security-policy'] == "default-src 'none'; sandbox"
    assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('content_type', ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'IMAGE/PNG; x=y'])
def test_raster_images_stay_inline(monkeypatch, caller, content_type):
    _serve(monkeypatch, content_type, 'inline; filename="diagram.png"')
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.headers['content-security-policy'] == "default-src 'none'; sandbox"


def test_ingest_attachment_is_never_upgraded_to_inline(monkeypatch, caller):
    _serve(monkeypatch, 'image/png', 'attachment; filename="diagram.png"')
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.headers['content-disposition'].startswith('attachment;')


def test_disposition_filename_is_header_safe(monkeypatch, caller):
    _serve(monkeypatch, 'image/svg+xml')
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/a%22b%C3%A4.svg', headers=caller)
    assert (
        response.headers['content-disposition'] == 'attachment; filename="a_b_.svg"; filename*=UTF-8\'\'a%22b%C3%A4.svg'
    )


@pytest.mark.parametrize('encoded', ['%2E', '%2E%2E', 'a%3Fb.png', 'a%23b.png', 'a%5Cb.png', 'a%0Ab.png', 'a%7Fb.png'])
def test_unsafe_filenames_are_refused_before_ingest(monkeypatch, caller, encoded):
    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_ingest_must_not_be_called))
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/{encoded}', headers=caller)
    assert response.status_code == 404


def test_filename_is_percent_encoded_in_upstream_path(monkeypatch, caller):
    seen: list = []
    _serve(monkeypatch, 'image/png', seen=seen)
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/my%20diagram%25.png', headers=caller)
    assert response.status_code == 200
    assert seen == [f'/api/v1/portal/releases/{_RELEASE}/artifacts/my%20diagram%25.png']


def test_transport_error_text_is_not_returned(monkeypatch, caller):
    def _raise(path):
        raise httpx.ConnectError('connect to ingest.internal:8000 refused')

    monkeypatch.setattr('app.api.portal_artifacts._client', lambda: _FakeHttpxClient(_raise))
    response = client.get(f'/v1/portal/releases/{_RELEASE}/artifacts/diagram.png', headers=caller)
    assert response.status_code == 502
    assert response.json() == {'detail': 'Ingest unreachable'}
