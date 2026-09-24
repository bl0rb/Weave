"""Tests for app.services.collections.readable_collections() -- the
Collections contract's read-authority function (point 4): which registry
rows a given caller may read, distinguishing PUBLIC collections
(`visibility == 'public'`) from RESTRICTED ones gated by `read_teams` and/or
`read_users`.

Runs against the pytest suite's own sqlite test.db (see tests/conftest.py),
same pattern as tests/test_search_service.py: each test seeds its own rows
via make_collection() and cleans up in a finally block.
"""

from app.models.models import Collection
from app.services.collections import readable_collections
from tests.conftest import TestingSessionLocal, make_collection


def _slugs(collections: list[Collection]) -> set[str]:
    return {c.slug for c in collections}


def test_readable_collections_includes_public_collections_for_any_team():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='public-docs', read_teams=[]))
        db.commit()

        assert _slugs(readable_collections(db, 'Kundenservice')) == {'public-docs'}
        assert _slugs(readable_collections(db, 'AnyOtherTeam')) == {'public-docs'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_includes_public_for_none_team():
    """`team=None` -- no team context at all -- still sees every PUBLIC
    collection, just none of the team-restricted ones (see the next test)."""
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='public-docs', read_teams=[]))
        db.add(make_collection(slug='eng-docs', visibility='restricted', read_teams=['Engineering']))
        db.commit()

        assert _slugs(readable_collections(db, None)) == {'public-docs'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_excludes_team_restricted_for_non_member():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='eng-docs', visibility='restricted', read_teams=['Engineering']))
        db.commit()

        assert _slugs(readable_collections(db, 'Kundenservice')) == set()
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_includes_team_restricted_for_member():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='eng-docs', visibility='restricted', read_teams=['Engineering', 'Platform']))
        db.commit()

        assert _slugs(readable_collections(db, 'Engineering')) == {'eng-docs'}
        assert _slugs(readable_collections(db, 'Platform')) == {'eng-docs'}
        assert _slugs(readable_collections(db, 'Kundenservice')) == set()
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_mixes_public_and_team_restricted():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='public-docs', read_teams=[]))
        db.add(make_collection(slug='eng-docs', visibility='restricted', read_teams=['Engineering']))
        db.add(make_collection(slug='support-docs', visibility='restricted', read_teams=['Kundenservice']))
        db.commit()

        assert _slugs(readable_collections(db, 'Engineering')) == {'public-docs', 'eng-docs'}
        assert _slugs(readable_collections(db, 'Kundenservice')) == {'public-docs', 'support-docs'}
        assert _slugs(readable_collections(db, teams=['Engineering', 'Kundenservice'])) == {'public-docs', 'eng-docs', 'support-docs'}
        assert _slugs(readable_collections(db, 'Engineering', teams=[])) == {'public-docs'}
        assert _slugs(readable_collections(db, teams=['Unknown'])) == {'public-docs'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_includes_restricted_collection_via_matching_user():
    """A `restricted` collection with no team match is still readable when
    the caller's user id is named in `read_users` -- the person-level ACL
    alongside `read_teams`."""
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='shared-with-me', visibility='restricted', read_teams=[], read_users=['user-123']))
        db.commit()

        assert _slugs(readable_collections(db, 'Kundenservice', user='user-123')) == {'shared-with-me'}
        assert _slugs(readable_collections(db, user='user-123')) == {'shared-with-me'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_restricted_with_no_acl_is_readable_by_nobody():
    """Fail-closed: a `restricted` collection with BOTH `read_teams` and
    `read_users` empty is not the old "empty read_teams = public" sentinel
    any more -- it is readable by nobody, not even a caller with a team and
    a user id."""
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='locked-out', visibility='restricted', read_teams=[], read_users=[]))
        db.commit()

        assert _slugs(readable_collections(db, 'Kundenservice', user='user-123')) == set()
        assert _slugs(readable_collections(db)) == set()
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_public_ignores_teams_and_users():
    """`visibility == 'public'` is readable regardless of `read_teams`,
    `read_users`, or the caller's own team/user -- even a totally unrelated
    one."""
    db = TestingSessionLocal()
    try:
        db.add(
            make_collection(
                slug='public-with-acls',
                visibility='public',
                read_teams=['Engineering'],
                read_users=['user-123'],
            )
        )
        db.commit()

        assert _slugs(readable_collections(db, 'Unrelated', user='someone-else')) == {'public-with-acls'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_no_user_does_not_wildcard_match_read_users():
    """`user=None` never matches a `read_users`-only grant -- a missing
    caller id is not a wildcard."""
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='shared-with-someone', visibility='restricted', read_teams=[], read_users=['user-123']))
        db.commit()

        assert _slugs(readable_collections(db)) == set()
        assert _slugs(readable_collections(db, 'Kundenservice')) == set()
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()
