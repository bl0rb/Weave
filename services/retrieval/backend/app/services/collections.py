"""Read-authority for the Collections registry (`collections` table --
app/models/models.py's `Collection`, a mirror Weave-Knowledge itself
periodically syncs from Weave-Ingest's own canonical registry, see that
model's docstring and the chunk-store contract's "Tabelle `collections`"
section).

Weave-Retrieval owns none of these rows -- it never writes a single one --
but the Collections contract (point 4) still names it the READ AUTHORITY
for "which collections may team X read": Weave-Ingest and Weave-Knowledge
both stop at storing/mirroring `visibility`/`read_teams`/`read_users`, and
it is this module, `readable_collections()`, that is the one place in the
whole system that actually evaluates what those fields mean for a given
caller. GET /api/v1/collections (app/api/collections.py) is the HTTP surface built on
top of it; app/services/search.py's `allowed_collections` enforcement is a
separate, independent HARD boundary that does not call through here at
all -- a caller (Weave-Runtime, ultimately Weave-API) is expected to call
this endpoint first to learn what a team may read, then pass that same list
back as `SearchRequest.allowed_collections` on the actual search call.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Collection


def readable_collections(
    db: Session, team: str | None = None, *, teams: list[str] | None = None, user: str | None = None
) -> list[Collection]:
    """Every `Collection` row the caller is allowed to read, per the
    contract's three-part rule: a collection is readable when it is PUBLIC
    (`visibility == 'public'`, the contract's own authoritative
    public/restricted flag -- see `Collection.visibility`'s docstring), OR
    the caller's team memberships intersect its `read_teams`, OR the
    caller's Weave-Ingest user id is named in its `read_users`. A
    `restricted` collection with both ACLs empty is therefore readable by
    nobody via this rule -- fail-closed, not a "public" sentinel.

    `team`/`teams` give the caller's team memberships (same as before);
    `user` is the caller's Weave-Ingest user id, checked against
    `read_users`. `team=None`/`teams=None` and `user=None` (no context at
    all -- an anonymous or otherwise unscoped caller) return only the public
    rows, same as passing a team/user that matches none of the
    restricted rows' ACLs -- a missing id is never treated as a wildcard
    match against `read_users`.

    `read_teams`/`read_users` are JSON columns (see app/models/models.py's
    Collection), so "does it list `team`/`user`" can't be expressed as a
    single portable SQL predicate across both dialects the way
    apply_filters()'s plain-column `allowed_teams`/`allowed_collections`
    checks can (see app/services/search.py for that contrast) -- filtered in
    Python instead, over every registry row. The `collections` table is
    expected to stay small (one row per named collection, not per
    document/chunk), so unlike apply_filters() this is not a hot,
    high-cardinality path that would need to push the check down into SQL.
    """
    memberships = set(teams if teams is not None else ([team] if team else []))
    collections = db.execute(select(Collection)).scalars().all()
    return [
        collection
        for collection in collections
        if collection.visibility == 'public'
        or memberships.intersection(collection.read_teams or [])
        or (user is not None and user in (collection.read_users or []))
    ]
