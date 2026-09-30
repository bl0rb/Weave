"""Knowledge space grants (ADR 0008): owners, members and readers, given
to persons (local or SSO accounts) or teams.

Covers what test_collections_api.py's own docstring says belongs in a
dedicated real-per-user-authz file: the role matrix (reader reads, member
uploads, owner manages and shares), POST/PATCH persisting and validating
`visibility` and `grants`, the registry ACL computed from them, and the two
directory endpoints (GET /directory/users, GET /directory/teams) that back
the person/team picker -- authorization and the no-email-leak guarantee.
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


def _create(client, name: str, **extra):
    return client.post('/api/v1/collections', json={'name': name, 'description': 'Test purpose', **extra})


def _subjects(body: dict) -> list[tuple]:
    return [(grant['user_id'], grant['team_id'], grant['role']) for grant in body['grants']]


# --- create: creator owns, visibility default --------------------------------

def test_create_makes_the_creator_owner_and_fails_closed_without_explicit_visibility():
    owner = _user('share-create-default')
    resp = _create(login_as(owner.username), 'Closed Space')
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['visibility'] == 'restricted'
    assert login_as(_user('share-create-default-other').username).get(
        f"/api/v1/collections/{body['collection_id']}"
    ).status_code == 404
    assert body['role'] == 'owner'
    assert body['can_manage'] is True
    assert _subjects(body) == [(owner.id, None, 'owner')]
    assert body['created_by'] == {'id': owner.id, 'username': owner.username}


def test_create_is_public_only_when_asked_explicitly():
    resp = _create(login_as(_user('share-create-public').username), 'Open Space', visibility='public')
    assert resp.status_code == 200, resp.text
    assert resp.json()['visibility'] == 'public'


def test_create_requires_a_purpose():
    client = login_as(_user('share-create-purpose').username)
    missing = client.post('/api/v1/collections', json={'name': 'No Purpose'})
    assert missing.status_code == 422
    blank = client.post('/api/v1/collections', json={'name': 'Blank Purpose', 'description': '   '})
    assert blank.status_code == 422


def test_create_defaults_restricted_when_somebody_is_named():
    owner = _user('share-create-restricted')
    other = _user('share-create-restricted-target')
    resp = _create(login_as(owner.username), 'Shared Space', grants=[{'user_id': other.id, 'role': 'reader'}])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['visibility'] == 'restricted'
    assert _subjects(body) == [(owner.id, None, 'owner'), (other.id, None, 'reader')]


def test_create_honors_explicit_visibility_and_responsible_team():
    team_id = _make_team('share-create-responsible')
    resp = _create(
        login_as(_user('share-create-explicit').username), 'Locked Down', visibility='restricted', responsible_team_id=team_id
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()['visibility'] == 'restricted'
    assert resp.json()['responsible_team']['id'] == team_id
    unknown = _create(login_as(_user('share-create-unknown-team').username), 'Bad Team', responsible_team_id='no-such-team')
    assert unknown.status_code == 422


# --- role matrix -------------------------------------------------------------

def test_reader_reads_but_never_uploads_manages_or_uses_the_directory():
    owner = _user('share-read-owner')
    grantee = _user('share-read-grantee')
    outsider = _user('share-read-outsider')

    created = _create(login_as(owner.username), 'Person Shared', grants=[{'user_id': grantee.id, 'role': 'reader'}])
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    grantee_client = login_as(grantee.username)
    detail = grantee_client.get(f'/api/v1/collections/{collection_id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['role'] == 'reader'
    assert detail.json()['can_upload'] is False
    assert detail.json()['can_manage'] is False
    upload = grantee_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('shared.pdf', b'%PDF-1.4 minimal', 'application/pdf')},
    )
    assert upload.status_code == 403, upload.text
    assert grantee_client.get('/api/v1/directory/users').status_code == 403
    assert grantee_client.get('/api/v1/directory/teams').status_code == 403

    outsider_client = login_as(outsider.username)
    assert outsider_client.get(f'/api/v1/collections/{collection_id}').status_code == 404


def test_person_member_uploads_but_cannot_change_settings_or_sharing():
    owner = _user('share-member-owner')
    member = _user('share-member-person')
    created = _create(login_as(owner.username), 'Member Space', grants=[{'user_id': member.id, 'role': 'member'}])
    collection_id = created.json()['collection_id']

    member_client = login_as(member.username)
    detail = member_client.get(f'/api/v1/collections/{collection_id}').json()
    assert (detail['role'], detail['can_upload'], detail['can_manage']) == ('member', True, False)
    uploaded = member_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('member.pdf', b'%PDF-1.4 minimal', 'application/pdf')},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert member_client.patch(f'/api/v1/collections/{collection_id}', json={'name': 'Nope'}).status_code == 403
    assert member_client.delete(f'/api/v1/collections/{collection_id}').status_code == 403
    assert member_client.get('/api/v1/directory/users').status_code == 403


def test_second_owner_manages_shares_and_may_hand_over():
    owner = _user('share-coowner-first')
    co_owner = _user('share-coowner-second')
    created = _create(login_as(owner.username), 'Co-owned', grants=[{'user_id': co_owner.id, 'role': 'owner'}])
    collection_id = created.json()['collection_id']

    co_client = login_as(co_owner.username)
    assert co_client.get('/api/v1/directory/users').status_code == 200
    handed_over = co_client.patch(
        f'/api/v1/collections/{collection_id}', json={'grants': [{'user_id': co_owner.id, 'role': 'owner'}]}
    )
    assert handed_over.status_code == 200, handed_over.text
    assert _subjects(handed_over.json()) == [(co_owner.id, None, 'owner')]
    # The creator is kept even after the hand-over.
    assert handed_over.json()['created_by']['id'] == owner.id
    # Restricted (somebody was named at creation): the former owner is out.
    assert login_as(owner.username).get(f'/api/v1/collections/{collection_id}').status_code == 404


def test_public_space_makes_every_user_a_reader():
    created = _create(login_as(_user('share-public-owner').username), 'Everyone', visibility='public')
    body = login_as(_user('share-public-reader').username).get(f"/api/v1/collections/{created.json()['collection_id']}")
    assert body.status_code == 200
    assert body.json()['role'] == 'reader'


def test_restricted_without_grants_is_owner_only_fail_closed():
    owner = _user('share-failclosed-owner')
    outsider = _user('share-failclosed-outsider')
    admin = _user('share-failclosed-admin', role=UserRole.ADMIN)

    created = _create(login_as(owner.username), 'Locked', visibility='restricted')
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    assert login_as(owner.username).get(f'/api/v1/collections/{collection_id}').status_code == 200
    assert login_as(outsider.username).get(f'/api/v1/collections/{collection_id}').status_code == 404
    assert login_as(admin.username).get(f'/api/v1/collections/{collection_id}').status_code == 200


# --- PATCH: validation -------------------------------------------------------

def test_patch_validates_grants():
    owner = _user('share-patch-owner')
    grantee = _user('share-patch-grantee')
    team_id = _make_team('share-patch-team')
    owner_client = login_as(owner.username)
    collection_id = _create(owner_client, 'Patch Target').json()['collection_id']
    me = {'user_id': owner.id, 'role': 'owner'}

    def patch(grants):
        return owner_client.patch(f'/api/v1/collections/{collection_id}', json={'grants': grants})

    bad_team = patch([me, {'team_id': 'no-such-team', 'role': 'reader'}])
    assert bad_team.status_code == 422
    assert 'no-such-team' in bad_team.json()['detail']
    bad_user = patch([me, {'user_id': 'no-such-user-id', 'role': 'reader'}])
    assert bad_user.status_code == 422
    assert 'no-such-user-id' in bad_user.json()['detail']
    assert patch([me, {'team_id': team_id, 'role': 'owner'}]).status_code == 422
    assert patch([me, {'user_id': grantee.id, 'team_id': team_id, 'role': 'reader'}]).status_code == 422
    assert patch([me, {'user_id': grantee.id, 'role': 'reader'}, {'user_id': grantee.id, 'role': 'member'}]).status_code == 422
    last_owner = patch([{'user_id': grantee.id, 'role': 'reader'}])
    assert last_owner.status_code == 422
    assert 'owner' in last_owner.json()['detail']

    deduplicated = patch([me, me, {'team_id': team_id, 'role': 'reader'}, {'team_id': team_id, 'role': 'reader'}])
    assert deduplicated.status_code == 200, deduplicated.text
    assert _subjects(deduplicated.json()) == [(owner.id, None, 'owner'), (None, team_id, 'reader')]


def test_patch_changes_the_role_of_an_existing_grant():
    owner = _user('share-role-owner')
    grantee = _user('share-role-grantee')
    team_id = _make_team('share-role-team')
    owner_client = login_as(owner.username)
    created = _create(
        owner_client, 'Role Change',
        grants=[{'user_id': grantee.id, 'role': 'reader'}, {'team_id': team_id, 'role': 'member'}],
    )
    collection_id = created.json()['collection_id']

    promoted = owner_client.patch(f'/api/v1/collections/{collection_id}', json={'grants': [
        {'user_id': owner.id, 'role': 'owner'}, {'user_id': grantee.id, 'role': 'owner'}, {'team_id': team_id, 'role': 'reader'},
    ]})
    assert promoted.status_code == 200, promoted.text
    assert sorted(_subjects(promoted.json()), key=str) == sorted(
        [(owner.id, None, 'owner'), (grantee.id, None, 'owner'), (None, team_id, 'reader')], key=str
    )
    assert login_as(grantee.username).get(f'/api/v1/collections/{collection_id}').json()['can_manage'] is True


def test_grant_details_resolve_username_and_team_without_email():
    owner = _user('share-details-owner')
    team_id = _make_team('share-details-team')
    grantee = _user('share-details-grantee', team_id=team_id)

    created = _create(login_as(owner.username), 'Details Target', grants=[{'user_id': grantee.id, 'role': 'reader'}])
    details = [grant for grant in created.json()['grants'] if grant['user_id'] == grantee.id]
    assert len(details) == 1
    assert details[0]['name'] == grantee.username
    assert details[0]['team'] is not None
    assert details[0]['is_active'] is True
    assert 'email' not in details[0]


# --- Registry: computed from the grants --------------------------------------

def test_registry_includes_visibility_and_every_granted_person():
    owner = _user('share-registry-owner')
    grantee = _user('share-registry-grantee')
    admin = _user('share-registry-admin', role=UserRole.ADMIN)

    created = _create(login_as(owner.username), 'Registry Shared', grants=[{'user_id': grantee.id, 'role': 'reader'}])
    slug = created.json()['slug']

    registry = login_as(admin.username).get('/api/v1/collections/registry')
    assert registry.status_code == 200
    entry = next(item for item in registry.json()['items'] if item['slug'] == slug)
    assert entry['visibility'] == 'restricted'
    assert entry['read_users'] == sorted([owner.id, grantee.id])


# --- Directory endpoints: auth gate + no email leak --------------------------

def test_directory_endpoints_reject_users_who_own_no_collection():
    outsider = _user('share-dir-outsider')
    outsider_client = login_as(outsider.username)
    assert outsider_client.get('/api/v1/directory/users').status_code == 403
    assert outsider_client.get('/api/v1/directory/teams').status_code == 403


def test_directory_users_allowed_for_admin_and_collection_owner_never_leaks_email():
    admin = _user('share-dir-admin', role=UserRole.ADMIN)
    owner = _user('share-dir-owner')
    findable = _user('share-dir-findable')

    owner_client = login_as(owner.username)
    created = _create(owner_client, 'Dir Owner Collection')
    assert created.status_code == 200, created.text

    # An owner of at least one collection may share -- allowed.
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
    assert entry['id'] == team_id
