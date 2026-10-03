"""Paged inventory of every stored document's identity -- job id, space
slug and status, never content -- for Weave-Ingest's 'verwaiste Dokumente
zurückziehen' admin action (its app/services/knowledge_orphans.py). Ingest
compares it with its own jobs and spaces and withdraws whatever nothing
stands behind any more through the regular `document.withdrawn` webhook;
this endpoint itself never changes anything.

Same internal credential as app/api/reindex.py: Ingest already holds it.
Keyset-paged on the unique `source_job_id` so a large corpus is read in
bounded steps instead of one unbounded response.
"""

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.reindex import _require_reindex_token
from app.core.db import get_db
from app.models.models import Document

router = APIRouter(prefix='/api/v1/internal/documents', tags=['internal-inventory'])


@router.get('')
def document_inventory(
    after: str = Query(default='', max_length=36),
    limit: int = Query(default=500, ge=1, le=1000),
    x_weave_reindex_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_reindex_token(x_weave_reindex_token)
    rows = db.execute(
        select(Document.source_job_id, Document.collection_slug, Document.status)
        .where(Document.source_job_id > after)
        .order_by(Document.source_job_id)
        .limit(limit)
    ).all()
    items = [
        {'job_id': job_id, 'collection_slug': slug, 'status': getattr(status, 'value', status)}
        for job_id, slug, status in rows
    ]
    return {'items': items, 'next_after': items[-1]['job_id'] if len(items) == limit else None}
