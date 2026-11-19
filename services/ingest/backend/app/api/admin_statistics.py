"""Admin overview statistics (GET /api/v1/admin/statistics).

Aggregate counts only (COUNT/SUM/GROUP BY, never rows) over Ingest's own
tables, plus Weave-Knowledge's corpus statistics proxied from its
GET /api/v1/internal/stats the same way reindex_vectors_status in
app/api/retrieval_provider.py proxies the re-index progress. A Knowledge
failure never fails the endpoint: `knowledge` is then None and
`knowledge_error` says why.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import origin_guard, require_admin
from app.core.config import settings
from app.database.session import get_db
from app.models.models import (
    Collection,
    CollectionVisibility,
    DocumentRelease,
    Job,
    JobStatus,
    ReviewTask,
    ReviewTaskStatus,
    StoredObject,
    Team,
    TopicSubscription,
    User,
    UserRole,
)
from app.schemas.admin_statistics import (
    AdminCollectionStatistics,
    AdminDocumentStatistics,
    AdminReviewTaskStatistics,
    AdminStatisticsResponse,
    AdminStorageStatistics,
    AdminUserStatistics,
    JobStatusCounts,
    KnowledgeStats,
    StorageBackendStatistics,
)
from app.services.document_freshness import in_chat_conditions
from app.services.security import enforce_rate_limit

router = APIRouter(
    prefix='/api/v1/admin/statistics',
    tags=['statistics-admin'],
    dependencies=[Depends(require_admin), Depends(origin_guard)],
)

_RECENT_DAYS = 30
_KNOWLEDGE_UNCONFIGURED = 'Der Wissensdienst ist nicht konfiguriert.'
_KNOWLEDGE_UNAVAILABLE = 'Die Statistik des Wissensdienstes konnte nicht abgerufen werden.'


def _count_if(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _document_statistics(db: Session, now: datetime) -> AdminDocumentStatistics:
    # Benchmark variants are not documents (see _apply_visible_filter in
    # app/api/routes.py); they are excluded from every count here.
    not_benchmark = Job.benchmark_run_id.is_(None)
    successor = aliased(Job)
    chain_head = ~select(successor.id).where(successor.previous_job_id == Job.id).exists()

    by_status = JobStatusCounts()
    for job_status, count in db.execute(
        select(Job.status, func.count()).where(not_benchmark).group_by(Job.status)
    ).all():
        setattr(by_status, JobStatus(job_status).value, int(count))

    total = db.scalar(select(func.count()).select_from(Job).where(not_benchmark, chain_head)) or 0
    released = db.scalar(
        select(func.count())
        .select_from(Job)
        .join(DocumentRelease, DocumentRelease.job_id == Job.id)
        .where(
            not_benchmark,
            # The portal's `in_chat` rule.
            *in_chat_conditions(),
        )
    ) or 0
    recent = db.scalar(
        select(func.count()).select_from(Job)
        .where(not_benchmark, Job.created_at >= now - timedelta(days=_RECENT_DAYS))
    ) or 0
    return AdminDocumentStatistics(
        total=int(total),
        versions=sum(by_status.model_dump().values()),
        by_status=by_status,
        released=int(released),
        uploaded_last_30_days=int(recent),
    )


def _storage_statistics(db: Session) -> AdminStorageStatistics:
    by_backend = {
        backend: StorageBackendStatistics(objects=int(objects), bytes=int(size or 0))
        for backend, objects, size in db.execute(
            select(StoredObject.backend, func.count(), func.coalesce(func.sum(StoredObject.size_bytes), 0))
            .group_by(StoredObject.backend)
        ).all()
    }
    return AdminStorageStatistics(
        objects=sum(item.objects for item in by_backend.values()),
        bytes=sum(item.bytes for item in by_backend.values()),
        by_backend=by_backend,
    )


def _knowledge_statistics() -> tuple[KnowledgeStats | None, str | None]:
    if not settings.portal_knowledge_base_url:
        return None, _KNOWLEDGE_UNCONFIGURED
    try:
        result = httpx.get(
            f'{settings.portal_knowledge_base_url.rstrip("/")}/api/v1/internal/stats',
            headers={'X-Weave-Reindex-Token': settings.portal_knowledge_webhook_secret}, timeout=5,
        )
        result.raise_for_status()
        return KnowledgeStats.model_validate(result.json()), None
    # InvalidURL (a malformed base URL) is no HTTPError.
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, ValidationError):
        return None, _KNOWLEDGE_UNAVAILABLE


@router.get('', response_model=AdminStatisticsResponse)
def admin_statistics(request: Request, db: Session = Depends(get_db)) -> AdminStatisticsResponse:
    enforce_rate_limit(request)
    now = datetime.now(timezone.utc)

    collections = db.execute(select(
        func.count(),
        _count_if(Collection.visibility == CollectionVisibility.PUBLIC),
        _count_if(Collection.visibility == CollectionVisibility.RESTRICTED),
    ).select_from(Collection)).one()
    users = db.execute(select(
        func.count(), _count_if(User.is_active.is_(True)), _count_if(User.role == UserRole.ADMIN),
    ).select_from(User)).one()
    tasks = db.execute(select(
        _count_if(ReviewTask.status == ReviewTaskStatus.OPEN.value),
        _count_if(ReviewTask.status == ReviewTaskStatus.IN_PROGRESS.value),
    ).select_from(ReviewTask)).one()
    knowledge, knowledge_error = _knowledge_statistics()

    return AdminStatisticsResponse(
        documents=_document_statistics(db, now),
        storage=_storage_statistics(db),
        collections=AdminCollectionStatistics(total=int(collections[0]), public=int(collections[1]), restricted=int(collections[2])),
        users=AdminUserStatistics(total=int(users[0]), active=int(users[1]), admins=int(users[2])),
        teams=int(db.scalar(select(func.count()).select_from(Team)) or 0),
        review_tasks=AdminReviewTaskStatistics(open=int(tasks[0]), in_progress=int(tasks[1])),
        subscriptions=int(db.scalar(select(func.count()).select_from(TopicSubscription)) or 0),
        knowledge=knowledge,
        knowledge_error=knowledge_error,
        generated_at=now,
    )
