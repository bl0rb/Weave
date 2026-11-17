"""Document freshness ('Aktualität & Änderungen') portal endpoints.

Registered in app/main.py behind the same session + CSRF gate as the other
portal routers (prefix /api/v1/portal):

- PUT  /documents/{job_id}/review          set/clear the next review date
- POST /documents/{job_id}/review/confirm  'Als geprüft bestätigen'
  Both need the document's operating rights (admin, uploader, or owner/
  member of its space -- portal.py's `_job_is_controlled`); invisible
  documents answer 404, superseded versions (a later version of the chain
  is released) 409. Logic: app/services/document_freshness.py.
- GET  /documents/{job_id}/diff?base=      'Versionen vergleichen': both
  versions must be in the same version chain and visible to the caller
  (404 otherwise); without `base` the previous version (409 when there is
  none). Logic: app/services/document_diff.py.
- GET/POST /subscriptions, DELETE /subscriptions/{id}   own topic
  subscriptions ('Thema abonnieren'); someone else's id answers 404.
- GET /changes, POST /changes/read, GET /changes/count   own change feed,
  only for spaces the caller may still read.
- GET /changes/search?q=&collection_id=&days=  ad hoc: recent released new
  versions in readable spaces matching q. Logic: app/services/change_feed.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.portal import _collection_for_job, _job_is_controlled, _load_visible_job, _protected
from app.database.session import get_db
from app.models.models import ChangeNotification, Collection, Job, TopicSubscription, User
from app.schemas.document_freshness import (
    ChangeCollectionRef,
    ChangeCountResponse,
    ChangeNotificationListResponse,
    ChangeNotificationOut,
    ChangeReadRequest,
    ChangeReadResponse,
    ChangeSearchItem,
    ChangeSearchResponse,
    ChangeSubscriptionRef,
    ChangeSummary,
    DocumentDiff,
    DocumentDiffVersion,
    DocumentReviewConfirm,
    DocumentReviewResponse,
    DocumentReviewUpdate,
    TopicSubscriptionCreate,
    TopicSubscriptionListResponse,
    TopicSubscriptionOut,
)
from app.services import change_feed, document_diff, document_freshness
from app.services.collection_access import collection_role
from app.services.quality_tasks import lineage_root_id

router = APIRouter(prefix='/api/v1/portal')

MAX_SUBSCRIPTIONS_PER_USER = 50
_NOT_FOUND = 'Document not found'


# --- review dates -------------------------------------------------------------


def _controlled_document(db: Session, job_id: str, user: User) -> tuple[Job, Collection]:
    job = _load_visible_job(db, job_id, user, for_update=True)
    collection = _collection_for_job(db, job)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    if not _job_is_controlled(db, job, collection, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User cannot review this document')
    if document_freshness.has_newer_release(db, job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='A newer version of this document has been released'
        )
    return job, collection


def _review_response(db: Session, job: Job) -> DocumentReviewResponse:
    return DocumentReviewResponse(**(document_freshness.review_info(db, job) or {}))


@router.put('/documents/{job_id}/review', response_model=DocumentReviewResponse)
def set_document_review(
    job_id: str, payload: DocumentReviewUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> DocumentReviewResponse:
    """Set (or clear with null) when this version is next due for review.
    The document review tick opens or dismisses the `review_due` task on
    its next run."""
    job, _collection = _controlled_document(db, job_id, user)
    document_freshness.set_review_due(db, job, payload.review_due_at)
    db.commit()
    return _review_response(db, job)


@router.post('/documents/{job_id}/review/confirm', response_model=DocumentReviewResponse)
def confirm_document_review(
    job_id: str,
    payload: DocumentReviewConfirm | None = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentReviewResponse:
    """'Als geprüft bestätigen': reviewed now by the caller; the next due
    date is the one sent, else now + the space's interval, else none. Active
    `review_due` tasks of this version are closed (done)."""
    job, collection = _controlled_document(db, job_id, user)
    document_freshness.confirm_review(db, job, collection, user, (payload or DocumentReviewConfirm()).review_due_at)
    db.commit()
    return _review_response(db, job)


# --- version comparison ----------------------------------------------------------


def _version_ref(job: Job) -> DocumentDiffVersion:
    return DocumentDiffVersion(job_id=job.id, document_version=job.document_version, created_at=job.created_at)


@router.get('/documents/{job_id}/diff', response_model=DocumentDiff)
def diff_document_versions(
    job_id: str,
    base: str | None = Query(None, max_length=36),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentDiff:
    """Compare `job_id` (target) with `base` (default: the previous
    version). Each version's Markdown is its release snapshot when released,
    else the canonical portal snapshot."""
    target = _load_visible_job(db, job_id, user)
    _protected(target)
    collection = _collection_for_job(db, target)
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document collection not found')
    if base is None:
        if not target.previous_job_id or db.get(Job, target.previous_job_id) is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document has no previous version')
        base = target.previous_job_id
    if base == target.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='base must be another version')
    try:
        base_job = _load_visible_job(db, base, user)
    except HTTPException as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from exc
    if lineage_root_id(db, base_job) != lineage_root_id(db, target):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    _protected(base_job)
    base_collection = _collection_for_job(db, base_job) or collection
    result = document_diff.diff_markdown(
        document_freshness.version_markdown(db, base_job, base_collection),
        document_freshness.version_markdown(db, target, collection),
    )
    return DocumentDiff(base=_version_ref(base_job), target=_version_ref(target), **result)


# --- topic subscriptions -------------------------------------------------------------


def _collection_ref(collection: Collection) -> ChangeCollectionRef:
    return ChangeCollectionRef(id=collection.id, name=collection.name, slug=collection.slug)


def _subscription_out(subscription: TopicSubscription) -> TopicSubscriptionOut:
    return TopicSubscriptionOut(
        id=subscription.id,
        label=subscription.label,
        query=subscription.query,
        terms=change_feed.query_terms(subscription.query),
        collection=_collection_ref(subscription.collection) if subscription.collection is not None else None,
        created_at=subscription.created_at,
    )


@router.get('/subscriptions', response_model=TopicSubscriptionListResponse)
def list_subscriptions(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> TopicSubscriptionListResponse:
    rows = db.scalars(
        select(TopicSubscription)
        .where(TopicSubscription.user_id == user.id)
        .order_by(TopicSubscription.created_at, TopicSubscription.id)
    ).all()
    return TopicSubscriptionListResponse(items=[_subscription_out(row) for row in rows])


@router.post('/subscriptions', response_model=TopicSubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_subscription(
    payload: TopicSubscriptionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> TopicSubscriptionOut:
    """Subscribe to a space and/or keywords. The space must be readable by
    the caller (404 otherwise); a query must contain at least one
    searchable keyword (stopwords alone are refused)."""
    if payload.collection_id is None and payload.query is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Provide a knowledge space and/or keywords'
        )
    collection = None
    if payload.collection_id is not None:
        collection = db.get(Collection, payload.collection_id)
        if collection is None or collection_role(db, collection, user) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    terms = change_feed.query_terms(payload.query)
    if payload.query is not None and not terms:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Query has no searchable keywords')
    existing = db.scalars(select(TopicSubscription).where(TopicSubscription.user_id == user.id)).all()
    for row in existing:
        same_terms = change_feed.query_terms(row.query) == terms
        if row.collection_id == payload.collection_id and same_terms:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Subscription already exists')
    if len(existing) >= MAX_SUBSCRIPTIONS_PER_USER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Too many subscriptions')
    label = payload.label or payload.query or (collection.name if collection is not None else '')
    subscription = TopicSubscription(
        user_id=user.id, collection_id=payload.collection_id, query=payload.query, label=label[:120],
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return _subscription_out(subscription)


@router.delete('/subscriptions/{subscription_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(
    subscription_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    """Delete an own subscription (its notifications go with it)."""
    subscription = db.get(TopicSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Subscription not found')
    db.delete(subscription)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- change feed ---------------------------------------------------------------------


def _own_notifications(db: Session, user: User):
    """Own notifications in spaces the caller may still read."""
    conditions = [ChangeNotification.user_id == user.id]
    readable = change_feed.readable_collection_ids(db, user)
    if readable is not None:
        conditions.append(ChangeNotification.collection_id.in_(readable))
    return conditions


def _summary(value) -> ChangeSummary:
    value = value if isinstance(value, dict) else {}
    return ChangeSummary(stats=value.get('stats'), lines=list(value.get('lines') or []))


@router.get('/changes', response_model=ChangeNotificationListResponse)
def list_changes(
    unread: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangeNotificationListResponse:
    """The caller's change feed, newest first (`unread=true`: only unread)."""
    conditions = _own_notifications(db, user)
    if unread:
        conditions.append(ChangeNotification.read_at.is_(None))
    total = db.scalar(select(func.count(ChangeNotification.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(ChangeNotification)
        .where(*conditions)
        .order_by(ChangeNotification.created_at.desc(), ChangeNotification.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    versions = dict(db.execute(
        select(Job.id, Job.document_version).where(Job.id.in_([row.job_id for row in rows]))
    ).all()) if rows else {}
    items = [
        ChangeNotificationOut(
            id=row.id,
            subscription=ChangeSubscriptionRef(id=row.subscription.id, label=row.subscription.label)
            if row.subscription is not None else None,
            job_id=row.job_id,
            previous_job_id=row.previous_job_id,
            document_version=versions.get(row.job_id),
            collection=_collection_ref(row.collection),
            change_type=row.change_type,
            title=row.title,
            summary=_summary(row.summary),
            matched_terms=list(row.matched_terms or []),
            read_at=row.read_at,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return ChangeNotificationListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post('/changes/read', response_model=ChangeReadResponse)
def mark_changes_read(
    payload: ChangeReadRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ChangeReadResponse:
    """Mark own notifications read: the listed `ids`, or `all`. Ids of other
    users are silently ignored."""
    if not payload.all and not payload.ids:
        return ChangeReadResponse(updated=0)
    conditions = [ChangeNotification.user_id == user.id, ChangeNotification.read_at.is_(None)]
    if not payload.all:
        conditions.append(ChangeNotification.id.in_(payload.ids))
    result = db.execute(
        update(ChangeNotification).where(*conditions).values(read_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return ChangeReadResponse(updated=result.rowcount or 0)


@router.get('/changes/count', response_model=ChangeCountResponse)
def count_unread_changes(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ChangeCountResponse:
    """Unread notifications, for the sidebar badge."""
    conditions = _own_notifications(db, user)
    conditions.append(ChangeNotification.read_at.is_(None))
    return ChangeCountResponse(unread=db.scalar(select(func.count(ChangeNotification.id)).where(*conditions)) or 0)


@router.get('/changes/search', response_model=ChangeSearchResponse)
def search_changes(
    q: str | None = Query(None, max_length=200),
    collection_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangeSearchResponse:
    """'Was hat sich geändert?': released new versions of the last `days`
    days in spaces the caller may read, matching every keyword of `q`
    (optional), each with its diff summary; newest first, at most 50. A
    `collection_id` the caller cannot read answers 404. `incomplete` = the
    per-request scan cap was reached before older releases were checked
    (narrow `days` or pick a space)."""
    if collection_id is not None:
        collection = db.get(Collection, collection_id)
        if collection is None or collection_role(db, collection, user) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Collection not found')
    q = (q or '').strip() or None
    changes, terms, incomplete = change_feed.search_changes(
        db, user, q=q, collection_id=collection_id, days=days
    )
    return ChangeSearchResponse(
        items=[
            ChangeSearchItem(
                job_id=change.job.id,
                previous_job_id=change.previous_job.id if change.previous_job is not None else None,
                document_version=change.job.document_version,
                collection=_collection_ref(change.collection),
                change_type=change.change_type,
                title=change.title,
                released_at=change.released_at,
                summary=_summary(change.summary),
                matched_terms=terms,
            )
            for change in changes
        ],
        terms=terms,
        incomplete=incomplete,
    )
