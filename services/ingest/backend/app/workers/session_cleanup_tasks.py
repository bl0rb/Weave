"""Session cleanup scheduler: a self-re-enqueuing Celery task
(`session_cleanup_tick`) that periodically deletes expired session rows.

Sessions are already lazily deleted one-at-a-time when a request presents an
expired cookie (see app/api/deps.get_current_user), but a session nobody
ever presents again -- an abandoned browser tab, a machine that was revoked
by deleting the user, a laptop that never comes back online -- is never
touched by that path and would otherwise accumulate in the `sessions` table
forever.

Same architecture as app/workers/refresh_tasks.py's confluence_refresh_tick
-- see that module's docstring for the full design rationale. Short version:
there is deliberately no Celery Beat in this deployment, so periodic work is
a chain of self-re-enqueued tasks (`self.app.send_task(..., countdown=...)`),
kicked off once by the existing `worker_ready` hook in app/workers/tasks.py
and registered with the Celery app via an explicit import at the bottom of
tasks.py. Singleton discipline (nothing else guarantees only one chain is
ever running across N worker replicas) is the same Redis SET-NX-EX lock
idiom, held and renewed for the chain's entire lifetime, with the winning
token threaded through the self-re-enqueue chain exactly like
confluence_refresh_tick's.

Unlike the refresh tick, the work itself (DELETE ... WHERE expires_at < now)
is trivially idempotent and side-effect-free if run twice concurrently, so
the lock here is purely to avoid every replica in a multi-replica deployment
redundantly re-running the same sweep on its own clock -- not a correctness
requirement the way Doppelstart-Schutz is for starting an import run.
"""

import logging
import uuid
from datetime import datetime, timezone

from redis import Redis
from sqlalchemy import delete

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.models import Session as SessionModel
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

TICK_TASK_NAME = 'session_cleanup_tick'

_CLEANUP_LOCK_KEY = 'worker:session-cleanup:tick-lock'
# Same reasoning as refresh_tasks._REFRESH_LOCK_TTL_MULTIPLIER/_FLOOR: a
# generous multiple of the tick interval, comfortably outliving a single
# tick so a healthy chain's own next renewal always lands well before
# expiry, while still reclaiming a genuinely dead chain in bounded time.
_CLEANUP_LOCK_TTL_MULTIPLIER = 3
_CLEANUP_LOCK_TTL_FLOOR_SECONDS = 60


def _cleanup_lock_ttl_seconds() -> int:
    return max(
        settings.session_cleanup_tick_seconds * _CLEANUP_LOCK_TTL_MULTIPLIER, _CLEANUP_LOCK_TTL_FLOOR_SECONDS
    )


def _try_acquire_cleanup_lock() -> str | None:
    """SET NX EX -- same lock idiom as tasks._try_acquire_recovery_lock /
    refresh_tasks._try_acquire_refresh_lock. Returns the winning token, or
    None if another live chain already holds it (the caller must stand down,
    not start a second chain)."""
    token = str(uuid.uuid4())
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    acquired = client.set(_CLEANUP_LOCK_KEY, token, nx=True, ex=_cleanup_lock_ttl_seconds())
    return token if acquired else None


def _renew_cleanup_lock(token: str) -> bool:
    """Per-tick heartbeat for the chain's leadership, mirroring
    import_tasks._commit_owned's lease-guard: only extends the TTL while
    `token` is still the current holder. False means this execution was
    superseded -- the caller must stop re-enqueuing so at most one chain
    ever survives."""
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    if client.get(_CLEANUP_LOCK_KEY) != token:
        return False
    client.expire(_CLEANUP_LOCK_KEY, _cleanup_lock_ttl_seconds())
    return True


def _acquire_or_renew(lock_token: str | None) -> str | None:
    """Returns the token this execution owns the lock with going forward, or
    None when it must stand down (do the sweep only if issued a token; never
    re-enqueue without one)."""
    if lock_token is None:
        return _try_acquire_cleanup_lock()
    return lock_token if _renew_cleanup_lock(lock_token) else None


def delete_expired_sessions() -> int:
    """Delete every session row whose expires_at is in the past. Returns the
    number of rows removed. Standalone (not gated on the leadership lock)
    so it stays directly testable/callable, exactly like
    refresh_tasks._dispatch_due_refreshes."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        result = db.execute(delete(SessionModel).where(SessionModel.expires_at < now))
        db.commit()
        return result.rowcount or 0
    finally:
        db.close()


@celery_app.task(name=TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def session_cleanup_tick(self, lock_token: str | None = None) -> None:
    """One sweep-and-delete cycle, then self-re-enqueue -- see the module
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
        logger.exception('session cleanup: acquire/renew of the leadership lock failed; retrying next tick')
        self.app.send_task(TICK_TASK_NAME, args=[lock_token], countdown=settings.session_cleanup_tick_seconds)
        return
    if token is None:
        logger.info(
            'session cleanup: leadership lock unavailable (already held elsewhere, or lease lost); standing down'
        )
        return
    try:
        deleted = delete_expired_sessions()
        if deleted:
            logger.info('session cleanup: deleted %s expired session(s)', deleted)
    except Exception:
        logger.exception('session cleanup tick failed')
    finally:
        self.app.send_task(TICK_TASK_NAME, args=[token], countdown=settings.session_cleanup_tick_seconds)
