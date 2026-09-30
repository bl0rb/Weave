"""Response shapes for GET /api/v1/collections and GET
/api/v1/releases/{release_id}/collection (app/api/collections.py)."""

from pydantic import BaseModel


class CollectionOut(BaseModel):
    slug: str
    name: str
    description: str | None = None
    # Derived from `Collection.visibility` (never exposed directly): `True`
    # exactly when `visibility == 'public'` -- see
    # app/services/collections.py:readable_collections(). A caller has no
    # legitimate use for the raw team-slug/user-id ACLs of a collection it
    # can read for a DIFFERENT reason (team or user membership), only for
    # whether reading it required membership at all.
    public: bool


class ReleaseCollectionOut(BaseModel):
    # The space a released document currently belongs to, `None` for a
    # document without one -- lets Weave-API check a caller's read access
    # before proxying that release's images (ADR 0008).
    collection: str | None = None
