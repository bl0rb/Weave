"""Finds Knowledge documents nothing in Ingest stands behind any more and
withdraws them through the existing KnowledgeWithdrawal outbox (ADR 0008).

Deleting a job that reached Knowledge has queued a withdrawal only since
the permission fix, and deleted spaces leave a slug tombstone only since
then. Documents of jobs deleted before that -- legacy and pre-release jobs,
jobs of deleted spaces -- are still indexed. A document counts as orphaned
when

- `job_missing`: its job no longer exists here,
- `collection_deleted`: its space slug is tombstoned,
- `collection_unknown`: its space slug names no space here at all, or
- `withdrawn`: its job has a withdrawal on record, yet it is still stored
  (still in delivery, or Knowledge restored from an older state).

Idempotent: an orphan whose withdrawal is still pending is only counted, a
delivered one is re-queued, everything else gets a new outbox row. The
publication tick delivers them; Knowledge treats an unknown job as a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Collection, CollectionSlugTombstone, Job, KnowledgeWithdrawal

PAGE_SIZE = 500
_TIMEOUT_SECONDS = 30.0


class KnowledgeInventoryError(Exception):
    """Knowledge's document inventory could not be read completely."""


class _InventoryItem(BaseModel):
    job_id: str
    collection_slug: str | None = None
    status: str


class _InventoryPage(BaseModel):
    items: list[_InventoryItem]
    next_after: str | None = None


@dataclass(frozen=True)
class Orphan:
    job_id: str
    collection_slug: str | None
    status: str
    reason: str
    withdrawal: str  # 'none', 'pending' or 'sent' -- state before any apply


@dataclass
class OrphanScan:
    scanned: int = 0
    orphans: list[Orphan] = field(default_factory=list)


def _orphan_reason(
    job_exists: bool, slug: str | None, live: set[str], tombstoned: set[str], withdrawal: str,
) -> str | None:
    if not job_exists:
        return 'job_missing'
    if slug and slug in tombstoned:
        return 'collection_deleted'
    if slug and slug not in live:
        return 'collection_unknown'
    if withdrawal != 'none':
        return 'withdrawn'
    return None


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)


def _fetch_pages(client: httpx.Client):
    url = f'{settings.portal_knowledge_base_url.rstrip("/")}/api/v1/internal/documents'
    headers = {'X-Weave-Reindex-Token': settings.portal_knowledge_webhook_secret}
    after = ''
    while True:
        try:
            response = client.get(url, params={'after': after, 'limit': PAGE_SIZE}, headers=headers)
            response.raise_for_status()
            page = _InventoryPage.model_validate(response.json())
        except (httpx.HTTPError, httpx.InvalidURL, ValidationError, ValueError) as exc:
            raise KnowledgeInventoryError('Knowledge document inventory could not be read') from exc
        yield page.items
        if page.next_after is None:
            return
        if page.next_after <= after:
            raise KnowledgeInventoryError('Knowledge document inventory did not advance')
        after = page.next_after


def find_orphans(db: Session) -> OrphanScan:
    """Reads the whole inventory before anything is withdrawn: a partial
    read raises KnowledgeInventoryError instead of returning a partial scan."""
    live = set(db.scalars(select(Collection.slug)).all())
    tombstoned = set(db.scalars(select(CollectionSlugTombstone.slug)).all())
    scan = OrphanScan()
    with _client() as client:
        for items in _fetch_pages(client):
            scan.scanned += len(items)
            job_ids = [item.job_id for item in items]
            if not job_ids:
                continue
            existing = set(db.scalars(select(Job.id).where(Job.id.in_(job_ids))).all())
            withdrawals = dict(db.execute(
                select(KnowledgeWithdrawal.job_id, KnowledgeWithdrawal.status)
                .where(KnowledgeWithdrawal.job_id.in_(job_ids))
            ).all())
            for item in items:
                withdrawal = withdrawals.get(item.job_id, 'none')
                reason = _orphan_reason(item.job_id in existing, item.collection_slug, live, tombstoned, withdrawal)
                if reason is not None:
                    scan.orphans.append(Orphan(item.job_id, item.collection_slug, item.status, reason, withdrawal))
    return scan


def queue_withdrawals(db: Session, scan: OrphanScan) -> int:
    """Queues (or re-queues) a withdrawal for every orphan whose withdrawal
    is not already pending. Returns how many; the caller commits."""
    queued = 0
    for start in range(0, len(scan.orphans), PAGE_SIZE):
        chunk = scan.orphans[start:start + PAGE_SIZE]
        entries = {
            entry.job_id: entry
            for entry in db.scalars(
                select(KnowledgeWithdrawal).where(KnowledgeWithdrawal.job_id.in_([o.job_id for o in chunk]))
            ).all()
        }
        for orphan in chunk:
            entry = entries.get(orphan.job_id)
            if entry is None:
                db.add(KnowledgeWithdrawal(job_id=orphan.job_id))
            elif entry.status == 'pending':
                continue
            else:
                entry.status = 'pending'
                entry.error_message = None
                entry.next_attempt_at = None
            queued += 1
    return queued
