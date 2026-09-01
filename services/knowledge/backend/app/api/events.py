"""Inbound webhook consumer for two Weave-Ingest event types, delivered over
the same signed webhook route: `document.processed` (see
contracts/events/document.processed.md + .schema.json) and
`collection.updated` (see _handle_collection_updated below for the
rationale -- there is no separate contract doc for it, it carries no
document-shaped payload at all).

`POST /api/v1/events/ingest` is the only route here. Processing order
mirrors the contract's own "Idempotenz"/"Signaturverfahren" sections
exactly:

1. HMAC-SHA256 verify the RAW request body against
   settings.weave_ingest_webhook_secret, constant-time
   (hmac.compare_digest) -- 401 on a missing/wrong signature, 503 when
   the secret itself is unset (fail closed: an unsigned-by-default ingress
   would let anyone write into the index). Applies identically
   to BOTH event types below -- there is only one signing secret and one
   verification step for this whole route.
2. Parse the envelope. `event == 'collection.updated'` is handled by
   _handle_collection_updated (immediate registry resync, see its
   docstring) and returns before any of the document.processed-specific
   steps below run. `event == 'document.processed'` continues through
   steps 3-5. Any other event type is silently ignored (204), not an error
   (a webhook connection can be subscribed to more than one event).
3. Idempotency: `event_key = f'{job_id}:{content_sha256}'` is inserted into
   `ingest_events` inside the SAME transaction as the Document upsert below
   (via `db.flush()`, not a separate `db.commit()`) -- a unique-constraint
   violation means this exact (job_id, content_sha256) pair was already
   processed, so nothing else in this request runs: no Document
   create/update, no Celery task, just `{'status': 'duplicate'}` (200).
   Weave-Ingest delivers at-least-once, so a redelivered event is the
   expected common case, not an error.
4. `quality.recommendation == 'block'` -> the Document is created/updated
   with status='blocked' and NO index task is enqueued (a blocked document
   is never indexed until someone re-processes it upstream with a
   passing/absent quality gate).
5. Otherwise: Document upsert (source_job_id unique) with status='pending'
   and `weave.knowledge.index_document` enqueued for it (202).

Two concurrent document.processed events for the SAME job_id but DIFFERENT
content_sha256 both have distinct event_keys, so both pass step 3's dedup
check -- and both can see "no existing Document for this job_id" before
either has committed. Only one of their INSERTs can win
documents.source_job_id's own unique constraint; the loser's `db.commit()`
raises IntegrityError. `_upsert_document` below is retried exactly once on
that: rollback, re-add the IngestEvent row (the rollback discards it too),
and re-run the same upsert -- this time the SELECT finds the winner's row,
so it becomes a normal update instead of a colliding insert. The response
shape is identical to the regular update path; Weave-Ingest still delivered
a real, distinct event, so it is still processed (task enqueued), never
silently dropped or surfaced as a raw 500.

`quality.recommendation is None` (no quality-gate result at all) is treated
like 'warn' for this branch -- i.e. NOT blocked, same as the contract's own
null-recommendation semantics ("Konsumenten behandeln null wie 'warn'").
The raw value (including None) is still stored verbatim in
`Document.quality_recommendation` for anyone auditing it later.

An optional `frontmatter.collection` slug (see the Collections contract
point 2) is denormalized onto `Document.collection_slug` in step 5's upsert
regardless of the block/accept outcome above -- a blocked document still
gets to carry its collection for whenever it is eventually re-processed and
accepted. If that slug isn't in the local `collections` registry mirror yet
(app/services/collection_sync.py, kept warm by
app/workers/collection_sync_tasks.py's periodic tick), `ingest_event` below
triggers exactly one lazy reload attempt of that mirror -- AFTER its own
`db.commit()`, in a separate session (see `_ensure_collection_known`'s
docstring for why: a second write transaction opened while the upsert's own
commit is still pending would deadlock against it on SQLite). A failed
reload is only logged, never surfaced to Weave-Ingest or turned into a
failure of this request -- indexing must proceed either way (contract point
3): the registry is Weave-Retrieval's read-time concern, not a precondition
for this service to do its own job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.models.models import Collection, Document, DocumentStatus, IngestEvent
from app.schemas.events import DocumentProcessedEvent
from app.services.collection_sync import CollectionSyncError, sync_collections
from app.workers.tasks import index_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/v1/events')

_SIGNATURE_HEADER = 'X-Weave-Ingest-Signature'
_SIGNATURE_PREFIX = 'sha256='

# Document columns copied straight off the event's top-level fields plus a
# few denormalized out of `frontmatter` -- see app/models/models.py's
# Document docstring for why team/department/tags get their own columns.
_EVENT_FIELDS = (
    'content_sha256', 'document_version', 'previous_job_id', 'original_filename',
    'markdown_url', 'engine',
)


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    secret = settings.weave_ingest_webhook_secret
    if not secret:
        # Fail closed, exactly like Weave-Tools' delegation-secret check.
        # Skipping verification on an unset secret would leave this route --
        # the one that writes documents into the index -- open to anyone who
        # can reach it: a forged document.processed carries whatever
        # markdown, team and collection slug the sender chooses, and the
        # search layer trusts what is indexed. 503 (misconfigured service),
        # not 401, so an operator can tell a deployment mistake from a
        # caller presenting a bad signature.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='service misconfigured',
        )
    if not signature_header or not signature_header.startswith(_SIGNATURE_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing or malformed signature')
    provided = signature_header[len(_SIGNATURE_PREFIX):]
    expected = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='signature mismatch')


def _ensure_collection_known(slug: str) -> None:
    """One-off lazy reload of the local collection-registry mirror when an
    event names a `collection` slug this service hasn't synced yet (see the
    Collections contract point 3) -- a brand-new collection can reach its
    first document before the next scheduled
    app/workers/collection_sync_tasks.py tick catches up to it.

    Runs in its OWN short-lived session, never the caller's request-scoped
    `db`: sync_collections() commits internally, and opening a second
    session that also writes to the same database WHILE `db`'s own
    Document/IngestEvent upsert transaction is still open would deadlock
    against it on SQLite ("database is locked", this service's local
    dev/test backend). That is exactly why ingest_event() below only calls
    this AFTER its own `db.commit()` has returned -- never from inside
    _upsert_document, which runs mid-transaction (see this module's
    docstring, step 3's flush-now/commit-later discipline and "FINDING 1").

    Never raises: a failed lazy reload must never block indexing (contract
    point 3 -- "ein fehlender Registry-Eintrag darf keine Indizierung
    verhindern"), only get logged. Whether `slug` ever actually appears in
    the mirror is enforced at search time by Weave-Retrieval, not here --
    this is best-effort freshness, not a gate.
    """
    sync_db = SessionLocal()
    try:
        if sync_db.get(Collection, slug) is not None:
            return
        try:
            sync_collections(sync_db)
        except CollectionSyncError:
            logger.warning(
                'document.processed event: collection %r is not in the local registry mirror and the '
                'lazy reload failed; indexing the document anyway', slug, exc_info=True,
            )
            return
        if sync_db.get(Collection, slug) is None:
            logger.warning(
                'document.processed event: collection %r is still unknown after a registry reload '
                '(not created upstream yet, or already renamed/removed there); indexing the document anyway',
                slug,
            )
    finally:
        sync_db.close()


def _handle_collection_updated(db: Session) -> Response:
    """Handles an inbound `collection.updated` event: Weave-Ingest sends
    this over the SAME signed webhook route as `document.processed`
    whenever its `collections` table changes (created/renamed/deleted, or
    -- the case this exists for -- an ACL edit such as `read_teams` shrinking
    to revoke a team's read access). Triggers an immediate
    app/services/collection_sync.py::sync_collections() rather than waiting
    for the next app/workers/collection_sync_tasks.py periodic tick (up to
    settings.collection_sync_tick_seconds, see that setting's docstring --
    this event is exactly why that window no longer needs to be wide: it is
    now a fallback, not the primary freshness mechanism).

    Deliberately NOT run through the `ingest_events` idempotency ledger that
    `document.processed` uses below (see IngestEvent's docstring and step 3
    above): that ledger's dedup key is `(job_id, content_sha256)`, and this
    event type carries neither -- it names no document and no job at all,
    only "the registry may have changed, go re-fetch the whole thing". A
    redelivered `collection.updated` (Weave-Ingest's at-least-once delivery
    applies here too) just runs sync_collections() an extra time, which is
    a plain full-mirror upsert (see that function's docstring) and therefore
    idempotent on its own -- there is nothing here a ledger row would guard
    against, and every ledger row would need a synthetic key never derived
    from the event's actual (empty) content.

    Errors are only logged, never turned into a non-200 response: a
    transient sync failure here (Weave-Ingest unreachable, say) must not
    push this webhook delivery into Weave-Ingest's retry loop -- the
    periodic tick above will pick up the same change on its own schedule
    regardless, so retrying the webhook itself would buy nothing beyond
    load. Uses the request-scoped `db` directly (unlike
    _ensure_collection_known's own short-lived session below) since this is
    the very first thing this request does to it -- no other write is open
    on it yet to deadlock against.
    """
    try:
        sync_collections(db)
    except CollectionSyncError:
        logger.warning(
            'collection.updated event: immediate sync_collections() failed; '
            'the next periodic collection_sync_tick will retry', exc_info=True,
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={'status': 'ok'})


def _parse_processed_at(value: str) -> datetime:
    """`processed_at` is 'YYYY-MM-DDTHH:MM:SSZ' per the contract; also
    tolerate an offset-suffixed ISO-8601 value defensively rather than
    assuming exactly one wire format forever. Falls back to "now" for a
    value that parses as neither -- this field is a display/audit
    timestamp, not something worth failing the whole webhook over."""
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        logger.warning('document.processed event: unparseable processed_at %r, using current time', value)
        return datetime.now(timezone.utc)


async def _read_raw_body(request: Request) -> bytes:
    return await request.body()


def _upsert_document(db: Session, event: DocumentProcessedEvent, frontmatter: dict, recommendation: str) -> tuple[Document, bool]:
    """Create-or-update the Document row for `event.job_id`, applying every
    field this event carries. Returns `(document, is_new)` -- `is_new`
    tells the caller whether this call issued an INSERT (and can therefore
    race another request's INSERT for the same job_id, see FINDING 1 in
    this module's docstring) or an UPDATE (which cannot collide on
    source_job_id's unique constraint, since it doesn't insert a new row).
    """
    document = db.execute(select(Document).where(Document.source_job_id == event.job_id)).scalar_one_or_none()
    is_new = document is None
    if is_new:
        document = Document(source_job_id=event.job_id)
        db.add(document)

    for field_name in _EVENT_FIELDS:
        setattr(document, field_name, getattr(event, field_name))
    document.quality_grade = event.quality.grade
    document.quality_recommendation = event.quality.recommendation
    document.frontmatter = frontmatter
    document.team = frontmatter.get('team')
    document.department = frontmatter.get('department')
    document.tags = frontmatter.get('tags') or []
    # 'collection'/'collection_name' are optional Weave-Ingest frontmatter
    # fields (see the Collections contract point 2) -- only the slug gets
    # its own column (same "one column, frontmatter stays the source of
    # truth for the rest" discipline as team/department above);
    # collection_name is still readable off `document.frontmatter` verbatim
    # if ever needed for display. The registry's read_teams ACL is
    # deliberately NEVER derived from frontmatter (contract point 2) --
    # only resolved later, read-only, out of the `collections` mirror.
    document.collection_slug = frontmatter.get('collection')
    document.processed_at = _parse_processed_at(event.processed_at)
    document.error = None
    document.status = DocumentStatus.BLOCKED if recommendation == 'block' else DocumentStatus.PENDING

    # The lazy registry reload (see _ensure_collection_known) deliberately
    # does NOT happen here, even though this is where collection_slug is
    # set: this function runs INSIDE the caller's still-open db transaction
    # (see this module's docstring, step 3's flush-now/commit-later
    # discipline) -- _ensure_collection_known opens its own session and
    # commits to the SAME database, which on SQLite (this service's local
    # dev/test backend) deadlocks against that still-uncommitted write lock
    # ("database is locked"). ingest_event() below calls it only AFTER its
    # own db.commit() has released that lock.

    return document, is_new


@router.post('/ingest')
def ingest_event(
    raw_body: bytes = Depends(_read_raw_body),
    signature_header: str | None = Header(default=None, alias=_SIGNATURE_HEADER),
    db: Session = Depends(get_db),
) -> Response:
    _verify_signature(raw_body, signature_header)

    try:
        raw_payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='invalid JSON body') from exc
    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='request body must be a JSON object')

    event_type = raw_payload.get('event')

    if event_type == 'collection.updated':
        return _handle_collection_updated(db)

    if event_type != 'document.processed':
        # Not an event type this service consumes -- ignored, not an error.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        event = DocumentProcessedEvent.model_validate(raw_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    event_key = f'{event.job_id}:{event.content_sha256}'
    payload_sha256 = hashlib.sha256(raw_body).hexdigest()

    # Flushed (not committed) so the unique-constraint check and the
    # Document upsert below land in one transaction: either both are
    # persisted together, or -- on a crash before the final commit -- a
    # subsequent redelivery finds neither and can safely retry the whole
    # thing, rather than seeing a "duplicate" that never actually created a
    # Document.
    db.add(IngestEvent(event_key=event_key, event_type=event.event, payload_sha256=payload_sha256))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(IngestEvent).where(IngestEvent.event_key == event_key)).scalar_one_or_none()
        if existing is not None and existing.payload_sha256 != payload_sha256:
            # Same (job_id, content_sha256) key, different body -- would
            # indicate a Weave-Ingest bug, not a normal at-least-once retry
            # (see IngestEvent's docstring). Still deduped either way: the
            # dedup key is (job_id, content_sha256) by contract, not a
            # payload hash.
            logger.warning('document.processed event %s redelivered with a different payload body', event_key)
        return JSONResponse(status_code=status.HTTP_200_OK, content={'status': 'duplicate'})

    frontmatter = event.frontmatter
    recommendation = event.quality.recommendation or 'warn'

    document, is_new = _upsert_document(db, event, frontmatter, recommendation)

    try:
        db.commit()
    except IntegrityError:
        # See FINDING 1 in this module's docstring. A unique-constraint
        # violation on what was supposed to be an UPDATE of an
        # already-loaded row would be a genuinely unexpected error (not
        # this race) -- don't swallow that case, only the documented
        # concurrent-INSERT one.
        db.rollback()
        if not is_new:
            raise
        db.add(IngestEvent(event_key=event_key, event_type=event.event, payload_sha256=payload_sha256))
        document, _ = _upsert_document(db, event, frontmatter, recommendation)
        db.commit()

    db.refresh(document)

    # Only now -- db's own write transaction is committed, its lock
    # released -- is it safe to open a second session for the lazy registry
    # reload (see _ensure_collection_known's docstring and _upsert_document's
    # comment above on why this can't happen any earlier).
    if document.collection_slug:
        _ensure_collection_known(document.collection_slug)

    if recommendation == 'block':
        return JSONResponse(
            status_code=status.HTTP_200_OK, content={'status': 'blocked', 'document_id': str(document.id)}
        )

    index_document.delay(str(document.id))

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED, content={'status': 'accepted', 'document_id': str(document.id)}
    )
