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
import time
import uuid
from datetime import datetime, timedelta, timezone

from redis import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import Document, DocumentStatus
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
    if _renew_sync_lock(lock_token):
        return lock_token
    # SH-01: a failed renewal alone doesn't mean another chain took over --
    # the key may simply have expired/been lost with no live holder. Try a
    # fresh NX acquire before standing down, so an orphaned lock is reclaimed
    # here instead of stranding the chain until worker_ready restarts it.
    return _try_acquire_sync_lock()


_REENQUEUE_MAX_ATTEMPTS = 3
_REENQUEUE_BACKOFF_SECONDS = 1


def _reenqueue_next_tick(token: str | None) -> None:
    """SH-01: bounded retries around the self-re-enqueue send -- a transient
    broker hiccup must not silently end the chain (previously an uncaught
    send_task exception here just died, same as a truly lost lock)."""
    for attempt in range(1, _REENQUEUE_MAX_ATTEMPTS + 1):
        try:
            celery_app.send_task(TICK_TASK_NAME, args=[token], countdown=settings.collection_sync_tick_seconds)
            return
        except Exception:
            if attempt >= _REENQUEUE_MAX_ATTEMPTS:
                logger.error(
                    'SH-01: collection sync failed to re-enqueue the next tick after %d attempt(s); '
                    'chain will stop until worker_ready restarts it', attempt,
                )
                return
            logger.warning('collection sync: re-enqueue attempt %d/%d failed, retrying', attempt, _REENQUEUE_MAX_ATTEMPTS)
            time.sleep(_REENQUEUE_BACKOFF_SECONDS)


_REDRIVE_MAX_PER_TICK = 50
_REDRIVE_CANDIDATE_LIMIT = 200


def _aware_utc(value: datetime) -> datetime:
    # Same naive-vs-aware sqlite fixup as Weave-Ingest's own
    # app/api/deps._aware_utc / import_tasks._aware_utc (a local copy --
    # workers here have no reason to import the API module).
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _redrive_counter(
    *, status: DocumentStatus, counter, max_attempts: int, backoff_fn, reindex: bool,
) -> int:
    """One redrive pass for a single (status, attempt-counter) combination --
    see _redrive_stalled_index_retries below for why there are three of
    these (incident 2026-09-22 added the embedding-failure counter/backoff
    on top of the pre-existing fetch-failure one). `counter` is a mapped
    Document column (Document.index_attempts or Document.embedding_attempts);
    `reindex` is threaded into the redriven task so a stalled REINDEX
    request (status stays INDEXED while embedding_attempts>0, see
    app/workers/tasks.py's _run_pipeline) resumes as a reindex, not a no-op
    against an already-INDEXED document.

    A document is due when it's still in `status`, has failed at least once
    but not exhausted `max_attempts`, and has sat untouched longer than
    `backoff_fn`'s own schedule for its attempt count *plus* a full extra
    tick of grace -- i.e. its originally-scheduled retry should have clearly
    already fired by now, not merely be about to.

    Local import of app.workers.tasks avoids the circular import (that
    module imports this one at the bottom to register collection_sync_tick).

    SH-03: the extra tick of grace matters because the row lock taken in
    _index_document_unlocked is released by the interim commit in the
    retry branch *before* that branch's own retry send_task runs, so a
    redrive racing a still-in-flight legitimate retry is not a safe no-op --
    it can acquire the row first and trigger a second real attempt and
    attempt-count increment for one logical failure. Checking staleness
    against exactly the retry's own backoff (rather than backoff + a tick of
    margin) would make that race the common case, not a rare one, under the
    default settings. A redriven document's `updated_at` is also bumped
    immediately so a delayed legitimate retry isn't redriven again on every
    subsequent tick.

    SH-03 fix 2: the grace margin above narrows but doesn't close the race
    with a still-in-flight legitimate retry (broker/worker delay bigger
    than backoff + margin). So before dispatching, each document is claimed
    with a conditional UPDATE ... WHERE id=:id AND updated_at=:observed AND
    counter=:observed_attempts (same targeted-UPDATE idiom as the
    post-redrive bump below) -- if a concurrent legitimate retry's own
    commit (which bumps updated_at via the model's onupdate, and always
    bumps its counter too) or another redrive already touched the row, the
    CAS affects zero rows and this dispatch is skipped instead of causing a
    second attempt + attempt-count increment for one logical failure.
    Requiring the counter to still match (not just updated_at) catches a
    concurrent legitimate commit that raced in between our candidate read
    and our claim -- updated_at alone would not, since both columns are set
    in the same commit.

    NOTE: this CAS only guards the instant of claiming/dispatch. It does
    not prevent an already in-flight legitimate retry (claimed before this
    redrive's UPDATE runs) from independently completing after the redrive
    has also been sent -- if that retry too fails transiently, both
    invocations will each increment the counter once the row lock in
    _index_document_unlocked is released by the other's commit, burning two
    attempts for one real stall event. This is a real, currently open gap
    in the retry-budget guarantee (not a closed no-op) during a sustained
    outage; closing it fully would need a per-document in-process/cross-
    replica reservation beyond this CAS.
    """
    from app.workers.tasks import INDEX_TASK_NAME

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        candidates = db.execute(
            select(Document.id, counter, Document.updated_at)
            .where(Document.status == status)
            .where(counter > 0)
            .where(counter < max_attempts)
            .order_by(Document.updated_at)
            .limit(_REDRIVE_CANDIDATE_LIMIT)
        ).all()
    finally:
        db.close()

    # SH-03: the original retry's own countdown already uses backoff_fn --
    # checking staleness against that exact same threshold can't tell a
    # genuinely stalled retry apart from one that is simply still
    # queued/in-flight and due to fire imminently. Require a full extra
    # tick of grace beyond the retry's own schedule so we only redrive once
    # it has clearly had time to run and hasn't.
    due = [
        (document_id, updated_at, attempts) for document_id, attempts, updated_at in candidates
        if _aware_utc(updated_at) <= now - timedelta(
            seconds=backoff_fn(attempts) + settings.collection_sync_tick_seconds,
        )
    ][:_REDRIVE_MAX_PER_TICK]

    redriven = 0
    for document_id, observed_updated_at, observed_attempts in due:
        # SH-03 fix 2: claim the document before dispatching -- a
        # conditional UPDATE that only proceeds if updated_at AND the
        # counter still match what we just read. This is the same
        # compare-and-swap the row's updated_at column already gets bumped
        # by (ORM onupdate) whenever a legitimate retry's own commit
        # touches the row; also requiring the counter to match catches a
        # concurrent legitimate commit that raced in between our candidate
        # read and this claim, so a concurrent legitimate retry or a second
        # redrive makes this a safe no-op instead of a duplicate attempt +
        # attempt-count increment.
        db = SessionLocal()
        try:
            result = db.execute(
                Document.__table__.update()
                .where(Document.id == document_id)
                .where(Document.updated_at == observed_updated_at)
                .where(counter == observed_attempts)
                .values(updated_at=datetime.now(timezone.utc))
            )
            db.commit()
            claimed = result.rowcount == 1
        except Exception:
            logger.exception('SH-03: failed to claim document %s before redrive', document_id)
            claimed = False
        finally:
            db.close()
        if not claimed:
            logger.info(
                'SH-03: skipping redrive for document %s; already claimed by a concurrent retry or redrive',
                document_id,
            )
            continue
        try:
            celery_app.send_task(INDEX_TASK_NAME, args=[str(document_id)], kwargs={'reindex': reindex})
        except Exception:
            logger.exception('SH-03: failed to re-drive stalled index retry for document %s', document_id)
            continue
        redriven += 1
    if redriven:
        logger.info('SH-03: re-drove %d stalled index retry(ies) (status=%s)', redriven, status.value)
    return redriven


def _redrive_stalled_index_retries() -> int:
    """SH-03: durable re-drive for a retry whose own self-re-enqueue
    send_task never reached the broker (see app/workers/tasks.py's
    TransientFetchError and EmbeddingProviderError retry branches) -- both
    deliberately leave the document retry-eligible instead of marking it
    FAILED, so this periodic sweep is what actually resends them. Runs
    four passes, one per (status, attempt-counter) combination a stalled
    retry can be sitting in (incident 2026-09-22 added the second, third
    and fourth on top of the original fetch-retry pass):

    1. PENDING + index_attempts: a stalled markdown-fetch retry (original
       behaviour, unchanged).
    2. PENDING + embedding_attempts: a stalled embedding retry for a
       document's FIRST index (status never advances past PENDING until a
       successful commit -- see _run_pipeline).
    3. INDEXED + embedding_attempts: a stalled embedding retry for a
       REINDEX of an already-INDEXED document (status stays INDEXED
       throughout a reindex attempt -- see _run_pipeline's reindex-aware
       skip check) -- redriven with reindex=True so it resumes as a
       reindex rather than a no-op.
    4. INDEXED + index_attempts: a stalled markdown-FETCH retry for a
       REINDEX of an already-INDEXED document (reindex=True skips the
       early-return in _run_pipeline, so a TransientFetchError there
       increments index_attempts while status stays INDEXED -- none of
       passes 1-3 match this combination, so without this pass such a
       stalled reindex is invisible to the redrive and can get stuck
       forever if its own retry send_task is also lost) -- redriven with
       reindex=True for the same reason as pass 3.
    """
    from app.workers.tasks import _MAX_ATTEMPTS, _backoff_seconds, _embedding_backoff_seconds

    embedding_max_attempts = settings.embedding_max_attempts
    redriven = _redrive_counter(
        status=DocumentStatus.PENDING, counter=Document.index_attempts,
        max_attempts=_MAX_ATTEMPTS, backoff_fn=_backoff_seconds, reindex=False,
    )
    redriven += _redrive_counter(
        status=DocumentStatus.PENDING, counter=Document.embedding_attempts,
        max_attempts=embedding_max_attempts, backoff_fn=_embedding_backoff_seconds, reindex=False,
    )
    redriven += _redrive_counter(
        status=DocumentStatus.INDEXED, counter=Document.embedding_attempts,
        max_attempts=embedding_max_attempts, backoff_fn=_embedding_backoff_seconds, reindex=True,
    )
    redriven += _redrive_counter(
        status=DocumentStatus.INDEXED, counter=Document.index_attempts,
        max_attempts=_MAX_ATTEMPTS, backoff_fn=_backoff_seconds, reindex=True,
    )
    return redriven


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
        _reenqueue_next_tick(lock_token)
        return
    if token is None:
        logger.info('collection sync: leadership lock unavailable (already held elsewhere, or lease lost); standing down')
        return
    try:
        count = _run_sync()
        logger.info('collection sync: synced %d collection(s)', count)
    except Exception:
        logger.exception('collection sync tick failed')
    try:
        # SH-03: durable re-drive for stalled index retries, piggybacked on
        # this already-periodic tick -- see _redrive_stalled_index_retries.
        _redrive_stalled_index_retries()
    except Exception:
        logger.exception('collection sync: stalled index retry re-drive failed')
    finally:
        _reenqueue_next_tick(token)
