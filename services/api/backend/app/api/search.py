"""POST /v1/search -- hybrid search over the collections THIS caller may
read, for clients that want hits instead of a bot answer (e.g. the Weave
desktop tray app). Same response shape as Weave-Tools' REST `search`.

The scope comes from the caller's identity only, exactly like
GET /v1/collections: the readable collections are asked of Weave-Retrieval
for `user.effective_teams`, and `collection` can only narrow that set. A
`collection` outside it returns an empty result -- no error, no hint that it
exists.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.schemas.search import (
    SearchHit,
    SearchRequest,
    SearchResponse,
    SearchScores,
    SearchSource,
    SearchTrace,
)
from app.services.ingest_identity import ingest_subject
from app.services.retrieval_client import RetrievalClientError, RetrievalRequestRejected, list_collections, search

router = APIRouter(prefix='/v1', tags=['search'])


def _format_page(page_start: int | None, page_end: int | None) -> str | None:
    if page_start is None:
        return None
    if page_end is None or page_end == page_start:
        return str(page_start)
    return f'{page_start}-{page_end}'


def _parse_scores(raw: object) -> SearchScores:
    if not isinstance(raw, dict):
        return SearchScores()
    try:
        return SearchScores.model_validate(raw)
    except ValidationError:
        return SearchScores()


def _parse_trace(raw: object) -> SearchTrace | None:
    """Best-effort copy of Weave-Retrieval's trace; an older/malformed one
    yields `None` rather than failing the search."""
    if not isinstance(raw, dict):
        return None
    try:
        return SearchTrace.model_validate(raw)
    except ValidationError:
        return None


@router.post('/search', response_model=SearchResponse)
def post_search(payload: SearchRequest, user: User = Depends(enforce_rate_limit)) -> SearchResponse:
    teams = user.effective_teams
    try:
        readable = [
            entry['slug']
            for entry in list_collections(team=teams, user=ingest_subject(user.oidc_subject))
            if isinstance(entry, dict) and isinstance(entry.get('slug'), str)
        ]
        if payload.collection is not None:
            if payload.collection not in readable:
                return SearchResponse(query=payload.query, results=[])
            readable = [payload.collection]
        if not readable:
            return SearchResponse(query=payload.query, results=[])
        body = search(
            query=payload.query,
            allowed_collections=readable,
            allowed_teams=teams,
            top_k=payload.top_k,
            final_k=payload.final_k,
        )
    except RetrievalRequestRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except RetrievalClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    hits = body.get('results') or []
    return SearchResponse(
        query=payload.query,
        results=[
            SearchHit(
                rank=hit.get('rank') or position,
                text=hit['text'],
                scores=_parse_scores(hit.get('scores')),
                source=SearchSource(
                    document=hit.get('original_filename') or hit['document_id'],
                    document_id=hit['document_id'],
                    chunk_id=hit['chunk_id'],
                    page=_format_page(hit.get('page_start'), hit.get('page_end')),
                    collection=hit.get('collection'),
                    source_url=hit.get('source_url'),
                    heading_path=hit.get('heading_path') or [],
                    document_version=hit.get('document_version'),
                    page_start=hit.get('page_start'),
                    page_end=hit.get('page_end'),
                    chunk_index=hit.get('chunk_index'),
                    title=hit.get('title'),
                    language=hit.get('language'),
                    document_type=hit.get('document_type'),
                    tags=hit.get('tags') or [],
                    source_kind=hit.get('source_kind'),
                    uploaded_at=hit.get('uploaded_at'),
                    fetched_at=hit.get('fetched_at'),
                    legal_status=hit.get('legal_status'),
                ),
            )
            for position, hit in enumerate(hits, start=1)
        ],
        trace=_parse_trace(body.get('trace')),
    )
