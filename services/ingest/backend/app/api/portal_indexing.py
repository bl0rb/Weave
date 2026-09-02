from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select

from app.api.deps import get_current_user
from app.api.routes import _apply_visible_filter
from app.database.session import get_db
from app.models.models import DocumentRelease, Job, User
from app.schemas.indexing import PortalIndexingItem, PortalIndexingResponse
from app.schemas.portal import PortalReleaseSummary
from app.services.indexing_status import ReleaseReference, fetch_indexing_status

router = APIRouter(prefix='/api/v1/portal')


@router.get('/indexing-status', response_model=PortalIndexingResponse)
def indexing_status(
    response: Response,
    job_id: list[UUID] = Query(min_length=1, max_length=50),
    db=Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalIndexingResponse:
    response.headers['Cache-Control'] = 'no-store'
    # The same visibility boundary as document/review/activity routes,
    # evaluated afresh on every poll. Hidden and nonexistent IDs are both
    # omitted; protected jobs are excluded even for administrators. Browser
    # input can select jobs, never supply a release/hash or an upstream URL.
    query = select(
        Job.id.label('job_id'), DocumentRelease.id.label('release_id'),
        DocumentRelease.markdown_sha256, DocumentRelease.status,
        DocumentRelease.created_at,
    ).select_from(Job).outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id).where(
        Job.id.in_([str(value) for value in job_id]), Job.password_hash.is_(None),
    )
    rows = db.execute(_apply_visible_filter(query, user)).all()
    references = [ReleaseReference(row.job_id, row.release_id, row.markdown_sha256) for row in rows if row.release_id]
    statuses = fetch_indexing_status(references)
    return PortalIndexingResponse(items=[PortalIndexingItem(
        job_id=row.job_id,
        release=PortalReleaseSummary(id=row.release_id, status=row.status, created_at=row.created_at) if row.release_id else None,
        indexing=statuses.get(row.job_id),
    ) for row in rows])
