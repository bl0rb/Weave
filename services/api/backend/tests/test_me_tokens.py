"""Self-service personal API tokens (GET/POST/DELETE /v1/me/tokens):
managed with a signed-in session only, never with a bearer token."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.security import hash_session_token
from app.models.models import ApiToken, User
from app.models.models import Session as SessionModel
from tests.conftest import auth_headers, client, make_user_with_token


@pytest.fixture(autouse=True)
def _self_service_tokens_enabled(monkeypatch):
    monkeypatch.setattr(settings, 'self_service_api_tokens_enabled', True)


def _session_headers(db_session, username: str) -> tuple[User, dict[str, str]]:
    user = User(username=username)
    db_session.add(user)
    db_session.flush()
    raw = f'session-{username}'
    db_session.add(SessionModel(
        user_id=user.id, token_hash=hash_session_token(raw), expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db_session.commit()
    return user, {'Cookie': f'{SESSION_COOKIE_NAME}={raw}'}


def test_a_signed_in_user_creates_lists_uses_and_revokes_a_token(db_session):
    user, session = _session_headers(db_session, 'token-owner')

    created = client.post('/v1/me/tokens', json={'label': 'n8n Vertrieb', 'expires_in_days': 30}, headers=session)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['label'] == 'n8n Vertrieb'
    assert body['expires_at'] is not None
    raw = body['token']
    assert '.' not in raw  # Weave-Tools tells personal tokens by the missing dot

    assert client.get('/v1/me', headers=auth_headers(raw)).json()['username'] == 'token-owner'
    listed = client.get('/v1/me/tokens', headers=session).json()['items']
    assert [(item['id'], item['label']) for item in listed] == [(body['id'], 'n8n Vertrieb')]
    assert 'token' not in listed[0]

    assert client.delete(f"/v1/me/tokens/{body['id']}", headers=session).status_code == 204
    assert client.get('/v1/me', headers=auth_headers(raw)).status_code == 401


def test_a_bearer_token_cannot_manage_tokens(db_session):
    _, raw = make_user_with_token(db_session, username='token-bearer')
    db_session.commit()
    for response in (
        client.get('/v1/me/tokens', headers=auth_headers(raw)),
        client.post('/v1/me/tokens', json={'label': 'x'}, headers=auth_headers(raw)),
    ):
        assert response.status_code == 403


def test_tokens_of_other_users_stay_invisible_and_untouchable(db_session):
    _, first = _session_headers(db_session, 'token-first')
    _, second = _session_headers(db_session, 'token-second')
    token_id = client.post('/v1/me/tokens', json={'label': 'Eigenes'}, headers=first).json()['id']

    assert client.get('/v1/me/tokens', headers=second).json()['items'] == []
    assert client.delete(f'/v1/me/tokens/{token_id}', headers=second).status_code == 404
    assert db_session.get(ApiToken, uuid.UUID(token_id)) is not None


def test_token_endpoints_refuse_while_self_service_is_switched_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'self_service_api_tokens_enabled', False)
    _, session = _session_headers(db_session, 'switched-off')

    assert client.get('/v1/me', headers=session).json()['api_tokens_enabled'] is False
    assert client.get('/v1/me/tokens', headers=session).status_code == 403
    assert client.post('/v1/me/tokens', json={'label': 'x'}, headers=session).status_code == 403
    assert client.delete(f'/v1/me/tokens/{uuid.uuid4()}', headers=session).status_code == 403
