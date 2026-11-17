"""GET /v1/sources/passage?document_id=<uuid>&chunk_id=<int>&context=<0..3>
-- the cited text passage behind a chat source ("Textstelle öffnen"): the
target chunk plus up to `context` neighbouring chunks of the same document,
proxied from Weave-Retrieval's POST /api/v1/passage
(app/services/retrieval_client.py's `passage`).

Same read authority as POST /v1/search and the chat's own sources: the
scope comes from the caller's identity only -- the collections Weave-
Retrieval says `user.effective_teams` (and the caller's Weave-Ingest
subject) may read, plus the `"__none__"` sentinel so legacy documents
without a collection stay reachable, gated by `allowed_teams` exactly as in
search. There is no parameter a caller could use to widen it.

A malformed id, a passage outside the caller's scope, an unknown document
or chunk and a superseded version all answer the same 404 -- no hint
whether a document exists. Weave-Retrieval unreachable (or failing) is 502.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.schemas.sources import PassageResponse
from app.services.ingest_identity import ingest_subject
from app.services.retrieval_client import RetrievalClientError, list_collections, passage

router = APIRouter(prefix='/v1', tags=['sources'])

# Weave-Retrieval's NO_COLLECTION_SENTINEL: documents without a collection
# (pre-Collections legacy), still team-gated via `allowed_teams`.
NO_COLLECTION_SENTINEL = '__none__'
_MAX_CHUNK_ID = 2**63 - 1


@router.get('/sources/passage', response_model=PassageResponse)
def get_passage(
    document_id: str,
    chunk_id: str,
    context: int = Query(default=1, ge=0, le=3),
    user: User = Depends(enforce_rate_limit),
) -> PassageResponse:
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Passage not found')
    try:
        parsed_document_id = str(uuid.UUID(document_id))
        parsed_chunk_id = int(chunk_id)
    except ValueError:
        raise not_found from None
    if not 0 <= parsed_chunk_id <= _MAX_CHUNK_ID:
        raise not_found

    teams = user.effective_teams
    try:
        readable = [
            entry['slug']
            for entry in list_collections(team=teams, user=ingest_subject(user.oidc_subject))
            if isinstance(entry, dict) and isinstance(entry.get('slug'), str)
        ]
        body = passage(
            document_id=parsed_document_id,
            chunk_id=parsed_chunk_id,
            context=context,
            allowed_collections=[*readable, NO_COLLECTION_SENTINEL],
            allowed_teams=teams,
        )
    except RetrievalClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if body is None:
        raise not_found

    try:
        return PassageResponse.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail='Weave-Retrieval returned an invalid passage'
        ) from exc
