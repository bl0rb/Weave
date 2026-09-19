"""Schritt 5 -- technical identities: admin CRUD (create/update/rotate/
revoke), the raw-token-shown-once discipline, and the internal introspection
endpoint Weave-Tools calls (active/inactive semantics, service-token gate,
last_used_at touch, denied-attempt auditing).
"""

from sqlalchemy import delete

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.models import Collection, TechnicalIdentity, UserRole
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as

INTROSPECT_URL = '/api/v1/internal/technical-identities/introspect'
TOOLS_INTROSPECTION_TOKEN = 'test-tools-introspection-token'

# `allowed_collections` is now validated against real Collection rows (same
# known-slug check as managed_bots.py) -- these are the two slugs the tests
# below grant to technical identities.
_KNOWN_COLLECTION_SLUGS = ('handbuch', 'faq')


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _configured_introspection_token(monkeypatch):
    monkeypatch.setattr(settings, 'tools_introspection_token', TOOLS_INTROSPECTION_TOKEN)


@pytest.fixture(autouse=True)
def _known_collections():
    with TestingSessionLocal() as db:
        for slug in _KNOWN_COLLECTION_SLUGS:
            if db.query(Collection).filter_by(slug=slug).first() is None:
                db.add(Collection(slug=slug, name=slug))
        db.commit()
    yield
    with TestingSessionLocal() as db:
        db.execute(delete(Collection).where(Collection.slug.in_(_KNOWN_COLLECTION_SLUGS)))
        db.commit()


def admin_client(suffix: str) -> TestClient:
    create_test_user(username=f'ti-admin-{suffix}', email=f'ti-admin-{suffix}@example.com', role=UserRole.ADMIN)
    return login_as(f'ti-admin-{suffix}')


def introspect(client: TestClient, token: str) -> dict:
    resp = client.post(INTROSPECT_URL, json={'token': token}, headers={'Authorization': f'Bearer {TOOLS_INTROSPECTION_TOKEN}'})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_returns_token_once_list_does_not():
    client = admin_client('crud')
    resp = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'n8n-integration', 'allowed_collections': ['handbuch'],
    })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created['token'].startswith('wti_')
    assert created['token_prefix'] == created['token'][:9]
    assert created['allowed_collections'] == ['handbuch']
    assert created['enabled'] is True
    assert created['revoked_at'] is None

    listed = client.get('/api/v1/auth/admin/technical-identities').json()['items']
    assert len(listed) == 1
    assert 'token' not in listed[0]
    assert listed[0]['token_prefix'] == created['token_prefix']


def test_new_identity_has_no_collections_by_default():
    client = admin_client('nogrant')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'bare'}).json()
    assert created['allowed_collections'] == []


def test_introspect_active_identity_returns_grants():
    client = admin_client('introspect-active')
    created = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'mcp-agent', 'allowed_collections': ['handbuch', 'faq'],
    }).json()

    result = introspect(client, created['token'])
    assert result == {
        'active': True,
        'identity_id': created['id'],
        'name': 'mcp-agent',
        'allowed_collections': ['handbuch', 'faq'],
        'expires_at': None,
    }


def test_introspect_unknown_token_is_inactive_not_an_error():
    client = admin_client('introspect-unknown')
    assert introspect(client, 'wti_this-was-never-issued') == {
        'active': False, 'identity_id': None, 'name': None, 'allowed_collections': None, 'expires_at': None,
    }


def test_introspect_revoked_identity_is_inactive():
    client = admin_client('introspect-revoked')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'to-revoke'}).json()
    revoke_resp = client.post(f"/api/v1/auth/admin/technical-identities/{created['id']}/revoke")
    assert revoke_resp.status_code == 204

    assert introspect(client, created['token'])['active'] is False
    # idempotent second revoke
    assert client.post(f"/api/v1/auth/admin/technical-identities/{created['id']}/revoke").status_code == 204


def test_introspect_disabled_identity_is_inactive():
    client = admin_client('introspect-disabled')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'to-disable'}).json()
    update_resp = client.put(f"/api/v1/auth/admin/technical-identities/{created['id']}", json={'enabled': False})
    assert update_resp.status_code == 200
    assert update_resp.json()['enabled'] is False

    assert introspect(client, created['token'])['active'] is False


def test_introspect_expired_identity_is_inactive():
    client = admin_client('introspect-expired')
    created = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'to-expire', 'expires_in_days': 1,
    }).json()

    db = TestingSessionLocal()
    try:
        row = db.get(TechnicalIdentity, created['id'])
        from datetime import datetime, timedelta, timezone
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    assert introspect(client, created['token'])['active'] is False


def test_rotate_issues_new_token_old_one_stops_working():
    client = admin_client('rotate')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'to-rotate'}).json()
    old_token = created['token']

    rotate_resp = client.post(f"/api/v1/auth/admin/technical-identities/{created['id']}/rotate")
    assert rotate_resp.status_code == 200
    rotated = rotate_resp.json()
    assert rotated['id'] == created['id']
    assert rotated['token'] != old_token
    assert rotated['token'].startswith('wti_')

    assert introspect(client, old_token)['active'] is False
    assert introspect(client, rotated['token'])['active'] is True


def test_create_rejects_unknown_collection_slug():
    client = admin_client('unknown-create')
    resp = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'bad-grant', 'allowed_collections': ['handbuch', 'does-not-exist'],
    })
    assert resp.status_code == 422
    assert 'does-not-exist' in resp.text


def test_update_rejects_unknown_collection_slug():
    client = admin_client('unknown-update')
    created = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'to-update-bad', 'allowed_collections': ['handbuch'],
    }).json()
    resp = client.put(f"/api/v1/auth/admin/technical-identities/{created['id']}", json={
        'allowed_collections': ['does-not-exist'],
    })
    assert resp.status_code == 422
    assert 'does-not-exist' in resp.text


def test_update_narrows_and_widens_collections():
    client = admin_client('update-collections')
    created = client.post('/api/v1/auth/admin/technical-identities', json={
        'name': 'to-update', 'allowed_collections': ['handbuch'],
    }).json()

    resp = client.put(f"/api/v1/auth/admin/technical-identities/{created['id']}", json={
        'allowed_collections': ['handbuch', 'faq'],
    })
    assert resp.status_code == 200
    assert sorted(resp.json()['allowed_collections']) == ['faq', 'handbuch']


def test_introspection_requires_service_token():
    client = admin_client('service-gate')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'gated'}).json()

    no_auth = client.post(INTROSPECT_URL, json={'token': created['token']})
    assert no_auth.status_code == 401

    wrong_token = client.post(INTROSPECT_URL, json={'token': created['token']}, headers={'Authorization': 'Bearer wrong'})
    assert wrong_token.status_code == 401


def test_introspection_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, 'tools_introspection_token', '')
    client = admin_client('unconfigured')
    resp = client.post(INTROSPECT_URL, json={'token': 'wti_whatever'}, headers={'Authorization': 'Bearer anything'})
    assert resp.status_code == 503


def test_non_admin_cannot_manage_technical_identities():
    create_test_user(username='ti-plain-user', email='ti-plain-user@example.com', role=UserRole.USER)
    client = login_as('ti-plain-user')
    resp = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'nope'})
    assert resp.status_code == 403


def test_audit_rows_recorded_for_create_rotate_revoke():
    client = admin_client('audit')
    created = client.post('/api/v1/auth/admin/technical-identities', json={'name': 'audited'}).json()
    client.post(f"/api/v1/auth/admin/technical-identities/{created['id']}/rotate")
    client.post(f"/api/v1/auth/admin/technical-identities/{created['id']}/revoke")

    audit = client.get(f"/api/v1/auth/admin/technical-identities/{created['id']}/audit").json()['items']
    events = {row['event'] for row in audit}
    assert events == {'created', 'rotated', 'revoked'}
    for row in audit:
        assert 'token' not in str(row['details']).lower() or 'wti_' not in str(row['details'])
