from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import get_current_user, require_admin
from app.api.routes import _apply_visible_filter
from app.database.session import get_db
from app.models.models import DocumentRelease, Job, User
from app.schemas.indexing import PortalIndexingItem, PortalIndexingResponse
from app.schemas.portal import PortalReleaseSummary
from app.services.indexing_status import ReleaseReference, fetch_indexing_status, fetch_indexing_diagnostics

router = APIRouter(prefix='/api/v1/portal')


def _released_sha256(payload: dict | None, fallback: str) -> str:
    # Knowledge verifies the bytes it downloads against payload['markdown_sha256'],
    # which is hashed AFTER image links are rewritten to absolute release URLs.
    # DocumentRelease.markdown_sha256 keeps the pre-rewrite digest for the
    # portal's optimistic-concurrency check, so comparing it here would report
    # 'mismatch' for every correctly indexed release that contains an image.
    released = payload.get('markdown_sha256') if isinstance(payload, dict) else None
    return released if isinstance(released, str) and released else fallback


@router.get('/documents/{job_id}/indexing-diagnostics')
def indexing_diagnostics(
    job_id: UUID, response: Response, db=Depends(get_db), user: User = Depends(require_admin),
) -> dict:
    job = db.get(Job, str(job_id))
    if job is None or job.password_hash is not None:
        raise HTTPException(404, 'Document not found')
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    if release is None:
        raise HTTPException(409, 'Document has not been released')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Disposition'] = f'attachment; filename="indexing-diagnostics-{job_id}.json"'
    return {
        'format_version': 1, 'generated_at': datetime.now(timezone.utc), 'job_id': job.id,
        'release': {
            'id': release.id, 'status': release.status, 'delivery_attempts': release.attempts,
            'created_at': release.created_at, 'updated_at': release.updated_at,
            'next_attempt_at': release.next_attempt_at,
        },
        'indexing': fetch_indexing_diagnostics(ReleaseReference(job.id, release.id, _released_sha256(release.payload, release.markdown_sha256))),
        'scope': 'Stored publication/indexing diagnostics; Kubernetes pod logs are not included. Raw errors and document contents are omitted.',
    }


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
        DocumentRelease.markdown_sha256, DocumentRelease.payload, DocumentRelease.status,
        DocumentRelease.created_at,
    ).select_from(Job).outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id).where(
        Job.id.in_([str(value) for value in job_id]), Job.password_hash.is_(None),
    )
    rows = db.execute(_apply_visible_filter(query, user, db=db)).all()
    references = [ReleaseReference(row.job_id, row.release_id, _released_sha256(row.payload, row.markdown_sha256)) for row in rows if row.release_id]
    statuses = fetch_indexing_status(references)
    return PortalIndexingResponse(items=[PortalIndexingItem(
        job_id=row.job_id,
        release=PortalReleaseSummary(id=row.release_id, status=row.status, created_at=row.created_at) if row.release_id else None,
        indexing=statuses.get(row.job_id),
    ) for row in rows])
