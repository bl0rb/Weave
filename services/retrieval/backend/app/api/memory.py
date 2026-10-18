"""POST /api/v1/memory -- condensed knowledge for overview questions
(ADR-0009). Service-token protected like /search; the read path and its
permission boundary live in app/services/memory.py."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_service_token
from app.core.db import get_db
from app.schemas.memory import MemoryRequest, MemoryResponse
from app.services.memory import memory_search

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_service_token)])


@router.post('/memory', response_model=MemoryResponse)
def memory(request: MemoryRequest, db: Session = Depends(get_db)) -> MemoryResponse:
    return memory_search(db, request)
