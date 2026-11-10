"""Admin re-ingest: re-convert every document and re-import every connection
with the current pipeline (converters, quality gate), and -- as a
deliberate admin decision -- release again what was released before.

Why new versions: a release is an immutable snapshot (DocumentRelease.job_id
is UNIQUE/RESTRICT, and Knowledge rejects a second release for the same
job), so a released document is re-converted as the next version of its
chain (document_version+1, previous_job_id = the released job). Knowledge
supersedes the old version once the new one is indexed. Unreleased
documents are re-converted in place, like a restart.

Plan (per chain head, uploads only -- imported pages and their attachments
belong to their connection):
- `rerelease` (only when the admin chose to release again): the head is
  released and live in Knowledge (per Knowledge's own inventory -- a release
  row alone may be superseded or undelivered) -> new version, released again
  when done (unless the body and quality grade did not change: then the new
  version is discarded and Knowledge keeps the current one untouched).
  Without that choice released documents are left as they are: an
  unreleased version on top of a released one would break Knowledge's
  one-hop supersede later.
- `reprocess`: the head is not released -> in place; review decisions
  (skip/park), the original upload time and the webhook connection are
  carried over (process_job drops them at its claim).
- skipped: still processing, withdrawn, no stored original, manually edited
  (unless the admin includes edited documents), released but its space is
  gone or it is password protected.
Connections: every ImportSync gets a forced refresh run (start_sync_run with
`force_reimport`): unchanged pages are converted again and chained as the next
version (import_tasks._import_one_page); afterwards every new version whose
predecessor was released and live is released again. The connection's own
auto-release does not act on a forced run. A forced run that does not finish
is rolled back (page states point at the previous versions again, its jobs
are deleted -- none of them could ever be released).

Execution: the work list is ReingestItem rows; `advance_run` performs one
lease-guarded step (finalize finished items, start new ones up to
settings.reingest_max_in_flight, one connection run at a time). It is
nudged by the API, by every re-ingested job and import run when it ends,
and by the publication tick as a safety net -- no long-running task, so a
run survives worker restarts.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.database.session import SessionLocal
from app.models.models import (
    REINGEST_ACTIVE_STATUSES,
    BackupRun,
    BackupRunKind,
    BackupRunStatus,
    Collection,
    CollectionRole,
    DocumentRelease,
    ImportRun,
    ImportRunStatus,
    ImportSource,
    ImportSync,
    Job,
    JobArtifact,
    JobStatus,
    KnowledgeWithdrawal,
    ReingestItem,
    ReingestRun,
    ReingestRunStatus,
    User,
)
from app.services.publications import (
    PublicationValidationError,
    _parse_frontmatter,
    canonical_snapshot,
    publication_configured,
)
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import aliased

# Knowledge document states that still stand for a released version; a
# 'superseded' document (or none at all) means a later version replaced it.
_KNOWLEDGE_LIVE = frozenset({'pending', 'indexed', 'blocked', 'failed'})

logger = logging.getLogger(__name__)

ADVANCE_TASK_NAME = 'reingest_advance'
# The backup run lock (app/api/backup.py): starting a re-ingest and starting
# a backup restore serialize on the same key, so neither can slip past the
# other's "is the other one active?" check.
_RUN_LOCK_KEY = 7302000101
_IMPORT_MODES = ('import', 'import_attachment')
# A queued job still PENDING after this long most likely lost its queue
# message; process_job's claim makes a re-send a no-op otherwise.
_REDISPATCH_AFTER = timedelta(minutes=10)
_MESSAGE_CHARS = 2000


class ReingestError(Exception):
    """Refusal to start a run; `code` maps to the API's HTTP status."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _settings(job: Job) -> dict:
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    value = info.get('settings')
    return value if isinstance(value, dict) else {}


def _info(job: Job) -> dict:
    return job.processing_info if isinstance(job.processing_info, dict) else {}


def is_manually_edited(job: Job) -> bool:
    """The current markdown holds human corrections: an editor save
    (processing_info.editor, dropped again by a restart) or a portal edit
    version. Re-converting from the original would discard them."""
    info = _info(job)
    return bool(info.get('editor')) or bool(info.get('portal_edit'))


def _short(message: object) -> str | None:
    text_value = str(message) if message is not None else ''
    return text_value[:_MESSAGE_CHARS] or None


# --- Planning ---------------------------------------------------------------------

@dataclass
class DocumentPlan:
    job_id: str
    label: str
    action: str | None  # 'rerelease' | 'reprocess' | None (skipped)
    reason: str | None = None


@dataclass
class ConnectionPlan:
    sync_id: str
    label: str
    ready: bool
    reason: str | None = None


def _classify(
    job: Job,
    *,
    released: bool,
    withdrawn: bool,
    collection_exists: bool,
    include_edited: bool,
    rerelease: bool,
    knowledge_status: str | None,
    knowledge_known: bool,
) -> tuple[str | None, str | None]:
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        return None, 'running'
    if withdrawn:
        return None, 'withdrawn'
    if not job.upload_object_id:
        return None, 'no_original'
    if is_manually_edited(job) and not include_edited:
        return None, 'edited'
    if released:
        if not rerelease:
            return None, 'released_kept'
        if not collection_exists:
            return None, 'collection_missing'
        if job.password_hash:
            return None, 'protected'
        if not knowledge_known:
            return None, 'knowledge_unavailable'
        if knowledge_status not in _KNOWLEDGE_LIVE:
            return None, 'not_live'
        return 'rerelease', None
    return 'reprocess', None


def knowledge_inventory() -> dict[str, str]:
    """job_id -> Knowledge document status. Raises KnowledgeInventoryError."""
    from app.services.knowledge_orphans import _client, _fetch_pages

    statuses: dict[str, str] = {}
    with _client() as client:
        for items in _fetch_pages(client):
            for item in items:
                statuses[item.job_id] = item.status
    return statuses


def _try_inventory(needed: bool) -> dict[str, str] | None:
    if not needed or not publication_configured():
        return None
    from app.services.knowledge_orphans import KnowledgeInventoryError

    try:
        return knowledge_inventory()
    except KnowledgeInventoryError:
        logger.warning('re-ingest: Knowledge inventory unavailable', exc_info=True)
        return None


def _chain_heads(db) -> list[Job]:
    """Newest version of every uploaded document. Loads only the columns
    the plan needs (never the markdown)."""
    from sqlalchemy.orm import load_only

    successor = aliased(Job)
    return list(db.scalars(
        select(Job)
        .options(load_only(
            Job.id, Job.original_filename, Job.status, Job.upload_object_id, Job.password_hash,
            Job.processing_info, Job.import_run_id, Job.benchmark_run_id, Job.mail_message_id,
        ))
        .where(Job.benchmark_run_id.is_(None), Job.mail_message_id.is_(None))
        .where(~select(successor.id).where(successor.previous_job_id == Job.id).exists())
        .order_by(Job.created_at, Job.id)
    ))


def plan_documents(
    db, *, include_edited: bool, rerelease: bool, knowledge: dict[str, str] | None
) -> tuple[list[DocumentPlan], int]:
    """Returns (plans for uploaded documents, number of imported documents
    -- those are re-ingested through their connection). `knowledge` is
    Knowledge's inventory (None: unavailable)."""
    released = set(db.scalars(select(DocumentRelease.job_id)))
    withdrawn = set(db.scalars(select(KnowledgeWithdrawal.job_id)))
    collections = set(db.scalars(select(Collection.id)))
    # A deleted sync leaves its runs with sync_id NULL (the documents stay):
    # no connection re-imports them.
    synced_runs = set(db.scalars(select(ImportRun.id).where(ImportRun.sync_id.is_not(None))))
    plans: list[DocumentPlan] = []
    imported = 0
    for job in _chain_heads(db):
        job_settings = _settings(job)
        if job_settings.get('mode') in _IMPORT_MODES:
            if job.import_run_id in synced_runs:
                imported += 1
            else:
                plans.append(DocumentPlan(job.id, (job.original_filename or job.id)[:255], None, 'connection_missing'))
            continue
        action, reason = _classify(
            job,
            released=job.id in released,
            withdrawn=job.id in withdrawn,
            collection_exists=job_settings.get('collection_id') in collections,
            include_edited=include_edited,
            rerelease=rerelease,
            knowledge_status=(knowledge or {}).get(job.id),
            knowledge_known=knowledge is not None,
        )
        plans.append(DocumentPlan(job.id, (job.original_filename or job.id)[:255], action, reason))
    return plans, imported


def _connection_blocker(db, sync: ImportSync) -> tuple[str | None, str]:
    from app.services.collection_access import collection_role, role_at_least

    source = db.get(ImportSource, sync.source_id) if sync.source_id else None
    scope = sync.root_page_title or sync.scope_value or ''
    label = f'{source.name} · {scope}' if source is not None else scope
    if source is None:
        return 'source_missing', label[:255]
    owner = db.get(User, source.owner_id) if source.owner_id else None
    if owner is None or not owner.is_active:
        return 'owner_inactive', label[:255]
    if not source.server_kind:
        return 'source_untested', label[:255]
    if sync.collection_id:
        collection = db.get(Collection, sync.collection_id)
        if collection is None:
            return 'collection_missing', label[:255]
        if not role_at_least(collection_role(db, collection, owner), CollectionRole.MEMBER):
            return 'collection_access', label[:255]
    return None, label[:255]


def plan_connections(db) -> list[ConnectionPlan]:
    plans = []
    for sync in db.scalars(select(ImportSync).order_by(ImportSync.created_at, ImportSync.id)):
        reason, label = _connection_blocker(db, sync)
        plans.append(ConnectionPlan(sync.id, label, reason is None, reason))
    return plans


def _live_import_job_ids(db, knowledge: dict[str, str] | None) -> list[str]:
    """Released, live versions of imported pages and attachments -- run-wide,
    not per sync: page states are shared by every sync of a source and space,
    and a version may come from a sync that was deleted since. A forced run
    converts these again and the re-ingest releases their successors."""
    if not knowledge:
        return []
    released = set(db.scalars(select(DocumentRelease.job_id)))
    live = [job_id for job_id, state in knowledge.items() if state in _KNOWLEDGE_LIVE and job_id in released]
    found: list[str] = []
    for start in range(0, len(live), 500):
        found.extend(db.scalars(
            select(Job.id).where(Job.id.in_(live[start:start + 500]), Job.import_run_id.is_not(None))
        ))
    return sorted(found)


_LIVE_CACHE: dict[str, frozenset[str]] = {}


def live_job_ids(db, run_id: str | None) -> frozenset[str]:
    """The run's live set (fixed when it was planned), cached per process:
    the import worker asks for every page."""
    if not run_id:
        return frozenset()
    cached = _LIVE_CACHE.get(run_id)
    if cached is None:
        run = db.get(ReingestRun, run_id)
        options = run.options if run is not None and isinstance(run.options, dict) else {}
        cached = frozenset(options.get('live_job_ids') or [])
        if len(_LIVE_CACHE) > 16:
            _LIVE_CACHE.clear()
        _LIVE_CACHE[run_id] = cached
    return cached


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def preview(db, *, include_edited: bool = False, rerelease: bool = True) -> dict:
    knowledge = _try_inventory(rerelease)
    documents, imported = plan_documents(db, include_edited=include_edited, rerelease=rerelease, knowledge=knowledge)
    connections = plan_connections(db)
    return {
        'documents': {
            'rerelease': sum(1 for plan in documents if plan.action == 'rerelease'),
            'reprocess': sum(1 for plan in documents if plan.action == 'reprocess'),
            'skipped': _count(plan.reason for plan in documents if plan.action is None),
            'imported': imported,
        },
        'connections': {
            'ready': sum(1 for plan in connections if plan.ready),
            'blocked': _count(plan.reason for plan in connections if not plan.ready),
        },
        'publication_configured': publication_configured(),
        'knowledge_available': knowledge is not None,
    }


# --- Run lifecycle -------------------------------------------------------------------

def active_run(db) -> ReingestRun | None:
    return db.scalar(
        select(ReingestRun).where(ReingestRun.status.in_(REINGEST_ACTIVE_STATUSES)).order_by(ReingestRun.created_at.desc())
    )


def _backup_import_running(db) -> bool:
    # Same staleness cutoff as app/api/backup._fail_stale_runs: a restore
    # whose pod died must not block re-ingests until the next backup action.
    cutoff = _now() - timedelta(seconds=settings.backup_run_stale_seconds)
    return db.scalar(
        select(BackupRun.id).where(
            BackupRun.kind == BackupRunKind.IMPORT,
            BackupRun.status.in_((BackupRunStatus.QUEUED, BackupRunStatus.RUNNING)),
            BackupRun.created_at >= cutoff,
        )
    ) is not None


def start_run(
    db,
    admin: User,
    *,
    include_documents: bool,
    include_connections: bool,
    rerelease: bool,
    accept_quality_warnings: bool,
    include_edited: bool,
    expected_documents: int,
    expected_connections: int,
    expected_rereleases: int | None = None,
) -> ReingestRun:
    """Create the run and its work list -- refused while another run or a
    backup import is active, or when the inventory changed since the
    preview the admin confirmed (expected counts). With
    `expected_rereleases` only the publishing part must match exactly:
    documents finishing or arriving between preview and confirmation only
    change what is re-processed in place."""
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _RUN_LOCK_KEY})
    if active_run(db) is not None:
        raise ReingestError('conflict', 'Es läuft bereits ein Re-Ingest. Bitte warten Sie, bis er abgeschlossen ist.')
    if _backup_import_running(db):
        raise ReingestError('conflict', 'Es läuft gerade eine Wiederherstellung. Bitte warten Sie, bis diese abgeschlossen ist.')
    if rerelease and not publication_configured():
        raise ReingestError('unavailable', 'Die Veröffentlichung an den Wissensdienst ist nicht konfiguriert.')
    if not include_documents and not include_connections:
        raise ReingestError('invalid', 'Bitte Dokumente und/oder Connections auswählen.')

    knowledge = _try_inventory(rerelease)
    if rerelease and knowledge is None:
        raise ReingestError('unavailable', 'Der Bestand des Wissensdienstes ist nicht abrufbar; ohne ihn lässt sich nicht prüfen, was live ist.')
    documents = (
        plan_documents(db, include_edited=include_edited, rerelease=rerelease, knowledge=knowledge)[0]
        if include_documents else []
    )
    connections = plan_connections(db) if include_connections else []
    actionable = sum(1 for plan in documents if plan.action)
    rereleases = sum(1 for plan in documents if plan.action == 'rerelease')
    ready = sum(1 for plan in connections if plan.ready)
    documents_changed = (
        rereleases != expected_rereleases if expected_rereleases is not None else actionable != expected_documents
    )
    if documents_changed or ready != expected_connections:
        raise ReingestError('conflict', 'Der Bestand hat sich seit der Vorschau geändert. Bitte die Vorschau neu laden.')
    if actionable == 0 and ready == 0:
        raise ReingestError('conflict', 'Es gibt nichts neu einzulesen.')

    run = ReingestRun(
        status=ReingestRunStatus.RUNNING,
        created_by=admin.id,
        options={
            'include_documents': include_documents,
            'include_connections': include_connections,
            'rerelease': rerelease,
            'accept_quality_warnings': accept_quality_warnings,
            'include_edited': include_edited,
            # Not part of the API response (app/api/reingest.py strips it).
            'live_job_ids': _live_import_job_ids(db, knowledge) if include_connections and rerelease else [],
        },
    )
    db.add(run)
    db.flush()
    for plan in documents:
        db.add(ReingestItem(
            run_id=run.id, kind='document', action=plan.action or 'none', label=plan.label, job_id=plan.job_id,
            status='pending' if plan.action else 'skipped', result=None if plan.action else plan.reason,
        ))
    for plan in connections:
        db.add(ReingestItem(
            run_id=run.id, kind='connection', action='reimport', label=plan.label, sync_id=plan.sync_id,
            status='pending' if plan.ready else 'skipped', result=None if plan.ready else plan.reason,
        ))
    db.commit()
    return run


def cancel_run(db, run: ReingestRun) -> ReingestRun:
    """Stop starting new items. Items already processing are finished (and
    released) normally: abandoning them would leave unreleased new versions
    -- and a forced import run cut short would leave pages on versions that
    can never be released."""
    cancelled = db.execute(
        update(ReingestRun)
        .where(ReingestRun.id == run.id, ReingestRun.status == ReingestRunStatus.RUNNING)
        .values(status=ReingestRunStatus.CANCELLING)
    ).rowcount
    if cancelled:
        db.execute(
            update(ReingestItem)
            .where(ReingestItem.run_id == run.id, ReingestItem.status == 'pending')
            .values(status='skipped', result='cancelled')
        )
    db.commit()
    return db.get(ReingestRun, run.id, populate_existing=True)


def item_counts(db, run_id: str) -> dict:
    """{'document': {'pending': n, ..., 'results': {...}}, 'connection': {...}}."""
    counts: dict[str, dict] = {'document': {'results': {}}, 'connection': {'results': {}}}
    rows = db.execute(
        select(ReingestItem.kind, ReingestItem.status, ReingestItem.result, func.count())
        .where(ReingestItem.run_id == run_id)
        .group_by(ReingestItem.kind, ReingestItem.status, ReingestItem.result)
    ).all()
    for kind, item_status, result, count in rows:
        bucket = counts.setdefault(kind, {'results': {}})
        bucket[item_status] = bucket.get(item_status, 0) + count
        if result:
            bucket['results'][result] = bucket['results'].get(result, 0) + count
    return counts


def nudge(run_id: str) -> None:
    """Ask a worker to advance the run now (best effort; the publication
    tick nudges active runs anyway)."""
    from app.workers.celery_app import celery_app

    try:
        celery_app.send_task(ADVANCE_TASK_NAME, args=[run_id])
    except Exception:
        logger.exception('could not enqueue a re-ingest step for run %s', run_id)


def _marker(job: Job | None) -> dict:
    """settings.reingest: what a re-ingest keeps on the job while it is being
    converted again -- process_job keeps settings through its claim (it
    drops everything else), and backups include it."""
    marker = _settings(job).get('reingest') if job is not None else None
    return marker if isinstance(marker, dict) else {}


def nudge_for_job(job: Job) -> None:
    run_id = _marker(job).get('run_id')
    if isinstance(run_id, str) and run_id:
        nudge(run_id)


def nudge_active_runs() -> int:
    """Publication-tick safety net: a run whose nudges were lost still advances."""
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(ReingestRun.id).where(ReingestRun.status.in_(REINGEST_ACTIVE_STATUSES))))
    for run_id in run_ids:
        nudge(run_id)
    return len(run_ids)


# --- Advancing -------------------------------------------------------------------------

# Work per step (finalized or started items, releases): keeps a step well
# inside its lease; a step that used it up nudges the next one.
_STEP_BUDGET = 25
_ITEM_MAX_ATTEMPTS = 3
_RUN_MAX_STEP_FAILURES = 5


class _LeaseLost(Exception):
    """Another step owns the run now; this one must stop writing."""


class _Step:
    def __init__(self, db, run_id: str, token: str):
        self.db = db
        self.run_id = run_id
        self.token = token
        self.budget = _STEP_BUDGET
        # Items that raised in this step: retried on a later step, not now.
        self.errored: set[int] = set()

    def renew(self) -> None:
        """Extend the lease and commit -- fails when another step took over,
        so every item transition is fenced by the lease."""
        renewed = self.db.execute(
            update(ReingestRun)
            .where(ReingestRun.id == self.run_id, ReingestRun.lease_token == self.token)
            .values(lease_until=_now() + timedelta(seconds=settings.reingest_lease_seconds))
        ).rowcount
        if not renewed:
            self.db.rollback()
            raise _LeaseLost(self.run_id)
        self.db.commit()

    def spend(self) -> bool:
        self.budget -= 1
        return self.budget >= 0

    @property
    def exhausted(self) -> bool:
        return self.budget <= 0


def advance_run(run_id: str) -> str:
    """One step of a run. Returns 'busy' (another step holds the lease),
    'active' or 'done'."""
    token = str(uuid.uuid4())
    with SessionLocal() as db:
        now = _now()
        claimed = db.execute(
            update(ReingestRun)
            .where(
                ReingestRun.id == run_id,
                ReingestRun.status.in_(REINGEST_ACTIVE_STATUSES),
                or_(ReingestRun.lease_until.is_(None), ReingestRun.lease_until < now),
            )
            .values(lease_token=token, lease_until=now + timedelta(seconds=settings.reingest_lease_seconds))
        ).rowcount
        db.commit()
        if not claimed:
            run = db.get(ReingestRun, run_id)
            return 'busy' if run is not None and run.status in REINGEST_ACTIVE_STATUSES else 'done'
        step = _Step(db, run_id, token)
        more = False
        try:
            run = db.get(ReingestRun, run_id)
            more = _advance(step, run)
            _record_step(db, run_id, token, failed=False)
            run = db.get(ReingestRun, run_id)
            outcome = 'active' if run.status in REINGEST_ACTIVE_STATUSES else 'done'
        except _LeaseLost:
            return 'busy'
        except Exception as exc:
            db.rollback()
            logger.exception('re-ingest step failed for run %s; retrying on the next step', run_id)
            _record_step(db, run_id, token, failed=True, error=exc)
            outcome = 'active'
        finally:
            db.execute(
                update(ReingestRun)
                .where(ReingestRun.id == run_id, ReingestRun.lease_token == token)
                .values(lease_token=None, lease_until=None)
            )
            db.commit()
    if more and outcome == 'active':
        nudge(run_id)
    return outcome


def _record_step(db, run_id: str, token: str, *, failed: bool, error: Exception | None = None) -> None:
    """Count consecutive failing steps; a run that keeps failing is ended."""
    run = db.get(ReingestRun, run_id)
    if run is None or run.lease_token != token:
        return
    progress = dict(run.summary) if isinstance(run.summary, dict) and run.status in REINGEST_ACTIVE_STATUSES else {}
    failures = int(progress.get('step_failures') or 0) + 1 if failed else 0
    if failed and failures >= _RUN_MAX_STEP_FAILURES:
        run.status = ReingestRunStatus.FAILED
        run.error_message = _short(error)
        run.finished_at = _now()
        run.summary = item_counts(db, run.id)
    elif run.status in REINGEST_ACTIVE_STATUSES:
        progress['step_failures'] = failures
        run.summary = progress
    run.updated_at = _now()
    db.commit()


def _advance(step: _Step, run: ReingestRun) -> bool:
    """Returns True when the step stopped on its budget (more to do now)."""
    db = step.db
    _finalize_documents(step, run)
    _finalize_connections(step, run)
    # A cancel commits from the API session; this one must see it.
    run = db.get(ReingestRun, run.id, populate_existing=True)
    if run.status == ReingestRunStatus.RUNNING and not step.exhausted:
        _start_documents(step, run)
        _start_connection(step, run)
    run = db.get(ReingestRun, run.id, populate_existing=True, with_for_update=True)
    open_items = db.scalar(
        select(func.count()).select_from(ReingestItem)
        .where(ReingestItem.run_id == run.id, ReingestItem.status.in_(('pending', 'queued')))
    )
    if not open_items:
        run.status = (
            ReingestRunStatus.CANCELLED if run.status == ReingestRunStatus.CANCELLING else ReingestRunStatus.FINISHED
        )
        run.finished_at = _now()
        run.summary = item_counts(db, run.id)
    run.updated_at = _now()
    step.renew()
    return step.exhausted and bool(open_items)


_QUEUED_MAX_ATTEMPTS = 6


def _retry_due(item: ReingestItem) -> bool:
    report = item.carry if isinstance(item.carry, dict) else {}
    retry_at = report.get('retry_at')
    if not isinstance(retry_at, str):
        return True
    try:
        return datetime.fromisoformat(retry_at) <= _now()
    except ValueError:
        return True


def _isolated(step: _Step, item_id: int, handler) -> None:
    """Run one item's transition; an unexpected error never fails the run.
    A pending item (nothing started yet) fails after a few attempts. A
    queued one is mid-transition: it is retried with a growing delay
    (outages of the object store or database pass) and only given up --
    after its job's changes are undone as far as possible -- when it keeps
    failing for about an hour."""
    db = step.db
    try:
        handler()
        step.renew()
    except _LeaseLost:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception('re-ingest item %s failed', item_id)
        step.errored.add(item_id)
        item = db.get(ReingestItem, item_id)
        if item is None:
            return
        report = dict(item.carry) if isinstance(item.carry, dict) else {}
        attempts = int(report.get('attempts') or 0) + 1
        report['attempts'] = attempts
        if item.status == 'queued':
            if attempts >= _QUEUED_MAX_ATTEMPTS:
                item.carry = report
                step.renew()
                _abandon(step, item_id, exc)
                return
            report['retry_at'] = (_now() + timedelta(minutes=2 ** attempts)).isoformat()
            item.carry = report
        else:
            item.carry = report
            if attempts >= _ITEM_MAX_ATTEMPTS:
                item.status, item.result, item.message = 'failed', 'error', _short(exc)
        step.renew()


def _abandon(step: _Step, item_id: int, error: Exception) -> None:
    """Give up on a queued item: undo what it changed as far as possible (the
    job's marker, a new version, a forced run), then fail it. A job still
    being converted is waited for instead."""
    db = step.db
    item = db.get(ReingestItem, item_id)
    cleanup_error: Exception | None = None
    try:
        if item.kind == 'document':
            job = db.get(Job, item.new_job_id or item.job_id) if (item.new_job_id or item.job_id) else None
            if job is not None and job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                return
            if job is not None:
                marker = restore_from_marker(job)
                if item.action == 'reprocess':
                    _keep_previous_state(db, job, marker, read_markdown=False)
                else:
                    _discard_version(db, job, item)
        else:
            import_run = db.get(ImportRun, item.import_run_id) if item.import_run_id else None
            if import_run is not None:
                if import_run.status in (ImportRunStatus.PENDING, ImportRunStatus.RUNNING):
                    return
                if import_run.status != ImportRunStatus.FINISHED:
                    _rollback_forced_run(db, import_run)
                else:
                    _restore_attachment_markers(db, import_run)
                    _discard_failed_versions(db, import_run)
        step.renew()
    except _LeaseLost:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception('re-ingest item %s: clean-up failed', item_id)
        cleanup_error = exc
    item = db.get(ReingestItem, item_id)
    item.status, item.result = 'failed', 'error'
    item.message = _short(error if cleanup_error is None else f'{error} (Aufräumen fehlgeschlagen: {cleanup_error})')
    step.renew()


def _dispatch_process_job(job: Job) -> None:
    from app.services.paddle_service import effective_pipeline_profile_id
    from app.workers.celery_app import celery_app

    job_settings = _settings(job)

    def _text(key: str) -> str | None:
        value = job_settings.get(key)
        return value if isinstance(value, str) else None

    celery_app.send_task(
        'process_job',
        args=[job.id, effective_pipeline_profile_id(_text('profile_id')), _text('mode'), _text('email'), _text('department')],
    )


def _set_marker(job: Job, marker: dict) -> None:
    """Store the re-ingest marker in the job's settings and take the webhook
    connection out until the item is finalized (a re-ingest must not flood
    users' webhook endpoints)."""
    info = copy.deepcopy(_info(job))
    job_settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    webhook = job_settings.pop('webhook_connection_id', None)
    if isinstance(webhook, str) and webhook:
        marker = {**marker, 'webhook_connection_id': webhook}
    job_settings['reingest'] = marker
    info['settings'] = job_settings
    job.processing_info = info


def restore_from_marker(job: Job) -> dict:
    """Undo _set_marker on a finished job: webhook connection, original
    upload time and review decision back where they belong. Returns the
    removed marker."""
    marker = _marker(job)
    if not marker:
        return {}
    info = copy.deepcopy(_info(job))
    job_settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    job_settings.pop('reingest', None)
    if marker.get('webhook_connection_id'):
        job_settings['webhook_connection_id'] = marker['webhook_connection_id']
    info['settings'] = job_settings
    if isinstance(marker.get('source_uploaded_at'), str):
        info['source_uploaded_at'] = marker['source_uploaded_at']
    if isinstance(marker.get('portal_review'), dict):
        info['portal_review'] = marker['portal_review']
    job.processing_info = info
    return marker


def _new_version(db, job: Job) -> Job:
    """Next version of a released document, to be converted again -- the
    same identity _new_edit_version (portal) carries, but PENDING and
    without markdown; process_job writes it."""
    job_settings = copy.deepcopy(_settings(job))
    job_settings.pop('reingest', None)
    version = Job(
        id=str(uuid.uuid4()),
        original_filename=job.original_filename,
        # The stored original is immutable and shared with the predecessor.
        upload_path=job.upload_path,
        upload_object_id=job.upload_object_id,
        upload_mime_type=job.upload_mime_type,
        upload_size_bytes=job.upload_size_bytes,
        status=JobStatus.PENDING,
        processing_info={
            'settings': job_settings,
            'execution': {'status': 'requeued', 'detail': 'Re-Ingest (Administration)'},
            'source_uploaded_at': _info(job).get('source_uploaded_at') or job.created_at.isoformat(),
        },
        owner_id=job.owner_id,
        import_run_id=job.import_run_id,
        mail_message_id=job.mail_message_id,
        content_sha256=job.content_sha256,
        password_hash=job.password_hash,
        document_version=job.document_version + 1,
        previous_job_id=job.id,
        tags=list(job.tags),
    )
    db.add(version)
    for artifact in db.scalars(select(JobArtifact).where(JobArtifact.job_id == job.id)):
        db.add(JobArtifact(
            job_id=version.id, kind=artifact.kind, filename=artifact.filename, content_type=artifact.content_type,
            object_id=artifact.object_id, size_bytes=artifact.size_bytes, source_url=artifact.source_url,
            sha256=artifact.sha256,
        ))
    db.flush()
    return version


def _has_successor(db, job_id: str) -> bool:
    return db.scalar(select(Job.id).where(Job.previous_job_id == job_id).limit(1)) is not None


def _profile_unavailable(db, job: Job) -> bool:
    """A VL profile whose connection was disabled or deleted fails the job
    (process_job) -- skip instead of turning a good document into a failed one."""
    from app.models.models import VlConnection

    connection_id = _settings(job).get('vl_connection_id')
    if not isinstance(connection_id, str) or not connection_id:
        return False
    connection = db.get(VlConnection, connection_id)
    return connection is None or not connection.enabled


def _collection_id(job: Job) -> str | None:
    value = _settings(job).get('collection_id')
    return value if isinstance(value, str) else None


def _start_document(db, run: ReingestRun, item: ReingestItem) -> Job | None:
    """Re-check the plan against the current state and queue the item.
    Returns the job to dispatch (after commit), or None if it was skipped."""
    from app.api.routes import _lock_document_chain

    item = db.get(ReingestItem, item.id, populate_existing=True, with_for_update=True)
    if item.status != 'pending':
        return None  # cancelled meanwhile
    job = db.get(Job, item.job_id) if item.job_id else None
    if job is None:
        item.status, item.result = 'skipped', 'job_missing'
        return None
    # Serializes with uploads of the same document and other re-ingest steps.
    _lock_document_chain(db, job.original_filename, _collection_id(job))
    job = db.get(Job, item.job_id, with_for_update=True, populate_existing=True)
    if _has_successor(db, job.id):
        # A newer version was uploaded meanwhile; it is not part of this run.
        item.status, item.result = 'skipped', 'superseded'
        return None
    collection_id = _collection_id(job)
    action, reason = _classify(
        job,
        released=db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None,
        withdrawn=db.get(KnowledgeWithdrawal, job.id) is not None,
        collection_exists=collection_id is not None and db.get(Collection, collection_id) is not None,
        include_edited=bool(run.options.get('include_edited')),
        rerelease=bool(run.options.get('rerelease')),
        # Live in Knowledge was checked when the run was planned.
        knowledge_status='indexed',
        knowledge_known=True,
    )
    if action is None:
        item.status, item.result = 'skipped', reason
        return None
    if action != item.action:
        # Released or un-released by someone since the admin confirmed the plan.
        item.status, item.result = 'skipped', 'changed'
        return None
    if _profile_unavailable(db, job):
        item.status, item.result = 'skipped', 'profile_unavailable'
        return None

    info = _info(job)
    marker: dict = {'run_id': run.id}
    if action == 'rerelease':
        target = _new_version(db, job)
        marker['source_job_id'] = job.id
        item.new_job_id = target.id
    else:
        # In place, like a restart -- but the current markdown stays until a
        # new one is written (process_job leaves it alone on failure), and
        # what process_job drops at its claim travels in the marker.
        target = job
        for key in ('source_uploaded_at', 'portal_review', 'editor'):
            if info.get(key):
                marker[key] = copy.deepcopy(info[key])
        marker['previous_status'] = job.status.value if hasattr(job.status, 'value') else str(job.status)
        marker['previous_execution'] = copy.deepcopy(info.get('execution')) if isinstance(info.get('execution'), dict) else None
        marker['previous_error'] = job.error_message
        execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
        target.processing_info = {
            **info,
            'execution': {**execution, 'status': 'requeued', 'detail': 'Re-Ingest (Administration)'},
        }
        target.status = JobStatus.PENDING
        target.claim_token = None
        target.error_message = None
    _set_marker(target, marker)
    item.status = 'queued'
    item.carry = None  # start-phase attempts do not count against finalizing
    return target


def _start_documents(step: _Step, run: ReingestRun) -> None:
    db = step.db
    if not run.options.get('include_documents'):
        return
    in_flight = db.scalar(
        select(func.count()).select_from(ReingestItem)
        .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'document', ReingestItem.status == 'queued')
    ) or 0
    slots = max(settings.reingest_max_in_flight, 1) - in_flight
    while slots > 0 and step.spend():
        item_id = db.scalar(
            select(ReingestItem.id)
            .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'document', ReingestItem.status == 'pending')
            .where(ReingestItem.id.not_in(step.errored) if step.errored else True)
            .order_by(ReingestItem.id)
            .limit(1)
        )
        if item_id is None:
            return
        dispatched: list[Job] = []

        def _start(item_id=item_id, dispatched=dispatched):
            item = db.get(ReingestItem, item_id)
            target = _start_document(db, run, item)
            if target is not None:
                dispatched.append(target)

        # Committed (by the lease renewal) before the dispatch: process_job
        # only claims a job it sees PENDING.
        _isolated(step, item_id, _start)
        for target in dispatched:
            try:
                _dispatch_process_job(target)
            except Exception:
                # The job stays PENDING and queued; _finalize_documents re-sends it.
                logger.exception('could not enqueue re-ingest job %s', target.id)
            slots -= 1


def _quality(job: Job) -> tuple[str | None, str | None]:
    from app.api.portal import _quality as portal_quality

    return portal_quality(job)


def _unchanged(old: Job, new: Job) -> bool:
    """Same body (frontmatter differs by design: identity, timestamps) and
    the same quality rating -- a release would change nothing."""
    try:
        old_body = _parse_frontmatter(old.result_markdown or '')[1]
        new_body = _parse_frontmatter(new.result_markdown or '')[1]
    except PublicationValidationError:
        return False
    return old_body.strip() == new_body.strip() and _quality(old) == _quality(new)


def _discard_version(db, job: Job, item: ReingestItem | None = None) -> bool:
    """Delete an unreleased re-ingest version nothing builds on -- under the
    chain lock, so a concurrent upload cannot chain onto it meanwhile."""
    from app.api.routes import _lock_document_chain

    _lock_document_chain(db, job.original_filename, _collection_id(job))
    db.flush()  # autoflush is off: keep the caller's marker restore across the reload
    job = db.get(Job, job.id, with_for_update=True, populate_existing=True)
    if job is None:
        return True
    if _has_successor(db, job.id) or is_manually_edited(job):
        return False
    if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
        return False
    if item is not None:
        item.new_job_id = None
    db.delete(job)
    return True


def _release(db, run: ReingestRun, job: Job, previous: DocumentRelease | None) -> tuple[bool, str | None]:
    """Release `job` through the portal's own release path (every check
    applies). Acts as the person who released the previous version, so
    'freigegeben von' stays theirs; falls back to the admin who started the
    run when that person can no longer release it. Grade C needs the admin's
    explicit choice (or an earlier explicit confirmation on the previous
    release). Commits on success."""
    from app.api.portal import _collection_for_job, release_portal_document
    from app.schemas.portal import PortalReleaseRequest
    from fastapi import HTTPException

    if not publication_configured():
        return False, 'Veröffentlichung nicht konfiguriert'
    if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)) is not None:
        return True, None  # someone released it meanwhile
    collection = _collection_for_job(db, job)
    if collection is None:
        return False, 'Keine Wissenssammlung zugeordnet'
    try:
        digest = canonical_snapshot(db, job, collection)[1]
    except PublicationValidationError as exc:
        return False, _short(exc)
    frozen = previous.payload if previous is not None and isinstance(previous.payload, dict) else {}
    accept = bool(run.options.get('accept_quality_warnings')) or bool(frozen.get('quality_override'))
    releasers: list[User] = []
    for user_id in (previous.owner_id if previous is not None else None, run.created_by):
        user = db.get(User, user_id) if user_id else None
        if user is not None and user.is_active and all(user.id != known.id for known in releasers):
            releasers.append(user)
    if not releasers:
        return False, 'Keine berechtigte Person für die Freigabe'
    job_id = job.id
    for index, user in enumerate(releasers):
        try:
            release_portal_document(job_id, PortalReleaseRequest(markdown_sha256=digest, accept_quality_warning=accept), db, user)
            return True, None
        except HTTPException as exc:
            db.rollback()
            if exc.status_code in (403, 404) and index < len(releasers) - 1:
                continue
            return False, _short(exc.detail)
        except PublicationValidationError as exc:
            db.rollback()
            return False, _short(exc)
    return False, None


def _keep_previous_state(db, job: Job, marker: dict, *, read_markdown: bool = True) -> None:
    """A failed in-place conversion: the document stays as it was (process_job
    leaves the markdown alone on failure)."""
    if job.status != JobStatus.FAILED or marker.get('previous_status') != JobStatus.FINISHED.value:
        return
    has_markdown = (
        bool(job.result_markdown) if read_markdown
        else db.scalar(select(Job.markdown_nonempty()).where(Job.id == job.id))
    )
    if not has_markdown:
        return
    info = copy.deepcopy(_info(job))
    if isinstance(marker.get('previous_execution'), dict):
        info['execution'] = marker['previous_execution']
    if isinstance(marker.get('editor'), dict):
        info['editor'] = marker['editor']
    job.processing_info = info
    job.status = JobStatus.FINISHED
    job.error_message = marker.get('previous_error')


def _finalize_document(db, run: ReingestRun, item: ReingestItem) -> None:
    target_id = item.new_job_id or item.job_id
    job = db.get(Job, target_id) if target_id else None
    if job is None:
        item.status, item.result = 'failed', 'job_missing'
        return
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        if job.status == JobStatus.PENDING and _aware(item.updated_at) < _now() - _REDISPATCH_AFTER:
            # Most likely a lost queue message; process_job's claim makes a
            # duplicate a no-op.
            _dispatch_process_job(job)
            item.updated_at = _now()
        return
    marker = restore_from_marker(job)
    failed = job.status == JobStatus.FAILED
    error = _short(job.error_message)

    if item.action == 'reprocess':
        _keep_previous_state(db, job, marker)
        item.status, item.result, item.message = ('failed', 'failed', error) if failed else ('done', 'reprocessed', None)
        return

    source_job_id = marker.get('source_job_id') or item.job_id
    source = db.get(Job, source_job_id) if source_job_id else None
    if failed:
        # The released version stays live; no failed version on top of it.
        _discard_version(db, job, item)
        item.status, item.result, item.message = 'failed', 'failed', error
        return
    if source is None or db.get(KnowledgeWithdrawal, source.id) is not None or job.previous_job_id != source.id:
        # Deleted/withdrawn while being re-converted: never re-publish it.
        _discard_version(db, job, item)
        item.status, item.result = 'skipped', 'withdrawn'
        return
    if _unchanged(source, job) and _discard_version(db, job, item):
        item.status, item.result = 'done', 'unchanged'
        return
    if not run.options.get('rerelease'):
        item.status, item.result = 'done', 'reprocessed'
        return
    db.commit()  # the restored provenance is part of the released snapshot
    item_id = item.id
    # Under the chain lock (held until the release commits): a version an
    # upload already built on must not be published next to that one.
    from app.api.routes import _lock_document_chain

    _lock_document_chain(db, job.original_filename, _collection_id(job))
    if _has_successor(db, job.id):
        item.status, item.result = 'skipped', 'superseded'
        return
    previous = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == source.id))
    ok, message = _release(db, run, job, previous)
    item = db.get(ReingestItem, item_id)
    item.status, item.result, item.message = ('done', 'released', None) if ok else ('failed', 'release_refused', message)


def _finalize_documents(step: _Step, run: ReingestRun) -> None:
    db = step.db
    item_ids = list(db.scalars(
        select(ReingestItem.id)
        .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'document', ReingestItem.status == 'queued')
        .order_by(ReingestItem.id)
    ))
    for item_id in item_ids:
        if step.exhausted:
            return
        item = db.get(ReingestItem, item_id)
        if not _retry_due(item):
            continue
        target_id = item.new_job_id or item.job_id
        job = db.get(Job, target_id) if target_id else None
        if job is not None and job.status == JobStatus.RUNNING:
            continue  # nothing to do yet; costs no budget
        if job is not None and job.status == JobStatus.PENDING and _aware(item.updated_at) >= _now() - _REDISPATCH_AFTER:
            continue
        step.spend()
        _isolated(step, item_id, lambda item_id=item_id: _finalize_document(db, run, db.get(ReingestItem, item_id)))


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _start_connection(step: _Step, run: ReingestRun) -> None:
    """One forced import run at a time: sources allow one active run, and
    their owners only one active import."""
    from app.workers.refresh_tasks import start_sync_run

    db = step.db
    if not run.options.get('include_connections'):
        return
    if db.scalar(
        select(ReingestItem.id)
        .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'connection', ReingestItem.status == 'queued')
        .limit(1)
    ) is not None:
        return
    item_ids = list(db.scalars(
        select(ReingestItem.id)
        .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'connection', ReingestItem.status == 'pending')
        .order_by(ReingestItem.id)
    ))
    for item_id in item_ids:
        if not step.spend():
            return
        started: list[bool] = []

        def _start(item_id=item_id, started=started):
            item = db.get(ReingestItem, item_id, populate_existing=True, with_for_update=True)
            if item.status != 'pending':
                return  # cancelled meanwhile
            orphan = _orphaned_import_run(db, run, item.sync_id) if item.sync_id else None
            if orphan is not None:
                # Started by an earlier step whose own commit was lost.
                item.import_run_id, item.status = orphan.id, 'queued'
                started.append(True)
                return
            sync = db.get(ImportSync, item.sync_id) if item.sync_id else None
            if sync is None:
                item.status, item.result = 'skipped', 'sync_missing'
                return
            reason, _ = _connection_blocker(db, sync)
            if reason is not None:
                item.status, item.result = 'skipped', reason
                return
            try:
                import_run = start_sync_run(db, sync, extra_options={
                    'force_reimport': True,
                    'reingest_run_id': run.id,
                    'reingest_include_edited': bool(run.options.get('include_edited')),
                    'reingest_rerelease': bool(run.options.get('rerelease')),
                })
            except ValueError as exc:
                db.rollback()
                item = db.get(ReingestItem, item_id)
                item.status, item.result, item.message = 'failed', 'blocked', _short(exc)
                return
            except Exception as exc:  # broker: start_sync_run marked its run failed
                db.rollback()
                item = db.get(ReingestItem, item_id)
                item.status, item.result, item.message = 'failed', 'not_queued', _short(exc)
                return
            if import_run is None:
                return  # the source is busy with another run; try the next connection
            item = db.get(ReingestItem, item_id)
            item.import_run_id = import_run.id
            item.status = 'queued'
            started.append(True)

        _isolated(step, item_id, _start)
        if started:
            return


def _orphaned_import_run(db, run: ReingestRun, sync_id: str) -> ImportRun | None:
    linked = set(db.scalars(
        select(ReingestItem.import_run_id).where(ReingestItem.run_id == run.id, ReingestItem.import_run_id.is_not(None))
    ))
    for candidate in db.scalars(
        select(ImportRun).where(ImportRun.sync_id == sync_id, ImportRun.created_at >= run.created_at)
        .order_by(ImportRun.created_at)
    ):
        options = candidate.options if isinstance(candidate.options, dict) else {}
        if options.get('reingest_run_id') == run.id and candidate.id not in linked:
            return candidate
    return None


def _finalize_connection(step: _Step, run: ReingestRun, item_id: int) -> None:
    db = step.db
    item = db.get(ReingestItem, item_id)
    import_run = db.get(ImportRun, item.import_run_id) if item.import_run_id else None
    if import_run is None:
        item.status, item.result = 'failed', 'run_missing'
        return
    if import_run.status in (ImportRunStatus.PENDING, ImportRunStatus.RUNNING):
        return
    if import_run.status != ImportRunStatus.FINISHED:
        if db.scalar(
            select(Job.id).where(Job.import_run_id == import_run.id, Job.status == JobStatus.RUNNING).limit(1)
        ) is not None:
            return  # let a running attachment OCR end before rolling back
        removed = _rollback_forced_run(db, import_run)
        item = db.get(ReingestItem, item_id)
        item.status, item.result = 'failed', 'import_failed'
        item.message = _short(
            f'{import_run.error_message or import_run.status.value} -- {removed} neu angelegte Versionen '
            'zurückgenommen; es gelten weiter die bisherigen.'
        )
        return
    if _waiting_for_attachments(db, import_run):
        return
    _discard_failed_versions(db, import_run)
    report = dict(item.carry) if isinstance(item.carry, dict) else {}
    if run.options.get('rerelease'):
        complete = _rerelease_import_jobs(step, run, import_run, report)
        item = db.get(ReingestItem, item_id)
        item.carry = report
        if not complete:
            return  # step budget used up; continue on the next step
    _restore_attachment_markers(db, import_run)
    refused = len(report.get('refused') or [])
    released = int(report.get('released') or 0)
    item.status = 'done' if not refused else 'failed'
    item.result = 'reimported' if not refused else 'release_refused'
    item.message = (
        f'{import_run.pages_imported} Seiten neu eingelesen, {import_run.pages_failed} fehlgeschlagen, '
        f'{released} erneut freigegeben' + (f', {refused} nicht freigegeben' if refused else '')
    )


def _finalize_connections(step: _Step, run: ReingestRun) -> None:
    db = step.db
    item_ids = list(db.scalars(
        select(ReingestItem.id)
        .where(ReingestItem.run_id == run.id, ReingestItem.kind == 'connection', ReingestItem.status == 'queued')
    ))
    for item_id in item_ids:
        if not _retry_due(db.get(ReingestItem, item_id)):
            continue
        if not step.spend():
            return
        _isolated(step, item_id, lambda item_id=item_id: _finalize_connection(step, run, item_id))


def _discard_failed_versions(db, import_run: ImportRun) -> None:
    """A failed re-OCR of an attachment must not stay chained on top of its
    previous version (same rule as for documents): it would block edits of
    that version, and a later version chained onto it could never supersede
    the live one."""
    for job in list(db.scalars(
        select(Job).where(
            Job.import_run_id == import_run.id, Job.status == JobStatus.FAILED, Job.previous_job_id.is_not(None),
        )
    )):
        if _is_forced_version(job):
            _discard_version(db, job)


def _is_forced_version(job: Job) -> bool:
    import_info = _settings(job).get('import')
    return isinstance(import_info, dict) and bool(import_info.get('forced_reimport'))


def _restore_attachment_markers(db, import_run: ImportRun) -> None:
    for job in db.scalars(select(Job).where(Job.import_run_id == import_run.id)):
        if _marker(job):
            restore_from_marker(job)


def _waiting_for_attachments(db, import_run: ImportRun) -> bool:
    """Attachment OCR of the run still to finish. A child whose queue message
    was lost stays PENDING (the run's backstop re-send already ran), so it is
    sent again after a while; process_job's claim makes that a no-op otherwise."""
    rows = db.execute(
        select(Job.id, Job.status, Job.updated_at)
        .where(Job.import_run_id == import_run.id, Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
    ).all()
    for job_id, job_status, updated_at in rows:
        if job_status == JobStatus.PENDING and _aware(updated_at) < _now() - _REDISPATCH_AFTER:
            job = db.get(Job, job_id)
            _dispatch_process_job(job)
            job.updated_at = _now()
    return bool(rows)


def _rollback_forced_run(db, import_run: ImportRun) -> int:
    """A forced run that did not finish: its versions can never be released
    (the run is not FINISHED) yet the page states already point at them, so
    later refreshes would skip those pages for good. Point the page states
    back at the previous versions and delete what the run created."""
    from app.api.routes import _lock_document_chain
    from app.models.models import ImportPageState

    jobs = list(db.scalars(select(Job).where(Job.import_run_id == import_run.id)))
    removed = 0
    for job in jobs:
        if _settings(job).get('mode') != 'import':
            continue
        previous = db.get(Job, job.previous_job_id) if job.previous_job_id else None
        previous_import = _settings(previous).get('import') if previous is not None else None
        previous_version = previous_import.get('source_page_version') if isinstance(previous_import, dict) else None
        values: dict = {'job_id': previous.id if previous is not None else None}
        if isinstance(previous_version, int):
            values['page_version'] = previous_version
        db.execute(update(ImportPageState).where(ImportPageState.job_id == job.id).values(**values))
    db.flush()
    for job in jobs:
        _lock_document_chain(db, job.original_filename, _collection_id(job))
        if _has_successor(db, job.id) or db.scalar(
            select(DocumentRelease.id).where(DocumentRelease.job_id == job.id)
        ) is not None:
            continue
        db.delete(job)
        removed += 1
    # Frees the source for other runs again (refresh_tasks._has_active_run).
    import_run.options = {**(import_run.options or {}), 'reingest_rolled_back': True}
    db.flush()
    return removed


def _rerelease_import_jobs(step: _Step, run: ReingestRun, import_run: ImportRun, report: dict) -> bool:
    """Release the new versions of this forced run whose predecessor was
    released and live in Knowledge when the run was planned (and has not been
    withdrawn since). Pages whose attachments could not be stored are left
    for review: their images would be missing from the release. Works in
    budgeted slices across steps (`report` keeps the tally); returns True
    when every candidate was handled."""
    from app.api.routes import _lock_document_chain

    db = step.db
    live = live_job_ids(db, run.id)
    refused = set(report.get('refused') or [])
    state = import_run.state if isinstance(import_run.state, dict) else {}
    pages_with_errors = {
        str(entry.get('page_id')) for entry in (state.get('errors') or []) if isinstance(entry, dict)
    }
    rows = db.execute(
        select(Job.id, Job.previous_job_id, Job.processing_info)
        .where(Job.import_run_id == import_run.id, Job.status == JobStatus.FINISHED, Job.previous_job_id.is_not(None))
    ).all()
    released_ids = set(db.scalars(
        select(DocumentRelease.job_id).where(DocumentRelease.job_id.in_([row[0] for row in rows]))
    )) if rows else set()
    for job_id, previous_job_id, info in rows:
        if previous_job_id not in live or job_id in released_ids or job_id in refused:
            continue
        previous = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == previous_job_id))
        if previous is None or db.get(KnowledgeWithdrawal, previous_job_id) is not None:
            continue
        job_settings = ((info or {}).get('settings') if isinstance(info, dict) else None) or {}
        import_info = job_settings.get('import') or {}
        if not import_info.get('forced_reimport'):
            continue  # changed upstream: goes through review like any refresh
        if job_settings.get('mode') == 'import' and str(import_info.get('source_page_id')) in pages_with_errors:
            refused.add(job_id)
            continue
        if not step.spend():
            report['refused'] = sorted(refused)
            return False
        job = db.get(Job, job_id)
        _lock_document_chain(db, job.original_filename, _collection_id(job))
        if _has_successor(db, job_id):
            refused.add(job_id)  # a later refresh built on it meanwhile
            continue
        ok, message = _release(db, run, job, previous)
        if ok:
            report['released'] = int(report.get('released') or 0) + 1
        else:
            refused.add(job_id)
            logger.info('re-ingest %s: release of import job %s refused: %s', run.id, job_id, message)
        report['refused'] = sorted(refused)
        item = db.scalar(select(ReingestItem).where(ReingestItem.run_id == run.id, ReingestItem.import_run_id == import_run.id))
        if item is not None:
            item.carry = dict(report)
        step.renew()
    report['refused'] = sorted(refused)
    return True
