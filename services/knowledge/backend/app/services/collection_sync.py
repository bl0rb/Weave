"""Pulls Weave-Ingest's authoritative collection registry into this
service's own read-only mirror (see app/models/models.py's `Collection`
docstring for why the mirror carries no FK and is allowed to lag).

Called from two places:
- app/workers/collection_sync_tasks.py's `collection_sync_tick` -- the
  periodic self-re-enqueuing sync (no Celery Beat in this deployment).
- app/api/events.py -- a one-off lazy reload when a `document.processed`
  event names a `collection` slug this mirror doesn't know yet (a brand-new
  collection can outrace the next scheduled tick).

Kept DB-session-argument-taking (not opening its own SessionLocal) so both
callers control the transaction: the periodic tick uses its own short-lived
session, while the lazy-reload path can be handed a fresh one too without
this module needing to care which.

SSRF note: unlike app/services/ingest_client.py's fetch_markdown, the URL
built here (`_registry_url()`) never incorporates ANY value from an inbound
event or other foreign data -- it is the fixed `/api/v1/collections/registry`
path appended to this service's own configured `weave_ingest_base_url`, the
same "build locally from settings, never from foreign data" discipline that
module's docstring describes, just with no attacker-influenced path segment
to begin with (ingest_client needs `_validate_markdown_url` because a job_id
interpolates into its path; there is nothing here for a malicious value to
interpolate into).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Collection

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_REGISTRY_PATH = '/api/v1/collections/registry'


def _registry_url() -> str:
    return f"{settings.weave_ingest_base_url.rstrip('/')}{_REGISTRY_PATH}"


class CollectionSyncError(Exception):
    """Raised for any failure fetching or parsing the registry response --
    a transport error, a non-2xx status, or a response shaped unlike the
    contract's `{'items': [{slug, name, description, read_teams}, ...]}`
    (see `_extract_registry_items` below for why a bare list is still
    tolerated defensively).

    Deliberately a single flat exception type (unlike ingest_client's
    Transient/Permanent split): callers here never retry synchronously --
    the periodic tick just logs and lets the next tick try again on its own
    schedule, and the lazy-reload path in app/api/events.py must swallow
    this and index the document anyway (contract point 3) -- neither caller
    branches on *why* the sync failed.
    """


def _extract_registry_items(payload: object, url: str) -> list:
    """Pulls the list of registry entries out of Weave-Ingest's response
    body.

    The canonical, contractual shape is the object envelope
    `{'items': [...]}` -- this matches Weave-Ingest's actual
    `CollectionRegistryResponse` (see its app/schemas/jobs.py), which every
    other list-returning endpoint over there (`CollectionListResponse`,
    `JobListResponse`, ...) uses too. A bare JSON list is still accepted
    defensively (some older/hypothetical caller or a hand-rolled test
    fixture might send one), but it is NOT the contract -- new code should
    only ever need to handle the object form.
    """
    if isinstance(payload, dict):
        items = payload.get('items')
        if isinstance(items, list):
            return items
        raise CollectionSyncError(
            f"{url!r} returned an object without a list 'items' field, expected {{'items': [...]}}"
        )
    if isinstance(payload, list):
        return payload
    raise CollectionSyncError(
        f"{url!r} returned {type(payload).__name__}, expected a JSON object shaped {{'items': [...]}}"
    )


def sync_collections(db: Session, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> int:
    """Fetch Weave-Ingest's collection registry and upsert this service's
    own `collections` mirror to match it exactly -- including deleting a
    locally-mirrored row whose slug is no longer present upstream (this is
    a full mirror, not an additive cache: a collection deleted at
    Weave-Ingest must stop being resolvable here too).

    Returns the number of collections in the upstream response (post-sync
    row count of `collections`). Raises CollectionSyncError on any failure;
    commits on success, never partially (a raised exception leaves `db`
    without an uncommitted sync in progress -- callers still own rollback
    if they wrap this in a larger transaction).
    """
    url = _registry_url()
    headers = {'Authorization': f'Bearer {settings.weave_ingest_api_token}'}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise CollectionSyncError(f'fetching {url!r} failed: {exc}') from exc

    if response.status_code != 200:
        raise CollectionSyncError(f'fetching {url!r} returned HTTP {response.status_code}')

    try:
        payload = response.json()
    except ValueError as exc:
        raise CollectionSyncError(f'{url!r} returned a non-JSON body: {exc}') from exc
    items = _extract_registry_items(payload, url)

    now = datetime.now(timezone.utc)
    seen_slugs: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict) or 'slug' not in entry:
            raise CollectionSyncError(f'{url!r} returned a malformed entry: {entry!r}')
        slug = entry['slug']
        seen_slugs.add(slug)
        collection = db.get(Collection, slug)
        if collection is None:
            collection = Collection(slug=slug)
            db.add(collection)
        collection.name = entry.get('name', slug)
        collection.description = entry.get('description')
        collection.read_teams = entry.get('read_teams') or []
        collection.synced_at = now

    # Full-mirror semantics: drop any local row the upstream response no
    # longer lists (a collection deleted at Weave-Ingest since the last
    # sync). Not gated on seen_slugs being non-empty vs. using
    # `.not_in(seen_slugs)` unconditionally -- an empty IN/NOT IN list is
    # dialect-fragile, so an empty registry response just deletes every
    # local row via the plain unfiltered branch instead.
    if seen_slugs:
        stale = db.execute(select(Collection).where(Collection.slug.not_in(seen_slugs))).scalars().all()
    else:
        stale = db.execute(select(Collection)).scalars().all()
    for row in stale:
        db.delete(row)

    db.commit()
    logger.info('collection sync: upserted %d collection(s), removed %d stale row(s)', len(seen_slugs), len(stale))
    return len(seen_slugs)
