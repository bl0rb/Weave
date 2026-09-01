"""Collection-registry sync scheduler: a self-re-enqueuing Celery task
(`collection_sync_tick`) that periodically pulls Weave-Ingest's
authoritative collection registry into this service's own `collections`
mirror (see app/services/collection_sync.py, app/models/models.py's
`Collection` docstring).

Same architecture as Weave-Ingest's own app/workers/session_cleanup_tasks.py
/ app/workers/refresh_tasks.py (this service's sibling repo -- see those
modules' docstrings for the full design rationale this one only summarizes):
there is deliberately no Celery Beat in this deployment, so periodic work is
a chain of self-re-enqueued tasks (`self.app.send_task(..., countdown=...)`),
kicked off once by the `worker_ready` hook in app/workers/tasks.py and
registered with the Celery app via an explicit import at the bottom of that
same module.

Singleton discipline (nothing else guarantees only one chain is ever running
across N worker replicas) is the same Redis SET-NX-EX lock idiom, held and
renewed for the chain's entire lifetime, with the winning token threaded
through the self-re-enqueue chain exactly like the Weave-Ingest tick tasks'.

Like session_cleanup_tick's sweep (and unlike confluence_refresh_tick's
crawl-dispatch), the work itself -- fetch the registry, upsert the mirror --
is trivially idempotent and side-effect-free if run twice concurrently, so
the lock here exists purely to stop every replica in a multi-replica
deployment from redundantly re-syncing on its own clock, not because a
double-run would corrupt anything.
"""

from __future__ import annotations

import logging
import uuid

from redis import Redis

from app.core.config import settings
from app.core.db import SessionLocal
from app.services.collection_sync import sync_collections
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

TICK_TASK_NAME = 'weave.knowledge.collection_sync_tick'

_SYNC_LOCK_KEY = 'worker:collection-sync:tick-lock'
# Same "generous multiple of the tick interval, floor for a fast tick"
# reasoning as Weave-Ingest's _REFRESH_LOCK_TTL_MULTIPLIER/_FLOOR: comfortably
# outlives one tick (so a healthy chain's own renewal always lands well
# before expiry) while still reclaiming a genuinely dead chain in bounded
# time.
_SYNC_LOCK_TTL_MULTIPLIER = 3
_SYNC_LOCK_TTL_FLOOR_SECONDS = 60


def _sync_lock_ttl_seconds() -> int:
    return max(settings.collection_sync_tick_seconds * _SYNC_LOCK_TTL_MULTIPLIER, _SYNC_LOCK_TTL_FLOOR_SECONDS)


def _try_acquire_sync_lock() -> str | None:
    """SET NX EX -- same lock idiom as Weave-Ingest's
    session_cleanup_tasks._try_acquire_cleanup_lock. Returns the winning
    token, or None if another live chain already holds it (the caller must
    stand down, not start a second chain)."""
    token = str(uuid.uuid4())
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    acquired = client.set(_SYNC_LOCK_KEY, token, nx=True, ex=_sync_lock_ttl_seconds())
    return token if acquired else None


def _renew_sync_lock(token: str) -> bool:
    """Per-tick heartbeat for the chain's leadership. False means this
    execution was superseded -- the caller must stop re-enqueuing so at most
    one chain ever survives."""
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    if client.get(_SYNC_LOCK_KEY) != token:
        return False
    client.expire(_SYNC_LOCK_KEY, _sync_lock_ttl_seconds())
    return True


def _acquire_or_renew(lock_token: str | None) -> str | None:
    """Returns the token this execution owns the lock with going forward, or
    None when it must stand down (sync only if issued a token; never
    re-enqueue without one)."""
    if lock_token is None:
        return _try_acquire_sync_lock()
    return lock_token if _renew_sync_lock(lock_token) else None


def _run_sync() -> int:
    """One sync pass in its own short-lived session -- standalone (not
    gated on the leadership lock) so it stays directly testable/callable,
    same shape as the Weave-Ingest tick tasks' own dispatch/sweep
    functions."""
    db = SessionLocal()
    try:
        return sync_collections(db)
    finally:
        db.close()


@celery_app.task(name=TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def collection_sync_tick(self, lock_token: str | None = None) -> None:
    """One fetch-and-upsert cycle, then self-re-enqueue -- see the module
    docstring for the singleton design. `lock_token=None` is the sentinel
    the worker_ready kickstart uses (it doesn't hold the lock yet, unlike
    every self-re-enqueued continuation, which always passes its held
    token); acks_late+reject_on_worker_lost means a hard worker kill
    mid-tick gets redelivered, which -- combined with the lock still being
    valid -- keeps the chain alive across that crash instead of silently
    dying with it.
    """
    try:
        token = _acquire_or_renew(lock_token)
    except Exception:
        # A Redis error here (unlike a clean None return) is transient, not
        # a legitimate loss of leadership -- re-enqueue with the SAME token
        # this execution was called with (never a freshly acquired one,
        # since acquire/renew itself is what just failed) so the next tick
        # simply retries instead of the whole chain silently dying on one
        # Redis blip.
        logger.exception('collection sync: acquire/renew of the leadership lock failed; retrying next tick')
        self.app.send_task(TICK_TASK_NAME, args=[lock_token], countdown=settings.collection_sync_tick_seconds)
        return
    if token is None:
        logger.info('collection sync: leadership lock unavailable (already held elsewhere, or lease lost); standing down')
        return
    try:
        count = _run_sync()
        logger.info('collection sync: synced %d collection(s)', count)
    except Exception:
        logger.exception('collection sync tick failed')
    finally:
        self.app.send_task(TICK_TASK_NAME, args=[token], countdown=settings.collection_sync_tick_seconds)
