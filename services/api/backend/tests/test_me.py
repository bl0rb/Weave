"""GET /v1/me + PUT /v1/me/locale (app/api/me.py): the caller's own profile
and UI-locale preference. A Weave-Ingest-backed identity (`oidc_subject`
prefixed `weave-ingest:`) forwards the locale write to that service first
(app/services/ingest_identity.update_identity_locale); a local-only account
stores it locally only.

Weave-Ingest is mocked at the same level tests/test_ingest_login.py mocks
it: the `httpx.Client` that app/services/ingest_identity.py's own
`_client()` constructs, via `httpx.MockTransport`.
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import User
from app.services import ingest_identity
from tests.conftest import auth_headers, client, make_user_with_token

INGEST_API_URL = 'http://ingest-backend.test'
HANDOFF_SECRET = 'shared-handoff-secret'


def _db():
    return SessionLocal()


@pytest.fixture()
def ingest_configured(monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', INGEST_API_URL)
    monkeypatch.setattr(settings, 'ingest_handoff_secret', HANDOFF_SECRET)
    return None


def _mock_ingest(monkeypatch, *, identity_locale='de', put_status=204, put_error=False, capture=None):
    """Replace the httpx client ingest_identity._client() builds. GET
    answers the auth-layer identity refresh every authenticated request for
    an ingest-backed user triggers (see app/core/auth.py's
    _refresh_ingest_identity); PUT answers this module's own locale push."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'GET':
            return httpx.Response(200, json={
                'subject': 'ingest-subject-1',
                'username': 'ingestuser',
                'email': 'ingestuser@example.test',
                'team': None,
                'is_admin': False,
                'locale': identity_locale,
            })
        if request.method == 'PUT':
            if capture is not None:
                capture['url'] = str(request.url)
                capture['secret'] = request.headers.get('X-Weave-Handoff-Secret')
                capture['body'] = json.loads(request.content.decode('utf-8'))
            if put_error:
                raise httpx.ConnectError('connection refused', request=request)
            return httpx.Response(put_status)
        raise AssertionError(f'unexpected method {request.method}')

    monkeypatch.setattr(
        ingest_identity, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


# --- GET /v1/me -----------------------------------------------------------

def test_get_me_returns_username_and_locale() -> None:
    db = _db()
    try:
        user, token = make_user_with_token(db, username='localereader')
    finally:
        db.close()

    resp = client.get('/v1/me', headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == {'username': 'localereader', 'locale': None}


def test_get_me_requires_authentication() -> None:
    assert client.get('/v1/me').status_code == 401


# --- PUT /v1/me/locale: local-only users -----------------------------------

def test_put_me_locale_stores_locally_for_a_local_only_user() -> None:
    db = _db()
    try:
        user, token = make_user_with_token(db, username='localesetter')
        user_id = user.id
    finally:
        db.close()

    resp = client.put('/v1/me/locale', json={'locale': 'de'}, headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == {'locale': 'de'}
    db = _db()
    try:
        assert db.get(User, user_id).locale == 'de'
    finally:
        db.close()


def test_put_me_locale_can_clear_a_previously_set_preference() -> None:
    db = _db()
    try:
        user, token = make_user_with_token(db, username='localeclearer')
        user.locale = 'en'
        db.commit()
    finally:
        db.close()

    resp = client.put('/v1/me/locale', json={'locale': None}, headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == {'locale': None}


def test_put_me_locale_rejects_an_invalid_value() -> None:
    db = _db()
    try:
        _, token = make_user_with_token(db, username='localeinvalid')
    finally:
        db.close()

    resp = client.put('/v1/me/locale', json={'locale': 'fr'}, headers=auth_headers(token))

    assert resp.status_code == 422


# --- PUT /v1/me/locale: Weave-Ingest-backed users ---------------------------

def test_put_me_locale_forwards_to_ingest_for_an_ingest_backed_user(ingest_configured, monkeypatch) -> None:
    capture: dict = {}
    _mock_ingest(monkeypatch, capture=capture)
    db = _db()
    try:
        user, token = make_user_with_token(
            db, username='ingestuser', oidc_subject='weave-ingest:ingest-subject-1'
        )
        user_id = user.id
    finally:
        db.close()

    resp = client.put('/v1/me/locale', json={'locale': 'en'}, headers=auth_headers(token))

    assert resp.status_code == 200
    assert resp.json() == {'locale': 'en'}
    assert capture['url'] == f'{INGEST_API_URL}/api/v1/auth/handoff/identity/ingest-subject-1/locale'
    assert capture['secret'] == HANDOFF_SECRET
    assert capture['body'] == {'locale': 'en'}
    db = _db()
    try:
        assert db.get(User, user_id).locale == 'en'
    finally:
        db.close()


def test_put_me_locale_503_leaves_the_local_value_unchanged(ingest_configured, monkeypatch) -> None:
    _mock_ingest(monkeypatch, identity_locale='de', put_error=True)
    db = _db()
    try:
        user, token = make_user_with_token(
            db, username='ingestunreachable', oidc_subject='weave-ingest:ingest-subject-2'
        )
        user.locale = 'de'
        db.commit()
        user_id = user.id
    finally:
        db.close()

    resp = client.put('/v1/me/locale', json={'locale': 'en'}, headers=auth_headers(token))

    assert resp.status_code == 503
    db = _db()
    try:
        assert db.get(User, user_id).locale == 'de'
    finally:
        db.close()
