"""The knowledge-access gate (app/core/auth.py's has_knowledge_access): a
non-admin without any EXPLICIT knowledge-space grant is turned away on every
authenticated route (403) and their personal token is reported inactive to
Weave-Tools/MCP; admins pass without a grant. The suite-wide conftest fixture
grants access, so every test here re-patches the Retrieval lookup itself.
"""

import pytest

from app.core.auth import NO_KNOWLEDGE_ACCESS_DETAIL
from app.core.config import settings
from app.services.retrieval_client import RetrievalClientError
from tests.conftest import auth_headers, client, make_user_with_token

_SERVICE_TOKEN = 'test-introspection-service-token'


def _grants(monkeypatch, allowed: bool, calls: list | None = None):
    def _check(*, team, user):
        if calls is not None:
            calls.append({'team': team, 'user': user})
        return allowed

    monkeypatch.setattr('app.core.auth.has_explicit_collection_access', _check)


def test_non_admin_without_grant_gets_403_on_authenticated_routes(db_session, monkeypatch):
    _grants(monkeypatch, False)
    _, raw_token = make_user_with_token(db_session, username='nogrant', team='Support')

    for path in ('/v1/bots', '/v1/me', '/v1/conversations', '/v1/models'):
        response = client.get(path, headers=auth_headers(raw_token))
        assert response.status_code == 403, path
        assert response.json() == {'detail': NO_KNOWLEDGE_ACCESS_DETAIL}, path


def test_non_admin_with_grant_passes_and_is_checked_by_team_and_subject(db_session, monkeypatch):
    calls: list = []
    _grants(monkeypatch, True, calls)
    _, raw_token = make_user_with_token(db_session, username='granted', team='Support')

    response = client.get('/v1/me', headers=auth_headers(raw_token))

    assert response.status_code == 200
    assert calls == [{'team': ['Support'], 'user': None}]


def test_admin_passes_without_grant_and_without_asking_retrieval(db_session, monkeypatch):
    calls: list = []
    _grants(monkeypatch, False, calls)
    _, raw_token = make_user_with_token(db_session, username='boss', is_admin=True)

    response = client.get('/v1/me', headers=auth_headers(raw_token))

    assert response.status_code == 200
    assert calls == []


def test_retrieval_unavailable_fails_closed_with_503(db_session, monkeypatch):
    def _unavailable(**_):
        raise RetrievalClientError('down')

    monkeypatch.setattr('app.core.auth.has_explicit_collection_access', _unavailable)
    _, raw_token = make_user_with_token(db_session, username='unknown-state', team='Support')

    response = client.get('/v1/me', headers=auth_headers(raw_token))

    assert response.status_code == 503


@pytest.mark.parametrize(('is_admin', 'allowed', 'active'), [(False, False, False), (False, True, True), (True, False, True)])
def test_introspection_reports_token_of_user_without_grant_inactive(db_session, monkeypatch, is_admin, allowed, active):
    monkeypatch.setattr(settings, 'introspection_service_token', _SERVICE_TOKEN)
    _grants(monkeypatch, allowed)
    _, raw_token = make_user_with_token(db_session, username='mcp-user', team='Support', is_admin=is_admin)

    response = client.post('/internal/tokens/introspect', json={'token': raw_token}, headers=auth_headers(_SERVICE_TOKEN))

    assert response.status_code == 200
    assert response.json()['active'] is active
