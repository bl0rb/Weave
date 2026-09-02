from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.api.routes import _apply_visible_filter
from app.database.session import get_db
from app.models.models import Collection, DocumentRelease, ImportRun, ImportRunStatus, Job, JobStatus, User
from app.schemas.portal_management import (
    PortalActivityCounts,
    PortalActivityItem,
    PortalActivityListResponse,
    PortalCollectionItem,
    PortalCollectionListResponse,
    PortalManagementOwner,
)


router = APIRouter(prefix='/api/v1/portal')

_PAGE_LIMIT_MAX = 500


def _collection_id_expr():
    """The established job -> collection association, kept in JSON for now."""
    return Job.processing_info['settings']['collection_id'].as_string()


def _quality_expressions():
    return (
        Job.processing_info['execution']['quality_gate']['grade'].as_string(),
        Job.processing_info['execution']['quality_gate']['recommendation'].as_string(),
    )


def _reviewable_job_expression(quality_grade, quality_recommendation):
    """SQL equivalent of portal.py's lightweight release eligibility.

    A reviewable document is a FINISHED job with non-empty result markdown,
    no password protection, a missing or FINISHED import run, no existing
    DocumentRelease, and a quality recommendation/grade that does not block
    release. This deliberately does not read or validate markdown content;
    canonical validation remains in the detail/release flow.
    """
    return and_(
        Job.status == JobStatus.FINISHED,
        func.length(func.coalesce(Job.result_markdown, '')) > 0,
        Job.password_hash.is_(None),
        or_(Job.import_run_id.is_(None), ImportRun.status == ImportRunStatus.FINISHED),
        DocumentRelease.id.is_(None),
        or_(quality_recommendation.is_(None), func.lower(quality_recommendation) != 'block'),
        or_(quality_grade.is_(None), func.lower(quality_grade) != 'c'),
    )


def _clean_query(value: str | None) -> str | None:
    cleaned = value.strip().lower() if value else ''
    return cleaned or None


def _build_admin_collections_query(q: str | None = None):
    """Build the collection inventory statement for SQLite or PostgreSQL.

    Jobs are aggregated before Collection is joined. The only GROUP BY is on
    the extracted collection-id text, so PostgreSQL never has to compare the
    Collection.read_teams JSON value (or any other JSON column).
    """
    quality_grade, quality_recommendation = _quality_expressions()
    reviewable = _reviewable_job_expression(quality_grade, quality_recommendation)
    collection_id_expr = _collection_id_expr()
    aggregates = (
        select(
            collection_id_expr.label('collection_id'),
            func.count(Job.id).label('document_count'),
            func.coalesce(func.sum(case((Job.status == JobStatus.PENDING, 1), else_=0)), 0).label('pending_count'),
            func.coalesce(func.sum(case((Job.status == JobStatus.RUNNING, 1), else_=0)), 0).label('running_count'),
            func.coalesce(func.sum(case((reviewable, 1), else_=0)), 0).label('review_count'),
            func.coalesce(func.sum(case((Job.status == JobStatus.FAILED, 1), else_=0)), 0).label('failed_count'),
            func.coalesce(func.sum(case((DocumentRelease.id.is_not(None), 1), else_=0)), 0).label('released_count'),
        )
        .select_from(Job)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
        .outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id)
        .where(Job.benchmark_run_id.is_(None))
        .group_by(collection_id_expr)
        .subquery('job_collection_aggregates')
    )
    query = (
        select(
            Collection.id,
            Collection.slug,
            Collection.name,
            Collection.description,
            Collection.read_teams,
            User.id,
            User.username,
            aggregates.c.document_count,
            aggregates.c.pending_count,
            aggregates.c.running_count,
            aggregates.c.review_count,
            aggregates.c.failed_count,
            aggregates.c.released_count,
            Collection.created_at,
            Collection.updated_at,
        )
        .select_from(Collection)
        .outerjoin(aggregates, aggregates.c.collection_id == Collection.id)
        .outerjoin(User, User.id == Collection.owner_id)
    )
    if q:
        pattern = f'%{q}%'
        query = query.where(or_(func.lower(Collection.name).like(pattern), func.lower(Collection.description).like(pattern)))
    return query


@router.get('/admin/collections', response_model=PortalCollectionListResponse)
def list_admin_collections(
    q: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=_PAGE_LIMIT_MAX),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> PortalCollectionListResponse:
    """Admin collection inventory with all counts computed in one aggregate.

    The LEFT JOIN keeps collections with no jobs in the result. Job rows are
    joined through processing_info.settings.collection_id and benchmark jobs
    are excluded from every aggregate. `released_count` means a
    DocumentRelease row exists, regardless of its delivery status.
    """
    del user  # The dependency performs the authorization; the query is admin-wide.
    cleaned_q = _clean_query(q)
    query = _build_admin_collections_query(cleaned_q)

    total_query = select(func.count(Collection.id))
    if cleaned_q:
        pattern = f'%{cleaned_q}%'
        total_query = total_query.where(
            or_(func.lower(Collection.name).like(pattern), func.lower(Collection.description).like(pattern))
        )
    total = int(db.scalar(total_query) or 0)
    rows = db.execute(query.order_by(Collection.created_at.desc(), Collection.id.desc()).offset(offset).limit(limit)).all()

    items = []
    for row in rows:
        owner = PortalManagementOwner(id=row[5], username=row[6]) if row[5] is not None else None
        items.append(
            PortalCollectionItem(
                collection_id=row[0],
                slug=row[1],
                name=row[2],
                description=row[3],
                read_teams=list(row[4] or []),
                owner=owner,
                document_count=int(row[7] or 0),
                pending_count=int(row[8] or 0),
                running_count=int(row[9] or 0),
                review_count=int(row[10] or 0),
                failed_count=int(row[11] or 0),
                released_count=int(row[12] or 0),
                created_at=row[13],
                updated_at=row[14],
            )
        )
    return PortalCollectionListResponse(items=items, total=total)


@router.get('/activity', response_model=PortalActivityListResponse)
def list_activity(
    q: str | None = None,
    status_filter: JobStatus | None = Query(default=None, alias='status'),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=_PAGE_LIMIT_MAX),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortalActivityListResponse:
    """Visible job activity as a blob-free SQL projection.

    Counts use the same visible, benchmark-free, filename-filtered job set as
    the list, while intentionally ignoring the selected status filter.
    Legacy jobs without a resolvable collection remain visible to admins and
    have null collection fields.
    """
    cleaned_q = _clean_query(q)
    collection_id_expr = _collection_id_expr()
    quality_grade, quality_recommendation = _quality_expressions()

    def apply_activity_filters(statement, *, include_status: bool):
        statement = _apply_visible_filter(statement, user)
        if cleaned_q:
            statement = statement.where(func.lower(Job.original_filename).like(f'%{cleaned_q}%'))
        if include_status and status_filter is not None:
            statement = statement.where(Job.status == status_filter)
        return statement

    count_query = apply_activity_filters(select(Job.status, func.count(Job.id)).group_by(Job.status), include_status=False)
    status_counts = {status.value.lower(): 0 for status in JobStatus}
    for job_status, count in db.execute(count_query).all():
        value = job_status.value if hasattr(job_status, 'value') else str(job_status)
        status_counts[value.lower()] = int(count)

    total_query = apply_activity_filters(select(func.count(Job.id)), include_status=True)
    total = int(db.scalar(total_query) or 0)

    query = apply_activity_filters(
        select(
            Job.id,
            Job.original_filename,
            Job.status,
            Job.created_at,
            Job.updated_at,
            Collection.id,
            Collection.name,
            Job.import_run_id,
            ImportRun.status,
            DocumentRelease.status,
            quality_grade,
            quality_recommendation,
        )
        .outerjoin(Collection, collection_id_expr == Collection.id)
        .outerjoin(ImportRun, ImportRun.id == Job.import_run_id)
        .outerjoin(DocumentRelease, DocumentRelease.job_id == Job.id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .offset(offset)
        .limit(limit),
        include_status=True,
    )
    rows = db.execute(query).all()

    items = []
    for row in rows:
        job_status = row[2].value if hasattr(row[2], 'value') else str(row[2])
        import_status = row[8].value if hasattr(row[8], 'value') else row[8]
        items.append(
            PortalActivityItem(
                id=row[0],
                original_filename=row[1],
                status=job_status,
                created_at=row[3],
                updated_at=row[4],
                collection_id=row[5],
                collection_name=row[6],
                import_run_id=row[7],
                import_status=import_status,
                release_status=row[9],
                quality_grade=row[10],
                quality_recommendation=row[11],
            )
        )
    return PortalActivityListResponse(
        items=items,
        total=total,
        counts=PortalActivityCounts(**status_counts),
    )
