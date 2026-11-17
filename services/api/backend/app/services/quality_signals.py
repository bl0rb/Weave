"""Anonymous quality signals for Weave-Ingest's review tasks
(contracts: Weave-Ingest's POST /api/v1/quality/signals, app/services/
quality_tasks.py there): a chat user's answer feedback (app/api/feedback.py)
and every unanswered turn (a `no_context` guard, app/api/chat.py).

Transactional outbox, in two halves:

1. `enqueue_feedback_signal` / `enqueue_unanswered_signal` write (or
   rewrite) one `QualitySignalOutbox` row in the CALLER's transaction --
   they never commit, never call out over the network, and are no-ops
   while `settings.quality_signals_enabled` is false.
2. `flush_outbox` forwards unforwarded rows (oldest first, a bounded batch)
   in ONE POST to `{INGEST_API_URL}/api/v1/quality/signals`, bearer
   `INGEST_SERVICE_TOKEN` (the same credential app/api/portal_artifacts.py
   presents to Weave-Ingest). Routes schedule it via FastAPI's
   `BackgroundTasks` (`flush_outbox_in_background`), so it runs only after
   the response was sent and can never fail or slow the user's request;
   every failure is logged and recorded on the rows (`attempts`,
   `last_error`), and the next flush simply retries them.

Payloads never carry who asked or rated: no user id, name, email or team --
only the question, the answer excerpt, the cited sources and the bot. The
knowledge owners on Weave-Ingest's side must never learn which chat user
gave a piece of feedback.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Conversation, Message, MessageFeedback, MessageRole, QualitySignalOutbox

logger = logging.getLogger(__name__)

SIGNALS_PATH = '/api/v1/quality/signals'
ANSWER_EXCERPT_MAX_LENGTH = 500
LAST_ERROR_MAX_LENGTH = 500
# Forwarded rows older than this are deleted during a flush: Weave-Ingest
# already holds them, the outbox only needs them for retries.
FORWARDED_RETENTION = timedelta(days=30)
# Weave-Runtime's NO_COLLECTION_SENTINEL ("documents without a collection"),
# which may appear in `trace.retrieval.collections` -- never a real slug.
NO_COLLECTION_SENTINEL = '__none__'
# The Source keys forwarded per cited source (contracts/internal-chat.md's
# `Source`); everything else (text, uploaded_by, notice, ...) stays here.
_SOURCE_KEYS = (
    'source_job_id',
    'collection',
    'document_version',
    'heading_path',
    'chunk_index',
    'chunk_id',
    'original_filename',
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # SQLite hands timezone-aware columns back naive; they were written as UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _guard(trace: object) -> dict:
    guard = trace.get('guard') if isinstance(trace, dict) else None
    return guard if isinstance(guard, dict) else {}


def _retrieval(trace: object) -> dict:
    retrieval = trace.get('retrieval') if isinstance(trace, dict) else None
    return retrieval if isinstance(retrieval, dict) else {}


def _trace_collections(trace: object) -> list[str]:
    collections = _retrieval(trace).get('collections')
    if not isinstance(collections, list):
        return []
    result: list[str] = []
    for slug in collections:
        if isinstance(slug, str) and slug and slug != NO_COLLECTION_SENTINEL and slug not in result:
            result.append(slug)
    return result


def _sources(message: Message) -> list[dict]:
    if not isinstance(message.sources, list):
        return []
    return [{key: source.get(key) for key in _SOURCE_KEYS} for source in message.sources if isinstance(source, dict)]


def _source_collections(sources: list[dict]) -> list[str]:
    result: list[str] = []
    for source in sources:
        slug = source.get('collection')
        if isinstance(slug, str) and slug and slug not in result:
            result.append(slug)
    return result


def preceding_question(db: Session, message: Message) -> str | None:
    """The content of the closest `user` message before `message` in the
    same conversation -- the question this answer responded to."""
    return db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.id < message.id,
            Message.role == MessageRole.USER,
        )
        .order_by(Message.id.desc())
        .limit(1)
    )


def _enqueue(db: Session, signal_key: str, payload: dict) -> QualitySignalOutbox:
    """Insert the outbox row for `signal_key`, or rewrite the existing one
    in place (new payload, re-enqueue time, not forwarded, no attempts) so
    only the latest state is ever sent. No commit -- the caller's."""
    row = db.scalar(select(QualitySignalOutbox).where(QualitySignalOutbox.signal_key == signal_key))
    if row is None:
        row = QualitySignalOutbox(signal_key=signal_key, payload=payload, created_at=_now(), attempts=0)
        db.add(row)
    else:
        row.payload = payload
        row.created_at = _now()
        row.forwarded_at = None
        row.attempts = 0
        row.last_error = None
    db.flush()
    return row


def feedback_signal_key(feedback: MessageFeedback) -> str:
    return f'feedback:{feedback.id}'


def build_feedback_payload(
    db: Session,
    *,
    feedback: MessageFeedback,
    message: Message,
    conversation: Conversation,
    retracted: bool,
) -> dict:
    """The `kind: "feedback"` payload for `feedback` on the assistant
    `message` -- see this module's docstring for what is never included."""
    sources = _sources(message)
    collections = _source_collections(sources) or _trace_collections(message.trace)
    return {
        'kind': 'feedback',
        'rating': feedback.rating,
        'comment': feedback.comment,
        'question': preceding_question(db, message),
        'answer_excerpt': (message.content or '')[:ANSWER_EXCERPT_MAX_LENGTH],
        'guard_reason': _guard(message.trace).get('reason'),
        'sources': sources,
        'collections': collections,
        'bot_id': conversation.bot_id,
        # When this rating was given (a changed rating is a new signal state).
        'created_at': _iso(feedback.updated_at or feedback.created_at),
        'retracted': retracted,
    }


def enqueue_feedback_signal(
    db: Session,
    *,
    feedback: MessageFeedback,
    message: Message,
    conversation: Conversation,
    retracted: bool = False,
) -> bool:
    """Queue (or re-queue) the signal for `feedback`; `retracted=True` when
    the user took the rating back. False (nothing queued) while quality
    signals are disabled."""
    if not settings.quality_signals_enabled:
        return False
    payload = build_feedback_payload(
        db, feedback=feedback, message=message, conversation=conversation, retracted=retracted
    )
    _enqueue(db, feedback_signal_key(feedback), payload)
    return True


def is_unanswered(trace: object) -> bool:
    """True for a turn the `no_context` guard answered with its fixed reply
    (retrieval ran but found nothing usable, contracts/internal-chat.md's
    GuardTrace) -- the knowledge gap the owners should hear about. Other
    guard reasons (no readable collections, a filter, a blocked topic, a
    clarifying question) are not gaps in the documents."""
    guard = _guard(trace)
    return guard.get('triggered') is True and guard.get('reason') == 'no_context'


def enqueue_unanswered_signal(db: Session, *, message: Message, conversation: Conversation) -> bool:
    """Queue `unanswered:<message.id>` if the persisted assistant `message`
    is an unanswered turn (`is_unanswered`). False when it is not, or while
    quality signals are disabled. An empty `collections` list is still
    queued; Weave-Ingest then has nothing to attribute it to and drops it."""
    if not settings.quality_signals_enabled or not is_unanswered(message.trace):
        return False
    rewritten_query = _retrieval(message.trace).get('rewritten_query')
    payload = {
        'kind': 'unanswered',
        'question': preceding_question(db, message),
        'guard_reason': _guard(message.trace).get('reason'),
        'collections': _trace_collections(message.trace),
        'bot_id': conversation.bot_id,
        'created_at': _iso(message.created_at),
        'rewritten_query': rewritten_query if isinstance(rewritten_query, str) else None,
    }
    _enqueue(db, f'unanswered:{message.id}', payload)
    return True


def _client() -> httpx.Client:
    """Factored out so tests can monkeypatch it (same seam as
    app/api/portal_artifacts.py's own `_client()`)."""
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_read_timeout_seconds,
        pool=settings.http_read_timeout_seconds,
    )
    headers = {'Authorization': f'Bearer {settings.ingest_service_token}'}
    return httpx.Client(base_url=settings.ingest_api_url.rstrip('/'), headers=headers, timeout=timeout)


def _record_failure(db: Session, rows: list[QualitySignalOutbox], error: str) -> None:
    for row in rows:
        row.attempts = (row.attempts or 0) + 1
        row.last_error = error[:LAST_ERROR_MAX_LENGTH]
    db.commit()


def _requeue_superseded(db: Session, sent: list[tuple]) -> int:
    """Re-queue every sent row whose `created_at` changed while the request
    was in flight (rewritten by `_enqueue`, or re-queued by another flush):
    a fresh `created_at` (re-enqueue time) and `forwarded_at` reset. Commits;
    returns how many rows were re-queued.

    Weave-Ingest upserts blindly by `signal_key` and the payload carries no
    version it could order by, so two overlapping flushes may reach it out
    of order: the flush that sent the NEWER state can finish (and mark the
    row forwarded) before the one that sent the OLDER state is processed,
    which would leave the stale state there for good. The flush that sent
    the stale state finds out afterwards -- its row no longer has the
    `created_at` it read -- and re-queues the row, so the latest state is
    sent again after its own request. The fresh `created_at` also makes a
    still-pending conditional mark of the other flush fail, so that one
    cannot mark the row forwarded behind this re-queue."""
    requeued = 0
    for row_id, created_at in sent:
        result = db.execute(
            update(QualitySignalOutbox)
            .where(QualitySignalOutbox.id == row_id, QualitySignalOutbox.created_at != created_at)
            .values(created_at=_now(), forwarded_at=None)
            .execution_options(synchronize_session=False)
        )
        requeued += result.rowcount or 0
    db.commit()
    return requeued


def _flush_batch(db: Session, limit: int) -> tuple[int, int, bool]:
    """One POST of up to `limit` unforwarded rows (oldest first); returns
    (rows marked forwarded, rows re-queued by `_requeue_superseded`,
    whether Weave-Ingest accepted the request)."""
    rows = list(
        db.scalars(
            select(QualitySignalOutbox)
            .where(QualitySignalOutbox.forwarded_at.is_(None))
            .order_by(QualitySignalOutbox.created_at, QualitySignalOutbox.id)
            .limit(limit)
            # SessionLocal does not expire on commit: refresh rows this
            # session already holds (e.g. from the previous round) instead of
            # resending their stale payload/`created_at`.
            .execution_options(populate_existing=True)
        )
    )
    if not rows:
        return 0, 0, True
    sent = [(row.id, row.created_at) for row in rows]
    body = {'signals': [{'signal_key': row.signal_key, 'payload': row.payload} for row in rows]}
    # Release the read transaction during the network call (see
    # app/api/chat.py's _release_db_connection for why).
    db.commit()

    try:
        with _client() as client:
            response = client.post(SIGNALS_PATH, json=body)
    except httpx.RequestError as exc:
        # The transport error names internal hosts/ports -- logs only.
        logger.warning('Forwarding %d quality signal(s) to Weave-Ingest failed: %r', len(rows), exc)
        _record_failure(db, rows, 'Weave-Ingest unreachable')
        # A timed-out request may still have been applied over a newer state.
        return 0, _requeue_superseded(db, sent), False

    if response.status_code >= 400:
        logger.warning(
            'Weave-Ingest rejected %d quality signal(s) with HTTP %s', len(rows), response.status_code
        )
        _record_failure(db, rows, f'Weave-Ingest returned HTTP {response.status_code}')
        return 0, _requeue_superseded(db, sent), False

    forwarded_at = _now()
    forwarded = 0
    for row_id, created_at in sent:
        result = db.execute(
            update(QualitySignalOutbox)
            .where(
                QualitySignalOutbox.id == row_id,
                QualitySignalOutbox.created_at == created_at,
                QualitySignalOutbox.forwarded_at.is_(None),
            )
            .values(forwarded_at=forwarded_at, last_error=None)
            .execution_options(synchronize_session=False)
        )
        forwarded += result.rowcount or 0
    db.commit()
    return forwarded, _requeue_superseded(db, sent), True


def flush_outbox(db: Session, limit: int = 100) -> int:
    """Forward up to `limit` unforwarded outbox rows (oldest first) to
    Weave-Ingest in one request; returns how many were marked forwarded.
    A no-op (0) while `INGEST_API_URL` or `INGEST_SERVICE_TOKEN` is unset.
    Never raises for a transport/HTTP failure: it is logged and recorded
    on the rows for the next flush to retry.

    A row rewritten while the request was in flight (a feedback changed or
    retracted meanwhile, see `_enqueue`) is NOT marked forwarded: the mark
    is conditional on the `created_at` that was read. It is re-queued
    instead (`_requeue_superseded`, which also covers overlapping flushes
    reaching Weave-Ingest out of order), and after a successful request
    ONE follow-up request sends the re-queued rows' latest state right away
    -- otherwise it would wait for whichever request schedules the next
    flush."""
    if not settings.ingest_api_url or not settings.ingest_service_token:
        return 0

    db.execute(
        delete(QualitySignalOutbox).where(
            QualitySignalOutbox.forwarded_at.is_not(None),
            QualitySignalOutbox.forwarded_at < _now() - FORWARDED_RETENTION,
        )
    )
    db.commit()

    forwarded, requeued, accepted = _flush_batch(db, limit)
    if requeued and accepted:
        # Only after a successful request: a failing Weave-Ingest is retried
        # by the next flush, not hammered here. Bounded to one follow-up.
        more, _, _ = _flush_batch(db, limit)
        forwarded += more
    return forwarded


def flush_outbox_in_background() -> None:
    """The `BackgroundTasks` entry point: its own session (the request's
    is closed by the time background tasks run), and nothing ever escapes
    -- a failed flush must not surface anywhere but the log."""
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        flush_outbox(db)
    except Exception:  # noqa: BLE001 -- best effort by design
        logger.exception('Flushing the quality signal outbox failed')
        db.rollback()
    finally:
        db.close()
