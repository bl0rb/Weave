"""Response shape for GET /api/v1/collections (app/api/collections.py)."""

from pydantic import BaseModel


class CollectionOut(BaseModel):
    slug: str
    name: str
    description: str | None = None
    # Derived from `Collection.read_teams` (never exposed directly): `True`
    # exactly when `read_teams` is empty, the Collections contract's own
    # "readable by everyone" sentinel -- see
    # app/services/collections.py:readable_collections(). A caller has no
    # legitimate use for the raw team-slug list of a collection it can read
    # for a DIFFERENT reason (team membership), only for whether reading it
    # required team membership at all.
    public: bool
