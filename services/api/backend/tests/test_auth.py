"""app/core/auth.py:get_current_user, exercised through any route that sits
behind it (GET /v1/conversations/{id} here -- a harmless choice since a
random, well-formed UUID that doesn't exist yet still reaches the 404 path
*after* auth succeeds, which is exactly the signal that distinguishes "auth
passed" from "auth failed" for a valid-but-unknown id).
"""

import uuid

from tests.conftest import auth_headers, client, make_user_with_token


def test_missing_token_returns_401():
    response = client.get(f'/v1/conversations/{uuid.uuid4()}')
    assert response.status_code == 401


def test_malformed_authorization_header_returns_401():
    response = client.get(
        f'/v1/conversations/{uuid.uuid4()}', headers={'Authorization': 'NotBearer something'}
    )
    assert response.status_code == 401


def test_wrong_token_returns_401(db_session):
    make_user_with_token(db_session, username='alice-wrong-token')
    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers('not-the-real-token'))
    assert response.status_code == 401


def test_expired_token_returns_401(db_session, expired_timestamp):
    _, raw_token = make_user_with_token(db_session, username='alice-expired', expires_at=expired_timestamp)
    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers(raw_token))
    assert response.status_code == 401


def test_disabled_user_returns_401(db_session):
    _, raw_token = make_user_with_token(db_session, username='alice-disabled', disabled=True)
    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers(raw_token))
    assert response.status_code == 401


def test_valid_token_passes_auth_and_reaches_404_for_unknown_conversation(db_session):
    _, raw_token = make_user_with_token(db_session, username='alice-valid')
    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers(raw_token))
    # 404 (not 401!) proves the token itself was accepted -- the request
    # simply names a conversation that doesn't exist.
    assert response.status_code == 404


def test_valid_token_touches_last_used_at(db_session):
    from app.models.models import ApiToken

    user, raw_token = make_user_with_token(db_session, username='alice-touch')
    token_row = db_session.query(ApiToken).filter(ApiToken.user_id == user.id).one()
    assert token_row.last_used_at is None
    # Release this session's own read transaction before the request below
    # writes through a *different* session (get_db yields a fresh one per
    # request) -- otherwise sqlite's snapshot for our still-open read would
    # hide that write from the refresh() below.
    db_session.commit()

    response = client.get(f'/v1/conversations/{uuid.uuid4()}', headers=auth_headers(raw_token))
    assert response.status_code == 404

    db_session.refresh(token_row)
    assert token_row.last_used_at is not None
