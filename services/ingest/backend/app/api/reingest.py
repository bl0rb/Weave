"""Admin re-ingest of every document and connection (app/services/reingest.py).

GET /preview shows what a run would do; POST /runs starts one with the
admin's explicit decision (re-release what was released before, accept
quality warnings, include manually edited documents) and the counts of the
preview that was confirmed -- a changed inventory is refused, like the
orphan cleanup's dry-run count. Progress is polled through GET /runs (no
rate limit, like the backup run list); problem items through
GET /runs/{id}/items.
"""

from __future__ import annotations

from app.api.deps import origin_guard, require_admin
from app.database.session import get_db
from app.models.models import ReingestItem, ReingestRun, User
from app.schemas.reingest import (
    ReingestItemListResponse,
    ReingestItemResponse,
    ReingestPreviewResponse,
    ReingestRunListResponse,
    ReingestRunResponse,
    ReingestStartRequest,
)
from app.services import reingest
from app.services.security import enforce_rate_limit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

router = APIRouter(
    prefix='/api/v1/admin/reingest',
    tags=['reingest-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)

_ERROR_STATUS = {
    'conflict': status.HTTP_409_CONFLICT,
    'unavailable': status.HTTP_503_SERVICE_UNAVAILABLE,
    'invalid': 422,
}


def _run_response(db: Session, run: ReingestRun) -> ReingestRunResponse:
    creator = db.get(User, run.created_by) if run.created_by else None
    return ReingestRunResponse(
        id=run.id,
        status=run.status.value if hasattr(run.status, 'value') else str(run.status),
        # The live set is internal (and large).
        options={key: value for key, value in (run.options or {}).items() if key != 'live_job_ids'},
        counts=reingest.item_counts(db, run.id),
        summary=run.summary,
        error_message=run.error_message,
        created_by=creator.username if creator is not None else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
    )


def _get_run(db: Session, run_id: str) -> ReingestRun:
    run = db.get(ReingestRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Re-Ingest nicht gefunden')
    return run


@router.get('/preview', response_model=ReingestPreviewResponse)
def preview_reingest(
    request: Request,
    include_edited: bool = Query(False),
    rerelease: bool = Query(True),
    db: Session = Depends(get_db),
) -> ReingestPreviewResponse:
    enforce_rate_limit(request)
    active = reingest.active_run(db)
    return ReingestPreviewResponse(
        **reingest.preview(db, include_edited=include_edited, rerelease=rerelease),
        active_run_id=active.id if active is not None else None,
    )


@router.post('/runs', response_model=ReingestRunResponse, status_code=status.HTTP_201_CREATED)
def start_reingest(
    payload: ReingestStartRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ReingestRunResponse:
    enforce_rate_limit(request)
    try:
        run = reingest.start_run(db, admin, **payload.model_dump())
    except reingest.ReingestError as exc:
        db.rollback()
        raise HTTPException(status_code=_ERROR_STATUS.get(exc.code, status.HTTP_409_CONFLICT), detail=exc.detail) from exc
    reingest.nudge(run.id)
    return _run_response(db, run)


@router.get('/runs', response_model=ReingestRunListResponse)
def list_reingest_runs(limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)) -> ReingestRunListResponse:
    runs = db.scalars(select(ReingestRun).order_by(ReingestRun.created_at.desc()).limit(limit)).all()
    return ReingestRunListResponse(items=[_run_response(db, run) for run in runs])


@router.get('/runs/{run_id}/items', response_model=ReingestItemListResponse)
def list_reingest_items(
    run_id: str,
    status_filter: list[str] | None = Query(None, alias='status'),
    kind: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ReingestItemListResponse:
    _get_run(db, run_id)
    query = select(ReingestItem).where(ReingestItem.run_id == run_id)
    if status_filter:
        query = query.where(ReingestItem.status.in_(status_filter))
    if kind:
        query = query.where(ReingestItem.kind == kind)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    # Failures first: planned skips can be many and must not hide them.
    items = db.scalars(
        query.order_by(case((ReingestItem.status == 'failed', 0), else_=1), ReingestItem.id).offset(offset).limit(limit)
    ).all()
    return ReingestItemListResponse(
        items=[ReingestItemResponse.model_validate(item, from_attributes=True) for item in items], total=total
    )


@router.post('/runs/{run_id}/cancel', response_model=ReingestRunResponse)
def cancel_reingest(run_id: str, request: Request, db: Session = Depends(get_db)) -> ReingestRunResponse:
    enforce_rate_limit(request)
    run = reingest.cancel_run(db, _get_run(db, run_id))
    reingest.nudge(run.id)
    return _run_response(db, run)
