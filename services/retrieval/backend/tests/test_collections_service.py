"""Tests for app.services.collections.readable_collections() -- the
Collections contract's read-authority function (point 4): which registry
rows a given team may read, distinguishing PUBLIC collections
(`read_teams == []`) from team-restricted ones.

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
        db.add(make_collection(slug='eng-docs', read_teams=['Engineering']))
        db.commit()

        assert _slugs(readable_collections(db, None)) == {'public-docs'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_excludes_team_restricted_for_non_member():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='eng-docs', read_teams=['Engineering']))
        db.commit()

        assert _slugs(readable_collections(db, 'Kundenservice')) == set()
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_readable_collections_includes_team_restricted_for_member():
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='eng-docs', read_teams=['Engineering', 'Platform']))
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
        db.add(make_collection(slug='eng-docs', read_teams=['Engineering']))
        db.add(make_collection(slug='support-docs', read_teams=['Kundenservice']))
        db.commit()

        assert _slugs(readable_collections(db, 'Engineering')) == {'public-docs', 'eng-docs'}
        assert _slugs(readable_collections(db, 'Kundenservice')) == {'public-docs', 'support-docs'}
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()
