"""Durable portal publication delivery and its non-blocking reconciler."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from redis import Redis
from sqlalchemy import or_, select, update

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.models import DocumentRelease
from app.services.publications import (
    build_collection_registry_changed_payload,
    publication_configured,
    release_endpoint,
)
from app.services.webhooks import send_webhook_request
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DELIVERY_TASK_NAME = 'deliver_release'
COLLECTION_NOTIFICATION_TASK_NAME = 'notify_collection_registry_changed'
TICK_TASK_NAME = 'publication_tick'
_LOCK_KEY = 'worker:portal-publication:tick-lock'
_MAX_ERROR_CHARS = 2000
_BACKOFF_SECONDS = (30, 60, 120, 240)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff(attempt: int) -> int:
    return _BACKOFF_SECONDS[min(max(attempt - 1, 0), len(_BACKOFF_SECONDS) - 1)]


def _claim(db, release_id: str) -> tuple[DocumentRelease | None, str | None]:
    token = str(uuid.uuid4())
    now = _now()
    lease_until = now + timedelta(seconds=max(1, settings.publication_lease_seconds))
    result = db.execute(
        update(DocumentRelease)
        .where(
            DocumentRelease.id == release_id,
            DocumentRelease.status == 'pending',
            or_(DocumentRelease.lease_until.is_(None), DocumentRelease.lease_until <= now),
            or_(DocumentRelease.next_attempt_at.is_(None), DocumentRelease.next_attempt_at <= now),
        )
        .values(lease_token=token, lease_until=lease_until, updated_at=now)
    )
    db.commit()
    if not result.rowcount:
        return None, None
    return db.get(DocumentRelease, release_id), token


def _finish(db, release_id: str, token: str, values: dict) -> None:
    values = {**values, 'updated_at': _now(), 'lease_token': None, 'lease_until': None}
    db.execute(
        update(DocumentRelease)
        .where(DocumentRelease.id == release_id, DocumentRelease.lease_token == token)
        .values(**values)
    )
    db.commit()


@celery_app.task(name=DELIVERY_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def deliver_release(self, release_id: str) -> None:
    db = SessionLocal()
    try:
        release, token = _claim(db, release_id)
        if release is None or token is None:
            return
        if not publication_configured():
            _finish(db, release_id, token, {
                'status': 'failed',
                'error_message': 'Portal publication is not configured',
            })
            return

        http_status, error_message = send_webhook_request(
            release_endpoint(),
            dict(release.payload),
            settings.portal_knowledge_webhook_secret,
            frozenset(settings.webhook_private_host_allowlist),
        )
        attempt = release.attempts + 1
        if error_message is None:
            _finish(db, release_id, token, {
                'status': 'sent', 'attempts': attempt, 'error_message': None,
                'next_attempt_at': None,
            })
            return

        final = 400 <= http_status < 500 or attempt >= max(1, settings.publication_max_attempts)
        _finish(db, release_id, token, {
            'status': 'failed' if final else 'pending',
            'attempts': attempt,
            'error_message': str(error_message)[:_MAX_ERROR_CHARS],
            'next_attempt_at': None if final else _now() + timedelta(seconds=_backoff(attempt)),
        })
        if not final:
            try:
                self.app.send_task(DELIVERY_TASK_NAME, args=[release_id], countdown=_backoff(attempt))
            except Exception:
                logger.exception('failed to schedule retry for release %s', release_id)
    except Exception:
        db.rollback()
        logger.exception('release delivery %s failed before durable result', release_id)
        # The lease expires and the reconciler will reclaim the row after a
        # crash or an unexpected local failure.
    finally:
        db.close()


@celery_app.task(
    name=COLLECTION_NOTIFICATION_TASK_NAME,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def notify_collection_registry_changed(self, slug: str, attempt: int = 0) -> None:
    """Tell Knowledge to re-fetch the Collection registry.

    This dedicated service-to-service channel replaces the former fan-out to
    arbitrary user webhooks. It carries no ACL and periodic registry polling
    remains the fallback if this best-effort notification cannot be sent.
    """
    if not publication_configured():
        logger.warning('collection registry changed, but Knowledge publication is not configured')
        return

    http_status, error_message = send_webhook_request(
        release_endpoint(),
        build_collection_registry_changed_payload(slug),
        settings.portal_knowledge_webhook_secret,
        frozenset(settings.webhook_private_host_allowlist),
    )
    if error_message is None:
        return

    next_attempt = attempt + 1
    final = 400 <= http_status < 500 or next_attempt >= max(1, settings.publication_max_attempts)
    if final:
        logger.error(
            'collection registry notification for %s failed after %s attempt(s): %s',
            slug,
            next_attempt,
            error_message,
        )
        return
    try:
        self.app.send_task(
            COLLECTION_NOTIFICATION_TASK_NAME,
            args=[slug, next_attempt],
            countdown=_backoff(next_attempt),
        )
    except Exception:
        logger.exception('failed to schedule collection registry notification retry for %s', slug)


def _lock_ttl() -> int:
    return max(60, settings.publication_tick_seconds * 3)


def _acquire_or_renew(token: str | None) -> str | None:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    if token is None:
        candidate = str(uuid.uuid4())
        return candidate if client.set(_LOCK_KEY, candidate, nx=True, ex=_lock_ttl()) else None
    if client.get(_LOCK_KEY) != token:
        return None
    client.expire(_LOCK_KEY, _lock_ttl())
    return token


def reconcile_due_releases() -> int:
    db = SessionLocal()
    try:
        now = _now()
        ids = list(db.scalars(
            select(DocumentRelease.id)
            .where(DocumentRelease.status == 'pending')
            .where(or_(DocumentRelease.next_attempt_at.is_(None), DocumentRelease.next_attempt_at <= now))
            .where(or_(DocumentRelease.lease_until.is_(None), DocumentRelease.lease_until <= now))
            .order_by(DocumentRelease.created_at)
            .limit(100)
        ).all())
    finally:
        db.close()
    for release_id in ids:
        try:
            celery_app.send_task(DELIVERY_TASK_NAME, args=[release_id])
        except Exception:
            logger.exception('failed to enqueue pending release %s', release_id)
    return len(ids)


@celery_app.task(name=TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def publication_tick(self, lock_token: str | None = None) -> None:
    try:
        token = _acquire_or_renew(lock_token)
    except Exception:
        logger.exception('publication tick lock failed; retrying next tick')
        self.app.send_task(TICK_TASK_NAME, args=[lock_token], countdown=settings.publication_tick_seconds)
        return
    if token is None:
        return
    try:
        reconcile_due_releases()
    finally:
        self.app.send_task(TICK_TASK_NAME, args=[token], countdown=settings.publication_tick_seconds)
