from pathlib import Path
import logging
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone

from celery.signals import worker_process_init, worker_ready
from redis import Redis
from sqlalchemy import and_, case, func, or_, select, update

from app.core.config import settings
from app.database.session import SessionLocal, engine
from app.models.models import ImportRun, ImportRunStatus, Job, JobStatus, Team, User, VlConnection
from app.services.paddle_service import (
    convert_to_markdown_with_details,
    get_paddle_settings,
    get_runtime_capability,
    is_paddle_available,
)
# Module-object import (security.decrypt_vl_api_key) rather than a
# from-import: matches import_tasks.py's late-binding convention for
# security.decrypt_import_credential, keeping the helper monkeypatchable in
# tests.
from app.services import object_store, security
from app.workers import webhook_tasks
from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


@worker_process_init.connect
def _reset_db_pool_after_fork(sender=None, **kwargs) -> None:  # pragma: no cover
    """Drop inherited DB pool references in every freshly forked pool child.

    The prefork master touches the database in the worker_ready recovery hook
    below, which leaves an open (possibly TLS) connection in the module-level
    engine's pool. Children forked afterwards (steady churn under
    CELERY_MAX_TASKS_PER_CHILD) inherit that socket, and two processes
    multiplexing one TLS stream corrupt it -- psycopg then fails with
    "SSL error: decryption failed or bad record mac". dispose(close=False)
    forgets the inherited connections without closing them (they still belong
    to the parent), so each child lazily opens its own fresh pool.
    """
    engine.dispose(close=False)


_RECOVERY_LOCK_KEY = 'worker:recovery:startup-lock'
# Lost-worker recoveries after which a job on a heavy profile is stopped for
# a manual retry with a lower profile instead of being retried on the same
# profile again (the usual cause is the OOM killer). One recovery alone is
# no evidence: a routine scale-down or rolling update also loses the running
# task once.
_PROFILE_DOWNGRADE_AFTER_RECOVERIES = 2
_LOWER_PROFILE_RETRY_MAP = {
    'ppocrv6_medium_structurev3': 'ppocrv6_small_structurev3',
    'ppocrv6_small_structurev3': 'ppocrv6_tiny_structurev3',
    'ppocrv6_medium': 'ppocrv6_tiny',
    'ppocrv6_small': 'ppocrv6_tiny',
}


def _job_stale_cutoff(now: datetime) -> datetime:
    """A RUNNING job last seen before this has lost its worker."""
    return now - timedelta(seconds=settings.job_stale_seconds)


def _job_last_seen():
    # Rows claimed before heartbeats existed fall back to updated_at.
    return func.coalesce(Job.heartbeat_at, Job.updated_at)


def active_running_job_ids(db) -> set[str]:
    """RUNNING jobs whose worker is still heartbeating.

    The database, not `celery inspect`, is the source of truth: inspect
    misses busy or slow workers in a large pool and returns nothing at all on
    a broker hiccup, which made a running job look restartable.
    """
    cutoff = _job_stale_cutoff(datetime.now(timezone.utc))
    return set(
        db.scalars(
            select(Job.id).where(Job.status == JobStatus.RUNNING).where(_job_last_seen() >= cutoff)
        ).all()
    )


def _owns_claim(db, job_id: str, token: str) -> bool:
    """Lock the job row and confirm this attempt still holds its claim.

    Called right before a terminal write: an attempt whose worker was
    presumed lost and whose job was reclaimed must neither overwrite the
    newer attempt's result nor fire its webhooks. On PostgreSQL the row lock
    keeps a concurrent reclaim out until the write has committed.
    """
    current = db.execute(
        select(Job.claim_token).where(Job.id == job_id).with_for_update()
    ).scalar_one_or_none()
    return current is not None and current == token


class _Heartbeat:
    """Refreshes jobs.heartbeat_at while process_job runs.

    Runs in a daemon thread with its own short-lived session, so the task's
    session stays outside any transaction during the conversion. Stops by
    itself once the claim is gone (the job was reclaimed elsewhere).
    """

    def __init__(self, job_id: str, token: str) -> None:
        self._job_id = job_id
        self._token = token
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f'job-heartbeat-{job_id}', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        interval = max(1, int(settings.job_heartbeat_seconds))
        while not self._stop.wait(interval):
            db = SessionLocal()
            try:
                beat = db.execute(
                    update(Job)
                    .where(Job.id == self._job_id, Job.claim_token == self._token)
                    # A heartbeat is not a content change: keep updated_at.
                    .values(heartbeat_at=datetime.now(timezone.utc), updated_at=Job.updated_at)
                )
                db.commit()
                if not beat.rowcount:
                    logger.warning('Job %s was reclaimed elsewhere; stopping its heartbeat', self._job_id)
                    return
            except Exception:
                db.rollback()
                logger.warning('Heartbeat for job %s failed; retrying', self._job_id, exc_info=True)
            finally:
                db.close()


# Scratch dirs under settings.worker_tmp_dir are named job-<pid>-<random>:
# the owning process id tells _purge_orphaned_task_dirs which ones were left
# behind by a process that died without cleaning up.
_TASK_DIR_PREFIX = 'job-'


def _new_task_dir() -> Path:
    """A private scratch directory for one task run; removed when it ends."""
    root = Path(settings.worker_tmp_dir)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f'{_TASK_DIR_PREFIX}{os.getpid()}-', dir=root))


@worker_process_init.connect
def _purge_orphaned_task_dirs(sender=None, **kwargs) -> None:  # pragma: no cover
    """Remove scratch dirs of task processes that died mid-task (hard time
    limit, OOM killer) and so never ran their cleanup. A dir whose owner pid
    no longer exists cannot be in use any more."""
    root = Path(settings.worker_tmp_dir)
    if not root.is_dir():
        return
    for entry in root.iterdir():
        pid_text = entry.name[len(_TASK_DIR_PREFIX):].split('-', 1)[0]
        if not entry.name.startswith(_TASK_DIR_PREFIX) or not pid_text.isdigit():
            continue
        try:
            os.kill(int(pid_text), 0)
        except ProcessLookupError:
            shutil.rmtree(entry, ignore_errors=True)
        except PermissionError:
            continue  # the pid belongs to a live process of another user


def _materialize_upload(db, job: Job, task_dir: Path) -> Path:
    """Copy the job's stored original into the task's scratch directory."""
    if job.upload_object_id is None:
        raise FileNotFoundError(f'Job {job.id} has no stored upload')
    suffix = Path(job.upload_path).suffix or Path(job.original_filename).suffix or '.pdf'
    return object_store.copy_to_path(db, job.upload_object_id, task_dir / f'{job.id}{suffix}')


def _normalize_execution_page_count(details: dict, upload_path: Path) -> dict:
    normalized = {**details}
    if isinstance(normalized.get('page_count'), int):
        return normalized

    structure = normalized.get('structure') if isinstance(normalized.get('structure'), dict) else {}
    structure_page_count = structure.get('page_count') if isinstance(structure.get('page_count'), int) else None
    if structure_page_count is not None:
        normalized['page_count'] = structure_page_count
        return normalized

    # For single-file non-PDF uploads (images, office docs converted to one stream),
    # expose at least a stable page_count value for UI consistency.
    if upload_path.suffix.lower() != '.pdf':
        normalized['page_count'] = 1

    return normalized


def _try_acquire_recovery_lock() -> tuple[Redis | None, str | None]:
    """Acquire a short-lived distributed lock for startup recovery."""
    token = str(uuid.uuid4())
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    acquired = client.set(_RECOVERY_LOCK_KEY, token, nx=True, ex=120)
    if acquired:
        return client, token
    return None, None


def _release_recovery_lock(client: Redis | None, token: str | None) -> None:
    if not client or not token:
        return
    try:
        current = client.get(_RECOVERY_LOCK_KEY)
        if current == token:
            client.delete(_RECOVERY_LOCK_KEY)
    except Exception:
        # Lock has an expiry and will self-heal; no hard failure required here.
        pass


def reap_stale_running_jobs() -> int:
    """Requeue RUNNING jobs whose worker stopped heartbeating.

    Runs at worker start and on every publication tick (see
    app/workers/publication_tasks.py), so a job lost to a killed worker, a
    scale-down or the OOM killer is picked up again within about
    job_stale_seconds -- not only after the broker's visibility timeout or
    the next worker restart. Clearing claim_token fences the lost attempt:
    should its worker still be alive after all, its heartbeat and its result
    write find the claim gone. After job_max_recoveries lost attempts in a
    row the job is failed instead of looping forever.
    """
    db = SessionLocal()
    to_restart: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    failed_jobs: list[Job] = []
    try:
        # skip_locked: a concurrent reaper (startup recovery on another
        # replica) or a terminal write holding the row just skips it.
        stale_jobs = db.scalars(
            select(Job)
            .where(Job.status == JobStatus.RUNNING)
            .where(_job_last_seen() < _job_stale_cutoff(datetime.now(timezone.utc)))
            .order_by(Job.updated_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale_jobs:
            info = job.processing_info if isinstance(job.processing_info, dict) else {}
            # Named job_settings (not `settings`): shadowing the module-level
            # settings would make the job_max_recoveries lookup an UnboundLocalError.
            job_settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
            execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
            recoveries = job.recovery_count + 1
            job.claim_token = None

            if recoveries > settings.job_max_recoveries:
                detail = f'Job was lost by its worker {recoveries} times in a row and has been stopped.'
                job.status = JobStatus.FAILED
                job.error_message = detail
                job.recovery_count = 0
                job.processing_info = {**info, 'execution': {**execution, 'status': 'failed', 'error': detail}}
                failed_jobs.append(job)
                continue

            profile_id = job_settings.get('profile_id') if isinstance(job_settings.get('profile_id'), str) else None
            mode = job_settings.get('mode') if isinstance(job_settings.get('mode'), str) else None
            email = job_settings.get('email') if isinstance(job_settings.get('email'), str) else None
            department = job_settings.get('department') if isinstance(job_settings.get('department'), str) else None

            job.recovery_count = recoveries
            job.status = JobStatus.PENDING
            job.error_message = None
            job.processing_info = {
                **info,
                'settings': job_settings,
                'execution': {
                    **execution,
                    'status': 'requeued',
                    'detail': 'Job was running during worker restart and has been requeued.',
                },
            }
            to_restart.append((job.id, profile_id, mode, email, department))

        db.commit()
        for job in failed_jobs:
            logger.warning('Stopping job %s after %s lost attempts', job.id, settings.job_max_recoveries + 1)
            try:
                webhook_tasks.dispatch_job_event(db, job, 'job.failed')
            except Exception:  # pragma: no cover - webhooks must never break recovery
                logger.exception('webhook dispatch failed for job %s (job.failed)', job.id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for job_id, profile_id, mode, email, department in to_restart:
        process_job.delay(job_id, profile_id, mode, email, department)
        logger.warning('Requeued job %s after its worker stopped heartbeating', job_id)

    return len(to_restart)


def requeue_running_jobs_after_restart() -> int:
    """Startup recovery: lost RUNNING jobs, stale import runs and stranded
    attachment children.

    This makes processing resilient across worker restarts and hard kills.
    The lost-job part also runs periodically (reap_stale_running_jobs).
    """
    db = SessionLocal()
    runs_to_requeue: list[tuple[str, int]] = []
    stranded_pending_children: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    stale_run_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.import_stale_run_seconds)
    try:
        # Import runs whose worker died without redelivery (hard-limit kill /
        # lost message): stale 'running' runs are replayed with their previous
        # chunk_seq -- the stale-lease reclaim in import_confluence makes that
        # safe, and a live run's fresh heartbeat makes the replay a no-op.
        # Stale 'pending' runs (creation message lost / send_task failed --
        # their updated_at is the creation time, untouched until the first
        # claim) are replayed with their CURRENT seq: the normal claim path
        # accepts pending status directly, and its chunk_seq guard makes a
        # duplicate creation message a no-op.
        stale_runs = db.scalars(
            select(ImportRun)
            .where(ImportRun.status.in_([ImportRunStatus.PENDING, ImportRunStatus.RUNNING]))
            .where(ImportRun.updated_at < stale_run_cutoff)
        ).all()
        runs_to_requeue = [
            (run.id, run.chunk_seq if run.status == ImportRunStatus.PENDING else run.chunk_seq - 1)
            for run in stale_runs
        ]

        # SH-04: PENDING attachment-OCR children of a run that already
        # finished (FINISHED/FAILED/CANCELLED) -- _finalize_run's backstop
        # re-send may have failed for these, or never reached them. Gated by
        # the same staleness cutoff as the ImportRun branch above so a child
        # just committed by a still-in-progress run isn't double-sent.
        terminal_pending_jobs = db.scalars(
            select(Job)
            .join(ImportRun, ImportRun.id == Job.import_run_id)
            .where(Job.status == JobStatus.PENDING)
            .where(Job.updated_at < stale_run_cutoff)
            .where(ImportRun.status.in_([
                ImportRunStatus.FINISHED, ImportRunStatus.FAILED, ImportRunStatus.CANCELLED,
            ]))
        ).all()
        for job in terminal_pending_jobs:
            info = job.processing_info if isinstance(job.processing_info, dict) else {}
            job_settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
            profile_id = job_settings.get('profile_id') if isinstance(job_settings.get('profile_id'), str) else None
            mode = job_settings.get('mode') if isinstance(job_settings.get('mode'), str) else None
            email = job_settings.get('email') if isinstance(job_settings.get('email'), str) else None
            department = job_settings.get('department') if isinstance(job_settings.get('department'), str) else None
            stranded_pending_children.append((job.id, profile_id, mode, email, department))

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    restarted_jobs = reap_stale_running_jobs()

    for job_id, profile_id, mode, email, department in stranded_pending_children:
        # SH-04: still PENDING, so the normal PENDING->RUNNING claim in
        # process_job makes a duplicate send (e.g. a concurrent late
        # _finalize_run backstop) a no-op -- same idempotency as to_restart.
        process_job.delay(job_id, profile_id, mode, email, department)
        logger.warning('Requeued stranded PENDING child job %s of a terminal import run', job_id)

    for run_id, replay_seq in runs_to_requeue:
        # By name so this module keeps zero imports from import_tasks at call
        # time; running runs replay with the PREVIOUS seq (reclaim path),
        # pending runs with their current seq (normal claim path).
        celery_app.send_task('import_confluence', args=[run_id, replay_seq])
        logger.warning('Requeued stale import run %s at chunk_seq %s', run_id, replay_seq)

    return restarted_jobs + len(runs_to_requeue) + len(stranded_pending_children)


@worker_ready.connect
def _recover_jobs_on_worker_ready(sender=None, **kwargs) -> None:  # pragma: no cover
    lock_client: Redis | None = None
    lock_token: str | None = None
    try:
        lock_client, lock_token = _try_acquire_recovery_lock()
        if not lock_client:
            logger.info('Skipping startup recovery; another worker instance is handling it.')
            return
        restarted = requeue_running_jobs_after_restart()
        if restarted:
            logger.warning('Recovered %s RUNNING job(s) after worker restart', restarted)
    except Exception as exc:
        logger.exception('Failed to recover RUNNING jobs after worker restart: %s', exc)
    finally:
        _release_recovery_lock(lock_client, lock_token)
        # The prefork master needs the database only for this recovery; give
        # its connection back instead of holding it idle for the pod's
        # lifetime (every pod counts against max_connections).
        engine.dispose()

    # Kickstart the confluence-refresh self-re-enqueue chain (see
    # app/workers/refresh_tasks.py's module docstring for the full design).
    # Deliberately NOT gated on the recovery lock above -- that one is a
    # one-shot startup action and releases (or expires) in seconds, whereas
    # the refresh chain must be revived by ANY future restart if it ever
    # died. confluence_refresh_tick is itself the single source of truth for
    # whether a chain is already alive (its own long-lived NX lock, checked
    # on every call including this token-less kickstart), so a redundant
    # send from a simultaneous multi-replica startup is a harmless no-op.
    try:
        celery_app.send_task('confluence_refresh_tick', args=[None])
    except Exception:
        logger.exception('Failed to kickstart the confluence-refresh tick chain')

    # Kickstart the session-cleanup self-re-enqueue chain (see
    # app/workers/session_cleanup_tasks.py's module docstring) -- same
    # not-gated-on-the-recovery-lock reasoning as the confluence-refresh
    # kickstart above: session_cleanup_tick owns its own long-lived NX lock
    # and is itself the single source of truth for whether a chain is
    # already alive, so a redundant send here is a harmless no-op.
    try:
        celery_app.send_task('session_cleanup_tick', args=[None])
    except Exception:
        logger.exception('Failed to kickstart the session-cleanup tick chain')

    # Publication outbox reconciler: the release row is committed before any
    # queue trigger, so a lost queue message is recovered by this chain.
    try:
        celery_app.send_task('publication_tick', args=[None])
    except Exception:
        logger.exception('Failed to kickstart the publication tick chain')


@celery_app.task(name='process_job', bind=True, acks_late=True, reject_on_worker_lost=True)
def process_job(
    self,
    job_id: str,
    profile_id: str | None = None,
    mode: str | None = None,
    email: str | None = None,
    department: str | None = None,
) -> None:
    db = SessionLocal()
    token: str | None = None
    heartbeat: _Heartbeat | None = None
    task_dir: Path | None = None
    try:
        now = datetime.now(timezone.utc)
        # Claim a PENDING job, or reclaim a RUNNING one whose worker stopped
        # heartbeating (lost worker, redelivered message). A live job's fresh
        # heartbeat turns every duplicate delivery into a no-op. The fresh
        # claim_token fences any older attempt out of the result write.
        new_token = str(uuid.uuid4())
        claimed = db.execute(
            update(Job)
            .where(Job.id == job_id)
            .where(or_(
                Job.status == JobStatus.PENDING,
                and_(Job.status == JobStatus.RUNNING, _job_last_seen() < _job_stale_cutoff(now)),
            ))
            .values(
                status=JobStatus.RUNNING,
                error_message=None,
                updated_at=now,
                heartbeat_at=now,
                claim_token=new_token,
                recovery_count=case(
                    (Job.status == JobStatus.RUNNING, Job.recovery_count + 1),
                    else_=Job.recovery_count,
                ),
            )
        )

        if not claimed.rowcount:
            return

        db.commit()
        token = new_token
        heartbeat = _Heartbeat(job_id, token)
        heartbeat.start()
        job = db.get(Job, job_id)
        if job is None:
            return

        effective_profile_id = profile_id

        # Do not auto-downgrade the profile after repeated worker loss (the
        # usual cause is the OOM killer on a heavy profile). Mark the job
        # failed with guidance so users can explicitly retry with a lower
        # profile; a single lost attempt is simply retried.
        if job.recovery_count >= _PROFILE_DOWNGRADE_AFTER_RECOVERIES and isinstance(profile_id, str):
            suggested = _LOWER_PROFILE_RETRY_MAP.get(profile_id)
            if suggested and suggested != profile_id:
                warning_detail = (
                    f'Worker-loss redelivery detected for profile {profile_id}. '
                    f'Automatic fallback is disabled. Retry manually with lower profile {suggested}.'
                )
                job.status = JobStatus.FAILED
                job.error_message = warning_detail
                existing = job.processing_info if isinstance(job.processing_info, dict) else {}
                settings = existing.get('settings') if isinstance(existing.get('settings'), dict) else {}
                runtime = existing.get('runtime') if isinstance(existing.get('runtime'), dict) else {}
                job.processing_info = {
                    **existing,
                    'settings': {
                        **settings,
                        'requested_profile_id': profile_id,
                        'profile_id': profile_id,
                    },
                    'runtime': runtime,
                    'execution': {
                        'status': 'failed',
                        'warning': warning_detail,
                        'error': warning_detail,
                        'suggested_profile_id': suggested,
                        'profile_id': profile_id,
                    },
                }
                job.recovery_count = 0
                db.commit()
                logger.warning('Stopping redelivered job %s for manual retry: %s -> %s', job_id, profile_id, suggested)
                try:
                    webhook_tasks.dispatch_job_event(db, job, 'job.failed')
                except Exception:  # pragma: no cover - webhooks must never break job completion
                    logger.exception('webhook dispatch failed for job %s (job.failed)', job_id)
                return

        runtime = get_paddle_settings()
        capability = get_runtime_capability()
        existing_info = job.processing_info if isinstance(job.processing_info, dict) else {}
        existing_settings = existing_info.get('settings') if isinstance(existing_info.get('settings'), dict) else {}

        # Benchmark variant jobs (see app/api/benchmarks.py) stamp
        # vl_connection_id into settings at creation time; every other job
        # leaves it unset and vl_override stays None, which is a no-op for
        # every non-openai_vision profile and byte-identical to the
        # env-based openai_vision path when it IS the profile.
        vl_connection_id = existing_settings.get('vl_connection_id') if isinstance(existing_settings, dict) else None
        vl_override: dict[str, str] | None = None
        if vl_connection_id:
            vl_conn = db.get(VlConnection, vl_connection_id)
            if vl_conn is None or not vl_conn.enabled:
                job.status = JobStatus.FAILED
                job.error_message = 'VL connection is no longer available'
                job.processing_info = {
                    **job.processing_info,
                    'execution': {'status': 'failed', 'error': job.error_message},
                }
                job.recovery_count = 0
                db.commit()
                try:
                    webhook_tasks.dispatch_job_event(db, job, 'job.failed')
                except Exception:  # pragma: no cover - webhooks must never break job completion
                    logger.exception('webhook dispatch failed for job %s (job.failed)', job_id)
                return
            vl_override = {
                'base_url': vl_conn.base_url,
                'api_key': security.decrypt_vl_api_key(vl_conn.api_key_encrypted),
                'model': vl_conn.model,
                'system_prompt': vl_conn.system_prompt,
                # Used by paddle_service to label user-facing error messages
                # (job.error_message, benchmark report/export) instead of the
                # admin-configured base_url -- see
                # paddle_service._call_vision_chat_api's docstring.
                'name': vl_conn.name,
            }

        # Callers translate a 'vl:<connection_id>' selection into the real
        # pipeline id before dispatch (effective_pipeline_profile_id), so this
        # task's profile_id parameter never carries the vl: form. The vl:
        # selection recorded in settings at creation time is the job's
        # user-facing profile identity (jobs table, detail page, restart's
        # previous_profile_id audit) and must survive the RUNNING transition —
        # vl_override resolution reads settings.vl_connection_id either way.
        existing_profile = (
            existing_settings.get('profile_id')
            if isinstance(existing_settings.get('profile_id'), str)
            else None
        )
        keep_vl_identity = bool(existing_profile) and existing_profile.startswith('vl:')

        execution_payload = {'status': 'running', 'started_at': now.isoformat()}
        job.processing_info = {
            'settings': {
                **existing_settings,
                'default_profile': runtime.get('default_profile'),
                'requested_profile_id': existing_profile if keep_vl_identity else profile_id,
                'profile_id': existing_profile if keep_vl_identity else effective_profile_id,
                'timeout_seconds': runtime.get('timeout_seconds'),
                'mode': mode,
                'email': email,
                'department': department,
            },
            'runtime': capability,
            'execution': execution_payload,
        }
        db.commit()

        task_dir = _new_task_dir()
        upload_path = _materialize_upload(db, job, task_dir)

        # Enrich the frontmatter metadata with everything the DB already
        # knows about this job -- job identity/version/hash/lineage plus the
        # uploader's identity -- so _build_rag_frontmatter can render it
        # without paddle_service needing its own DB access. profile_id and
        # engine are deliberately NOT set here: only paddle_service knows
        # which pipeline/fallback actually ran for this conversion.
        owner_username: str | None = None
        team_name: str | None = None
        if job.owner_id:
            owner = db.get(User, job.owner_id)
            if owner is not None:
                owner_username = owner.username
                if owner.team_id:
                    owner_team = db.get(Team, owner.team_id)
                    team_name = owner_team.name if owner_team is not None else None

        # Collection identity (see app/api/routes.py's upload_document_to_
        # collection/start_collection_processing, which stamp both into
        # processing_info.settings) -- only set when the job actually belongs
        # to a collection, so _build_rag_frontmatter's 'collection'/
        # 'collection_name' fields stay absent for ordinary single uploads.
        collection_slug = existing_settings.get('collection_slug')
        collection_name = existing_settings.get('collection_name')

        metadata = {
            'mode': mode or 'single',
            'email': email or '',
            'department': department or '',
            'job_id': job.id,
            'original_filename': job.original_filename,
            'document_version': job.document_version,
            'content_sha256': job.content_sha256,
            'previous_job_id': job.previous_job_id,
            'uploaded_by': owner_username,
            'team': team_name,
            'tags': sorted(tag.name for tag in job.tags),
            'collection_slug': collection_slug if isinstance(collection_slug, str) else None,
            'collection_name': collection_name if isinstance(collection_name, str) else None,
        }
        # End the read transaction the lookups above opened: the conversion
        # can run for many minutes and must not hold a connection "idle in
        # transaction" (and its table locks, which would block migrations).
        db.commit()

        markdown, details = convert_to_markdown_with_details(
            str(upload_path),
            profile_id=effective_profile_id,
            vl_override=vl_override,
            metadata=metadata,
        )
        details = _normalize_execution_page_count(details, upload_path)
        if not _owns_claim(db, job_id, token):
            db.rollback()
            logger.warning('Discarding result of job %s: another attempt has taken it over', job_id)
            return

        finished_now = datetime.now(timezone.utc)
        job.status = JobStatus.FINISHED
        job.result_markdown = markdown
        existing = job.processing_info if isinstance(job.processing_info, dict) else {}
        job.processing_info = {
            **existing,
            'execution': {
                'status': 'finished',
                **details,
                'started_at': now.isoformat(),
                'finished_at': finished_now.isoformat(),
                'duration_seconds': round((finished_now - now).total_seconds(), 3),
            },
        }
        job.error_message = None
        job.recovery_count = 0
        db.commit()
        try:
            webhook_tasks.dispatch_job_event(db, job, 'job.finished')
        except Exception:  # pragma: no cover - webhooks must never break job completion
            logger.exception('webhook dispatch failed for job %s (job.finished)', job_id)
        # document.processed (contracts/events/document.processed.md) rides
        # alongside job.finished -- same per-task opt-in, own try/except so a
        # failure here can never mask (or get masked by) the job.finished
        # dispatch above, and never fires for job.failed.
        try:
            webhook_tasks.dispatch_job_event(db, job, 'document.processed')
        except Exception:  # pragma: no cover - webhooks must never break job completion
            logger.exception('webhook dispatch failed for job %s (document.processed)', job_id)
    except Exception as exc:  # pragma: no cover
        db.rollback()
        # Only the attempt that still holds the claim may fail the job; a
        # failure before the claim, or after a takeover, leaves it alone.
        job = db.get(Job, job_id) if token is not None and _owns_claim(db, job_id, token) else None
        if job is not None:
            failed_now = datetime.now(timezone.utc)
            job.status = JobStatus.FAILED
            job.recovery_count = 0
            job.error_message = str(exc)
            existing = job.processing_info if isinstance(job.processing_info, dict) else {}
            job.processing_info = {
                **existing,
                'execution': {
                    'status': 'failed',
                    'error': str(exc),
                    'started_at': now.isoformat(),
                    'finished_at': failed_now.isoformat(),
                    'duration_seconds': round((failed_now - now).total_seconds(), 3),
                },
            }
            db.commit()
            try:
                webhook_tasks.dispatch_job_event(db, job, 'job.failed')
            except Exception:  # pragma: no cover - webhooks must never break job completion
                logger.exception('webhook dispatch failed for job %s (job.failed)', job_id)
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        if task_dir is not None:
            shutil.rmtree(task_dir, ignore_errors=True)
        db.close()


@celery_app.task(name='probe_paddle')
def probe_paddle() -> dict[str, str | None]:
    if is_paddle_available():
        return {'status': 'running', 'detail': None, **get_runtime_capability()}
    return {
        'status': 'stopped',
        'detail': 'PaddleOCR package not available in worker image',
        **get_runtime_capability(),
    }


# The worker entrypoint is `celery -A app.workers.tasks`, so any task module
# must be imported from here to register with the app.
import app.workers.import_tasks  # noqa: E402,F401  (registers import_confluence)
import app.workers.refresh_tasks  # noqa: E402,F401  (registers confluence_refresh_tick)
import app.workers.session_cleanup_tasks  # noqa: E402,F401  (registers session_cleanup_tick)
import app.workers.publication_tasks  # noqa: E402,F401  (registers publication tasks)
# webhook_tasks (registers deliver_webhook) is already imported above (as
# `webhook_tasks`, module-object style) for the completion hooks' own use --
# no separate registration-only import needed here, unlike the three above.
