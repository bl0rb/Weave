"""Knowledge memory admin surface (ADR-0009) for Weave-Ingest's admin UI:
metrics and a manual rebuild. Same shared secret header as the internal
reindex routes (app/api/reindex.py); Ingest proxies both, the browser never
talks to this service directly."""

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy.orm import Session

from app.api.reindex import _require_reindex_token
from app.core.db import get_db
from app.services import memory

router = APIRouter(prefix='/api/v1/internal/memory', tags=['internal-memory'])


@router.get('/status')
def memory_status(
    x_weave_reindex_token: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    _require_reindex_token(x_weave_reindex_token)
    return memory.memory_status(db)


@router.post('/rebuild')
def memory_rebuild(
    collection: str | None = Body(default=None, embed=True),
    x_weave_reindex_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    _require_reindex_token(x_weave_reindex_token)
    return {'queued': memory.request_rebuild(db, collection)}
