"""Security hardening of the cross-origin login handoff and of the
rate-limit/identity-refresh order (app/api/auth.py, app/core/auth.py,
app/core/ratelimit.py):

1. A login that resolved a `return_to` (cross-origin chat, native loopback)
   gets the one-time exchange code ONLY -- no `weave_api_session` cookie on
   the gateway origin that the chat's logout could never revoke.
2. The `return_to` inside the signed state cookie is re-checked against the
   allowlist at the callback: a forged cookie (leaked SECRET_KEY) or an
   allowlist that shrank mid-login is a 400, and no code is minted.
3. The per-user rate limit is charged BEFORE the Weave-Ingest identity
   refresh, so a throttled request never reaches Weave-Ingest.
"""

import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from app.core import ratelimit
from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.ratelimit import FixedWindowRateLimiter
from app.core.security import sign_value
from app.models.models import Session as SessionModel
from app.models.models import SessionExchangeCode
from app.services import ingest_identity
from app.services.ingest_identity import IngestIdentity
from tests.conftest import auth_headers, client, make_user_with_token

INGEST_LOGIN_URL = 'http://ingest.test/login'
INGEST_API_URL = 'http://ingest-backend.test'
CHAT_RETURN_TO = 'http://chat.test/api/auth/sso/callback'
IDENTITY = {'subject': 'sec-ingest-user', 'username': 'sec', 'email': 'sec@example.test', 'team': None, 'is_admin': False}
PROTECTED_URL = '/v1/conversations/00000000-0000-0000-0000-000000000001'


@pytest.fixture(autouse=True)
def _cookie_isolation():
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture()
def ingest_configured(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_login_url', INGEST_LOGIN_URL)
    monkeypatch.setattr(settings, 'ingest_api_url', INGEST_API_URL)
    monkeypatch.setattr(settings, 'ingest_handoff_secret', 'sec-handoff-secret')
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [CHAT_RETURN_TO])


def _mock_ingest(monkeypatch, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(200, json=IDENTITY)

    monkeypatch.setattr(ingest_identity, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def _ingest_login(params: dict) -> str:
    resp = client.get('/v1/auth/ingest/login', params=params, follow_redirects=False)
    assert resp.status_code == 302
    return parse_qs(urlsplit(resp.headers['location']).query)['handoff_state'][0]


def _ingest_callback(state: str, *, headers: dict | None = None):
    return client.get(
        '/v1/auth/ingest/callback',
        params={'code': 'handoff-code', 'state': state},
        headers=headers,
        follow_redirects=False,
    )


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def _cookie_header(name: str, value: str) -> dict:
    cookie = SimpleCookie()
    cookie[name] = value
    return {'Cookie': cookie[name].OutputString()}


# --- 1. no gateway session for a handoff login -------------------------------


def test_cross_origin_ingest_login_sets_no_gateway_session(ingest_configured, monkeypatch):
    _mock_ingest(monkeypatch)
    resp = _ingest_callback(_ingest_login({'return_to': CHAT_RETURN_TO}))

    assert resp.status_code == 302
    assert resp.headers['location'].startswith(f'{CHAT_RETURN_TO}?code=')
    assert SESSION_COOKIE_NAME not in resp.cookies
    with _db() as db:
        # The only session that will ever exist is the one the chat
        # exchanges the code for -- and that one its logout revokes.
        assert db.query(SessionModel).count() == 0
        assert db.query(SessionExchangeCode).count() == 1
    # Nothing on the gateway origin authenticates the browser afterwards.
    assert client.get(PROTECTED_URL).status_code == 401


def test_native_loopback_ingest_login_sets_no_gateway_session(ingest_configured, monkeypatch):
    monkeypatch.setattr(settings, 'native_login_enabled', True)
    _mock_ingest(monkeypatch)
    challenge = create_s256_code_challenge('v' * 50)
    state = _ingest_login({'return_to': 'http://127.0.0.1:49152/callback', 'code_challenge': challenge})

    resp = _ingest_callback(state)

    assert resp.status_code == 302
    assert resp.headers['location'].startswith('http://127.0.0.1:49152/callback?code=')
    assert SESSION_COOKIE_NAME not in resp.cookies


def test_same_origin_ingest_login_still_gets_its_cookie(ingest_configured, monkeypatch):
    _mock_ingest(monkeypatch)
    resp = _ingest_callback(_ingest_login({}))

    assert resp.status_code == 302
    assert SESSION_COOKIE_NAME in resp.cookies
    assert client.get(PROTECTED_URL).status_code == 404


# --- 2. return_to re-validated at the callback --------------------------------


def test_ingest_callback_rejects_a_forged_return_to(ingest_configured, monkeypatch):
    calls: list = []
    _mock_ingest(monkeypatch, calls)
    forged = sign_value(json.dumps({'state': 'forged-state', 'return_to': 'https://evil.example/steal', 'code_challenge': None}))

    resp = _ingest_callback('forged-state', headers=_cookie_header('weave_api_ingest_state', forged))

    assert resp.status_code == 400
    assert 'location' not in resp.headers
    assert calls == []  # the handoff code was never even redeemed
    with _db() as db:
        assert db.query(SessionExchangeCode).count() == 0
        assert db.query(SessionModel).count() == 0


def test_ingest_callback_rejects_a_forged_loopback_without_challenge(ingest_configured, monkeypatch):
    monkeypatch.setattr(settings, 'native_login_enabled', True)
    _mock_ingest(monkeypatch)
    forged = sign_value(
        json.dumps({'state': 's', 'return_to': 'http://127.0.0.1:4444/callback', 'code_challenge': None})
    )

    resp = _ingest_callback('s', headers=_cookie_header('weave_api_ingest_state', forged))

    assert resp.status_code == 400


def test_ingest_callback_rejects_a_return_to_the_allowlist_dropped_mid_login(ingest_configured, monkeypatch):
    _mock_ingest(monkeypatch)
    state = _ingest_login({'return_to': CHAT_RETURN_TO})
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [])

    resp = _ingest_callback(state)

    assert resp.status_code == 400
    assert SESSION_COOKIE_NAME not in resp.cookies


def test_oidc_callback_rejects_a_forged_return_to(monkeypatch):
    monkeypatch.setattr(settings, 'oidc_issuer', 'https://issuer.example.test')
    monkeypatch.setattr(settings, 'oidc_client_id', 'weave-api-test-client')

    def _no_network(*args, **kwargs):  # the IdP must never be contacted
        raise AssertionError('provider contacted')

    monkeypatch.setattr('app.api.auth.get_discovery_document', _no_network)
    forged = sign_value(
        json.dumps(
            {
                'state': 'st',
                'nonce': 'n',
                'code_verifier': 'v',
                'return_to': 'https://evil.example/steal',
                'code_challenge': None,
            }
        )
    )

    resp = client.get(
        '/v1/auth/oidc/callback',
        params={'code': 'c', 'state': 'st'},
        headers=_cookie_header('weave_api_oidc_state', forged),
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert 'location' not in resp.headers


# --- 3. rate limit charged before the Weave-Ingest refresh --------------------


def test_throttled_request_never_reaches_weave_ingest(db_session, monkeypatch):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1, clock=lambda: 0.0))
    fetches: list[str] = []

    def _fetch(subject):
        fetches.append(subject)
        return IngestIdentity(subject=subject, username='x', email='', team=None, is_admin=False)

    monkeypatch.setattr('app.core.auth.fetch_identity', _fetch)
    _, raw_token = make_user_with_token(db_session, username='sec-throttled', oidc_subject='weave-ingest:sec-1')
    headers = auth_headers(raw_token)

    assert client.get(PROTECTED_URL, headers=headers).status_code == 404
    assert fetches == ['sec-1']

    for _ in range(3):
        assert client.get(PROTECTED_URL, headers=headers).status_code == 429
    assert fetches == ['sec-1']  # still one: throttled requests stopped before Ingest


def test_allowed_request_still_refreshes_and_rejects_a_gone_ingest_account(db_session, monkeypatch):
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=10, clock=lambda: 0.0))
    monkeypatch.setattr('app.core.auth.fetch_identity', lambda subject: None)
    _, raw_token = make_user_with_token(db_session, username='sec-gone', oidc_subject='weave-ingest:sec-gone')

    assert client.get(PROTECTED_URL, headers=auth_headers(raw_token)).status_code == 401


def test_router_and_route_dependency_charge_the_budget_once(db_session, monkeypatch):
    # app/api/bots.py lists enforce_rate_limit on the router AND the route.
    monkeypatch.setattr('app.api.bots.list_bots', lambda *args: [])
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=2, clock=lambda: 0.0))
    _, raw_token = make_user_with_token(db_session, username='sec-once')
    headers = auth_headers(raw_token)

    assert client.get('/v1/bots', headers=headers).status_code == 200
    assert client.get('/v1/bots', headers=headers).status_code == 200
    assert client.get('/v1/bots', headers=headers).status_code == 429
