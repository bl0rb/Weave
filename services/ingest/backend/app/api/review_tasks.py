"""Quality signals and 'Prüfaufgaben' (review tasks).

Two routers:

- `router_service` -- POST /api/v1/quality/signals: service-to-service
  intake of Weave-API's anonymous quality-signal outbox (feedback,
  unanswered questions), and POST /api/v1/quality/contradictions: Weave-
  Knowledge's possible-contradiction reports. Only the service bearer
  (`require_service_token` in app/api/deps.py) is accepted, never a user
  session. Processing lives in app/services/quality_tasks.py.
- `router` -- /api/v1/portal/review-tasks and /api/v1/portal/quality/summary:
  the knowledge owners' inbox. Registered in app/main.py behind the same
  session + CSRF gate as the other portal routers. A task is visible to
  admins and to owners/members of its collection
  (app/services/collection_access.py); anything else answers 404, never 403,
  so task ids do not leak across spaces.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_service_token
from app.database.session import get_db
from app.models.models import (
    REVIEW_TASK_ACTIVE_STATUSES,
    REVIEW_TASK_CLOSED_STATUSES,
    Collection,
    CollectionRole,
    Job,
    QualitySignal,
    ReviewTask,
    ReviewTaskKind,
    ReviewTaskStatus,
    User,
    UserRole,
)
from app.schemas.review_tasks import (
    ContradictionBatch,
    QualityDocumentStats,
    QualityFeedbackCounts,
    QualitySignalBatch,
    QualitySignalBatchResponse,
    QualitySummaryResponse,
    QualityTaskCounts,
    ReviewTaskCollectionRef,
    ReviewTaskCountResponse,
    ReviewTaskDocumentRef,
    ReviewTaskKindValue,
    ReviewTaskListResponse,
    ReviewTaskOut,
    ReviewTaskUpdate,
    ReviewTaskUserRef,
)
from app.services import document_freshness, quality_tasks
from app.services.collection_access import collection_role, member_collection_filter, role_at_least

router = APIRouter(prefix='/api/v1/portal')
router_service = APIRouter(prefix='/api/v1/quality', dependencies=[Depends(require_service_token)])

_SUMMARY_DAYS = 90
_SUMMARY_DOCUMENTS = 10
_STATUS_ORDER = {'open': 0, 'in_progress': 1, 'done': 2, 'dismissed': 3}
_TASK_NOT_FOUND = 'Review task not found'


@router_service.post('/signals', response_model=QualitySignalBatchResponse)
def receive_quality_signals(payload: QualitySignalBatch, db: Session = Depends(get_db)) -> QualitySignalBatchResponse:
    """Idempotent upsert of a batch of signals by `signal_key`. Malformed
    signals are skipped and not counted in `accepted`; the batch as a whole
    still succeeds so the sender marks it forwarded."""
    accepted = quality_tasks.ingest_signals(db, [(item.signal_key, item.payload) for item in payload.signals])
    db.commit()
    return QualitySignalBatchResponse(accepted=accepted)


@router_service.post('/contradictions', response_model=QualitySignalBatchResponse)
def receive_contradictions(payload: ContradictionBatch, db: Session = Depends(get_db)) -> QualitySignalBatchResponse:
    """Create/update one `contradiction` Prüfaufgabe per unordered document
    pair. Reports naming an unknown space or job are ignored (not counted
    in `accepted`); the batch still succeeds."""
    accepted = quality_tasks.ingest_contradictions(db, [report.model_dump() for report in payload.reports])
    db.commit()
    return QualitySignalBatchResponse(accepted=accepted)


def _visible_collections(db: Session, user: User):
    """SQL predicate on Collection for the spaces whose tasks `user` sees;
    None for admins (everything)."""
    if user.role == UserRole.ADMIN:
        return None
    return member_collection_filter(db, user)


def _can_see(db: Session, collection: Collection, user: User) -> bool:
    return role_at_least(collection_role(db, collection, user), CollectionRole.MEMBER)


def _load_task(db: Session, task_id: str, user: User) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if task is None or not _can_see(db, task.collection, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_TASK_NOT_FOUND)
    return task


def _document(job: Job | None) -> ReviewTaskDocumentRef | None:
    if job is None:
        return None
    return ReviewTaskDocumentRef(job_id=job.id, title=job.original_filename, document_version=job.document_version)


def _out(task: ReviewTask) -> ReviewTaskOut:
    return ReviewTaskOut(
        id=task.id,
        kind=task.kind,
        status=task.status,
        title=task.title,
        collection=ReviewTaskCollectionRef(id=task.collection.id, name=task.collection.name, slug=task.collection.slug),
        document=_document(task.job),
        related_document=_document(task.related_job),
        occurrence_count=task.occurrence_count,
        first_seen_at=task.first_seen_at,
        last_seen_at=task.last_seen_at,
        assignee=ReviewTaskUserRef(id=task.assignee.id, username=task.assignee.username) if task.assignee else None,
        resolved_at=task.resolved_at,
        resolution_note=task.resolution_note,
        details=task.details if isinstance(task.details, dict) else {},
    )


def _scoped(query, db: Session, user: User, collection_id: str | None):
    query = query.join(Collection, Collection.id == ReviewTask.collection_id)
    visible = _visible_collections(db, user)
    if visible is not None:
        query = query.where(visible)
    if collection_id is not None:
        query = query.where(ReviewTask.collection_id == collection_id)
    return query


@router.get('/review-tasks', response_model=ReviewTaskListResponse)
def list_review_tasks(
    status_filter: str = Query('active', alias='status', pattern='^(open|in_progress|done|dismissed|active)$'),
    kind: ReviewTaskKindValue | None = None,
    collection_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewTaskListResponse:
    """Tasks of the caller's spaces, sorted by status (open, in progress,
    done, dismissed), then most recently seen first. `status=active`
    (default) = open + in_progress."""
    statuses = REVIEW_TASK_ACTIVE_STATUSES if status_filter == 'active' else (status_filter,)
    conditions = [ReviewTask.status.in_(statuses)]
    if kind is not None:
        conditions.append(ReviewTask.kind == kind)
    base = _scoped(select(ReviewTask), db, user, collection_id).where(*conditions)
    total = db.scalar(_scoped(select(func.count(ReviewTask.id)), db, user, collection_id).where(*conditions)) or 0
    status_rank = case(_STATUS_ORDER, value=ReviewTask.status, else_=len(_STATUS_ORDER))
    tasks = db.scalars(
        base.options(
            selectinload(ReviewTask.collection), selectinload(ReviewTask.job),
            selectinload(ReviewTask.related_job), selectinload(ReviewTask.assignee),
        )
        .order_by(status_rank, ReviewTask.last_seen_at.desc(), ReviewTask.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ReviewTaskListResponse(items=[_out(task) for task in tasks], total=total, page=page, page_size=page_size)


@router.get('/review-tasks/count', response_model=ReviewTaskCountResponse)
def count_review_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReviewTaskCountResponse:
    """Active (open + in_progress) tasks of the caller's spaces, for the
    sidebar badge."""
    rows = db.execute(
        _scoped(select(ReviewTask.kind, func.count(ReviewTask.id)), db, user, None)
        .where(ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES))
        .group_by(ReviewTask.kind)
    ).all()
    by_kind = {kind.value: 0 for kind in ReviewTaskKind}
    for kind, count in rows:
        by_kind[kind] = int(count)
    return ReviewTaskCountResponse(open=sum(by_kind.values()), by_kind=by_kind)


@router.get('/review-tasks/{task_id}', response_model=ReviewTaskOut)
def get_review_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReviewTaskOut:
    return _out(_load_task(db, task_id, user))


def _confirms_review(db: Session, task: ReviewTask, new_status: str) -> bool:
    """'Erledigt' on an active `review_due` task of the current released
    version means the review was done: it is recorded like POST
    /documents/{id}/review/confirm (last reviewed = now/caller, next due
    date from the space's interval), so the document is no longer overdue.
    'Verwerfen' (dismissed) only silences the reminder for that date."""
    return (
        task.kind == ReviewTaskKind.REVIEW_DUE.value
        and new_status == ReviewTaskStatus.DONE.value
        and task.status in REVIEW_TASK_ACTIVE_STATUSES
        and task.job is not None
        and not document_freshness.has_newer_release(db, task.job)
    )


@router.patch('/review-tasks/{task_id}', response_model=ReviewTaskOut)
def update_review_task(
    task_id: str, payload: ReviewTaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ReviewTaskOut:
    """Assign, take over, close or reopen a task. done/dismissed record
    who resolved it and when; done on a `review_due` task also confirms the
    document's review (`_confirms_review`). A closed task can only be
    reopened while no other active task covers the same cluster (409
    otherwise)."""
    task = _load_task(db, task_id, user)
    fields = payload.model_fields_set
    if 'assignee_id' in fields:
        if payload.assignee_id is None:
            task.assignee_id = None
        else:
            assignee = db.get(User, payload.assignee_id)
            if assignee is None or not assignee.is_active or not _can_see(db, task.collection, assignee):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail='Assignee must be an owner or member of this knowledge space',
                )
            task.assignee_id = assignee.id
    if payload.status is not None and payload.status != task.status:
        if payload.status in REVIEW_TASK_ACTIVE_STATUSES and task.status in REVIEW_TASK_CLOSED_STATUSES:
            quality_tasks.lock_review_tasks(db)
            other = quality_tasks.find_active_task(
                db, collection_id=task.collection_id, kind=task.kind, cluster_key=task.cluster_key
            )
            if other is not None and other.id != task.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='Another active task already covers this topic',
                )
        if _confirms_review(db, task, payload.status):
            document_freshness.confirm_review(db, task.job, task.collection, user)
        quality_tasks.resolve_task(task, payload.status, user_id=user.id)
    if 'resolution_note' in fields:
        task.resolution_note = payload.resolution_note
    db.commit()
    db.refresh(task)
    return _out(task)


@router.get('/quality/summary', response_model=QualitySummaryResponse)
def quality_summary(
    collection_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> QualitySummaryResponse:
    """Feedback and unanswered questions of the last 90 days, current task
    counts and the documents with the most negative feedback, over the
    caller's spaces (or one of them: 404 when not visible). Each signal
    counts once, however many documents of a space it cited."""
    if collection_id is not None:
        collection = db.get(Collection, collection_id)
        if collection is None or not _can_see(db, collection, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    visible = _visible_collections(db, user)
    since = datetime.now(timezone.utc) - timedelta(days=_SUMMARY_DAYS)
    query = (
        select(QualitySignal.signal_key, QualitySignal.kind, QualitySignal.rating, QualitySignal.job_id)
        .join(Collection, Collection.id == QualitySignal.collection_id)
        .where(
            QualitySignal.retracted.is_(False),
            QualitySignal.created_at >= since,
            QualitySignal.kind.in_(quality_tasks.SIGNAL_KINDS),
        )
    )
    if visible is not None:
        query = query.where(visible)
    if collection_id is not None:
        query = query.where(QualitySignal.collection_id == collection_id)

    ratings: dict[str, str] = {}
    unanswered: set[str] = set()
    per_job: dict[str, Counter] = defaultdict(Counter)
    for signal_key, kind, rating, job_id in db.execute(query):
        base = quality_tasks.base_signal_key(signal_key)
        if kind == 'unanswered':
            unanswered.add(base)
        elif rating in quality_tasks.FEEDBACK_RATINGS:
            ratings[base] = rating
            if job_id is not None:
                per_job[job_id][rating] += 1
    rating_counts = Counter(ratings.values())

    task_rows = db.execute(
        _scoped(select(ReviewTask.status, func.count(ReviewTask.id)), db, user, collection_id)
        .where(ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES))
        .group_by(ReviewTask.status)
    ).all()
    task_counts = {row[0]: int(row[1]) for row in task_rows}

    ranked = sorted(
        per_job.items(),
        key=lambda item: (-(item[1]['wrong'] + item[1]['outdated']), -sum(item[1].values()), item[0]),
    )[:_SUMMARY_DOCUMENTS]
    titles = dict(db.execute(select(Job.id, Job.original_filename).where(Job.id.in_([job_id for job_id, _ in ranked]))).all()) if ranked else {}
    return QualitySummaryResponse(
        feedback=QualityFeedbackCounts(**{rating: rating_counts.get(rating, 0) for rating in quality_tasks.FEEDBACK_RATINGS}),
        unanswered=len(unanswered),
        tasks=QualityTaskCounts(
            open=task_counts.get(ReviewTaskStatus.OPEN.value, 0),
            in_progress=task_counts.get(ReviewTaskStatus.IN_PROGRESS.value, 0),
        ),
        documents=[
            QualityDocumentStats(
                job_id=job_id, title=titles.get(job_id, job_id),
                helpful=counts['helpful'], wrong=counts['wrong'], outdated=counts['outdated'],
            )
            for job_id, counts in ranked
        ],
    )
