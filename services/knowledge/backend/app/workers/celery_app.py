from celery import Celery

from app.core.config import settings

celery_app = Celery('weave_knowledge_worker', broker=settings.redis_url, backend=settings.redis_url)

# ADR-0001 (Weave-Ingest docs/adr/0001-queue-topologie.md): every Weave
# service gets its own named Celery queue -- even though every service
# shares one Redis instance today -- so worker pools scale independently and
# never cross-consume another service's tasks. task_routes mirrors the
# ADR's own example (a wildcard on the service's task-name prefix); with
# only one task family so far this is equivalent to task_default_queue
# alone, but it's the shape every future weave.knowledge.* task should fall
# into without needing its own route entry.
_QUEUE_NAME = 'weave.knowledge.index'

celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    # Same acks-late/prefetch discipline as Weave-Ingest's celery_app.py: a
    # worker that dies mid-task must not silently drop it (acks_late +
    # reject_on_worker_lost -> redelivered to another worker), and prefetch=1
    # keeps one in-flight task per worker slot instead of hoarding a batch.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_default_queue=_QUEUE_NAME,
    task_routes={
        'weave.knowledge.*': {'queue': _QUEUE_NAME},
    },
)
