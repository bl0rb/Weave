from datetime import datetime

from pydantic import BaseModel


class CollectionSummary(BaseModel):
    """One row of GET /api/v1/collections -- an operations/debug surface
    over this service's own `collections` registry mirror (see
    app/models/models.py's Collection docstring), NOT the authority on
    `read_teams` -- Weave-Ingest is (see contracts/chunk-store.md's
    Collections section). `document_count` is computed at request time
    (COUNT of `documents.collection_slug == slug`), not a stored column, so
    it is always current as of this call.
    """

    slug: str
    name: str
    description: str | None = None
    read_teams: list[str] = []
    synced_at: datetime
    document_count: int


class CollectionListResponse(BaseModel):
    items: list[CollectionSummary]
    total: int
