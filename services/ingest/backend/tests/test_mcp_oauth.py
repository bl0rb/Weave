"""Real JWT verification and identity binding for Keycloak and Entra."""

import time
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from app.main import app
from app.models.models import AuthProvider, Team, User
from conftest import TestingSessionLocal

URL = '/api/v1/internal/mcp-oauth/introspect'
ISSUER = 'https://idp.example.com/realms/weave'
AUDIENCE = 'https://weave.example.com/mcp'
HEADERS = {'Authorization': 'Bearer tools-test-service-token'}


@pytest.fixture
def oauth(monkeypatch):
    suffix = str(uuid4())
    key = RSAKey.generate_key(2048, parameters={'kid': 'test-mcp'})
    keys = KeySet.import_key_set({'keys': [key.as_dict(private=False)]})
    monkeypatch.setattr(settings, 'tools_introspection_token', 'tools-test-service-token')
    monkeypatch.setattr(settings, 'mcp_oauth_provider_slug', suffix)
    monkeypatch.setattr(settings, 'mcp_oauth_audience', AUDIENCE)
    monkeypatch.setattr(settings, 'mcp_oauth_required_scopes', ['mcp.read'])
    monkeypatch.setattr(settings, 'mcp_oauth_subject_claim', 'sub')
    monkeypatch.setattr('app.api.mcp_oauth.get_discovery_document', lambda _: {'issuer': ISSUER, 'jwks_uri': ISSUER + '/keys'})
    monkeypatch.setattr('app.api.mcp_oauth.fetch_jwks', lambda _, **__: keys)
    with TestingSessionLocal() as db:
        provider = AuthProvider(
            slug=suffix, display_name='SSO', issuer_url=ISSUER,
            client_id='portal-client', client_secret_encrypted='unused', enabled=True,
        )
        team = Team(name='finance-' + suffix)
        db.add_all([provider, team])
        db.flush()
        user = User(
            username='mcp-' + suffix, email=suffix + '@example.com',
            oidc_provider_id=provider.id, oidc_subject='portal-subject',
            oidc_object_id=str(uuid4()), team_id=team.id,
        )
        db.add(user)
        db.commit()
        return {'key': key, 'user': user, 'provider': provider, 'team': team, 'client': TestClient(app)}


def token(oauth, **overrides):
    claims = {
        'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'portal-subject',
        'exp': int(time.time()) + 300, 'iat': int(time.time()), 'scope': 'mcp.read',
    }
    claims.update(overrides)
    return jwt.encode({'alg': 'RS256', 'kid': 'test-mcp', 'typ': 'at+jwt'}, claims, oauth['key'])


def introspect(oauth, raw_token, *, issuer=ISSUER):
    return oauth['client'].post(URL, json={'token': raw_token, 'issuer': issuer}, headers=HEADERS)


def test_keycloak_resolves_db_identity_and_ignores_claimed_groups(oauth):
    result = introspect(oauth, token(oauth, groups=['admins'], username='root', user_id='attacker'))
    assert result.status_code == 200
    assert result.json() == {
        'active': True, 'user_id': oauth['user'].id, 'subject': oauth['user'].id,
        'username': oauth['user'].username, 'teams': [oauth['team'].name],
    }


@pytest.mark.parametrize('claims', [
    {'aud': 'portal-client'}, {'aud': 'other-resource'}, {'iss': 'https://attacker.example'},
    {'exp': int(time.time()) - 60}, {'exp': None}, {'nbf': int(time.time()) + 300},
    {'sub': 'unknown'}, {'sub': None},
])
def test_invalid_token_claims_never_resolve_a_user(oauth, claims):
    result = introspect(oauth, token(oauth, **claims))
    assert result.status_code == 200
    assert result.json() == {'active': False}


def test_invalid_signature_and_plain_token_are_rejected(oauth):
    other = dict(oauth, key=RSAKey.generate_key(2048, parameters={'kid': 'test-mcp'}))
    for raw_token in (token(other), 'not-a-jwt'):
        assert introspect(oauth, raw_token).json() == {'active': False}


def test_missing_delegated_scope_requests_step_up(oauth):
    assert introspect(oauth, token(oauth, scope='profile')).json() == {
        'active': False, 'error': 'insufficient_scope',
    }


def test_entra_uses_verified_object_id_across_different_pairwise_subjects(oauth, monkeypatch):
    monkeypatch.setattr(settings, 'mcp_oauth_subject_claim', 'oid')
    result = introspect(oauth, token(oauth, sub='different-native-client-sub', oid=oauth['user'].oidc_object_id, scope=None, scp='mcp.read'))
    assert result.json()['user_id'] == oauth['user'].id
    assert introspect(oauth, token(oauth, oid=str(uuid4()))).json() == {'active': False}
    assert introspect(oauth, token(oauth)).json() == {'active': False}


@pytest.mark.parametrize('target', ['user', 'provider'])
def test_disabled_users_and_providers_are_checked_on_every_request(oauth, target):
    raw_token = token(oauth)
    assert introspect(oauth, raw_token).json()['active'] is True
    with TestingSessionLocal() as db:
        row = db.get(User if target == 'user' else AuthProvider, oauth[target].id)
        if target == 'user':
            row.is_active = False
        else:
            row.enabled = False
        db.commit()
    assert introspect(oauth, raw_token).json() == {'active': False}


def test_client_cannot_select_an_unconfigured_provider(oauth):
    assert introspect(oauth, token(oauth), issuer='https://attacker.example').json() == {'active': False}


def test_internal_endpoint_requires_service_credential(oauth, monkeypatch):
    payload = {'token': token(oauth), 'issuer': ISSUER}
    assert oauth['client'].post(URL, json=payload).status_code == 401
    monkeypatch.setattr(settings, 'tools_introspection_token', '')
    assert oauth['client'].post(URL, json=payload, headers=HEADERS).status_code == 503


def test_provider_failure_is_not_an_invalid_token(oauth, monkeypatch):
    from app.services.oidc import OIDCError

    def fail(_, **__):
        raise OIDCError('offline')
    monkeypatch.setattr('app.api.mcp_oauth.fetch_jwks', fail)
    assert introspect(oauth, token(oauth)).status_code == 503


def test_object_identity_migration_preserves_existing_users_and_rolls_back():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/0041_oidc_object_identity.py'
    spec = importlib.util.spec_from_file_location('object_identity_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, oidc_provider_id TEXT)'))
        connection.execute(text("INSERT INTO users VALUES ('existing-user', 'user@example.org', 'provider')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert 'oidc_object_id' in {column['name'] for column in inspect(connection).get_columns('users')}
            assert connection.execute(text('SELECT id, oidc_object_id FROM users')).one() == ('existing-user', None)
            assert any(c['name'] == 'uq_users_oidc_provider_object' for c in inspect(connection).get_unique_constraints('users'))
            # The sqlite batch rebuild must not lose 0004_auth's lower(email) index.
            assert connection.execute(text("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_users_email_lower'")).scalar() == 1
            migration.downgrade()
            assert 'oidc_object_id' not in {column['name'] for column in inspect(connection).get_columns('users')}
            assert connection.execute(text('SELECT id FROM users')).scalar() == 'existing-user'
