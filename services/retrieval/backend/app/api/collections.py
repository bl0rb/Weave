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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_service_token
from app.core.db import get_db
from app.schemas.collections import CollectionOut
from app.services.collections import readable_collections

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_service_token)])


@router.get('/collections', response_model=list[CollectionOut])
def list_collections(
    team: str | None = Query(default=None),
    teams: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[CollectionOut]:
    # No `team` query param at all -- not merely an empty string -- means
    # "no team context", i.e. only PUBLIC collections come back (see
    # readable_collections()'s own `team=None` semantics). A caller that
    # actually knows the requesting team's slug is expected to always pass
    # it; this is not a way to escalate to "every collection", only ever a
    # narrower view than passing a real team would give.
    collections = readable_collections(db, team, teams=teams)
    return [
        CollectionOut(slug=c.slug, name=c.name, description=c.description, public=not c.read_teams)
        for c in collections
    ]
