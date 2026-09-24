"""HTTP-level tests for GET /api/v1/collections: auth gating (same
require_service_token dependency as POST /api/v1/search, see
tests/test_search_api.py's own auth-gating section) and the `team`/`user`
query params' effect on which rows come back (app/services/collections.py's
own readable_collections() unit tests cover the underlying logic in
isolation).
"""

from app.models.models import Collection
from tests.conftest import AUTH_HEADERS, TestingSessionLocal, client, make_collection


def _seed(*collections: Collection) -> None:
    db = TestingSessionLocal()
    try:
        db.add_all(collections)
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    db = TestingSessionLocal()
    try:
        db.query(Collection).delete()
        db.commit()
    finally:
        db.close()


# --- auth gating --------------------------------------------------------------


def test_collections_without_token_is_401():
    resp = client.get('/api/v1/collections')
    assert resp.status_code == 401


def test_collections_with_wrong_token_is_401():
    resp = client.get('/api/v1/collections', headers={'Authorization': 'Bearer wrong-token'})
    assert resp.status_code == 401


# --- team parameter --------------------------------------------------------------


def test_collections_without_team_returns_only_public():
    _seed(
        make_collection(slug='public-docs', name='Public Docs', read_teams=[]),
        make_collection(slug='eng-docs', name='Engineering Docs', visibility='restricted', read_teams=['Engineering']),
    )
    try:
        resp = client.get('/api/v1/collections', headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body == [{'slug': 'public-docs', 'name': 'Public Docs', 'description': None, 'public': True}]
    finally:
        _cleanup()


def test_collections_with_team_returns_public_plus_team_restricted():
    _seed(
        make_collection(slug='public-docs', name='Public Docs', read_teams=[]),
        make_collection(slug='eng-docs', name='Engineering Docs', visibility='restricted', read_teams=['Engineering']),
        make_collection(slug='support-docs', name='Support Docs', visibility='restricted', read_teams=['Kundenservice']),
    )
    try:
        resp = client.get('/api/v1/collections', params={'team': 'Engineering'}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        slugs = {c['slug'] for c in body}
        assert slugs == {'public-docs', 'eng-docs'}

        eng_entry = next(c for c in body if c['slug'] == 'eng-docs')
        assert eng_entry['public'] is False
        public_entry = next(c for c in body if c['slug'] == 'public-docs')
        assert public_entry['public'] is True
    finally:
        _cleanup()


def test_collections_with_unknown_team_returns_only_public():
    _seed(
        make_collection(slug='public-docs', name='Public Docs', read_teams=[]),
        make_collection(slug='eng-docs', name='Engineering Docs', visibility='restricted', read_teams=['Engineering']),
    )
    try:
        resp = client.get('/api/v1/collections', params={'team': 'NoSuchTeam'}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert {c['slug'] for c in resp.json()} == {'public-docs'}
    finally:
        _cleanup()


def test_collections_includes_description_when_set():
    _seed(make_collection(slug='public-docs', name='Public Docs', description='Alles fuer alle.', read_teams=[]))
    try:
        resp = client.get('/api/v1/collections', headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()[0]['description'] == 'Alles fuer alle.'
    finally:
        _cleanup()


# --- user parameter --------------------------------------------------------------


def test_collections_with_user_returns_public_plus_person_shared():
    _seed(
        make_collection(slug='public-docs', name='Public Docs', read_teams=[]),
        make_collection(
            slug='shared-with-me', name='Shared With Me', visibility='restricted', read_teams=[], read_users=['user-123']
        ),
    )
    try:
        resp = client.get('/api/v1/collections', params={'user': 'user-123'}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert {c['slug'] for c in resp.json()} == {'public-docs', 'shared-with-me'}
    finally:
        _cleanup()


def test_collections_public_field_reflects_visibility_not_empty_read_teams():
    """Key regression test: a `restricted` collection with empty
    `read_teams` used to be indistinguishable from a public one (the old
    "empty read_teams = public" sentinel). Now `public` in the response
    reflects `visibility` directly, so this one must report `public: False`
    even though `read_teams` is empty -- it's only visible here because
    `read_users` grants it.
    """
    _seed(
        make_collection(
            slug='shared-with-me', name='Shared With Me', visibility='restricted', read_teams=[], read_users=['user-123']
        )
    )
    try:
        resp = client.get('/api/v1/collections', params={'user': 'user-123'}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body == [{'slug': 'shared-with-me', 'name': 'Shared With Me', 'description': None, 'public': False}]
    finally:
        _cleanup()
