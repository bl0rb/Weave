"""POST /internal/tokens/introspect -- the service-to-service token
introspection surface that makes Weave-API the identity authority for the
rest of the system (README's Einordnung): another service holding the
`INTROSPECTION_SERVICE_TOKEN` can resolve a raw Personal-API-Token to its
owning identity WITHOUT sharing this service's `users`/`api_tokens` tables
-- the concrete motivation being a later MCP service that needs to check
"who is this, and are they an admin" against a token it was just handed,
without a direct database connection into `weave_api`.

Unversioned and under `/internal`, not `/v1` (see app/main.py's own
`/health` for the same reasoning): this is never called by an end user, and
sits behind a completely different credential (`require_introspection_
service_token`, app/core/auth.py) than every `/v1/*` route's end-user
Bearer token -- a valid Personal-API-Token gets exactly the same 401 as any
other wrong string here, see that dependency's own docstring.

Response shape (deliberately a plain dict, not a declared `response_model`
-- see app/schemas/introspection.py):

- Unknown/expired token, or a disabled user's token: `{"active": False}`,
  HTTP 200 -- NEVER a 404/401 for "no such token", so a caller of this
  endpoint gets no oracle over whether a given token string ever existed
  (`resolve_api_token`'s own non-enumeration discipline, app/core/auth.py,
  carried through here rather than re-introducing a distinction it
  deliberately collapses).
- A valid, non-expired token of an enabled user: `{"active": True,
  "user_id", "username", "team", "is_admin"}` -- `user_id` as `str(uuid)`
  (JSON has no native UUID type), `team`/`is_admin` straight off the User
  row (`app/models/models.py`; `team` is `None` for a user with none
  assigned yet, `is_admin` defaults `False` until some future
  admin-management surface can set it).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_introspection_service_token, resolve_api_token
from app.core.db import get_db
from app.schemas.introspection import TokenIntrospectionRequest

router = APIRouter(prefix='/internal', tags=['internal'], dependencies=[Depends(require_introspection_service_token)])


@router.post('/tokens/introspect')
def introspect_token(body: TokenIntrospectionRequest, db: Session = Depends(get_db)) -> dict:
    user = resolve_api_token(db, body.token)
    if user is None:
        return {'active': False}

    return {
        'active': True,
        'user_id': str(user.id),
        'username': user.username,
        'team': user.team,
        'is_admin': user.is_admin,
    }
