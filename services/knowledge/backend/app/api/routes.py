from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.models import Collection, Document, DocumentStatus
from app.schemas.collections import CollectionListResponse, CollectionSummary
from app.schemas.documents import DocumentDetail, DocumentListResponse, DocumentSummary

router = APIRouter(prefix='/api/v1')

# Server-side cap on `limit`, same clamp-never-raise discipline Weave-Ingest
# uses for its own list endpoints (e.g. _JOB_LIST_PAGE_LIMIT_MAX in
# app/api/routes.py) -- a caller can ask for fewer rows, never more.
_DOCUMENT_LIST_PAGE_LIMIT_MAX = 200


@router.get('/documents', response_model=DocumentListResponse)
def list_documents(
    db: Session = Depends(get_db),
    status_filter: DocumentStatus | None = Query(default=None, alias='status'),
    team: str | None = None,
    limit: int = Query(default=50, ge=1, le=_DOCUMENT_LIST_PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    query = select(Document)
    if status_filter is not None:
        query = query.where(Document.status == status_filter)
    if team is not None:
        query = query.where(Document.team == team)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    rows = db.execute(
        query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    return DocumentListResponse(
        items=[DocumentSummary.model_validate(doc) for doc in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get('/documents/{document_id}', response_model=DocumentDetail)
def get_document(document_id: UUID, db: Session = Depends(get_db)) -> DocumentDetail:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail='document not found')
    return DocumentDetail.model_validate(document)


@router.get('/collections', response_model=CollectionListResponse)
def list_collections(db: Session = Depends(get_db)) -> CollectionListResponse:
    """Operations/debug view over this service's own `collections` registry
    mirror (see app/models/models.py's Collection docstring) -- NOT the
    authority on any collection's `read_teams` ACL (Weave-Ingest is; see
    contracts/chunk-store.md's Collections section). Mainly useful to answer
    "did the sync actually run" / "how many documents has this collection
    picked up" without a direct DB connection.
    """
    counts = dict(
        db.execute(
            select(Document.collection_slug, func.count())
            .where(Document.collection_slug.is_not(None))
            .group_by(Document.collection_slug)
        ).all()
    )
    rows = db.execute(select(Collection).order_by(Collection.slug)).scalars().all()
    items = [
        CollectionSummary(
            slug=row.slug,
            name=row.name,
            description=row.description,
            read_teams=row.read_teams,
            synced_at=row.synced_at,
            document_count=counts.get(row.slug, 0),
        )
        for row in rows
    ]
    return CollectionListResponse(items=items, total=len(items))
