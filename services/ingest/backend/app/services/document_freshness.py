"""Review dates of released documents ('Prüfung & Aktualität').

Per document version (Job): `review_due_at` (when the content is due for a
review), `last_reviewed_at` / `last_reviewed_by_id`; per knowledge space:
`Collection.review_interval_days` (NULL = no periodic review). Migration
0063_document_freshness.

Writers:
- `apply_release_review`: called by every path that creates a
  DocumentRelease (app/api/portal.py's release_portal_document -- which the
  editor, auto-release and re-ingest go through -- and the bulk release).
  A new version counts as reviewed: last_reviewed_at/by = now/releaser;
  review_due_at = the frontmatter's `review_due` (ISO date,
  contracts/frontmatter.schema.json) or now + the space's interval, else
  NULL. Older versions of the chain lose their due date and their active
  `review_due` tasks are closed (done) -- only the current version is ever
  due.
- `set_review_due` / `confirm_review`: the portal endpoints PUT
  /documents/{id}/review and POST /documents/{id}/review/confirm
  (app/api/document_freshness.py).
- `run_review_tick`: the document_review_tick
  (app/workers/freshness_tasks.py). For every *current released version*
  (released, not withdrawn, no newer released version in its chain) due
  within `document_review_lead_days` it keeps exactly one active
  `review_due` Prüfaufgabe (cluster_key `review_due:<job_id>`, details
  `{review_due_at, overdue, last_reviewed_at}`) via
  app/services/quality_tasks.ensure_task, and emits the webhook event
  `document.review_due` (contracts/events/document.review_due.md) once per
  task creation. A task dismissed by hand is not reopened for the same due
  date ('Erledigt' in the inbox confirms the review instead, see
  app/api/review_tasks.py); active tasks of documents that are no longer
  due are dismissed.
- `has_newer_release`: the review writers refuse superseded versions (409).

`version_markdown` is the Markdown of one version as the portal shows it
(release snapshot with relative image links, else the canonical snapshot) --
shared by the diff endpoint and the change feed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.models.models import (
    REVIEW_TASK_ACTIVE_STATUSES,
    REVIEW_TASK_CLOSED_STATUSES,
    Collection,
    DocumentRelease,
    Job,
    KnowledgeWithdrawal,
    ReviewTask,
    ReviewTaskKind,
    ReviewTaskStatus,
    User,
)
from app.services import quality_tasks
from app.services.publications import PublicationValidationError, canonical_snapshot, restore_relative_image_urls

logger = logging.getLogger(__name__)

REVIEW_DUE_KIND = ReviewTaskKind.REVIEW_DUE.value
_NEW_VERSION_NOTE = 'Neue Version freigegeben'
_CONFIRMED_NOTE = 'Als geprüft bestätigt'
_NOT_DUE_NOTE = 'Prüfung nicht mehr fällig'
_MAX_DUE_PER_TICK = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """sqlite hands DateTime(timezone=True) back naive; every write is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def due_at_from_date(value: date) -> datetime:
    """A review date means 'due from the start of that day' (UTC)."""
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def parse_review_due(value) -> datetime | None:
    """The frontmatter's `review_due`: an ISO date (YAML dates arrive as
    'YYYY-MM-DD' strings after publications._json_compatible) or an ISO
    date-time. Anything else is ignored (logged), never a release error."""
    if isinstance(value, datetime):
        return aware(value)
    if isinstance(value, date):
        return due_at_from_date(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return due_at_from_date(date.fromisoformat(text))
    except ValueError:
        pass
    try:
        return aware(datetime.fromisoformat(text.replace('Z', '+00:00')))
    except ValueError:
        logger.info('ignoring invalid frontmatter review_due %r', text[:40])
        return None


def interval_due(collection: Collection | None, now: datetime) -> datetime | None:
    days = collection.review_interval_days if collection is not None else None
    return now + timedelta(days=days) if days else None


def ancestors(db: Session, job: Job) -> list[Job]:
    """Older versions of `job`'s chain (previous_job_id), newest first."""
    result: list[Job] = []
    seen = {job.id}
    current = job
    while current.previous_job_id and len(seen) < 1000:
        previous = db.get(Job, current.previous_job_id)
        if previous is None or previous.id in seen:
            break
        seen.add(previous.id)
        result.append(previous)
        current = previous
    return result


def has_newer_release(db: Session, job: Job) -> bool:
    """Any later version of `job`'s chain (successors of successors too,
    released or not in between) has a release: `job` is superseded. The
    tick's SQL `newer_release_exists` only looks at the direct successor;
    the review writers use this stricter check."""
    seen = {job.id}
    frontier = [job.id]
    while frontier and len(seen) < 1000:
        successors = db.scalars(select(Job.id).where(Job.previous_job_id.in_(frontier))).all()
        frontier = [successor for successor in successors if successor not in seen]
        if not frontier:
            return False
        seen.update(frontier)
        if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id.in_(frontier)).limit(1)) is not None:
            return True
    return False


def apply_release_review(
    db: Session, job: Job, collection: Collection | None, frontmatter: dict | None, user_id: str | None,
    now: datetime | None = None,
) -> None:
    """Release hook (see module docstring). Flushes nothing itself; the
    caller's commit stores it together with the DocumentRelease."""
    now = now or _now()
    explicit = parse_review_due((frontmatter or {}).get('review_due'))
    job.review_due_at = explicit or interval_due(collection, now)
    job.last_reviewed_at = now
    job.last_reviewed_by_id = user_id
    for older in ancestors(db, job):
        older.review_due_at = None
        quality_tasks.close_active_tasks(
            db, kind=REVIEW_DUE_KIND, job_id=older.id, status=ReviewTaskStatus.DONE.value,
            user_id=user_id, note=_NEW_VERSION_NOTE,
        )


def review_info(db: Session, job: Job, now: datetime | None = None) -> dict | None:
    """The detail view's `review` block; None when the version has neither
    a due date nor a recorded review."""
    if job.review_due_at is None and job.last_reviewed_at is None:
        return None
    now = now or _now()
    reviewer = db.get(User, job.last_reviewed_by_id) if job.last_reviewed_by_id else None
    due = aware(job.review_due_at)
    return {
        'review_due_at': due,
        'last_reviewed_at': aware(job.last_reviewed_at),
        'last_reviewed_by': reviewer.username if reviewer is not None else None,
        'overdue': due is not None and due <= now,
    }


def set_review_due(db: Session, job: Job, value: date | None) -> None:
    """PUT /documents/{id}/review: set or clear the due date. The tick
    opens/dismisses the matching task on its next run."""
    job.review_due_at = due_at_from_date(value) if value is not None else None


def confirm_review(
    db: Session, job: Job, collection: Collection | None, user: User, explicit: date | None = None,
    now: datetime | None = None,
) -> int:
    """POST /documents/{id}/review/confirm: reviewed now by `user`; next due
    date = `explicit`, else now + interval, else NULL. Closes the version's
    active review_due tasks (done); returns how many."""
    now = now or _now()
    job.last_reviewed_at = now
    job.last_reviewed_by_id = user.id
    job.review_due_at = due_at_from_date(explicit) if explicit is not None else interval_due(collection, now)
    return quality_tasks.close_active_tasks(
        db, kind=REVIEW_DUE_KIND, job_id=job.id, status=ReviewTaskStatus.DONE.value,
        user_id=user.id, note=_CONFIRMED_NOTE,
    )


# --- current released versions (SQL) ----------------------------------------


def newer_release_exists():
    """Correlated EXISTS: a direct successor of Job in its chain has a
    release (then Job is no longer the version in the chat)."""
    successor = aliased(Job)
    successor_release = aliased(DocumentRelease)
    return (
        select(successor.id)
        .join(successor_release, successor_release.job_id == successor.id)
        .where(successor.previous_job_id == Job.id)
        .exists()
    )


def withdrawn_exists():
    return select(KnowledgeWithdrawal.job_id).where(KnowledgeWithdrawal.job_id == Job.id).exists()


def review_due_conditions(now: datetime) -> list:
    """WHERE parts for 'current released version due by `now`'; the query
    must already (outer) join DocumentRelease on Job (portal list)."""
    return [
        DocumentRelease.id.is_not(None),
        Job.review_due_at.is_not(None),
        Job.review_due_at <= now,
        ~newer_release_exists(),
        ~withdrawn_exists(),
    ]


def version_markdown(db: Session, job: Job, collection: Collection | None) -> str:
    """Markdown of one version as the portal shows it: the release snapshot
    (image links made relative again, so two releases' differing absolute
    URLs are no diff noise) or the canonical snapshot, falling back to the
    raw result for a version that cannot be canonicalised."""
    release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
    if release is not None:
        return restore_relative_image_urls(release.markdown_snapshot or '', release.id)
    if collection is not None:
        try:
            return canonical_snapshot(db, job, collection)[0]
        except PublicationValidationError:
            pass
    return job.result_markdown or ''


# --- tick ---------------------------------------------------------------------


def _task_title(job: Job) -> str:
    return f'Prüfung fällig: „{job.original_filename}“'


def _details(job: Job, now: datetime) -> dict:
    due = aware(job.review_due_at)
    return {
        'review_due_at': due.isoformat() if due else None,
        'overdue': due is not None and due <= now,
        'last_reviewed_at': aware(job.last_reviewed_at).isoformat() if job.last_reviewed_at else None,
    }


def _closed_for_same_date(db: Session, collection_id: str, job: Job) -> bool:
    """A review_due task for this exact due date was already closed by hand
    (dismissed, 'Verwerfen'): do not nag again for that date. Tasks the
    tick dismissed itself because the date moved away (`_NOT_DUE_NOTE`,
    no resolver) do not count: when that date becomes current again, a new
    task is opened. ('Erledigt' on the inbox confirms the review --
    app/api/review_tasks.py -- which moves the due date on.)"""
    due = aware(job.review_due_at)
    closed = db.execute(
        select(ReviewTask.details, ReviewTask.resolved_by_id, ReviewTask.resolution_note).where(
            ReviewTask.collection_id == collection_id,
            ReviewTask.kind == REVIEW_DUE_KIND,
            ReviewTask.cluster_key == f'review_due:{job.id}',
            ReviewTask.status.in_(REVIEW_TASK_CLOSED_STATUSES),
        )
    ).all()
    return any(
        isinstance(details, dict) and details.get('review_due_at') == due.isoformat()
        and not (resolved_by_id is None and note == _NOT_DUE_NOTE)
        for details, resolved_by_id, note in closed
    )


def _due_conditions(horizon: datetime) -> list:
    """WHERE parts on Job: a released, current, not withdrawn version due by
    `horizon` (the tick's selection)."""
    return [
        select(DocumentRelease.id).where(DocumentRelease.job_id == Job.id).exists(),
        Job.review_due_at.is_not(None),
        Job.review_due_at <= horizon,
        Job.benchmark_run_id.is_(None),
        ~newer_release_exists(),
        ~withdrawn_exists(),
    ]


def run_review_tick(db: Session, now: datetime | None = None) -> dict:
    """One pass (see module docstring). Commits. Returns counts plus the
    `(job_id, collection_id)` pairs whose task was created -- the caller
    emits the webhook for those after the commit."""
    now = now or _now()
    horizon = now + timedelta(days=max(settings.document_review_lead_days, 0))
    quality_tasks.lock_review_tasks(db)
    jobs = db.scalars(
        select(Job)
        .where(*_due_conditions(horizon))
        .order_by(Job.review_due_at, Job.id)
        .limit(_MAX_DUE_PER_TICK)
    ).all()
    created: list[tuple[str, str]] = []
    updated = 0
    due_job_ids: set[str] = set()
    for job in jobs:
        collection = quality_tasks.collection_for_job(db, job)
        if collection is None:
            continue
        due_job_ids.add(job.id)
        details = _details(job, now)
        cluster_key = f'review_due:{job.id}'
        task = quality_tasks.find_active_task(
            db, collection_id=collection.id, kind=REVIEW_DUE_KIND, cluster_key=cluster_key
        )
        if task is None:
            if _closed_for_same_date(db, collection.id, job):
                continue
            task, _created = quality_tasks.ensure_task(
                db, collection_id=collection.id, kind=REVIEW_DUE_KIND, cluster_key=cluster_key,
                title=_task_title(job), job_id=job.id, details=details, seen_at=now,
            )
            created.append((job.id, collection.id))
            continue
        if task.details != details:
            task.details = details
            updated += 1
    stale = [
        task for task in db.scalars(
            select(ReviewTask).where(
                ReviewTask.kind == REVIEW_DUE_KIND, ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES)
            )
        ).all()
        if task.job_id not in due_job_ids
    ]
    if len(jobs) >= _MAX_DUE_PER_TICK:
        # The due query was capped: a job beyond the cap is still due, its
        # task must not be dismissed (it is picked up on a later run).
        stale_job_ids = sorted({task.job_id for task in stale if task.job_id})
        beyond_cap = set(db.scalars(
            select(Job.id).where(*_due_conditions(horizon), Job.id.in_(stale_job_ids))
        ).all()) if stale_job_ids else set()
        stale = [task for task in stale if task.job_id not in beyond_cap]
    dismissed = 0
    for task in stale:
        quality_tasks.resolve_task(task, ReviewTaskStatus.DISMISSED.value, user_id=None, note=_NOT_DUE_NOTE)
        dismissed += 1
    db.commit()
    return {'created': created, 'updated': updated, 'dismissed': dismissed}
