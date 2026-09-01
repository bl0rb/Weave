"""GET /v1/auth/oidc/login, GET /v1/auth/oidc/callback, POST /v1/auth/logout
(app/api/auth.py) against a MOCKED OIDC provider -- no real network access
anywhere in this file. Mocked at the same level tests/test_streaming_api.py
mocks Weave-Runtime at: the `httpx.Client` CLASS app/services/oidc.py's own
`_client()` constructs, via `httpx.MockTransport` (see the `provider`
fixture below) -- so these tests exercise the REAL discovery/token-exchange/
JWKS-fetch code, not a stubbed-out version of it.

ID tokens are signed for real, with a freshly generated RSA keypair per
test (`joserfc.jwk.RSAKey`) -- `validate_id_token` (app/services/oidc.py)
runs its real signature/claims/nonce verification against whatever public
JWKS the mocked `/jwks` endpoint serves, so a wrong-signature or
wrong-nonce test genuinely exercises that verification failing, not just a
mocked-away rejection.

The bottom section ("cross-origin post-login handoff") additionally covers
`return_to`/POST /v1/auth/session/exchange -- the `OIDC_POST_LOGIN_ALLOWED_
URLS` allowlist check, the one-time exchange code's single-use/expiry
enforcement, and that it never appears in a log record.
"""

import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.security import hash_exchange_code, hash_session_token
from app.models.models import Session as SessionModel
from app.models.models import SessionExchangeCode, User
from app.services import oidc as oidc_service
from tests.conftest import client

ISSUER = 'https://issuer.example.test'
CLIENT_ID = 'weave-api-test-client'
CLIENT_SECRET = 'test-client-secret'
REDIRECT_URL = 'http://testserver/v1/auth/oidc/callback'


@pytest.fixture(autouse=True)
def _oidc_test_isolation():
    """Every test in this file gets: (1) a clean TestClient cookie jar --
    the OIDC state cookie and/or session cookie set by one test must never
    leak into the next (in THIS file or, since `client` is the one shared
    instance every test module in this suite imports, any other), and (2) a
    cleared discovery-document cache (app/services/oidc.py's
    `_discovery_cache` is a module-level, process-wide dict keyed by issuer
    URL -- every test in this file reuses the same `ISSUER` string, so a
    stale cached document from an earlier test would otherwise leak into a
    later one instead of that test's own mocked discovery response ever
    being fetched)."""
    client.cookies.clear()
    oidc_service._discovery_cache.clear()
    yield
    client.cookies.clear()
    oidc_service._discovery_cache.clear()


@pytest.fixture
def oidc_settings(monkeypatch):
    """Enables OIDC (both oidc_issuer and oidc_client_id set --
    app/api/auth.py's `oidc_enabled()`) with fixed test values. Tests that
    want OIDC left disabled simply don't request this fixture -- the
    Settings defaults (empty strings) already mean "not configured".
    """
    monkeypatch.setattr(settings, 'oidc_issuer', ISSUER)
    monkeypatch.setattr(settings, 'oidc_client_id', CLIENT_ID)
    monkeypatch.setattr(settings, 'oidc_client_secret', CLIENT_SECRET)
    monkeypatch.setattr(settings, 'oidc_redirect_url', REDIRECT_URL)
    monkeypatch.setattr(settings, 'oidc_scopes', 'openid email profile')
    monkeypatch.setattr(settings, 'oidc_team_claim', '')


@pytest.fixture
def provider(monkeypatch):
    """Installs a MockTransport-backed httpx.Client for every client
    app/services/oidc.py's `_client()` builds during this test -- mirrors
    tests/test_streaming_api.py's own `runtime` fixture, just pointed at
    `app.services.oidc.httpx.Client` instead of
    `app.services.runtime_client.httpx.Client`."""

    class _Provider:
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []

        def handle(self, handler) -> None:
            def _recording_handler(request: httpx.Request) -> httpx.Response:
                self.requests.append(request)
                return handler(request)

            transport = httpx.MockTransport(_recording_handler)
            real_client_cls = httpx.Client

            def _patched_client_cls(*args, **kwargs):
                kwargs.setdefault('transport', transport)
                return real_client_cls(*args, **kwargs)

            monkeypatch.setattr(oidc_service.httpx, 'Client', _patched_client_cls)

    return _Provider()


def _discovery_document() -> dict:
    return {
        'issuer': ISSUER,
        'authorization_endpoint': f'{ISSUER}/authorize',
        'token_endpoint': f'{ISSUER}/token',
        'jwks_uri': f'{ISSUER}/jwks',
    }


def _sign_id_token(
    signing_key: RSAKey,
    *,
    kid: str,
    issuer: str = ISSUER,
    audience: str = CLIENT_ID,
    subject: str = 'sub-1',
    nonce: str | None,
    exp: int | None = None,
    extra_claims: dict | None = None,
) -> str:
    claims = {
        'iss': issuer,
        'aud': audience,
        'sub': subject,
        'exp': exp if exp is not None else int(time.time()) + 300,
        'nonce': nonce,
    }
    if extra_claims:
        claims.update(extra_claims)
    header = {'alg': 'RS256', 'kid': kid}
    return joserfc_jwt.encode(header, claims, signing_key)


def _make_handler(
    *,
    publish_key: RSAKey,
    id_token_factory,
    token_status: int = 200,
    jwks_status: int = 200,
):
    """Builds a MockTransport handler routing by path -- discovery, JWKS
    (serving `publish_key`'s PUBLIC half only), and the token endpoint
    (returning whatever `id_token_factory()` produces, called fresh on
    every POST so it can embed the nonce this specific test's callback
    request actually carries)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/.well-known/openid-configuration':
            return httpx.Response(200, json=_discovery_document())
        if path == '/jwks':
            return httpx.Response(jwks_status, json={'keys': [publish_key.as_dict(private=False)]})
        if path == '/token':
            return httpx.Response(token_status, json={'id_token': id_token_factory(), 'access_token': 'fake-at'})
        return httpx.Response(404)

    return handler


def _login_and_extract_state_and_nonce() -> tuple[str, str]:
    response = client.get('/v1/auth/oidc/login', follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers['location']).query)
    return query['state'][0], query['nonce'][0]


def _callback(*, code: str = 'test-code', state: str) -> httpx.Response:
    return client.get('/v1/auth/oidc/callback', params={'code': code, 'state': state}, follow_redirects=False)


# --- OIDC disabled -------------------------------------------------------------


def test_oidc_endpoints_404_when_not_configured():
    """No oidc_settings fixture here -- settings.oidc_issuer/client_id are
    both empty (the Settings default), so every endpoint this router adds
    must behave as if it were never registered at all."""
    assert client.get('/v1/auth/oidc/login', follow_redirects=False).status_code == 404
    assert client.get('/v1/auth/oidc/callback', params={'code': 'x', 'state': 'y'}).status_code == 404
    assert client.post('/v1/auth/logout').status_code == 404
    assert client.post('/v1/auth/session/exchange', json={'code': 'irrelevant'}).status_code == 404


# --- happy path ------------------------------------------------------------


def test_oidc_login_provisions_a_new_user_and_creates_a_session(db_session, provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key,
                kid='key-1',
                subject='sub-alice',
                nonce=nonce_holder.get('nonce'),
                extra_claims={'preferred_username': 'alice.oidc'},
            ),
        )
    )

    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce

    response = _callback(state=state)
    assert response.status_code == 302
    assert response.headers['location'] == '/'
    assert SESSION_COOKIE_NAME in response.cookies

    user = db_session.query(User).filter(User.oidc_subject == 'sub-alice').one()
    assert user.username == 'alice.oidc'
    assert user.disabled is False

    session_row = db_session.query(SessionModel).filter(SessionModel.user_id == user.id).one()
    assert session_row.token_hash  # only the hash is ever stored, never the raw cookie value

    # The state cookie is cleared -- proving state/nonce are single-use
    # (see module docstring): replaying the exact same callback request
    # again now finds no state cookie at all.
    replay = _callback(state=state)
    assert replay.status_code == 400


def test_oidc_login_with_team_claim_resolves_team_from_a_list_valued_claim(db_session, provider, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_issuer', ISSUER)
    monkeypatch.setattr(settings, 'oidc_client_id', CLIENT_ID)
    monkeypatch.setattr(settings, 'oidc_client_secret', CLIENT_SECRET)
    monkeypatch.setattr(settings, 'oidc_redirect_url', REDIRECT_URL)
    monkeypatch.setattr(settings, 'oidc_scopes', 'openid email profile')
    monkeypatch.setattr(settings, 'oidc_team_claim', 'groups')

    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key,
                kid='key-1',
                subject='sub-bob',
                nonce=nonce_holder.get('nonce'),
                extra_claims={'preferred_username': 'bob.oidc', 'groups': ['Legal', 'Support']},
            ),
        )
    )

    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce
    response = _callback(state=state)
    assert response.status_code == 302

    user = db_session.query(User).filter(User.oidc_subject == 'sub-bob').one()
    assert user.team == 'Legal'  # first element of the list-valued claim


def test_session_cookie_authenticates_ordinary_endpoints(db_session, provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-carol', nonce=nonce_holder.get('nonce')
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce
    assert _callback(state=state).status_code == 302

    # The session cookie set by the callback above is now in the shared
    # TestClient's cookie jar -- a plain GET with no Authorization header at
    # all must authenticate via it. 404 (not 401!) proves auth succeeded --
    # same discipline as tests/test_auth.py's own bearer-token version of
    # this assertion.
    response = client.get(f'/v1/conversations/{uuid.uuid4()}')
    assert response.status_code == 404


def test_logout_deletes_the_session_and_clears_the_cookie(db_session, provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-dave', nonce=nonce_holder.get('nonce')
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce
    assert _callback(state=state).status_code == 302

    user = db_session.query(User).filter(User.oidc_subject == 'sub-dave').one()
    assert db_session.query(SessionModel).filter(SessionModel.user_id == user.id).count() == 1

    logout_response = client.post('/v1/auth/logout')
    assert logout_response.status_code == 200
    assert db_session.query(SessionModel).filter(SessionModel.user_id == user.id).count() == 0

    # The cookie itself is now worthless: the next request looks
    # unauthenticated, exactly like it never had one.
    response = client.get(f'/v1/conversations/{uuid.uuid4()}')
    assert response.status_code == 401


# --- rejections --------------------------------------------------------------


def test_callback_rejects_a_wrong_state(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(publish_key=key, id_token_factory=lambda: _sign_id_token(key, kid='key-1', nonce='irrelevant'))
    )
    _login_and_extract_state_and_nonce()  # sets the real state cookie, discarded below

    response = _callback(state='not-the-real-state')
    assert response.status_code == 400


def test_callback_rejects_a_wrong_nonce(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(
            publish_key=key,
            # Always signs a fixed, wrong nonce -- never the one the login
            # step actually generated -- so validate_id_token's nonce check
            # is exactly what fails here.
            id_token_factory=lambda: _sign_id_token(key, kid='key-1', subject='sub-eve', nonce='not-the-real-nonce'),
        )
    )
    state, _real_nonce = _login_and_extract_state_and_nonce()

    response = _callback(state=state)
    assert response.status_code == 502
    assert 'nonce' in response.json()['detail'].lower()


def test_callback_rejects_an_expired_id_token(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key,
                kid='key-1',
                subject='sub-frank',
                nonce=nonce_holder.get('nonce'),
                exp=int(time.time()) - 100,
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce

    response = _callback(state=state)
    assert response.status_code == 502


def test_callback_rejects_a_wrongly_signed_id_token(provider, oidc_settings):
    """The published JWKS carries one key; the ID token is signed with a
    DIFFERENT one -- signature verification must fail regardless of every
    other claim being otherwise perfectly valid."""
    published_key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    forged_key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=published_key,
            id_token_factory=lambda: _sign_id_token(
                forged_key, kid='key-1', subject='sub-mallory', nonce=nonce_holder.get('nonce')
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce

    response = _callback(state=state)
    assert response.status_code == 502


def test_callback_rejects_login_for_a_disabled_user(db_session, provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    existing = User(username='gina.disabled', oidc_subject='sub-gina', disabled=True)
    db_session.add(existing)
    db_session.commit()

    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-gina', nonce=nonce_holder.get('nonce')
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce

    response = _callback(state=state)
    assert response.status_code == 401
    assert db_session.query(SessionModel).filter(SessionModel.user_id == existing.id).count() == 0


# --- FIX 2: the signed state cookie is cleared on EVERY exit from the
# callback, not just the success path -----------------------------------------
#
# This module's own docstring promises the cookie is deleted "on every path
# out of the callback that got far enough to read it, success or failure".
# Each test below replays the exact same request a second time after the
# first one fails, and requires the SPECIFIC "no state cookie at all" 400
# (`_MISSING_STATE_COOKIE_DETAIL`) -- not just any 400 -- since that's the
# one response shape that's only reachable when the cookie is truly gone: if
# the failing branch had left it behind, the replay would instead re-read
# the very same cookie and fail some other way (a second "state mismatch",
# say), proving the leak.

_MISSING_STATE_COOKIE_DETAIL = 'Missing OIDC state (IdP-initiated login is not supported)'


def test_callback_clears_the_state_cookie_after_a_state_mismatch(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(publish_key=key, id_token_factory=lambda: _sign_id_token(key, kid='key-1', nonce='irrelevant'))
    )
    _login_and_extract_state_and_nonce()  # sets the real state cookie, discarded below

    first = _callback(state='not-the-real-state')
    assert first.status_code == 400
    assert first.json()['detail'] == 'OIDC state mismatch'

    replay = _callback(state='not-the-real-state')
    assert replay.status_code == 400
    assert replay.json()['detail'] == _MISSING_STATE_COOKIE_DETAIL


def test_callback_clears_the_state_cookie_after_a_provider_error(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(publish_key=key, id_token_factory=lambda: _sign_id_token(key, kid='key-1', nonce='irrelevant'))
    )
    state, _nonce = _login_and_extract_state_and_nonce()

    first = client.get(
        '/v1/auth/oidc/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False
    )
    assert first.status_code == 400
    assert 'access_denied' in first.json()['detail']

    replay = client.get(
        '/v1/auth/oidc/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False
    )
    assert replay.status_code == 400
    assert replay.json()['detail'] == _MISSING_STATE_COOKIE_DETAIL


def test_callback_clears_the_state_cookie_after_a_missing_code(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(publish_key=key, id_token_factory=lambda: _sign_id_token(key, kid='key-1', nonce='irrelevant'))
    )
    state, _nonce = _login_and_extract_state_and_nonce()

    first = client.get('/v1/auth/oidc/callback', params={'state': state}, follow_redirects=False)
    assert first.status_code == 400
    assert first.json()['detail'] == 'Missing authorization code'

    replay = client.get('/v1/auth/oidc/callback', params={'state': state}, follow_redirects=False)
    assert replay.status_code == 400
    assert replay.json()['detail'] == _MISSING_STATE_COOKIE_DETAIL


def test_callback_clears_the_state_cookie_after_an_invalid_id_token(provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    provider.handle(
        _make_handler(
            # Always signs a fixed, wrong nonce, so validate_id_token's
            # nonce check is exactly what fails -- same shape as
            # test_callback_rejects_a_wrong_nonce above, just also proving
            # the cookie doesn't survive that failure.
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-eve-fix2', nonce='not-the-real-nonce'
            ),
        )
    )
    state, _real_nonce = _login_and_extract_state_and_nonce()

    first = _callback(state=state)
    assert first.status_code == 502

    replay = _callback(state=state)
    assert replay.status_code == 400
    assert replay.json()['detail'] == _MISSING_STATE_COOKIE_DETAIL


def test_callback_clears_the_state_cookie_after_login_for_a_disabled_user(db_session, provider, oidc_settings):
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    existing = User(username='gina-fix2-disabled', oidc_subject='sub-gina-fix2', disabled=True)
    db_session.add(existing)
    db_session.commit()

    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-gina-fix2', nonce=nonce_holder.get('nonce')
            ),
        )
    )
    state, nonce = _login_and_extract_state_and_nonce()
    nonce_holder['nonce'] = nonce

    first = _callback(state=state)
    assert first.status_code == 401

    replay = _callback(state=state)
    assert replay.status_code == 400
    assert replay.json()['detail'] == _MISSING_STATE_COOKIE_DETAIL


def test_expired_session_cookie_returns_401(db_session, expired_timestamp):
    user = User(username='henry-expired-session')
    db_session.add(user)
    db_session.flush()
    session_row = SessionModel(
        user_id=user.id,
        token_hash=hash_session_token('a-raw-session-token'),
        expires_at=expired_timestamp,
    )
    db_session.add(session_row)
    db_session.commit()

    response = client.get(
        f'/v1/conversations/{uuid.uuid4()}', headers={'Cookie': f'{SESSION_COOKIE_NAME}=a-raw-session-token'}
    )
    assert response.status_code == 401
    assert db_session.query(SessionModel).filter(SessionModel.user_id == user.id).count() == 0


def test_redirect_target_cannot_be_pointed_at_a_foreign_domain(provider, oidc_settings):
    """No `next`/redirect-target parameter this router reads exists at all
    (see app/api/auth.py's `_POST_LOGIN_REDIRECT` docstring) -- even an
    attempt to smuggle one in via the authorize redirect's own query string
    changes nothing about where the callback sends the browser afterwards."""
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(
                key, kid='key-1', subject='sub-ivan', nonce=nonce_holder.get('nonce')
            ),
        )
    )

    response = client.get(
        '/v1/auth/oidc/login', params={'next': 'https://evil.example.com/steal'}, follow_redirects=False
    )
    query = parse_qs(urlsplit(response.headers['location']).query)
    state, nonce = query['state'][0], query['nonce'][0]
    nonce_holder['nonce'] = nonce

    callback_response = client.get(
        '/v1/auth/oidc/callback',
        params={'code': 'test-code', 'state': state, 'next': 'https://evil.example.com/steal'},
        follow_redirects=False,
    )
    assert callback_response.status_code == 302
    assert callback_response.headers['location'] == '/'


# --- cross-origin post-login handoff (return_to / session exchange) ----------
#
# GET /v1/auth/oidc/login?return_to=..., GET /v1/auth/oidc/callback's
# handling of it, and POST /v1/auth/session/exchange -- the mechanism a
# chat UI on a DIFFERENT origin than this gateway uses to get its own
# session token (app/api/auth.py's module docstring, "Cross-origin
# post-login handoff" section).

_ALLOWED_UI_ORIGIN = 'https://ui.example.test'


def _run_login_and_callback(provider, *, return_to: str | None, subject: str) -> httpx.Response:
    """A full, real login+callback (real RSA-signed ID token, mocked
    discovery/token/JWKS -- same discipline as every other test above) with
    the given `return_to`, returning the callback's own (redirect-not-
    followed) response."""
    key = RSAKey.generate_key(2048, parameters={'kid': 'key-1'}, private=True)
    nonce_holder: dict[str, str] = {}
    provider.handle(
        _make_handler(
            publish_key=key,
            id_token_factory=lambda: _sign_id_token(key, kid='key-1', subject=subject, nonce=nonce_holder.get('nonce')),
        )
    )
    params = {'return_to': return_to} if return_to is not None else {}
    login_response = client.get('/v1/auth/oidc/login', params=params, follow_redirects=False)
    assert login_response.status_code == 302
    query = parse_qs(urlsplit(login_response.headers['location']).query)
    state, nonce = query['state'][0], query['nonce'][0]
    nonce_holder['nonce'] = nonce
    return _callback(state=state)


def test_allowlisted_return_to_redirects_with_a_code_and_still_sets_the_session_cookie(
    db_session, provider, oidc_settings, monkeypatch
):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])

    response = _run_login_and_callback(
        provider, return_to=f'{_ALLOWED_UI_ORIGIN}/after-login', subject='sub-handoff-happy'
    )
    assert response.status_code == 302
    # Both paths run side by side -- see app/api/auth.py's own docstring on
    # why: this gateway's own cookie is set unconditionally, regardless of
    # whether return_to also resolved to a cross-origin handoff.
    assert SESSION_COOKIE_NAME in response.cookies

    location = response.headers['location']
    assert location.startswith(f'{_ALLOWED_UI_ORIGIN}/after-login?code=')
    code = parse_qs(urlsplit(location).query)['code'][0]

    user = db_session.query(User).filter(User.oidc_subject == 'sub-handoff-happy').one()
    row = db_session.query(SessionExchangeCode).filter(SessionExchangeCode.user_id == user.id).one()
    assert row.code_hash == hash_exchange_code(code)  # the raw code itself is never persisted
    assert row.used_at is None

    exchange_response = client.post('/v1/auth/session/exchange', json={'code': code})
    assert exchange_response.status_code == 200
    body = exchange_response.json()
    assert isinstance(body['session_token'], str) and body['session_token']
    assert body['expires_at']

    # The EXCHANGED token -- not the cookie the callback also happened to
    # set on this shared TestClient -- must itself authenticate a real
    # endpoint: 404 (not 401) proves it worked, same discipline as every
    # other cookie-auth test in this file.
    auth_check = client.get(
        f'/v1/conversations/{uuid.uuid4()}', headers={'Cookie': f'{SESSION_COOKIE_NAME}={body["session_token"]}'}
    )
    assert auth_check.status_code == 404


def test_exchange_code_can_be_redeemed_exactly_once(provider, oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(provider, return_to=_ALLOWED_UI_ORIGIN, subject='sub-handoff-once')
    code = parse_qs(urlsplit(response.headers['location']).query)['code'][0]

    first = client.post('/v1/auth/session/exchange', json={'code': code})
    assert first.status_code == 200

    second = client.post('/v1/auth/session/exchange', json={'code': code})
    assert second.status_code == 400


def test_expired_exchange_code_is_rejected(db_session, expired_timestamp, oidc_settings):
    user = User(username='handoff-expired')
    db_session.add(user)
    db_session.flush()
    raw_code = 'an-expired-raw-exchange-code'
    db_session.add(
        SessionExchangeCode(user_id=user.id, code_hash=hash_exchange_code(raw_code), expires_at=expired_timestamp)
    )
    db_session.commit()

    response = client.post('/v1/auth/session/exchange', json={'code': raw_code})
    assert response.status_code == 400


def test_unknown_exchange_code_gives_the_identical_generic_response_as_an_expired_one(
    db_session, expired_timestamp, oidc_settings
):
    """A caller must never be able to tell "no such code" apart from "that
    code already expired" -- both collapse into the exact same status and
    body (app/api/auth.py's `exchange_session_code`)."""
    user = User(username='handoff-unknown-vs-expired')
    db_session.add(user)
    db_session.flush()
    raw_expired_code = 'a-different-expired-raw-code'
    db_session.add(
        SessionExchangeCode(
            user_id=user.id, code_hash=hash_exchange_code(raw_expired_code), expires_at=expired_timestamp
        )
    )
    db_session.commit()

    expired_response = client.post('/v1/auth/session/exchange', json={'code': raw_expired_code})
    unknown_response = client.post('/v1/auth/session/exchange', json={'code': 'this-code-was-never-issued'})

    assert expired_response.status_code == unknown_response.status_code == 400
    assert expired_response.json() == unknown_response.json()


def test_return_to_outside_the_allowlist_is_ignored_not_errored(db_session, provider, oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])

    response = _run_login_and_callback(
        provider, return_to='https://evil.example.com/steal', subject='sub-handoff-disallowed'
    )
    assert response.status_code == 302
    # Silently fell back to the fixed target -- never an error, and never a
    # redirect anywhere near the disallowed origin.
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_with_no_allowlist_configured_is_ignored(db_session, provider, oidc_settings):
    """The default (`OIDC_POST_LOGIN_ALLOWED_URLS` unset, i.e. `[]`) means
    the handoff is opt-in per deployment -- a `return_to` is ignored even
    when it would otherwise look plausible, exactly like every deployment
    before this setting existed."""
    response = _run_login_and_callback(
        provider, return_to=f'{_ALLOWED_UI_ORIGIN}/after-login', subject='sub-handoff-no-allowlist'
    )
    assert response.status_code == 302
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_bypass_appended_domain_suffix_is_ignored(db_session, provider, oidc_settings, monkeypatch):
    # 'ui.example.test' is a STRING prefix of 'ui.example.test.evil.com',
    # but a completely different, attacker-controlled host.
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider, return_to='https://ui.example.test.evil.example/steal', subject='sub-bypass-domain'
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_bypass_different_port_is_ignored(db_session, provider, oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider, return_to='https://ui.example.test:9999/steal', subject='sub-bypass-port'
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_bypass_userinfo_smuggling_a_different_host_is_ignored(
    db_session, provider, oidc_settings, monkeypatch
):
    # 'https://ui.example.test@evil.example/...' textually STARTS WITH the
    # allowed origin, but per RFC 3986 userinfo syntax its actual host is
    # 'evil.example' -- urlsplit(...).hostname already strips the userinfo
    # prefix, which is exactly what makes this rejection work.
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider, return_to='https://ui.example.test@evil.example/steal', subject='sub-bypass-userinfo'
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_case_variation_in_an_allowed_host_still_matches(db_session, provider, oidc_settings, monkeypatch):
    """Not an attack -- a regression guard proving the FIX 1 pre-check
    below doesn't accidentally start rejecting a legitimately-cased
    version of an ALLOWED host: scheme/host are already compared via
    urlsplit's own lowercased `.hostname`, so case alone must keep
    matching exactly as it did before."""
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider, return_to='HTTPS://UI.EXAMPLE.TEST/after-login', subject='sub-case-variant-allowed'
    )
    assert response.status_code == 302
    # `_append_code_param` round-trips the candidate through urlsplit/
    # urlunsplit, which normalises the scheme (its own `.scheme` is always
    # lowercase) but leaves the netloc's original casing alone -- either
    # way, this is still the ALLOWED host, just proving the match itself
    # wasn't defeated by the caller's casing.
    assert response.headers['location'].startswith('https://UI.EXAMPLE.TEST/after-login?code=')
    assert db_session.query(SessionExchangeCode).count() == 1


# --- return_to bypass: parser differential (backslash / control characters,
# FIX 1) --------------------------------------------------------------------
#
# Python's urlsplit() follows RFC 3986; a real browser follows the WHATWG URL
# spec. For http/https, WHATWG treats a backslash '\' exactly like '/' --
# an authority/path separator -- while urlsplit treats it as an ordinary
# character with no structural meaning. That gap is a genuine open redirect:
# the server-side allowlist check (this file's other return_to tests) can
# say "yes, this matches", while the browser that actually navigates the
# 302 goes to a completely different, attacker-controlled host -- and takes
# the one-time cross-origin handoff `code` (or, for a same-origin deployment
# with no handoff at all, simply the user) there with it. Every case below
# must be ignored exactly like a plain non-allowlisted URL: no exception, no
# distinguishing error, no SessionExchangeCode minted, and the browser ends
# up at the fixed `_POST_LOGIN_REDIRECT`, never near the attacker's host.


def test_return_to_bypass_backslash_before_at_sign_is_ignored(db_session, provider, oidc_settings, monkeypatch):
    """The exact case from the security review: urlsplit reads the WHOLE
    'evil.example\\@ui.example.test' span as one netloc (no delimiter
    before the '@'), returning hostname 'ui.example.test' -- an allowlist
    MATCH -- while a real browser treats '\\' as '/', terminates the
    authority there, and navigates to 'evil.example' instead, carrying the
    one-time exchange code straight to the attacker."""
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider, return_to='https://evil.example\\@ui.example.test/steal', subject='sub-bypass-backslash-at'
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_bypass_multiple_backslashes_is_ignored(db_session, provider, oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider,
        return_to='https://evil.example\\\\@ui.example.test\\\\/steal',
        subject='sub-bypass-multi-backslash',
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_return_to_bypass_mixed_slash_and_backslash_is_ignored(db_session, provider, oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider,
        return_to='https:/\\evil.example\\@ui.example.test/steal',
        subject='sub-bypass-mixed-slashes',
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


@pytest.mark.parametrize(
    'control_char',
    [
        pytest.param('\t', id='tab'),
        pytest.param('\n', id='newline'),
        pytest.param('\r', id='carriage-return'),
    ],
)
def test_return_to_bypass_control_characters_is_ignored(
    db_session, provider, oidc_settings, monkeypatch, control_char
):
    """An otherwise-legitimate-looking, allowlisted-path candidate with a
    stray tab/newline/carriage-return spliced into it must still be
    ignored outright -- the pre-check has to hold on its own rather than
    depend on whatever a given urllib version happens to strip
    internally (see `_has_unambiguous_url_syntax`'s docstring)."""
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])
    response = _run_login_and_callback(
        provider,
        return_to=f'{_ALLOWED_UI_ORIGIN}/after{control_char}login',
        subject=f'sub-bypass-control-char-{hash(control_char)}',
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


@pytest.mark.parametrize(
    'dotted_path',
    [
        pytest.param('/app/../../admin', id='parent-segments'),
        pytest.param('/app/./../admin', id='current-then-parent-segment'),
    ],
)
def test_return_to_bypass_dot_segments_is_ignored(db_session, provider, oidc_settings, monkeypatch, dotted_path):
    # httpx/browsers normalise dot-segments before actually requesting the
    # URL, so a path-scoped allowlist entry would otherwise be satisfiable
    # by a return_to that ends up somewhere else entirely once normalised.
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [f'{_ALLOWED_UI_ORIGIN}/app'])
    response = _run_login_and_callback(
        provider, return_to=f'{_ALLOWED_UI_ORIGIN}{dotted_path}', subject=f'sub-bypass-dots-{hash(dotted_path)}'
    )
    assert response.headers['location'] == '/'
    assert db_session.query(SessionExchangeCode).count() == 0


def test_exchange_code_never_appears_in_any_log_record(provider, oidc_settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [_ALLOWED_UI_ORIGIN])

    with caplog.at_level('DEBUG'):
        response = _run_login_and_callback(provider, return_to=_ALLOWED_UI_ORIGIN, subject='sub-handoff-no-logs')
        code = parse_qs(urlsplit(response.headers['location']).query)['code'][0]
        client.post('/v1/auth/session/exchange', json={'code': code})

    for record in caplog.records:
        assert code not in record.getMessage()
        assert all(code != str(arg) for arg in (record.args or ()))


@pytest.mark.parametrize('candidate_path', [
    '/ui/%2e%2e/evil',
    '/ui/%2E%2E/evil',
    '/ui/%2e./evil',
    '/ui/.%2e/evil',
])
def test_return_to_rejects_percent_encoded_dot_segments(candidate_path):
    # A browser normalises these down to '/evil' before navigating, outside
    # the allowlisted '/ui' prefix -- the raw-path check used to wave them
    # through and hand the one-time exchange code to an unintended path.
    from app.api.auth import _return_to_matches_base

    assert _return_to_matches_base(
        'https://ui.example.com' + candidate_path, 'https://ui.example.com/ui'
    ) is False


def test_return_to_still_allows_ordinary_encoded_paths():
    # Percent-encoding itself must stay legal -- only dot-segments are the problem.
    from app.api.auth import _return_to_matches_base

    assert _return_to_matches_base(
        'https://ui.example.com/ui/a%20b/callback', 'https://ui.example.com/ui'
    ) is True
