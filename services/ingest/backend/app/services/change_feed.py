"""Topic subscriptions and the change feed ('Was hat sich geändert?').

- `process_pending_releases` -- the change_notification_tick
  (app/workers/freshness_tasks.py): every DocumentRelease with
  `subscribers_notified_at IS NULL` (oldest first, in batches) is described
  once (`describe_release`) and matched against the TopicSubscriptions; a
  match becomes a ChangeNotification (at most one per subscription and
  job), then the release is marked notified. Releases older than
  `_BACKLOG_DAYS` (e.g. restored from a pre-0063 backup) are marked without
  notifying, so a backlog never floods the feeds.
- `search_changes` -- GET /api/v1/portal/changes/search: the same
  description and matcher over the recent releases the caller may read,
  ad hoc ("Was hat sich an unseren Reiseregeln geändert?").

Visibility: a subscriber/caller must be able to *read* the space
(collection_role: any grant, a public space, or admin) -- the same people
who get the document's content in the chat. Only released content is ever
compared (release snapshot against the previously released version's
snapshot), never an unreleased draft.

Matching: the subscription's `query` is normalised like the knowledge-gap
clustering (app/services/quality_tasks.normalize_terms: lowercase, umlaut
folding, stopwords dropped, light stemming); EVERY keyword must occur in
the release's token set -- title, file name, tags, heading paths and the
changed text of an update (removed lines included), or the whole text of a
new document. A keyword of >= 4 characters also matches as a word prefix,
so 'Reise' finds the German compound 'Reisekosten'. A subscription without
query matches every change in its space. `query_terms` additionally drops
the filler words of a change question („Was hat sich an unseren
Reiseregeln geändert?“ -> ['reiseregel']), which would otherwise never occur
in a document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.models import (
    ChangeNotification,
    Collection,
    DocumentRelease,
    Job,
    KnowledgeWithdrawal,
    Tag,
    TopicSubscription,
    User,
    job_tags,
)
from app.services import document_diff
from app.services.collection_access import collection_role, user_team_roles
from app.services.document_freshness import aware, version_markdown
from app.services.publications import restore_relative_image_urls
from app.services.quality_tasks import collection_for_job, normalize_terms

logger = logging.getLogger(__name__)

MAX_SUMMARY_LINES = 10
_PREFIX_MIN = 4
_BATCH = 50
_MAX_BATCHES = 10
_BACKLOG_DAYS = 7
SEARCH_LIMIT = 50
_SEARCH_SCAN = 150  # releases described (snapshot reads + diff) per search request


def _now() -> datetime:
    return datetime.now(timezone.utc)


_FILLER = frozenset(normalize_terms('''
    geändert geaendert ändern änderung änderungen aenderung aenderungen neu neue neuer neues neuen
    aktuell aktuelle aktuellen aktualisiert unseren unserer unserem eure euren eurer letzte letzten zuletzt
    seit heute gestern woche wochen monat monaten gilt gelten bitte
    changed change changes new newest updated update updates recent recently latest
'''))


def query_terms(query: str | None) -> list[str]:
    """Keywords of a subscription query or change question: the gap
    clustering's normalised tokens minus change-question filler words."""
    return [term for term in normalize_terms(query) if term not in _FILLER] if query else []


def keyword_matches(keywords: list[str], tokens: set[str]) -> bool:
    """Every keyword occurs in `tokens` (exactly, or as a prefix when it
    has >= 4 characters). No keywords = match."""
    for keyword in keywords:
        if keyword in tokens:
            continue
        if len(keyword) >= _PREFIX_MIN and any(token.startswith(keyword) for token in tokens):
            continue
        return False
    return True


@dataclass
class ReleaseChange:
    release: DocumentRelease
    job: Job
    collection: Collection
    previous_job: Job | None
    change_type: str  # 'new' | 'updated'
    title: str
    summary: dict
    tokens: set[str]

    @property
    def released_at(self) -> datetime:
        return aware(self.release.created_at)


def previous_released(db: Session, job: Job) -> Job | None:
    """The nearest older version of the chain that has a release -- what
    the chat showed before this one."""
    seen = {job.id}
    current = job
    while current.previous_job_id and len(seen) < 1000:
        previous = db.get(Job, current.previous_job_id)
        if previous is None or previous.id in seen:
            return None
        seen.add(previous.id)
        if db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == previous.id)) is not None:
            return previous
        current = previous
    return None


def _frontmatter(release: DocumentRelease) -> dict:
    payload = release.payload if isinstance(release.payload, dict) else {}
    frontmatter = payload.get('frontmatter')
    return frontmatter if isinstance(frontmatter, dict) else {}


def describe_release(db: Session, release: DocumentRelease, job: Job, collection: Collection) -> ReleaseChange:
    frontmatter = _frontmatter(release)
    title = frontmatter.get('title') if isinstance(frontmatter.get('title'), str) and frontmatter['title'].strip() else None
    title = (title or job.original_filename).strip()[:500]
    target_markdown = restore_relative_image_urls(release.markdown_snapshot or '', release.id)
    previous = previous_released(db, job)
    texts = [title, job.original_filename]
    tags = frontmatter.get('tags')
    if isinstance(tags, list):
        texts.extend(str(tag) for tag in tags if isinstance(tag, (str, int)))
    texts.extend(db.scalars(
        select(Tag.name).join(job_tags, job_tags.c.tag_id == Tag.id).where(job_tags.c.job_id == job.id)
    ).all())
    if previous is None:
        sections = document_diff.split_sections(target_markdown)
        for section in sections:
            texts.extend(section.heading_path)
            texts.extend(section.lines)
        summary = {'stats': None, 'lines': []}
        change_type = 'new'
    else:
        diff = document_diff.diff_markdown(version_markdown(db, previous, collection), target_markdown)
        texts.extend(document_diff.changed_text(diff))
        summary = {'stats': diff['stats'], 'lines': diff['summary'][:MAX_SUMMARY_LINES]}
        change_type = 'updated'
    return ReleaseChange(
        release=release, job=job, collection=collection, previous_job=previous, change_type=change_type,
        title=title, summary=summary, tokens=set(normalize_terms('\n'.join(texts))),
    )


def _withdrawn(db: Session, job_id: str) -> bool:
    return db.get(KnowledgeWithdrawal, job_id) is not None


def _can_read(db: Session, collection: Collection, user: User | None) -> bool:
    return user is not None and user.is_active and collection_role(db, collection, user) is not None


def process_release(db: Session, release: DocumentRelease, subscriptions: list[TopicSubscription], now: datetime) -> int:
    """Match one release; marks it notified (no commit). Returns the
    number of notifications created."""
    created = 0
    try:
        if aware(release.created_at) < now - timedelta(days=_BACKLOG_DAYS):
            return 0
        job = db.get(Job, release.job_id)
        if job is None or job.benchmark_run_id is not None or _withdrawn(db, job.id):
            return 0
        collection = collection_for_job(db, job)
        if collection is None:
            return 0
        relevant = [sub for sub in subscriptions if sub.collection_id in (None, collection.id)]
        if not relevant:
            return 0
        change: ReleaseChange | None = None
        for subscription in relevant:
            user = db.get(User, subscription.user_id)
            if not _can_read(db, collection, user):
                continue
            keywords = query_terms(subscription.query)
            if subscription.query and not keywords:
                continue
            if change is None:
                change = describe_release(db, release, job, collection)
            if not keyword_matches(keywords, change.tokens):
                continue
            exists = db.scalar(select(ChangeNotification.id).where(
                ChangeNotification.subscription_id == subscription.id, ChangeNotification.job_id == job.id
            ))
            if exists is not None:
                continue
            db.add(ChangeNotification(
                user_id=subscription.user_id,
                subscription_id=subscription.id,
                job_id=job.id,
                previous_job_id=change.previous_job.id if change.previous_job is not None else None,
                collection_id=collection.id,
                change_type=change.change_type,
                title=change.title,
                summary=change.summary,
                matched_terms=keywords,
                created_at=now,
            ))
            created += 1
        return created
    finally:
        release.subscribers_notified_at = now


def process_pending_releases(db: Session, now: datetime | None = None) -> int:
    """The tick's work (see module docstring). Commits per batch; returns
    the number of notifications created."""
    now = now or _now()
    total = 0
    for _ in range(_MAX_BATCHES):
        releases = db.scalars(
            select(DocumentRelease)
            .where(DocumentRelease.subscribers_notified_at.is_(None))
            # Only releases delivered to Weave-Knowledge: the feed must not
            # announce a version the chat cannot answer from yet. A pending
            # or failed release is picked up once its delivery succeeds.
            .where(DocumentRelease.status == 'sent')
            .order_by(DocumentRelease.created_at, DocumentRelease.id)
            .limit(_BATCH)
        ).all()
        if not releases:
            break
        subscriptions = list(db.scalars(select(TopicSubscription).order_by(TopicSubscription.created_at)).all())
        if not subscriptions:
            db.execute(
                update(DocumentRelease)
                .where(DocumentRelease.id.in_([release.id for release in releases]))
                .values(subscribers_notified_at=now)
            )
            db.commit()
            continue
        for release in releases:
            try:
                total += process_release(db, release, subscriptions, now)
            except Exception:  # noqa: BLE001 - one broken release must not stall the feed
                logger.exception('change feed: release %s could not be matched; skipped', release.id)
                db.rollback()
                db.execute(
                    update(DocumentRelease).where(DocumentRelease.id == release.id).values(subscribers_notified_at=now)
                )
            db.commit()
    return total


def readable_collection_ids(db: Session, user: User) -> set[str] | None:
    """Ids of every space `user` may read; None for admins (all)."""
    from app.models.models import UserRole

    if user.role == UserRole.ADMIN:
        return None
    team_roles = user_team_roles(db, user)
    return {
        collection.id
        for collection in db.scalars(select(Collection)).all()
        if collection_role(db, collection, user, team_roles=team_roles) is not None
    }


def search_changes(
    db: Session, user: User, *, q: str | None, collection_id: str | None, days: int, now: datetime | None = None,
) -> tuple[list[ReleaseChange], list[str], bool]:
    """Recent releases in spaces `user` may read whose change matches `q`,
    newest first, at most SEARCH_LIMIT. Returns (changes, keywords,
    incomplete).

    Read access, the time window and withdrawals are filtered in SQL, so
    other spaces' releases never use up the scan. Describing a release
    (snapshot reads + diff) is the expensive part: at most `_SEARCH_SCAN`
    releases are described per request; `incomplete` is True when older
    candidates were left unchecked because of that cap."""
    now = now or _now()
    keywords = query_terms(q)
    readable = readable_collection_ids(db, user)
    if readable is not None and not readable:
        return [], keywords, False
    collection_id_expr = Job.processing_info['settings']['collection_id'].as_string()
    query = (
        select(DocumentRelease)
        .join(Job, Job.id == DocumentRelease.job_id)
        .where(
            DocumentRelease.created_at >= now - timedelta(days=days),
            Job.benchmark_run_id.is_(None),
            ~select(KnowledgeWithdrawal.job_id).where(KnowledgeWithdrawal.job_id == Job.id).exists(),
        )
        .order_by(DocumentRelease.created_at.desc(), DocumentRelease.id)
        .limit(_SEARCH_SCAN + 1)
    )
    if readable is not None:
        query = query.where(collection_id_expr.in_(sorted(readable)))
    if collection_id is not None:
        query = query.where(collection_id_expr == collection_id)
    releases = db.scalars(query).all()
    results: list[ReleaseChange] = []
    described = 0
    for release in releases:
        if described >= _SEARCH_SCAN:
            return results, keywords, True
        job = db.get(Job, release.job_id)
        collection = collection_for_job(db, job) if job is not None else None
        if collection is None or (readable is not None and collection.id not in readable):
            continue
        described += 1
        change = describe_release(db, release, job, collection)
        if keyword_matches(keywords, change.tokens):
            results.append(change)
            if len(results) >= SEARCH_LIMIT:
                break
    return results, keywords, False
