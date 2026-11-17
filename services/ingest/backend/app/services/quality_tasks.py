"""Review tasks ('Prüfaufgaben') from anonymous quality signals.

Single writer of `review_tasks` and `quality_signals` (app/models/models.py).
Two entry points:

- `ingest_signals`: the batch Weave-API forwards to POST
  /api/v1/quality/signals (app/api/review_tasks.py) -- chat feedback
  (`helpful|wrong|outdated`, possibly retracted) and unanswered questions
  (the answer guard's `no_context`). Idempotent per `signal_key`: a resent or
  updated signal upserts the same rows and recomputes the tasks they feed.
- `ensure_task` / `find_active_task` / `resolve_task` / `refresh_task`: the
  generic task primitives, reused by the other task kinds (`review_due`
  from the document review tick, `contradiction` from Weave-Knowledge's
  reports) so every kind keeps the same dedup and closing rules.

Attribution. A signal names knowledge spaces by slug (`collections`) and,
for feedback, the cited sources (`sources[].source_job_id` = the Ingest job
that was released). One QualitySignal row is stored per target -- per cited
document (job) for feedback with resolvable sources, else per collection --
with the internal key `<signal_key>|<job_id or collection_id>`, so the same
signal always maps to the same rows. Unknown slugs and jobs are ignored; a
signal without any target (e.g. `unanswered` with no collections) is
accepted and dropped as unattributable.

Tasks. At most one *active* (open/in_progress) task per (collection, kind,
cluster_key); a done/dismissed task is never reopened by a signal -- a new
matching signal opens a new task.
- `knowledge_gap` (from `unanswered`): the question is normalised into a
  token set (`normalize_terms`: lowercase, umlaut folding, punctuation and
  de/en stopwords dropped, light suffix stemming) and compared with the
  stored `details.tokens` of the space's active gap tasks: Jaccard >= 0.5,
  or one set contained in the other with >= 2 tokens. The best match is
  joined, else a new task (`title` = the question) is opened.
- `answer_feedback` (from `wrong`/`outdated`, not retracted): one task per
  cited document lineage (`cluster_key = feedback:<root job id>`), or per
  space when the answer had no resolvable source (`feedback:none`).
- `helpful` and retracted feedback are stored for the statistics only.
- `contradiction` (from Weave-Knowledge's POST /api/v1/quality/contradictions,
  `ingest_contradictions`): one task per unordered document pair
  (`cluster_key = contradiction:<min job id>:<max job id>`, job_id = the
  smaller id, related_job_id = the other); every distinct excerpt pair is
  one signal (`contradiction:<min>:<max>:<hash>`), the task's details keep
  the latest 5 pairs. Reports naming an unknown space or a job outside it
  are ignored.
`occurrence_count`, `last_seen_at` and `details` are always recomputed from
the non-retracted linked rows (`refresh_task`), so updates and retractions
stay correct; an `open` task left without live signals is dismissed
("zurückgezogen").

No chat user identity ever reaches this module: payloads are reduced to a
whitelist of fields (`_sanitize`) before anything is stored.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models.models import (
    REVIEW_TASK_ACTIVE_STATUSES,
    REVIEW_TASK_CLOSED_STATUSES,
    Collection,
    Job,
    QualitySignal,
    ReviewTask,
    ReviewTaskKind,
    ReviewTaskStatus,
)

logger = logging.getLogger(__name__)

# Incoming keys get `|<36-char id>` appended internally; the column is 128.
SIGNAL_KEY_MAX_LENGTH = 128 - 37
MAX_SIGNALS_PER_BATCH = 500

FEEDBACK_RATINGS = ('helpful', 'wrong', 'outdated')
TASK_RATINGS = ('wrong', 'outdated')
SIGNAL_KINDS = ('feedback', 'unanswered')

_DETAIL_LIST_LIMIT = 20
_TITLE_MAX = 200
_TEXT_MAX = 2000
_EXCERPT_MAX = 500
_SOURCES_MAX = 50
_COLLECTIONS_MAX = 50
_RETRACTED_NOTE = 'zurückgezogen'
# pg_advisory_xact_lock key serialising task creation (one active task per
# cluster), next to reingest.py's 7302000101.
_TASK_LOCK_KEY = 7302000201

# ---------------------------------------------------------------------------
# Question normalisation (shared with topic subscriptions' keyword matcher)

_FOLD = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'})
_STOPWORDS = frozenset("""
    a ab aber alle allem allen aller alles als also am an ans ander andere anderen auch auf aus bei beim bin bis bist
    bitte da dabei damit dann darauf darf das dass davon dazu dein deine dem den denn der des dessen die dies
    diese diesem diesen dieser dieses doch dort du durch ein eine einem einen einer eines er es etwas euch euer
    fuer gibt hab habe haben hat hatte hier hin ich ihm ihn ihr ihre im in ins ist ja jede jedem jeden jeder jetzt kann
    kannst kein keine koennen koennte man mehr mein meine mich mir mit muss muessen nach nicht noch nun nur ob oder
    ohne sehr sein seine sich sie sind so soll sollen sollte ueber um und uns unser unsere unter viel vom von vor
    war waren warum was weil welche welchem welchen welcher welches wenn wer werde werden wie wieso wir wird wo
    woher wohin wurde zu zum zur zwischen
    about after all also am an and any are as at be been being but by can could did do does doing for from had has
    have how i if in into is it its me my no not of on or our please should so than that the their them then there
    these they this those to too us was we were what when where which who whom why will with would you your
""".split())
_SUFFIXES = ('ungen', 'heiten', 'keiten', 'ung', 'heit', 'keit', 'lich', 'ing', 'ern', 'en', 'er', 'es', 'em', 'ed', 'e', 's', 'n')
_MIN_STEM = 3


def _fold(value: str) -> str:
    value = (value or '').lower().translate(_FOLD)
    return ''.join(ch for ch in unicodedata.normalize('NFKD', value) if not unicodedata.combining(ch))


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            return token[: -len(suffix)]
    return token


def normalize_terms(value: str | None) -> list[str]:
    """Sorted distinct content tokens of `value` (see module docstring).
    Deterministic and dependency-free; also the keyword matcher for topic
    subscriptions, so 'Reisekosten' and 'reisekosten?' match alike."""
    tokens = set()
    for token in re.findall(r'[a-z0-9]+', _fold(value or '')):
        if token in _STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        tokens.add(_stem(token))
    return sorted(tokens)


def terms_similarity(a: set[str] | list[str], b: set[str] | list[str]) -> float:
    """Match score of two token sets, 0.0 when they do not cluster: the
    Jaccard index when >= 0.5, at least 0.5 when one set (>= 2 tokens) is
    contained in the other."""
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    jaccard = len(left & right) / len(left | right)
    if jaccard >= 0.5:
        return jaccard
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    if len(smaller) >= 2 and smaller <= larger:
        return max(jaccard, 0.5)
    return 0.0


def gap_cluster_key(question: str, tokens: list[str]) -> str:
    basis = ' '.join(tokens) if tokens else ' '.join(_fold(question).split())
    return 'gap:' + hashlib.sha256(basis.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Generic task primitives (all kinds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """sqlite returns DateTime(timezone=True) naive; every write is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def lock_review_tasks(db: Session) -> None:
    """Serialise task creation for this transaction on PostgreSQL, so two
    concurrent batches cannot both open 'the one active task' of a
    cluster. sqlite (tests) has a single writer anyway."""
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _TASK_LOCK_KEY})


def find_active_task(db: Session, *, collection_id: str, kind: str, cluster_key: str) -> ReviewTask | None:
    # Sessions run with autoflush=False: make pending status changes (e.g.
    # close_active_tasks earlier in this transaction) visible to the query.
    db.flush()
    return db.scalar(
        select(ReviewTask)
        .where(
            ReviewTask.collection_id == collection_id,
            ReviewTask.kind == kind,
            ReviewTask.cluster_key == cluster_key,
            ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES),
        )
        .order_by(ReviewTask.created_at, ReviewTask.id)
        .limit(1)
    )


def ensure_task(
    db: Session,
    *,
    collection_id: str,
    kind: str,
    cluster_key: str,
    title: str,
    job_id: str | None = None,
    related_job_id: str | None = None,
    details: dict | None = None,
    seen_at: datetime | None = None,
) -> tuple[ReviewTask, bool]:
    """The active task of (collection, kind, cluster_key), created when
    there is none; returns (task, created). An existing task is only
    touched (`last_seen_at`) -- its title, documents and details belong to
    the caller (e.g. `refresh_task`). Call `lock_review_tasks` first in the
    same transaction when concurrent writers are possible."""
    seen_at = _aware(seen_at) or _now()
    task = find_active_task(db, collection_id=collection_id, kind=kind, cluster_key=cluster_key)
    if task is not None:
        if _aware(task.last_seen_at) is None or _aware(task.last_seen_at) < seen_at:
            task.last_seen_at = seen_at
        return task, False
    task = ReviewTask(
        collection_id=collection_id,
        kind=kind,
        status=ReviewTaskStatus.OPEN.value,
        title=(title or '').strip()[:_TITLE_MAX] or kind,
        cluster_key=cluster_key[:255],
        job_id=job_id,
        related_job_id=related_job_id,
        details=details or {},
        occurrence_count=1,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
    db.add(task)
    db.flush()
    return task, True


def resolve_task(task: ReviewTask, status: str, *, user_id: str | None, note: str | None = None) -> None:
    """Move `task` to `status`. Closing (done/dismissed) records who and
    when; moving back to an active status clears that again."""
    task.status = status
    if status in REVIEW_TASK_CLOSED_STATUSES:
        task.resolved_at = _now()
        task.resolved_by_id = user_id
    else:
        task.resolved_at = None
        task.resolved_by_id = None
    if note is not None:
        task.resolution_note = note


def close_active_tasks(
    db: Session, *, kind: str, job_id: str, status: str = ReviewTaskStatus.DONE.value,
    user_id: str | None = None, note: str | None = None,
) -> int:
    """Close every active task of `kind` about `job_id` (e.g. `review_due`
    after a confirmed review); returns how many."""
    tasks = db.scalars(
        select(ReviewTask).where(
            ReviewTask.kind == kind, ReviewTask.job_id == job_id, ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES)
        )
    ).all()
    for task in tasks:
        resolve_task(task, status, user_id=user_id, note=note)
    db.flush()
    return len(tasks)


def base_signal_key(signal_key: str) -> str:
    return signal_key.split('|', 1)[0]


def refresh_task(db: Session, task: ReviewTask) -> None:
    """Recompute occurrence_count, last_seen_at and the kind's details from
    the live linked signals (`_wants_task`). Closed tasks are history and
    stay untouched; an open task without live signals is dismissed."""
    if task.status not in REVIEW_TASK_ACTIVE_STATUSES:
        return
    rows = db.scalars(select(QualitySignal).where(QualitySignal.task_id == task.id)).all()
    if not rows:
        return  # not signal-backed (e.g. review_due): nothing to recompute
    live = sorted((row for row in rows if _wants_task(row)), key=_signal_time, reverse=True)
    task.occurrence_count = len({base_signal_key(row.signal_key) for row in live})
    if live:
        task.last_seen_at = max(_signal_time(row) for row in live)
    # Recomputed even without live rows, so withdrawn content never stays
    # visible (e.g. an in_progress task whose last feedback was retracted).
    if task.kind == ReviewTaskKind.KNOWLEDGE_GAP.value:
        task.details = _gap_details(task, live)
    elif task.kind == ReviewTaskKind.ANSWER_FEEDBACK.value:
        task.details = _feedback_details(live)
    elif task.kind == ReviewTaskKind.CONTRADICTION.value:
        task.details = _contradiction_details(live)
    if not live and task.status == ReviewTaskStatus.OPEN.value:
        resolve_task(task, ReviewTaskStatus.DISMISSED.value, user_id=None, note=_RETRACTED_NOTE)


def _signal_time(row: QualitySignal) -> datetime:
    parsed = _parse_time((row.payload or {}).get('created_at'))
    return parsed or _aware(row.created_at) or _now()


def _gap_details(task: ReviewTask, live: list[QualitySignal]) -> dict:
    questions: dict[str, dict] = {}
    rewritten: list[str] = []
    seen_bases: set[str] = set()
    for row in live:  # newest first
        base = base_signal_key(row.signal_key)
        if base in seen_bases:
            continue
        seen_bases.add(base)
        payload = row.payload or {}
        question = (payload.get('question') or '').strip()
        if question:
            entry = questions.setdefault(question, {'text': question, 'created_at': _signal_time(row).isoformat(), 'count': 0})
            entry['count'] += 1
        query = (payload.get('rewritten_query') or '').strip()
        if query and query not in rewritten:
            rewritten.append(query)
    previous = task.details if isinstance(task.details, dict) else {}
    return {
        'tokens': list(previous.get('tokens') or []),
        'questions': list(questions.values())[:_DETAIL_LIST_LIMIT],
        'rewritten_queries': rewritten[:_DETAIL_LIST_LIMIT],
    }


def _feedback_details(live: list[QualitySignal]) -> dict:
    counts = {rating: 0 for rating in TASK_RATINGS}
    samples: list[dict] = []
    seen_bases: set[str] = set()
    for row in live:  # newest first
        base = base_signal_key(row.signal_key)
        if base in seen_bases:
            continue
        seen_bases.add(base)
        payload = row.payload or {}
        if row.rating in counts:
            counts[row.rating] += 1
        if len(samples) < _DETAIL_LIST_LIMIT:
            source = next(
                (item for item in payload.get('sources') or [] if row.job_id and item.get('source_job_id') == row.job_id),
                None,
            )
            samples.append({
                'rating': row.rating,
                'comment': payload.get('comment'),
                'question': payload.get('question'),
                'answer_excerpt': payload.get('answer_excerpt'),
                'heading_path': (source or {}).get('heading_path'),
                'created_at': _signal_time(row).isoformat(),
            })
    return {'counts': counts, 'samples': samples}


_CONTRADICTION_PAIRS = 5
_PAIR_FIELDS = ('excerpt', 'heading_path', 'facts')


def _contradiction_details(live: list[QualitySignal]) -> dict:
    pairs: list[dict] = []
    similarities: list[float] = []
    for row in live:  # newest first
        payload = row.payload or {}
        if isinstance(payload.get('similarity'), (int, float)):
            similarities.append(float(payload['similarity']))
        if len(pairs) < _CONTRADICTION_PAIRS:
            pairs.append({
                **{key: payload.get(key) for key in ('job_id', 'other_job_id', 'similarity')},
                **{key: payload.get(key) for field_ in _PAIR_FIELDS for key in (field_, f'other_{field_}')},
                'created_at': _signal_time(row).isoformat(),
            })
    return {'pairs': pairs, 'max_similarity': max(similarities) if similarities else None}


# ---------------------------------------------------------------------------
# Signal intake


class SignalRejected(ValueError):
    """A malformed signal: skipped (and logged) without failing the batch,
    so one bad outbox row never blocks the sender's retries of the rest."""


@dataclass(frozen=True)
class _Target:
    collection: Collection
    job: Job | None

    @property
    def suffix(self) -> str:
        return self.job.id if self.job is not None else self.collection.id


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    parsed = _aware(parsed)
    return min(parsed, _now())


def _text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _str_list(value, limit: int, item_limit: int = 255) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip()[:item_limit] not in result:
            result.append(item.strip()[:item_limit])
        if len(result) >= limit:
            break
    return result


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sanitize(payload: dict) -> dict:
    """Whitelisted, size-bounded copy of an incoming payload. Anything not
    listed here (in particular any user/team identity a sender might add by
    mistake) is never stored."""
    if not isinstance(payload, dict):
        raise SignalRejected('payload must be an object')
    kind = payload.get('kind')
    if kind not in SIGNAL_KINDS:
        raise SignalRejected(f'unsupported kind {kind!r}')
    retracted = payload.get('retracted') is True
    created = _parse_time(payload.get('created_at')) or _now()
    clean = {
        'kind': kind,
        'question': _text(payload.get('question'), _TEXT_MAX),
        'guard_reason': _text(payload.get('guard_reason'), 64),
        'collections': _str_list(payload.get('collections'), _COLLECTIONS_MAX),
        'bot_id': _text(payload.get('bot_id'), 64),
        'created_at': created.isoformat(),
        'retracted': retracted,
    }
    if kind == 'unanswered':
        clean['rewritten_query'] = _text(payload.get('rewritten_query'), _TEXT_MAX)
        return clean
    rating = payload.get('rating')
    if rating not in FEEDBACK_RATINGS:
        if not retracted:
            raise SignalRejected(f'invalid rating {rating!r}')
        rating = None
    sources = []
    for item in (payload.get('sources') if isinstance(payload.get('sources'), list) else [])[:_SOURCES_MAX]:
        if not isinstance(item, dict):
            continue
        sources.append({
            'source_job_id': _text(item.get('source_job_id'), 36),
            'collection': _text(item.get('collection'), 255),
            'document_version': _int_or_none(item.get('document_version')),
            'heading_path': _str_list(item.get('heading_path'), 12, 300),
            'chunk_index': _int_or_none(item.get('chunk_index')),
            'chunk_id': _int_or_none(item.get('chunk_id')),
            'original_filename': _text(item.get('original_filename'), 255),
        })
    clean.update({
        'rating': rating,
        'comment': _text(payload.get('comment'), _TEXT_MAX),
        'answer_excerpt': _text(payload.get('answer_excerpt'), _EXCERPT_MAX),
        'sources': sources,
    })
    return clean


def collection_for_job(db: Session, job: Job) -> Collection | None:
    """The established job -> collection association (processing_info JSON,
    see portal.py's `_collection_for_job`)."""
    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    settings_ = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    collection_id = settings_.get('collection_id')
    return db.get(Collection, collection_id) if isinstance(collection_id, str) else None


def lineage_root_id(db: Session, job: Job) -> str:
    """Id of the first version of `job`'s version chain (previous_job_id)."""
    current, seen = job, {job.id}
    while current.previous_job_id and len(seen) < 1000:
        previous = db.get(Job, current.previous_job_id)
        if previous is None or previous.id in seen:
            break
        seen.add(previous.id)
        current = previous
    return current.id


def _collections(db: Session, slugs: list[str]) -> list[Collection]:
    if not slugs:
        return []
    found = {row.slug: row for row in db.scalars(select(Collection).where(Collection.slug.in_(slugs))).all()}
    return [found[slug] for slug in slugs if slug in found]


def _targets(db: Session, payload: dict) -> list[_Target]:
    targets: dict[str, _Target] = {}
    if payload['kind'] == 'feedback':
        for source in payload.get('sources') or []:
            job_id = source.get('source_job_id')
            job = db.get(Job, job_id) if job_id else None
            if job is None or job.benchmark_run_id is not None or job.id in targets:
                continue
            collection = collection_for_job(db, job)
            if collection is not None:
                targets[job.id] = _Target(collection, job)
    if not targets:
        # unanswered, or feedback without a resolvable source: the spaces.
        for collection in _collections(db, payload.get('collections') or []):
            targets.setdefault(collection.id, _Target(collection, None))
    return list(targets.values())


def _rows_for(db: Session, signal_key: str) -> list[QualitySignal]:
    return list(db.scalars(
        select(QualitySignal).where(
            or_(QualitySignal.signal_key == signal_key, QualitySignal.signal_key.startswith(f'{signal_key}|', autoescape=True))
        )
    ).all())


def _wants_task(row: QualitySignal) -> bool:
    """Whether the row feeds a task: not retracted, and for feedback only
    the ratings `wrong`/`outdated`."""
    if row.retracted:
        return False
    return row.kind != 'feedback' or row.rating in TASK_RATINGS


# Upper bound of active gap tasks a new question is compared with (the most
# recently seen ones), so one space with a huge backlog cannot make every
# signal batch slow.
_GAP_CANDIDATES_MAX = 500


def _gap_candidates(db: Session, collection_id: str, cache: dict | None) -> list[tuple[str, set[str]]]:
    """`(task id, token set)` of the collection's active gap tasks, oldest
    first; only the id and details columns are loaded, and the list is
    cached per collection for the whole signal batch."""
    if cache is not None and collection_id in cache:
        return cache[collection_id]
    rows = db.execute(
        select(ReviewTask.id, ReviewTask.details, ReviewTask.created_at)
        .where(
            ReviewTask.collection_id == collection_id,
            ReviewTask.kind == ReviewTaskKind.KNOWLEDGE_GAP.value,
            ReviewTask.status.in_(REVIEW_TASK_ACTIVE_STATUSES),
        )
        .order_by(ReviewTask.last_seen_at.desc(), ReviewTask.id)
        .limit(_GAP_CANDIDATES_MAX)
    ).all()
    rows = sorted(rows, key=lambda r: (_aware(r.created_at) or datetime.min.replace(tzinfo=timezone.utc), r.id))
    candidates = [
        (task_id, set((details or {}).get('tokens') or []) if isinstance(details, dict) else set())
        for task_id, details, _created in rows
    ]
    if cache is not None:
        cache[collection_id] = candidates
    return candidates


def _assign_gap_task(
    db: Session, row: QualitySignal, payload: dict, seen_at: datetime, cache: dict | None = None
) -> ReviewTask:
    question = payload.get('question') or payload.get('rewritten_query') or ''
    tokens = normalize_terms(question)
    cluster_key = gap_cluster_key(question, tokens)
    kind = ReviewTaskKind.KNOWLEDGE_GAP.value
    task = find_active_task(db, collection_id=row.collection_id, kind=kind, cluster_key=cluster_key)
    if task is None and tokens:
        scored = [
            (score, index, task_id)
            for index, (task_id, stored) in enumerate(_gap_candidates(db, row.collection_id, cache))
            if (score := terms_similarity(tokens, stored)) > 0
        ]
        # Best score first, the oldest task on a tie; a cached candidate the
        # batch has closed meanwhile is skipped.
        for _score, _index, task_id in sorted(scored, key=lambda item: (-item[0], item[1])):
            candidate = db.get(ReviewTask, task_id)
            if candidate is not None and candidate.status in REVIEW_TASK_ACTIVE_STATUSES:
                task = candidate
                break
    if task is not None:
        if _aware(task.last_seen_at) < seen_at:
            task.last_seen_at = seen_at
        return task
    task, _created = ensure_task(
        db, collection_id=row.collection_id, kind=kind, cluster_key=cluster_key,
        title=question or 'Unbeantwortete Frage', details={'tokens': tokens, 'questions': [], 'rewritten_queries': []},
        seen_at=seen_at,
    )
    if cache is not None and row.collection_id in cache:
        cache[row.collection_id].append((task.id, set(tokens)))
    return task


def _assign_feedback_task(db: Session, row: QualitySignal, target: _Target, seen_at: datetime) -> ReviewTask:
    kind = ReviewTaskKind.ANSWER_FEEDBACK.value
    if target.job is None:
        task, _created = ensure_task(
            db, collection_id=target.collection.id, kind=kind, cluster_key='feedback:none',
            title='Rückmeldungen zu Antworten ohne Quellenbezug', seen_at=seen_at,
        )
        return task
    task, created = ensure_task(
        db, collection_id=target.collection.id, kind=kind, cluster_key=f'feedback:{lineage_root_id(db, target.job)}',
        title=f'Rückmeldungen zu „{target.job.original_filename}“', job_id=target.job.id, seen_at=seen_at,
    )
    if not created:
        # Point the task at the newest cited version of the document.
        current = db.get(Job, task.job_id) if task.job_id else None
        if current is None or target.job.document_version > current.document_version:
            task.job_id = target.job.id
            task.title = f'Rückmeldungen zu „{target.job.original_filename}“'[:_TITLE_MAX]
    return task


def ingest_signal(db: Session, signal_key: str, raw_payload: dict, gap_cache: dict | None = None) -> None:
    """Upsert one signal (see module docstring). Raises SignalRejected for
    a malformed payload before writing anything."""
    payload = _sanitize(raw_payload)
    seen_at = _parse_time(payload['created_at']) or _now()
    targets = _targets(db, payload)
    existing = {row.signal_key: row for row in _rows_for(db, signal_key)}
    touched: dict[str, ReviewTask] = {}
    current_keys: set[str] = set()

    def touch(task_id: str | None) -> None:
        if task_id and task_id not in touched:
            task = db.get(ReviewTask, task_id)
            if task is not None:
                touched[task_id] = task

    for target in targets:
        key = f'{signal_key}|{target.suffix}'
        current_keys.add(key)
        row = existing.get(key)
        was_live = row is not None and _wants_task(row)
        if row is None:
            row = QualitySignal(signal_key=key)
            db.add(row)
        row.kind = payload['kind']
        row.rating = payload.get('rating')
        row.collection_id = target.collection.id
        row.job_id = target.job.id if target.job is not None else None
        row.payload = payload
        row.retracted = payload['retracted']
        linked = db.get(ReviewTask, row.task_id) if row.task_id is not None else None
        if linked is not None and (
            linked.status in REVIEW_TASK_ACTIVE_STATUSES or was_live or not _wants_task(row)
        ):
            # Stays linked even when it no longer counts (retracted, or e.g.
            # 'wrong' changed to 'helpful'); refresh_task only counts live rows.
            # A plain resend of a live signal keeps a closed task closed.
            touch(row.task_id)
        elif _wants_task(row):
            # Unlinked, or live again (e.g. wrong -> helpful -> wrong) after its
            # task was closed: closed tasks are never reopened, so it opens or
            # joins an active task.
            if row.kind == 'unanswered':
                task = _assign_gap_task(db, row, payload, seen_at, gap_cache)
            else:
                task = _assign_feedback_task(db, row, target, seen_at)
            row.task_id = task.id
            touched[task.id] = task
    for key, row in existing.items():
        if key not in current_keys and not row.retracted:
            # Target no longer named by the updated signal.
            row.retracted = True
            touch(row.task_id)
    db.flush()
    for task in touched.values():
        refresh_task(db, task)
    db.flush()


def ingest_signals(db: Session, signals: list[tuple[str, dict]]) -> int:
    """Process a batch in order (a key repeated later in the batch wins);
    returns how many signals were accepted. Malformed ones are skipped.
    The caller commits."""
    lock_review_tasks(db)
    accepted = 0
    # Active gap tasks per collection, loaded once per batch (the batch holds
    # the review-task lock, so no other writer changes them meanwhile).
    gap_cache: dict = {}
    for signal_key, payload in signals:
        try:
            ingest_signal(db, signal_key, payload, gap_cache)
        except SignalRejected as exc:
            logger.warning('quality signal %s skipped: %s', signal_key, exc)
            continue
        accepted += 1
    return accepted


# ---------------------------------------------------------------------------
# Contradiction intake (Weave-Knowledge)


def _similarity(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(min(max(float(value), -1.0), 1.0), 4)


def ingest_contradiction(db: Session, report: dict) -> bool:
    """Store one possible-contradiction report (see module docstring);
    False when it is ignored (unknown space, unknown job, a job outside
    the space, or both sides the same document)."""
    collection = db.scalar(select(Collection).where(Collection.slug == report.get('collection_slug')))
    if collection is None:
        return False
    jobs = []
    for key in ('source_job_id', 'other_source_job_id'):
        job_id = report.get(key)
        job = db.get(Job, job_id) if isinstance(job_id, str) and job_id else None
        if job is None or job.benchmark_run_id is not None:
            return False
        owner = collection_for_job(db, job)
        if owner is None or owner.id != collection.id:
            return False
        jobs.append(job)
    source, other = jobs
    if source.id == other.id:
        return False
    sides = {
        source.id: {field_: report.get(field_) for field_ in _PAIR_FIELDS},
        other.id: {field_: report.get(f'other_{field_}') for field_ in _PAIR_FIELDS},
    }
    low, high = sorted((source.id, other.id))
    low_job, high_job = (source, other) if low == source.id else (other, source)

    def side(job_id: str) -> dict:
        values = sides[job_id]
        return {
            'excerpt': _text(values['excerpt'], _TEXT_MAX),
            'heading_path': _str_list(values['heading_path'], 12, 300),
            'facts': _str_list(values['facts'], 20, 100),
        }

    first, second = side(low), side(high)
    now = _now()
    payload = {
        'kind': 'contradiction',
        'job_id': low,
        'other_job_id': high,
        'similarity': _similarity(report.get('similarity')),
        **first,
        **{f'other_{key}': value for key, value in second.items()},
        'created_at': now.isoformat(),
    }
    digest = hashlib.sha256(f"{first['excerpt'] or ''}\x00{second['excerpt'] or ''}".encode()).hexdigest()[:16]
    signal_key = f'contradiction:{low}:{high}:{digest}'
    row = db.scalar(select(QualitySignal).where(QualitySignal.signal_key == signal_key))
    if row is None:
        row = QualitySignal(signal_key=signal_key)
        db.add(row)
    else:
        payload['created_at'] = (row.payload or {}).get('created_at') or payload['created_at']
    row.kind = 'contradiction'
    row.rating = None
    row.collection_id = collection.id
    row.job_id = low
    row.payload = payload
    row.retracted = False
    if row.task_id is None:
        task, _created = ensure_task(
            db, collection_id=collection.id, kind=ReviewTaskKind.CONTRADICTION.value,
            cluster_key=f'contradiction:{low}:{high}',
            title=f'Möglicher Widerspruch: „{low_job.original_filename}“ ↔ „{high_job.original_filename}“',
            job_id=low, related_job_id=high, seen_at=now,
        )
        row.task_id = task.id
    db.flush()
    task = db.get(ReviewTask, row.task_id)
    if task is not None:
        refresh_task(db, task)
    db.flush()
    return True


def ingest_contradictions(db: Session, reports: list[dict]) -> int:
    """Process a batch from POST /api/v1/quality/contradictions; returns how
    many reports were stored. The caller commits."""
    lock_review_tasks(db)
    accepted = 0
    for report in reports:
        if ingest_contradiction(db, report):
            accepted += 1
    return accepted
