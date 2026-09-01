"""The service token in front of the read API (app/core/auth.py).

These routes hand out the indexed corpus itself, past the team/`read_teams`
scoping every search goes through -- so what matters here is not that the
happy path works (every other test file covers that) but that the three
ways of NOT having the credential all end in a refusal.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.conftest import AUTH_HEADERS, TEST_TOKEN

# A client that carries nothing -- unlike tests/conftest.py's shared one.
anonymous = TestClient(app)

READ_ROUTES = [
    '/api/v1/documents',
    '/api/v1/documents/00000000-0000-0000-0000-000000000000',
    '/api/v1/collections',
]


@pytest.mark.parametrize('route', READ_ROUTES)
def test_read_routes_reject_a_request_without_a_token(route: str) -> None:
    assert anonymous.get(route).status_code == 401


@pytest.mark.parametrize('route', READ_ROUTES)
def test_read_routes_reject_a_wrong_token(route: str) -> None:
    resp = anonymous.get(route, headers={'Authorization': 'Bearer not-the-token'})

    assert resp.status_code == 401


def test_a_non_bearer_authorization_header_is_rejected() -> None:
    resp = anonymous.get('/api/v1/documents', headers={'Authorization': TEST_TOKEN})

    assert resp.status_code == 401


def test_an_unconfigured_token_fails_closed_rather_than_open(monkeypatch) -> None:
    # The dangerous shape: with an empty configured token, a caller sending
    # an equally empty bearer would compare equal. 503, never 200.
    monkeypatch.setattr(settings, 'knowledge_api_token', '')

    assert anonymous.get('/api/v1/documents').status_code == 503
    assert anonymous.get('/api/v1/documents', headers={'Authorization': 'Bearer '}).status_code == 503
    assert anonymous.get('/api/v1/documents', headers=AUTH_HEADERS).status_code == 503


def test_health_stays_open_because_a_probe_has_no_token() -> None:
    assert anonymous.get('/health').status_code == 200


def test_health_stays_open_even_with_no_token_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'knowledge_api_token', '')

    assert anonymous.get('/health').status_code == 200
