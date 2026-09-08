"""GET /v1/collections -- the collections THIS authenticated caller may
read, resolved by asking Weave-Retrieval (the Collections read-authority,
Vertrag item 4) about the caller's own team via
app/services/retrieval_client.py. Behind the same auth+ratelimit dependency
as every other authenticated route (app/core/ratelimit.py's
enforce_rate_limit).

`team` is always `user.team` off the authenticated caller's own User row
(app/models/models.py) -- there is no request parameter a caller could use
to ask for a DIFFERENT team's readable collections; this route answers
"what can I read", never "what can team X read".
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.services.retrieval_client import RetrievalClientError, list_collections

router = APIRouter(prefix='/v1', tags=['collections'])


@router.get('/collections')
def get_collections(user: User = Depends(enforce_rate_limit)) -> list:
    try:
        return list_collections(team=user.effective_teams)
    except RetrievalClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
