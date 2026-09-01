"""Auth gating for every /internal/* route (require_service_token, see
app/core/auth.py) -- exercised once against GET /internal/bots since the
dependency is applied at the router level (app/api/internal.py) and
therefore behaves identically for every route on it; test_internal_api.py's
own POST /internal/chat tests don't repeat these cases.
"""

from app.core.config import settings
from tests.conftest import AUTH_HEADERS, client


def test_internal_bots_without_token_is_401():
    resp = client.get('/internal/bots')
    assert resp.status_code == 401


def test_internal_bots_with_malformed_authorization_header_is_401():
    resp = client.get('/internal/bots', headers={'Authorization': 'not-bearer-at-all'})
    assert resp.status_code == 401


def test_internal_bots_with_wrong_token_is_401():
    resp = client.get('/internal/bots', headers={'Authorization': 'Bearer wrong-token'})
    assert resp.status_code == 401


def test_internal_bots_with_empty_configured_token_is_503(monkeypatch):
    # A misconfigured deployment (RUNTIME_API_TOKEN unset) must refuse every
    # request with 503, even one carrying what *was* a valid token a moment
    # ago -- never fall back to "auth disabled".
    monkeypatch.setattr(settings, 'runtime_api_token', '')
    resp = client.get('/internal/bots', headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_internal_bots_with_correct_token_succeeds():
    resp = client.get('/internal/bots', headers=AUTH_HEADERS)
    assert resp.status_code == 200
