"""GET /api/v1/collections -- the Collections read-authority endpoint.

Behind `require_service_token` (see app/core/auth.py), exactly like
POST /api/v1/search (app/api/search.py) -- this is a service-to-service
surface, never called directly by an end user. The actual "which
collections may this team read" logic lives in app/services/collections.py;
this module is just the HTTP shell around it, same split as search.py's own
relationship to app/services/search.py.

Callers (Weave-Runtime, ultimately on behalf of Weave-API's gateway) are
expected to call this endpoint to resolve a team's readable collection
slugs, then forward that same list as `SearchRequest.allowed_collections` on
the actual POST /api/v1/search call -- this endpoint does not itself gate
search results, it only answers "what's readable", app/services/search.py's
apply_filters() is the one hard boundary that actually enforces it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_service_token
from app.core.db import get_db
from app.schemas.collections import CollectionOut, ReleaseCollectionOut
from app.services.collections import readable_collections, release_document

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_service_token)])


@router.get('/collections', response_model=list[CollectionOut])
def list_collections(
    team: str | None = Query(default=None),
    teams: list[str] | None = Query(default=None),
    user: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[CollectionOut]:
    # No `team` query param at all -- not merely an empty string -- means
    # "no team context", i.e. only PUBLIC collections (plus any matched via
    # `user`) come back (see readable_collections()'s own `team=None`
    # semantics). A caller that actually knows the requesting team's slug
    # is expected to always pass it; this is not a way to escalate to
    # "every collection", only ever a narrower view than passing a real
    # team would give. `user`, when given, is the caller's Weave-Ingest
    # user id, checked against a restricted collection's `read_users`.
    collections = readable_collections(db, team, teams=teams, user=user)
    return [
        CollectionOut(slug=c.slug, name=c.name, description=c.description, public=c.visibility == 'public')
        for c in collections
    ]


@router.get('/releases/{release_id}/collection', response_model=ReleaseCollectionOut)
def get_release_collection(release_id: str, db: Session = Depends(get_db)) -> ReleaseCollectionOut:
    """The space the document released as `release_id` belongs to -- what
    Weave-API checks against the caller's readable collections (above)
    before proxying that release's images. 404 for an unknown release."""
    document = release_document(db, release_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Release not found')
    return ReleaseCollectionOut(collection=document.collection_slug)
