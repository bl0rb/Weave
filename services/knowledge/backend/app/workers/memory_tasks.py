"""Knowledge memory build task (ADR-0009, app/services/memory.py).

Enqueued only by the periodic collection_sync_tick through
`schedule_memory_builds()`, one run per collection whose memory is due --
the debounce state lives in the database, so no trigger is lost to a
restart and a bulk import still costs a single run per collection.
"""

from __future__ import annotations

import logging

from app.core.db import SessionLocal
from app.services import memory
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

BUILD_TASK_NAME = 'weave.knowledge.build_memory'


@celery_app.task(name=BUILD_TASK_NAME, acks_late=True, reject_on_worker_lost=True)
def build_memory(collection_slug: str) -> None:
    db = SessionLocal()
    try:
        memory.build_collection_memory(db, collection_slug)
    except Exception:  # noqa: BLE001 - a failed build is claimable again later
        logger.exception('knowledge memory: build for collection %s failed', collection_slug)
    finally:
        db.close()


def schedule_memory_builds() -> int:
    """Enqueue a build for every due collection; returns how many. A send
    that never reaches the broker leaves the claim to go stale, after which
    the collection is due again."""
    db = SessionLocal()
    try:
        slugs = memory.claim_due_collections(db)
    finally:
        db.close()
    for slug in slugs:
        celery_app.send_task(BUILD_TASK_NAME, args=[slug])
    return len(slugs)
