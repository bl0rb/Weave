"""ADR-0001 queue topology: this service's tasks must land on its own named
queue, never Celery's generic default -- see
app/workers/celery_app.py and Weave-Ingest's docs/adr/0001-queue-topologie.md.
"""

from app.workers.celery_app import celery_app
from app.workers.collection_sync_tasks import collection_sync_tick
from app.workers.tasks import index_document


def test_task_default_queue_is_the_service_specific_queue():
    assert celery_app.conf.task_default_queue == 'weave.knowledge.index'


def test_task_routes_send_weave_knowledge_tasks_to_the_named_queue():
    routes = celery_app.conf.task_routes
    assert routes['weave.knowledge.*']['queue'] == 'weave.knowledge.index'


def test_acks_late_and_prefetch_one_are_set():
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1


def test_index_document_task_is_registered_under_the_expected_name():
    assert index_document.name == 'weave.knowledge.index_document'
    assert 'weave.knowledge.index_document' in celery_app.tasks


def test_collection_sync_tick_task_is_registered_under_the_expected_name():
    assert collection_sync_tick.name == 'weave.knowledge.collection_sync_tick'
    assert 'weave.knowledge.collection_sync_tick' in celery_app.tasks


def test_collection_sync_tick_falls_under_the_weave_knowledge_queue_wildcard():
    """No dedicated task_routes entry needed for the periodic tick -- its
    name shares the SAME 'weave.knowledge.' prefix the existing
    'weave.knowledge.*' route (asserted above) already matches, so it lands
    on this service's one named queue without any routing change."""
    assert collection_sync_tick.name.startswith('weave.knowledge.')
