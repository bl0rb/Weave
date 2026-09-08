"""Read-authority for the Collections registry (`collections` table --
app/models/models.py's `Collection`, a mirror Weave-Knowledge itself
periodically syncs from Weave-Ingest's own canonical registry, see that
model's docstring and the chunk-store contract's "Tabelle `collections`"
section).

Weave-Retrieval owns none of these rows -- it never writes a single one --
but the Collections contract (point 4) still names it the READ AUTHORITY
for "which collections may team X read": Weave-Ingest and Weave-Knowledge
both stop at storing/mirroring `read_teams`, and it is this module,
`readable_collections()`, that is the one place in the whole system that
actually evaluates what that field means for a given team. GET
/api/v1/collections (app/api/collections.py) is the HTTP surface built on
top of it; app/services/search.py's `allowed_collections` enforcement is a
separate, independent HARD boundary that does not call through here at
all -- a caller (Weave-Runtime, ultimately Weave-API) is expected to call
this endpoint first to learn what a team may read, then pass that same list
back as `SearchRequest.allowed_collections` on the actual search call.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Collection


def readable_collections(db: Session, team: str | None = None, *, teams: list[str] | None = None) -> list[Collection]:
    """Every `Collection` row `team` is allowed to read: every PUBLIC
    collection (`read_teams == []`, the contract's own "readable by
    everyone" sentinel -- see `Collection.read_teams`'s docstring) plus,
    when `team` is given, every collection whose `read_teams` names it
    explicitly. `team=None` (no team context at all -- an anonymous or
    otherwise unscoped caller) returns only the public ones, same as
    passing a team that happens to match none of the team-restricted rows.

    `read_teams` is a JSON column (see app/models/models.py's Collection),
    so "does it list `team`" can't be expressed as a single portable SQL
    predicate across both dialects the way apply_filters()'s plain-column
    `allowed_teams`/`allowed_collections` checks can (see
    app/services/search.py for that contrast) -- filtered in Python instead,
    over every registry row. The `collections` table is expected to stay
    small (one row per named collection, not per document/chunk), so unlike
    apply_filters() this is not a hot, high-cardinality path that would need
    to push the check down into SQL.
    """
    memberships = set(teams if teams is not None else ([team] if team else []))
    collections = db.execute(select(Collection)).scalars().all()
    return [
        collection
        for collection in collections
        if not collection.read_teams or memberships.intersection(collection.read_teams)
    ]
