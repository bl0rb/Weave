"""POST /internal/tokens/introspect (app/api/internal.py) -- the
service-to-service identity-authority surface behind
`require_introspection_service_token` (app/core/auth.py). Every test sets
`settings.introspection_service_token` itself (via monkeypatch) rather than
relying on a suite-wide default, so "unconfigured" is exercised as
explicitly as "configured with a mismatched value".
"""

import uuid

import pytest

from app.core.config import settings
from tests.conftest import auth_headers, client, make_user_with_token

_SERVICE_TOKEN = 'test-introspection-service-token'


@pytest.fixture(autouse=True)
def _configure_service_token(monkeypatch):
    monkeypatch.setattr(settings, 'introspection_service_token', _SERVICE_TOKEN)


def _introspect(token: str, *, headers: dict[str, str] | None = None):
    return client.post('/internal/tokens/introspect', json={'token': token}, headers=headers)


def test_valid_personal_token_returns_active_identity(db_session):
    user, raw_token = make_user_with_token(db_session, username='intro-alice', team='Support', is_admin=True)

    response = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        'active': True,
        'user_id': str(user.id),
        'username': 'intro-alice',
        'team': 'Support',
        'is_admin': True,
    }


def test_valid_personal_token_without_team_or_admin_reports_both_as_falsy(db_session):
    user, raw_token = make_user_with_token(db_session, username='intro-plain')

    response = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body['active'] is True
    assert body['user_id'] == str(user.id)
    assert body['team'] is None
    assert body['is_admin'] is False


def test_unknown_token_returns_active_false_with_http_200(db_session):
    response = _introspect(str(uuid.uuid4()), headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    assert response.json() == {'active': False}


def test_expired_token_returns_active_false(db_session, expired_timestamp):
    _, raw_token = make_user_with_token(db_session, username='intro-expired', expires_at=expired_timestamp)

    response = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    assert response.json() == {'active': False}


def test_disabled_user_returns_active_false(db_session):
    _, raw_token = make_user_with_token(db_session, username='intro-disabled', disabled=True)

    response = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    assert response.json() == {'active': False}


def test_unknown_and_valid_tokens_are_indistinguishable_besides_the_active_flag(db_session):
    """Both branches return HTTP 200 with no other structural difference --
    a caller gets no oracle over whether a given token string ever existed
    beyond the `active` boolean itself."""
    unknown = _introspect(str(uuid.uuid4()), headers=auth_headers(_SERVICE_TOKEN))
    assert unknown.status_code == 200

    _, raw_token = make_user_with_token(db_session, username='intro-oracle-check')
    known = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))
    assert known.status_code == 200

    assert unknown.status_code == known.status_code == 200


def test_missing_service_token_header_returns_401(db_session):
    _, raw_token = make_user_with_token(db_session, username='intro-no-header')

    response = _introspect(raw_token)

    assert response.status_code == 401


def test_wrong_service_token_returns_401(db_session):
    _, raw_token = make_user_with_token(db_session, username='intro-wrong-header')

    response = _introspect(raw_token, headers=auth_headers('not-the-service-token'))

    assert response.status_code == 401


def test_unconfigured_service_token_returns_503(monkeypatch, db_session):
    monkeypatch.setattr(settings, 'introspection_service_token', '')
    _, raw_token = make_user_with_token(db_session, username='intro-unconfigured')

    response = _introspect(raw_token, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 503


def test_endpoint_is_not_reachable_with_a_personal_api_token(db_session):
    """A caller's own valid Personal-API-Token is just an arbitrary wrong
    string as far as `require_introspection_service_token` is concerned --
    it must never double as the service credential, even for its own
    owner's introspect call."""
    _, raw_token = make_user_with_token(db_session, username='intro-self-service')

    response = _introspect(raw_token, headers=auth_headers(raw_token))

    assert response.status_code == 401
