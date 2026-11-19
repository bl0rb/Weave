"""POST /api/v1/memory -- condensed knowledge for overview questions
(ADR-0009). Service-token protected like /search; the read path and its
permission boundary live in app/services/memory.py."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import require_service_token
from app.core.db import get_db
from app.schemas.memory import MemoryRequest, MemoryResponse
from app.schemas.search import SearchRequest
from app.services.memory import memory_search
from app.services.search import refresh_control_plane, resolve_final_k, resolve_top_k

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_service_token)])


@router.post('/memory', response_model=MemoryResponse)
def memory(request: MemoryRequest, db: Session = Depends(get_db)) -> MemoryResponse:
    # Once per request, before validating -- the same order as
    # app/api/search.py; every search below runs with refresh=False.
    refresh_control_plane()
    if request.include_chunks:
        sizes = SearchRequest(query=request.query, top_k=request.top_k, final_k=request.final_k)
        top_k, final_k = resolve_top_k(sizes), resolve_final_k(sizes)
        if final_k > top_k:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'final_k ({final_k}) must not exceed top_k ({top_k})',
            )
    return memory_search(db, request)
