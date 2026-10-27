"""Confluence-refresh scheduler: a self-re-enqueuing Celery task
(`confluence_refresh_tick`) that periodically starts a fresh crawl run for
every enabled ImportSync whose interval has elapsed (one active run per
source at a time; see `_has_active_run`).

There is deliberately no Celery Beat in this deployment, so the recurring
tick is a chain of self-re-enqueued tasks: each execution re-sends itself
via `self.app.send_task(..., countdown=confluence_refresh_tick_seconds)`.
The chain is kicked off by the existing `worker_ready` hook in
app/workers/tasks.py (`celery_app.send_task('confluence_refresh_tick')`, by
name only. This module is registered through an explicit import at the bottom
of tasks.py.

Singleton discipline (no Beat means nothing else guarantees only one chain
is ever running) is a Redis SET-NX-EX lock, styled after
tasks._try_acquire_recovery_lock -- but held and renewed for the chain's
entire lifetime rather than released after one startup action, since a
short-lived lock would let every later, unrelated worker restart start a
second competing chain even while the first one is perfectly healthy. The
winning token is threaded through the self-re-enqueue chain (the same way
import_confluence threads `chunk_seq`) and renewed once per tick
(_renew_refresh_lock, mirroring import_tasks._commit_owned's lease-guard: a
failed renewal means this execution was superseded, or the lock lapsed with
nobody holding it, and it must stop re-enqueuing itself either way). Every
`confluence_refresh_tick()` call -- both the token-less kickstart from
worker_ready and every self-re-enqueued continuation -- goes through the
same acquire-or-renew check, so a redundant kickstart from a simultaneous
multi-replica startup, or from a worker restarting years into an
already-healthy chain, is a harmless no-op rather than a second chain.

The refresh run itself reuses the existing `import_confluence` chunked crawl
task completely unchanged -- it is enqueued exactly like a user-started run
(see app/api/import_routes.create_import_run), just with
`options['is_refresh'] = True`. ImportRun has no dedicated "is this a
refresh" column (none was added by the schema change this feature builds
on); `options` is already an arbitrary JSON settings-snapshot per the model
docstring, so that is the "existing ImportRun pattern" this flag piggybacks
on instead of a new migration. The per-page diff against ImportPageState
(skip unchanged pages, version-chain changed ones) lives in
app/workers/import_tasks.py, gated on that same flag; the
last_success_at/last_error bookkeeping on ImportSync lives there too (set
when a run of the sync reaches a terminal state).

Confluence path/space context (frontmatter `space` / `confluence_path` /
`parent_title` / `depth`, the breadcrumb line, and `path_tags` -- see
app/services/confluence.PageContext and app/workers/import_tasks.py's
_import_one_page) needs NO extra plumbing here: a refresh run is a normal
`import_confluence` crawl from the copied scope's root (frontier below), so
it goes through the exact same one-fetch_context-per-run root resolution and
per-page path threading as a manually started run -- a changed page's
re-import gets a freshly walked ancestor path (from the CURRENT tree
shape, not a stale cached one) at zero extra API cost per page.
"""

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from redis import Redis
from sqlalchemy import select

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.models import Collection, CollectionRole, ImportRun, ImportRunStatus, ImportSource, ImportSync, User
from app.services.collection_access import collection_role, role_at_least
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

TICK_TASK_NAME = 'confluence_refresh_tick'

# Same cap as import_tasks._ERROR_MESSAGE_MAX_CHARS -- kept as a separate
# local constant rather than importing it, same "workers don't reach into
# each other's private constants" discipline as the local _aware_utc copy
# below.
_START_FAILURE_MESSAGE_MAX_CHARS = 2000

_REFRESH_LOCK_KEY = 'worker:confluence-refresh:tick-lock'
# A generous multiple of the tick interval: must comfortably outlive a
# single tick (including a slow due-sources scan) so a healthy chain's own
# next renewal always lands well before expiry, while still reclaiming a
# genuinely dead chain (holder crashed and redelivery also failed) in
# bounded time rather than never.
_REFRESH_LOCK_TTL_MULTIPLIER = 3
_REFRESH_LOCK_TTL_FLOOR_SECONDS = 60


def _refresh_lock_ttl_seconds() -> int:
    return max(settings.confluence_refresh_tick_seconds * _REFRESH_LOCK_TTL_MULTIPLIER, _REFRESH_LOCK_TTL_FLOOR_SECONDS)


def _try_acquire_refresh_lock() -> str | None:
    """SET NX EX -- same lock idiom as tasks._try_acquire_recovery_lock.
    Returns the winning token, or None if another live chain already holds
    it (in which case the caller must stand down, not start a second
    chain)."""
    token = str(uuid.uuid4())
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    acquired = client.set(_REFRESH_LOCK_KEY, token, nx=True, ex=_refresh_lock_ttl_seconds())
    return token if acquired else None


def _renew_refresh_lock(token: str) -> bool:
    """Per-tick heartbeat for the chain's leadership, mirroring
    import_tasks._commit_owned's lease-guard: only extends the TTL while
    `token` is still the current holder. False means this execution was
    superseded (the lock lapsed and either nobody or a newer chain took
    over) -- the caller must stop re-enqueuing so at most one chain ever
    survives."""
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    if client.get(_REFRESH_LOCK_KEY) != token:
        return False
    client.expire(_REFRESH_LOCK_KEY, _refresh_lock_ttl_seconds())
    return True


def _acquire_or_renew(lock_token: str | None) -> str | None:
    """Returns the token this execution owns the lock with going forward, or
    None when it must stand down (do the dispatch work only if issued a
    token; never re-enqueue without one)."""
    if lock_token is not None and _renew_refresh_lock(lock_token):
        return lock_token
    # SH-01: no carried token, or the carried one was lost (TTL expiry,
    # Redis restart/eviction) -- try a fresh acquisition instead of ending
    # the chain here. Standing down (None) means another replica already
    # holds it live, so exactly one chain survives.
    return _try_acquire_refresh_lock()


_REENQUEUE_MAX_ATTEMPTS = 3
_REENQUEUE_RETRY_SECONDS = (1, 2)


def _reenqueue_tick(self, token: str | None) -> None:
    """SH-01: this self-re-enqueue is what keeps the refresh-tick chain
    alive -- a transient broker error here must not silently end it. Retry
    a bounded number of times with a short backoff before giving up; log
    at ERROR on final failure so operators can see the chain died."""
    for attempt in range(1, _REENQUEUE_MAX_ATTEMPTS + 1):
        try:
            self.app.send_task(TICK_TASK_NAME, args=[token], countdown=settings.confluence_refresh_tick_seconds)
            return
        except Exception:
            if attempt >= _REENQUEUE_MAX_ATTEMPTS:
                logger.error(
                    'SH-01: confluence refresh tick chain ended -- failed to re-enqueue next tick after %s attempt(s)',
                    attempt,
                    exc_info=True,
                )
                return
            time.sleep(_REENQUEUE_RETRY_SECONDS[min(attempt - 1, len(_REENQUEUE_RETRY_SECONDS) - 1)])


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + '...'


def _aware_utc(value: datetime) -> datetime:
    # Local copy of app.api.deps._aware_utc's naive-vs-aware sqlite fixup --
    # workers must not import the API module (same reasoning as
    # import_tasks._attach_tags being a local copy of routes._attach_tags).
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def effective_interval_seconds(sync: ImportSync) -> int:
    # Same clamp-never-lower-than-the-floor discipline as the API write path
    # (PATCH /import/syncs/{id}), applied again here defensively -- NULL
    # (never configured) falls back to the floor rather than being treated
    # as "due immediately"/"never due".
    return max(
        sync.interval_seconds or settings.confluence_refresh_min_interval_seconds,
        settings.confluence_refresh_min_interval_seconds,
    )


def next_refresh_at(sync: ImportSync) -> datetime | None:
    """When the scheduler will next start this sync (None: disabled). A
    sync that never succeeded is due at once."""
    if not sync.enabled:
        return None
    if sync.last_success_at is None:
        return datetime.now(timezone.utc)
    return _aware_utc(sync.last_success_at) + timedelta(seconds=effective_interval_seconds(sync))


def _is_due(sync: ImportSync, now: datetime) -> bool:
    due_at = next_refresh_at(sync)
    return due_at is not None and due_at <= now


def _has_active_run(db, source_id: str) -> bool:
    # Per source, not per sync: one crawl at a time per Confluence
    # connection keeps its rate limit and the shared page states safe; other
    # due syncs of the same source start on a later tick.
    return (
        db.scalar(
            select(ImportRun.id)
            .where(ImportRun.source_id == source_id)
            .where(ImportRun.status.in_([ImportRunStatus.PENDING, ImportRunStatus.RUNNING]))
            .limit(1)
        )
        is not None
    )


def _reap_stale_runs(db, source: ImportSource, now: datetime) -> int:
    """Flip this source's wedged 'pending'/'running' runs to failed -- same
    heartbeat rule as import_routes._is_stale_active (updated_at older than
    settings.import_stale_run_seconds) -- so a lost worker or a lost queue
    message can never block the Doppelstart-Schutz in _dispatch_due_refreshes
    forever. Returns the number of runs reaped and surfaces the condition on
    each affected sync's last_error (cleared again by
    import_tasks._record_refresh_outcome once its next run succeeds)."""
    stale_before = now - timedelta(seconds=settings.import_stale_run_seconds)
    stale_runs = [
        run
        for run in db.scalars(
            select(ImportRun).where(
                ImportRun.source_id == source.id,
                ImportRun.status.in_([ImportRunStatus.PENDING, ImportRunStatus.RUNNING]),
            )
        ).all()
        if _aware_utc(run.updated_at) < stale_before
    ]
    for run in stale_runs:
        run.status = ImportRunStatus.FAILED
        run.error_message = 'worker lost; run stalled'
        run.finished_at = now
        if run.sync_id and (sync := db.get(ImportSync, run.sync_id)) is not None:
            sync.last_error = 'run stalled (worker lost) and was marked failed; the sync continues with the next run'
    if stale_runs:
        logger.warning(
            'confluence refresh: source %s had %d stalled run(s) (no heartbeat for %ds); marked failed',
            source.id, len(stale_runs), settings.import_stale_run_seconds,
        )
        db.commit()
    return len(stale_runs)


def start_sync_run(db, sync: ImportSync) -> ImportRun | None:
    """Start a run of `sync` -- the scheduler's refresh and the API's "sync
    now". Scope, target space and options come from the sync itself.
    Returns the queued run, or None when the sync is gone or its source
    already has an active run. Raises ValueError when the run must not
    start (owner deactivated, space gone or no longer controllable)."""
    source = db.scalar(select(ImportSource).where(ImportSource.id == sync.source_id).with_for_update())
    sync = db.get(ImportSync, sync.id)
    if source is None or sync is None or _has_active_run(db, source.id):
        return None

    options = dict(sync.options) if isinstance(sync.options, dict) else {}
    source_owner = db.get(User, source.owner_id)
    if source_owner is not None and not source_owner.is_active:
        # Deactivation blocks the person at once (ADR 0008); reactivating
        # resumes the refresh without touching any grant.
        raise ValueError('import source owner is deactivated; refusing the refresh')
    if sync.collection_id:
        collection = db.get(Collection, sync.collection_id)
        if collection is None:
            raise ValueError('assigned collection no longer exists; refusing an unassigned refresh')
        if source_owner is None:
            raise ValueError('import source owner no longer exists; refusing collection assignment')
        if not role_at_least(collection_role(db, collection, source_owner), CollectionRole.MEMBER):
            raise ValueError('assigned collection is no longer controllable by the import source owner')
        options['collection_id'] = collection.id
        options['collection_slug'] = collection.slug
        options['collection_name'] = collection.name
    # Re-clamped: settings (e.g. import_max_pages) may have changed since the
    # sync was saved -- same clamp-never-raise discipline as
    # import_routes.create_import_run / import_tasks.import_confluence.
    options['max_pages'] = min(options.get('max_pages') or settings.import_max_pages, settings.import_max_pages)
    raw_max_depth = options.get('max_depth')
    options['max_depth'] = (
        settings.import_max_depth if raw_max_depth is None else min(int(raw_max_depth), settings.import_max_depth)
    )
    # Diff against ImportPageState (skip unchanged pages, chain changed
    # ones) -- import_tasks.py keys its refresh path off this flag.
    options['is_refresh'] = True

    frontier = [[sync.scope_value, 0]] if sync.scope_type in ('page', 'url') else []
    run = ImportRun(
        source_id=source.id,
        sync_id=sync.id,
        owner_id=source.owner_id,
        kind=source.kind,
        scope_type=sync.scope_type,
        scope_value=sync.scope_value,
        options=options,
        state={'frontier': frontier, 'visited': {}, 'errors': []},
    )
    db.add(run)
    db.commit()
    # Enqueued by name only, exactly like import_routes.create_import_run --
    # this module never imports the worker task module.
    try:
        celery_app.send_task('import_confluence', args=[run.id, 0])
    except Exception:
        run.status = ImportRunStatus.FAILED
        run.error_message = 'Sync could not be queued; please retry'
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    logger.info('confluence refresh: started run %s for sync %s (source %s)', run.id, sync.id, source.id)
    return run


def _dispatch_due_refreshes() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # A deactivated owner's syncs are skipped, not failed on every tick.
        # Longest-waiting first, so one busy source cannot starve its others.
        syncs = db.scalars(
            select(ImportSync)
            .join(ImportSource, ImportSource.id == ImportSync.source_id)
            .join(User, User.id == ImportSource.owner_id)
            .where(ImportSync.enabled.is_(True), User.is_active.is_(True))
            .order_by(ImportSync.last_success_at.is_not(None), ImportSync.last_success_at, ImportSync.created_at)
        ).all()
        reaped: set[str] = set()
        for sync in syncs:
            if not _is_due(sync, now):
                continue
            if sync.source_id not in reaped:
                _reap_stale_runs(db, sync.source, now)
                reaped.add(sync.source_id)
            if _has_active_run(db, sync.source_id):
                continue  # Doppelstart-Schutz: this source is still crawling another (or this) sync.
            try:
                start_sync_run(db, sync)
            except Exception as exc:
                logger.exception('confluence refresh: failed to start a run for sync %s (source %s)', sync.id, sync.source_id)
                db.rollback()
                # Surface the failure on the sync itself: this crash happened
                # before (or while) an ImportRun even existed, so without this
                # there is nothing to see beyond the worker log.
                fresh_sync = db.get(ImportSync, sync.id)
                if fresh_sync is not None:
                    fresh_sync.last_error = _truncate(f'failed to start a refresh run: {exc}', _START_FAILURE_MESSAGE_MAX_CHARS)
                    db.commit()
    finally:
        db.close()


@celery_app.task(name=TICK_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def confluence_refresh_tick(self, lock_token: str | None = None) -> None:
    """One scan-and-dispatch cycle, then self-re-enqueue -- see the module
    docstring for the singleton design. `lock_token=None` is the sentinel
    the worker_ready kickstart uses (it doesn't hold the lock yet, unlike
    every self-re-enqueued continuation, which always passes its held
    token); acks_late+reject_on_worker_lost means a hard worker kill mid-tick
    gets redelivered, which -- combined with the lock still being valid --
    is exactly what keeps the chain alive across that crash instead of
    silently dying with it.
    """
    try:
        token = _acquire_or_renew(lock_token)
    except Exception:
        # A Redis error here (unlike a clean None return) is transient, not
        # a legitimate loss of leadership -- re-enqueue with the SAME token
        # this execution was called with (never a freshly acquired one, since
        # acquire/renew itself is what just failed) so the next tick simply
        # retries instead of the whole self-re-enqueuing chain silently
        # dying on one Redis blip.
        logger.exception('confluence refresh: acquire/renew of the leadership lock failed; retrying next tick')
        _reenqueue_tick(self, lock_token)
        return
    if token is None:
        logger.info('confluence refresh: leadership lock unavailable (already held elsewhere, or lease lost); standing down')
        return
    try:
        _dispatch_due_refreshes()
    except Exception:
        logger.exception('confluence refresh tick failed')
    finally:
        _reenqueue_tick(self, token)
