"""POST /api/v1/search -- the hybrid-search endpoint.

Every route on this router sits behind `require_service_token` (see
app/core/auth.py), applied once at the router level below rather than
per-route, since this router has exactly one endpoint today and any future
addition to it is just as much an internal search-pipeline surface.

The actual pipeline (parallel pgvector KNN + tsvector fulltext search
against contracts/chunk-store.md's `chunks` table,
Reciprocal Rank Fusion, optional reranking via settings.rerank_provider,
metadata access-control filtering) lives in app/services/search.py -- this
module is just the HTTP shell around it: request validation, the
`final_k > top_k` guard below, and turning `search()`'s `(results, trace)`
pair into a SearchResponse. That guard still matters even though
`search()` itself also trims to `final_k` after reranking: a reranker can
narrow a candidate pool, it can never be asked to produce MORE final
results than the `top_k` candidates it was actually given, so this is
rejected as a 400 up front rather than silently clamped.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import require_service_token
from app.core.db import get_db
from app.schemas.search import SearchRequest, SearchResponse
from app.services.search import resolve_final_k, resolve_top_k
from app.services.search import search as run_search

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_service_token)])


@router.post('/search', response_model=SearchResponse)
def search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    # 422 for a structurally invalid body (e.g. an empty `query`) is already
    # handled by FastAPI/pydantic before this function body ever runs --
    # SearchRequest.query's `min_length=1` (app/schemas/search.py) is enough
    # on its own. The one validation rule pydantic can't express (it depends
    # on TWO fields' resolved values, each of which may fall back to a
    # settings default) is checked here instead: a reranker can narrow a
    # candidate set down, it can never be asked to hand back MORE final
    # results than it was given candidates for.
    top_k = resolve_top_k(request)
    final_k = resolve_final_k(request)
    if final_k > top_k:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'final_k ({final_k}) must not exceed top_k ({top_k})',
        )

    results, trace = run_search(db, request)
    return SearchResponse(query=request.query, results=results, trace=trace)
