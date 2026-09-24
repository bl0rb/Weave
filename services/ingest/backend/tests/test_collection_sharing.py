"""Per-person collection sharing: `visibility` + `read_users` (see
app/models/models.py's Collection docstring and the rc/loom-redesign task
that added them alongside the pre-existing `read_teams` team ACL).

Covers what test_collections_api.py's own docstring says belongs in a
dedicated real-per-user-authz file: `_can_read_collection`/
`_can_manage_collection` granting access via `read_users` exactly like they
already do via `read_teams`, POST/PATCH persisting and validating
`visibility`/`read_users`, and the two new directory endpoints
(GET /directory/users, GET /directory/teams) that back the person/team
picker -- authorization and the no-email-leak guarantee.
"""

import uuid

import pytest

from app.models.models import Team, UserRole
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limiter.reset()
    yield


def _user(prefix: str, **kwargs):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', **kwargs)


def _make_team(name_prefix: str) -> str:
    db = TestingSessionLocal()
    try:
        team = Team(name=f'{name_prefix}-{uuid.uuid4().hex[:8]}')
        db.add(team)
        db.commit()
        db.refresh(team)
        return team.id
    finally:
        db.close()


# --- create: visibility default/backward-compat -----------------------------

def test_create_defaults_public_when_no_teams_or_users_named():
    owner_client = login_as(_user('share-create-public').username)
    resp = owner_client.post('/api/v1/collections', json={'name': 'Open Space'})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['visibility'] == 'public'
    assert body['read_teams'] == []
    assert body['read_users'] == []


def test_create_defaults_restricted_when_teams_or_users_named():
    owner = _user('share-create-restricted')
    other = _user('share-create-restricted-target')
    owner_client = login_as(owner.username)
    resp = owner_client.post(
        '/api/v1/collections', json={'name': 'Shared Space', 'read_users': [other.id]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['visibility'] == 'restricted'
    assert body['read_users'] == [other.id]


def test_create_honors_explicit_visibility_override():
    owner_client = login_as(_user('share-create-explicit').username)
    # No teams/users named, but visibility explicitly forced restricted --
    # a fail-closed collection nobody but its owner/admins may read yet.
    resp = owner_client.post(
        '/api/v1/collections', json={'name': 'Locked Down', 'visibility': 'restricted'}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()['visibility'] == 'restricted'


# --- read access via read_users, mirroring read_teams ------------------------

def test_read_users_grants_read_and_manage_like_read_teams_does():
    owner = _user('share-read-owner')
    grantee = _user('share-read-grantee')
    outsider = _user('share-read-outsider')

    owner_client = login_as(owner.username)
    created = owner_client.post(
        '/api/v1/collections', json={'name': 'Person Shared', 'read_users': [grantee.id]}
    )
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    grantee_client = login_as(grantee.username)
    detail = grantee_client.get(f'/api/v1/collections/{collection_id}')
    assert detail.status_code == 200, detail.text
    # read_users grants the same "eligible member" upload/operate rights
    # read_teams membership already grants -- see _can_manage_collection.
    assert detail.json()['can_upload'] is True
    assert detail.json()['can_manage'] is False

    outsider_client = login_as(outsider.username)
    assert outsider_client.get(f'/api/v1/collections/{collection_id}').status_code == 404


def test_restricted_with_no_teams_or_users_is_owner_only_fail_closed():
    """A RESTRICTED collection with neither read_teams nor read_users
    configured is readable only by its owner/admins -- not by everyone, the
    old (pre-`visibility`) empty-read_teams-means-public sentinel no longer
    applies once visibility is explicit."""
    owner = _user('share-failclosed-owner')
    outsider = _user('share-failclosed-outsider')
    admin = _user('share-failclosed-admin', role=UserRole.ADMIN)

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Locked', 'visibility': 'restricted'})
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    assert login_as(outsider.username).get(f'/api/v1/collections/{collection_id}').status_code == 404
    assert login_as(admin.username).get(f'/api/v1/collections/{collection_id}').status_code == 200


# --- PATCH: validation + de-dup ----------------------------------------------

def test_patch_rejects_unknown_team_and_unknown_user_id():
    owner_client = login_as(_user('share-patch-owner').username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Patch Target'})
    collection_id = created.json()['collection_id']

    bad_team = owner_client.patch(f'/api/v1/collections/{collection_id}', json={'read_teams': ['no-such-team']})
    assert bad_team.status_code == 422
    assert 'no-such-team' in bad_team.json()['detail']

    bad_user = owner_client.patch(f'/api/v1/collections/{collection_id}', json={'read_users': ['no-such-user-id']})
    assert bad_user.status_code == 422
    assert 'no-such-user-id' in bad_user.json()['detail']


def test_patch_deduplicates_read_teams_and_read_users():
    owner = _user('share-patch-dedup-owner')
    grantee = _user('share-patch-dedup-grantee')
    team_id = _make_team('share-patch-dedup-team')
    db = TestingSessionLocal()
    try:
        team_name = db.get(Team, team_id).name
    finally:
        db.close()

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Dedup Target'})
    collection_id = created.json()['collection_id']

    patched = owner_client.patch(
        f'/api/v1/collections/{collection_id}',
        json={'read_teams': [team_name, team_name], 'read_users': [grantee.id, grantee.id]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()['read_teams'] == [team_name]
    assert patched.json()['read_users'] == [grantee.id]


def test_patch_read_user_details_resolves_username_and_team_without_email():
    owner = _user('share-details-owner')
    team_id = _make_team('share-details-team')
    grantee = _user('share-details-grantee', team_id=team_id)

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Details Target'})
    collection_id = created.json()['collection_id']

    patched = owner_client.patch(f'/api/v1/collections/{collection_id}', json={'read_users': [grantee.id]})
    assert patched.status_code == 200, patched.text
    details = patched.json()['read_user_details']
    assert len(details) == 1
    assert details[0]['id'] == grantee.id
    assert details[0]['username'] == grantee.username
    assert 'email' not in details[0]


# --- Registry surfaces visibility/read_users too -----------------------------

def test_registry_includes_visibility_and_read_users():
    owner = _user('share-registry-owner')
    grantee = _user('share-registry-grantee')
    admin = _user('share-registry-admin', role=UserRole.ADMIN)

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Registry Shared', 'read_users': [grantee.id]})
    slug = created.json()['slug']

    registry = login_as(admin.username).get('/api/v1/collections/registry')
    assert registry.status_code == 200
    entry = next(item for item in registry.json()['items'] if item['slug'] == slug)
    assert entry['visibility'] == 'restricted'
    assert entry['read_users'] == [grantee.id]


# --- Directory endpoints: auth gate + no email leak --------------------------

def test_directory_endpoints_reject_users_who_cannot_manage_any_collection():
    outsider = _user('share-dir-outsider')
    outsider_client = login_as(outsider.username)
    assert outsider_client.get('/api/v1/directory/users').status_code == 403
    assert outsider_client.get('/api/v1/directory/teams').status_code == 403


def test_directory_users_allowed_for_admin_and_collection_owner_never_leaks_email():
    admin = _user('share-dir-admin', role=UserRole.ADMIN)
    owner = _user('share-dir-owner')
    findable = _user('share-dir-findable')

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Dir Owner Collection'})
    assert created.status_code == 200, created.text

    # The collection owner can manage at least one collection -- allowed.
    owned_search = owner_client.get('/api/v1/directory/users', params={'q': findable.username[:8]})
    assert owned_search.status_code == 200, owned_search.text
    match = next(item for item in owned_search.json()['items'] if item['id'] == findable.id)
    assert match['username'] == findable.username
    assert 'email' not in match
    assert match['display_name'] is None

    admin_search = login_as(admin.username).get('/api/v1/directory/users', params={'q': findable.username[:8]})
    assert admin_search.status_code == 200
    assert any(item['id'] == findable.id for item in admin_search.json()['items'])


def test_directory_users_excludes_inactive_accounts():
    admin = _user('share-dir-active-admin', role=UserRole.ADMIN)
    inactive = _user('share-dir-inactive-target', is_active=False)

    admin_client = login_as(admin.username)
    resp = admin_client.get('/api/v1/directory/users', params={'q': inactive.username[:8]})
    assert resp.status_code == 200
    assert all(item['id'] != inactive.id for item in resp.json()['items'])


def test_directory_teams_lists_member_counts():
    admin = _user('share-dir-team-admin', role=UserRole.ADMIN)
    team_id = _make_team('share-dir-team')
    _user('share-dir-team-member', team_id=team_id)
    db = TestingSessionLocal()
    try:
        team_name = db.get(Team, team_id).name
    finally:
        db.close()

    resp = login_as(admin.username).get('/api/v1/directory/teams')
    assert resp.status_code == 200
    entry = next(item for item in resp.json()['items'] if item['name'] == team_name)
    assert entry['member_count'] == 1
