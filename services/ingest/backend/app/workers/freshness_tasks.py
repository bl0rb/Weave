"""Document freshness ticks: `document_review_tick` (due reviews ->
`review_due` Prüfaufgaben + webhook event document.review_due, see
app/services/document_freshness.py) and `change_notification_tick` (new
releases -> topic-subscription notifications, see
app/services/change_feed.py).

Same architecture as app/workers/session_cleanup_tasks.py -- see that
module's docstring: no Celery Beat, each tick is a self-re-enqueuing chain
kicked off by the `worker_ready` hook in app/workers/tasks.py and registered
via an explicit import at the bottom of tasks.py; a Redis SET-NX-EX lock per
chain (held and renewed for the chain's lifetime, its token threaded
through the re-enqueues) keeps exactly one chain alive across replicas.
Both passes are idempotent (one active task per document, one
notification per subscription and job, `subscribers_notified_at` marks
progress), so the lock only avoids redundant work.

`run_document_review_pass` / `run_change_notification_pass` are the
lock-free bodies, directly callable from tests.
"""

import logging
import time
import uuid

from redis import Redis

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.models import Collection, Job
from app.services import change_feed, document_freshness
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

REVIEW_TICK_TASK_NAME = 'document_review_tick'
CHANGE_TICK_TASK_NAME = 'change_notification_tick'

_LOCK_KEYS = {
    REVIEW_TICK_TASK_NAME: 'worker:document-review:tick-lock',
    CHANGE_TICK_TASK_NAME: 'worker:change-notification:tick-lock',
}
_LOCK_TTL_MULTIPLIER = 3
_LOCK_TTL_FLOOR_SECONDS = 60
_REENQUEUE_MAX_ATTEMPTS = 3
_REENQUEUE_RETRY_SECONDS = (1, 2)


def _interval(task_name: str) -> int:
    if task_name == REVIEW_TICK_TASK_NAME:
        return max(settings.document_review_tick_seconds, 1)
    return max(settings.change_notification_tick_seconds, 1)


def _lock_ttl(task_name: str) -> int:
    return max(_interval(task_name) * _LOCK_TTL_MULTIPLIER, _LOCK_TTL_FLOOR_SECONDS)


def _client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _acquire_or_renew(task_name: str, lock_token: str | None) -> str | None:
    """The token this execution owns the chain's lock with, or None when
    another live chain holds it (stand down, do not re-enqueue)."""
    client = _client()
    key = _LOCK_KEYS[task_name]
    if lock_token is not None and client.get(key) == lock_token:
        client.expire(key, _lock_ttl(task_name))
        return lock_token
    token = str(uuid.uuid4())
    return token if client.set(key, token, nx=True, ex=_lock_ttl(task_name)) else None


def _reenqueue(task, task_name: str, token: str | None) -> None:
    for attempt in range(1, _REENQUEUE_MAX_ATTEMPTS + 1):
        try:
            task.app.send_task(task_name, args=[token], countdown=_interval(task_name))
            return
        except Exception:
            if attempt >= _REENQUEUE_MAX_ATTEMPTS:
                logger.error('%s chain ended -- failed to re-enqueue after %s attempt(s)', task_name, attempt, exc_info=True)
                return
            time.sleep(_REENQUEUE_RETRY_SECONDS[min(attempt - 1, len(_REENQUEUE_RETRY_SECONDS) - 1)])


def run_document_review_pass() -> dict:
    """One review pass plus the webhook for every newly opened task."""
    db = SessionLocal()
    try:
        result = document_freshness.run_review_tick(db)
        from app.workers.webhook_tasks import dispatch_review_due_event

        sent = 0
        for task_id, job_id, collection_id in result['pending_webhooks']:
            try:
                job, collection = db.get(Job, job_id), db.get(Collection, collection_id)
                if job is not None and collection is not None:
                    sent += dispatch_review_due_event(db, job, collection)
                # Marked only after a successful dispatch: a failure (or a
                # worker that dies in between) leaves it pending, and the
                # next tick sends it.
                document_freshness.mark_webhook_sent(db, task_id)
                db.commit()
            except Exception:  # noqa: BLE001 - the task exists; a webhook failure must not undo it
                logger.exception('document review: webhook dispatch failed for job %s', job_id)
                db.rollback()
        return {**result, 'webhooks': sent}
    finally:
        db.close()


def run_change_notification_pass() -> int:
    db = SessionLocal()
    try:
        return change_feed.process_pending_releases(db)
    finally:
        db.close()


def _tick(task, task_name: str, lock_token: str | None, body) -> None:
    try:
        token = _acquire_or_renew(task_name, lock_token)
    except Exception:
        logger.exception('%s: acquire/renew of the leadership lock failed; retrying next tick', task_name)
        _reenqueue(task, task_name, lock_token)
        return
    if token is None:
        logger.info('%s: leadership lock held elsewhere; standing down', task_name)
        return
    try:
        body()
    except Exception:
        logger.exception('%s failed', task_name)
    finally:
        _reenqueue(task, task_name, token)


@celery_app.task(name=REVIEW_TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def document_review_tick(self, lock_token: str | None = None) -> None:
    def body() -> None:
        result = run_document_review_pass()
        if result['created'] or result['dismissed']:
            logger.info(
                'document review: %s task(s) opened, %s dismissed, %s webhook(s)',
                len(result['created']), result['dismissed'], result['webhooks'],
            )

    _tick(self, REVIEW_TICK_TASK_NAME, lock_token, body)


@celery_app.task(name=CHANGE_TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def change_notification_tick(self, lock_token: str | None = None) -> None:
    def body() -> None:
        created = run_change_notification_pass()
        if created:
            logger.info('change feed: %s notification(s) created', created)

    _tick(self, CHANGE_TICK_TASK_NAME, lock_token, body)
