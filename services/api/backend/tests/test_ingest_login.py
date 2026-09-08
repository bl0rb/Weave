"""GET /v1/auth/ingest/login + /v1/auth/ingest/callback (app/api/auth.py):
the federated login that borrows Weave-Ingest's identities instead of
configuring an identity provider here a second time.

Weave-Ingest is mocked at the same level tests/test_oidc_auth.py mocks the
OIDC provider: the `httpx.Client` that app/services/ingest_identity.py's
own `_client()` constructs, via `httpx.MockTransport` -- so the real
request-building, status handling and response parsing run, only the
network is replaced.
"""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.security import hash_exchange_code
from app.models.models import Session as SessionModel
from app.models.models import SessionExchangeCode, User
from app.services import ingest_identity
from tests.conftest import client

INGEST_LOGIN_URL = 'http://ingest.test/login'
INGEST_API_URL = 'http://ingest-backend.test'
HANDOFF_SECRET = 'shared-handoff-secret'
CHAT_RETURN_TO = 'http://chat.test/api/auth/sso/callback'

IDENTITY = {
    'subject': 'ingest-user-id-1',
    'username': 'matze',
    'email': 'matze@example.test',
    'team': 'rechtsabteilung',
    'is_admin': True,
}


@pytest.fixture(autouse=True)
def _cookie_isolation():
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_login_url', INGEST_LOGIN_URL)
    monkeypatch.setattr(settings, 'ingest_api_url', INGEST_API_URL)
    monkeypatch.setattr(settings, 'ingest_handoff_secret', HANDOFF_SECRET)
    monkeypatch.setattr(settings, 'oidc_post_login_allowed_urls', [CHAT_RETURN_TO])
    return None


def _mock_ingest(monkeypatch, *, identity=None, status_code=200, capture=None):
    """Replace the httpx client ingest_identity._client() builds."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture['url'] = str(request.url)
            capture['secret'] = request.headers.get('X-Weave-Handoff-Secret')
            capture['body'] = json.loads(request.content.decode('utf-8'))
        if status_code != 200:
            return httpx.Response(status_code, json={'detail': 'nope'})
        return httpx.Response(200, json=identity if identity is not None else IDENTITY)

    monkeypatch.setattr(
        ingest_identity, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


def _start_login(return_to: str | None = CHAT_RETURN_TO):
    params = {} if return_to is None else {'return_to': return_to}
    return client.get('/v1/auth/ingest/login', params=params, follow_redirects=False)


def _state_from(response) -> str:
    return parse_qs(urlsplit(response.headers['location']).query)['handoff_state'][0]


def _callback(code: str, state: str):
    return client.get(
        '/v1/auth/ingest/callback', params={'code': code, 'state': state}, follow_redirects=False
    )


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


# --- disabled by default ------------------------------------------------------

def test_both_routes_are_404_while_unconfigured() -> None:
    assert client.get('/v1/auth/ingest/login', follow_redirects=False).status_code == 404
    assert client.get('/v1/auth/ingest/callback', follow_redirects=False).status_code == 404


# --- step 1 -------------------------------------------------------------------

def test_login_redirects_to_ingest_asking_for_a_handoff(configured) -> None:
    resp = _start_login()

    assert resp.status_code == 302
    location = urlsplit(resp.headers['location'])
    assert f'{location.scheme}://{location.netloc}{location.path}' == INGEST_LOGIN_URL
    query = parse_qs(location.query)
    assert query['handoff'] == ['1']
    assert query['handoff_state'][0]
    # The state lives in a signed, path-scoped cookie, not in the URL alone.
    assert 'weave_api_ingest_state' in resp.cookies


def test_login_preserves_a_query_string_the_login_url_already_has(configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ingest_login_url', 'http://ingest.test/login?tenant=acme')

    query = parse_qs(urlsplit(_start_login().headers['location']).query)

    assert query['tenant'] == ['acme']
    assert query['handoff'] == ['1']


# --- step 2: the happy path ---------------------------------------------------

def test_round_trip_provisions_the_user_and_hands_a_code_to_the_chat(configured, monkeypatch) -> None:
    capture: dict = {}
    _mock_ingest(monkeypatch, capture=capture)
    start = _start_login()

    resp = _callback('handoff-code-1', _state_from(start))

    assert resp.status_code == 302
    # The redemption went out server-to-server, with the shared secret.
    assert capture['url'] == f'{INGEST_API_URL}/api/v1/auth/handoff/exchange'
    assert capture['secret'] == HANDOFF_SECRET
    assert capture['body'] == {'code': 'handoff-code-1'}

    # ...and the browser goes back to the chat with a one-time code for it.
    location = urlsplit(resp.headers['location'])
    assert f'{location.scheme}://{location.netloc}{location.path}' == CHAT_RETURN_TO
    chat_code = parse_qs(location.query)['code'][0]

    db = _db()
    try:
        user = db.query(User).one()
        # Keyed on Weave-Ingest's user id, namespaced so it can never
        # collide with a directly-configured OIDC provider's subject.
        assert user.oidc_subject == 'weave-ingest:ingest-user-id-1'
        assert user.username == 'matze'
        assert user.team == 'rechtsabteilung'
        assert user.is_admin is True
        assert db.query(SessionExchangeCode).filter_by(code_hash=hash_exchange_code(chat_code)).count() == 1
    finally:
        db.close()


def test_the_handed_over_code_works_at_the_existing_exchange_endpoint(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch)
    start = _start_login()
    resp = _callback('handoff-code-2', _state_from(start))
    chat_code = parse_qs(urlsplit(resp.headers['location']).query)['code'][0]

    exchanged = client.post('/v1/auth/session/exchange', json={'code': chat_code})

    assert exchanged.status_code == 200
    assert exchanged.json()['session_token']


def test_a_second_login_resyncs_team_and_admin_but_keeps_one_account(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch)
    start = _start_login()
    _callback('code-a', _state_from(start))

    # The admin moved them to another team and revoked admin over in Ingest.
    demoted = {**IDENTITY, 'team': 'buchhaltung', 'is_admin': False, 'username': 'renamed-there'}
    _mock_ingest(monkeypatch, identity=demoted)
    start = _start_login()
    _callback('code-b', _state_from(start))

    db = _db()
    try:
        user = db.query(User).one()
        assert user.team == 'buchhaltung'
        assert user.is_admin is False
        # The display name here does NOT follow a rename: it is unique in
        # this database and may have been suffixed on creation.
        assert user.username == 'matze'
    finally:
        db.close()


def test_existing_session_refreshes_memberships_and_fails_closed(configured, monkeypatch):
    _mock_ingest(monkeypatch, identity={**IDENTITY, 'teams': ['rechtsabteilung', 'buchhaltung']})
    start = _start_login(return_to=None)
    assert _callback('live-identity-code', _state_from(start)).status_code == 302
    _mock_ingest(monkeypatch, identity={**IDENTITY, 'teams': [], 'team': None, 'is_admin': False})
    protected_url = '/v1/conversations/00000000-0000-0000-0000-000000000001'
    assert client.get(protected_url).status_code == 404
    with _db() as db:
        user = db.query(User).one()
        assert user.effective_teams == []
        assert user.is_admin is False
    _mock_ingest(monkeypatch, status_code=503)
    assert client.get(protected_url).status_code == 503
    _mock_ingest(monkeypatch, status_code=404)
    assert client.get(protected_url).status_code == 401


# --- step 2: everything that must not work ------------------------------------

def test_callback_without_the_state_cookie_is_rejected(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch)

    resp = _callback('some-code', 'some-state')

    assert resp.status_code == 400
    assert _db().query(SessionModel).count() == 0


def test_callback_with_a_foreign_state_is_rejected(configured, monkeypatch) -> None:
    # Login CSRF: without this check, anyone able to make a victim's browser
    # fetch this callback with a code of their own would sign that browser
    # into THEIR account, and everything typed into the chat afterwards
    # would land in the attacker's history.
    _mock_ingest(monkeypatch)
    _start_login()

    resp = _callback('some-code', 'not-the-state-we-issued')

    assert resp.status_code == 400
    db = _db()
    try:
        assert db.query(User).count() == 0
        assert db.query(SessionModel).count() == 0
    finally:
        db.close()


def test_a_code_ingest_rejects_produces_a_generic_401(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch, status_code=401)
    start = _start_login()

    resp = _callback('stale-code', _state_from(start))

    assert resp.status_code == 401
    assert resp.json()['detail'] == 'Login failed'
    db = _db()
    try:
        assert db.query(User).count() == 0
        assert db.query(SessionModel).count() == 0
    finally:
        db.close()


def test_an_unreachable_ingest_produces_the_same_401(configured, monkeypatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection refused', request=request)

    monkeypatch.setattr(
        ingest_identity, '_client', lambda: httpx.Client(transport=httpx.MockTransport(refuse))
    )
    start = _start_login()

    assert _callback('any-code', _state_from(start)).status_code == 401


def test_callback_fails_closed_without_a_shared_secret(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch)
    start = _start_login()
    monkeypatch.setattr(settings, 'ingest_handoff_secret', '')

    resp = _callback('a-code', _state_from(start))

    assert resp.status_code == 503


def test_a_return_to_outside_the_allowlist_falls_back_instead_of_leaking_a_code(
    configured, monkeypatch
) -> None:
    _mock_ingest(monkeypatch)
    start = _start_login(return_to='http://evil.test/steal')

    resp = _callback('a-code', _state_from(start))

    assert resp.status_code == 302
    assert resp.headers['location'] == '/'
    # The login itself succeeded (a session cookie for this gateway), but no
    # one-time code was ever minted for anybody to carry away.
    assert SESSION_COOKIE_NAME in resp.cookies
    assert _db().query(SessionExchangeCode).count() == 0


def test_a_disabled_local_account_cannot_sign_in(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch)
    start = _start_login()
    _callback('code-1', _state_from(start))
    db = _db()
    try:
        db.query(User).update({'disabled': True})
        db.commit()
    finally:
        db.close()

    start = _start_login()
    resp = _callback('code-2', _state_from(start))

    assert resp.status_code == 401


def test_the_state_cookie_is_cleared_on_every_exit(configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch, status_code=401)
    start = _start_login()

    resp = _callback('stale', _state_from(start))

    # A stale state cookie would make the NEXT attempt fail on a mismatch
    # that has nothing to do with that attempt.
    assert 'weave_api_ingest_state' in resp.headers.get('set-cookie', '')
    assert 'Max-Age=0' in resp.headers['set-cookie'] or 'expires=' in resp.headers['set-cookie'].lower()
