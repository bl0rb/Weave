"""Celery entry point of the admin re-ingest (app/services/reingest.py).

`reingest_advance` performs one lease-guarded step of a run. It is sent by
the API when a run starts, by process_job when a re-ingested job ends and by
import_tasks when a forced import run ends; the publication tick advances
active runs as well, so a lost message only delays a run. A step that finds
the lease taken retries shortly (bounded), so the nudge of a job that ended
during another step is not lost until the next tick.
"""

from __future__ import annotations

import logging

from app.services import reingest
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_BUSY_RETRIES = 3
_BUSY_RETRY_SECONDS = 5


@celery_app.task(name=reingest.ADVANCE_TASK_NAME, acks_late=True, reject_on_worker_lost=True)
def reingest_advance(run_id: str, attempt: int = 0) -> str:
    outcome = reingest.advance_run(run_id)
    if outcome == 'busy' and attempt < _BUSY_RETRIES:
        try:
            celery_app.send_task(reingest.ADVANCE_TASK_NAME, args=[run_id, attempt + 1], countdown=_BUSY_RETRY_SECONDS)
        except Exception:
            logger.exception('could not re-enqueue re-ingest step for run %s', run_id)
    return outcome
