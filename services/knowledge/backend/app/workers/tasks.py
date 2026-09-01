"""Index pipeline Celery task: fetch -> chunk -> enrich -> embed -> persist.

Enqueued by app/api/events.py right after a document.processed webhook is
accepted (see that module's docstring for the receiver side of this
contract) -- the task itself only ever needs `document_id`; every other
input (`previous_job_id`, `frontmatter`) is already sitting on the Document
row the webhook receiver wrote. The markdown fetch itself is keyed off
`document.source_job_id`, NOT `document.markdown_url` -- see
app/services/ingest_client.py's fetch_markdown docstring (FINDING 3) for
why the event's own markdown_url field is never used to build the actual
outbound request.

Retry policy for a transient app.services.ingest_client.fetch_markdown
failure mirrors Weave-Ingest's own app/workers/webhook_tasks.py's
deliver_webhook: a self-re-enqueue via `self.app.send_task(...,
countdown=...)` rather than Celery's built-in Task.retry. Same reasoning as
that module's own docstring gives for the choice -- a countdown-based
re-send keeps this task's control flow linear (no exception-driven retry
loop), and the attempt count is persisted on `Document.index_attempts`
rather than threaded through task arguments or Celery's own
`self.request.retries` (which only increments through a *real*
broker-mediated redelivery, never a direct call -- see
celery.app.task.Task.retry's `request.called_directly` short-circuit --
which would make this task's retry behavior unable to be exercised by a
plain, no-broker-needed unit test).

Backoff schedule (30, 60, 120, 240 seconds, max 5 total attempts) is the
same as webhook_tasks.py's own _BACKOFF_SECONDS/_MAX_ATTEMPTS, which in turn
mirrors contracts/events/document.processed.md's own delivery-retry section
("bis zu 5 Versuche mit exponentiellem Backoff (30s, 60s, 120s, 240s)").
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from celery.signals import worker_ready
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.models import Chunk, Document, DocumentStatus
from app.services import ingest_client
from app.services.chunker import chunk_markdown
from app.services.embeddings import embed_chunks, get_provider
from app.services.enrichment import build_chunk_meta
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

INDEX_TASK_NAME = 'weave.knowledge.index_document'

# Total attempts a document gets at fetching its markdown before this task
# gives up and marks it status='failed' for good (attempt 1 is the initial
# try, not a retry) -- see the module docstring for where this schedule
# comes from.
_MAX_ATTEMPTS = 5
_BACKOFF_SECONDS = (30, 60, 120, 240)


def _backoff_seconds(attempt: int) -> int:
    index = min(attempt - 1, len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[max(index, 0)]


def _mark_failed(db: Session, document: Document, error: str) -> None:
    document.status = DocumentStatus.FAILED
    document.error = error
    db.commit()


def _supersede_previous_version(db: Session, document: Document) -> None:
    """Delete the previous job's chunks and mark it superseded, in the SAME
    transaction as the current document's own indexing commit (the caller
    commits right after this returns) -- see app/api/events.py's docstring
    on `previous_job_id`, Weave-Ingest's own version-chain field, copied
    onto Document verbatim at webhook receipt.

    A no-op if there is no previous_job_id, or it doesn't resolve to a
    Document this service has ever indexed (an event received before this
    service existed, a previous_job_id typo/bug upstream, or a version
    already superseded by an even-later one).
    """
    if not document.previous_job_id:
        return
    previous = db.execute(
        select(Document).where(Document.source_job_id == document.previous_job_id)
    ).scalar_one_or_none()
    if previous is None or previous.id == document.id:
        return
    db.execute(delete(Chunk).where(Chunk.document_id == previous.id))
    previous.status = DocumentStatus.SUPERSEDED
    previous.chunk_count = 0


def _run_pipeline(self, db: Session, document: Document) -> None:
    try:
        markdown = ingest_client.fetch_markdown(document.source_job_id)
    except (ValueError, ingest_client.PermanentFetchError) as exc:
        # ValueError is fetch_markdown's SSRF-guard rejection (see its
        # docstring) -- no request was ever made. Both cases are equally
        # unretryable: an unchanged markdown_url will fail the same way
        # every time.
        logger.error('index_document: permanent failure for document %s: %s', document.id, exc)
        _mark_failed(db, document, str(exc))
        return
    except ingest_client.TransientFetchError as exc:
        document.index_attempts += 1
        attempts = document.index_attempts
        if attempts >= _MAX_ATTEMPTS:
            logger.error(
                'index_document: exhausted %d attempt(s) fetching document %s: %s', attempts, document.id, exc,
            )
            _mark_failed(db, document, str(exc))
            return
        db.commit()
        countdown = _backoff_seconds(attempts)
        logger.warning(
            'index_document: transient fetch error for document %s (attempt %d/%d); retrying in %ds: %s',
            document.id, attempts, _MAX_ATTEMPTS, countdown, exc,
        )
        self.app.send_task(INDEX_TASK_NAME, args=[str(document.id)], countdown=countdown)
        return

    document.markdown_body = markdown
    document.index_attempts = 0

    frontmatter, chunks = chunk_markdown(markdown)
    # Kept in lockstep with the freshly-parsed body: build_chunk_meta below
    # and embed_chunks's own use of document.frontmatter (see
    # app/services/embeddings.py) must both see the exact same frontmatter
    # that actually produced these chunks, not whatever was stored at
    # webhook-receipt time (should be byte-identical per the contract, but
    # this removes any chance of drift between the two).
    document.frontmatter = frontmatter

    # Defensive: wipe any chunks a previous (crashed/partial) attempt at
    # indexing THIS SAME document already wrote -- an at-least-once task
    # redelivery (acks_late) must not hit chunks' own
    # (document_id, chunk_index) unique constraint on the rewrite below.
    db.execute(delete(Chunk).where(Chunk.document_id == document.id))

    chunk_rows = [
        Chunk(
            document_id=document.id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            heading_path=chunk.heading_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            char_count=chunk.char_count,
            meta=build_chunk_meta(frontmatter, chunk),
        )
        for chunk in chunks
    ]

    provider = get_provider()
    embed_chunks(db, document, chunk_rows, provider)

    document.chunk_count = len(chunk_rows)
    document.indexed_at = datetime.now(timezone.utc)
    document.status = DocumentStatus.INDEXED
    document.error = None

    _supersede_previous_version(db, document)

    db.commit()


@celery_app.task(name=INDEX_TASK_NAME, bind=True, acks_late=True, reject_on_worker_lost=True)
def index_document(self, document_id: str) -> None:
    """Run fetch -> chunk -> embed -> persist for one Document row.

    Never raises on a fetch failure -- both the permanent and the
    (exhausted) transient case end in a committed status='failed', and a
    still-retryable transient failure re-enqueues itself and returns
    normally -- so a worker restart / task redelivery never sees this task
    as crash-looping. Any other unexpected failure (an embedding-provider
    error, a bug) is still caught here and also ends in status='failed',
    same defensive outer catch-all as Weave-Ingest's own deliver_webhook.
    """
    db = SessionLocal()
    try:
        document = db.get(Document, uuid.UUID(document_id))
        if document is None:
            logger.warning('index_document: document %s not found; nothing to do', document_id)
            return
        _run_pipeline(self, db, document)
    except Exception as exc:  # noqa: BLE001 - defensive terminal transition, see docstring
        logger.exception('index_document: unexpected failure for document %s', document_id)
        db.rollback()
        document = db.get(Document, uuid.UUID(document_id))
        if document is not None:
            document.status = DocumentStatus.FAILED
            document.error = str(exc)
            db.commit()
    finally:
        db.close()


@worker_ready.connect
def _kickstart_collection_sync_tick(sender=None, **kwargs) -> None:  # pragma: no cover
    """Kickstart the collection-sync self-re-enqueue chain (see
    app/workers/collection_sync_tasks.py's module docstring for the full
    design) on every worker start. Sent by name only, token-less
    (args=[None]) -- same "by name, no direct import needed for the send
    itself" style as Weave-Ingest's own worker_ready hook kickstarting
    confluence_refresh_tick/session_cleanup_tick. collection_sync_tick is
    itself the single source of truth for whether a chain is already alive
    (its own long-lived Redis NX lock, checked on every call including this
    token-less kickstart), so a redundant send from a simultaneous
    multi-replica startup, or from a worker restarting years into an
    already-healthy chain, is a harmless no-op rather than a second chain.
    """
    try:
        celery_app.send_task('weave.knowledge.collection_sync_tick', args=[None])
    except Exception:
        logger.exception('Failed to kickstart the collection-sync tick chain')


# Registers weave.knowledge.collection_sync_tick with the Celery app the
# same way Weave-Ingest's own tasks.py registers import_tasks.py /
# refresh_tasks.py / session_cleanup_tasks.py -- an explicit import at the
# bottom of the module Celery is started with (`celery -A app.workers.tasks
# worker`, see README.md), placed after every definition above purely by
# convention (the import itself has no ordering dependency on anything
# above it).
import app.workers.collection_sync_tasks  # noqa: E402,F401  (registers weave.knowledge.collection_sync_tick)
