"""Tests for the periodic collection-sync tick in
app/workers/collection_sync_tasks.py: the self-re-enqueue control flow
(sweep-and-reenqueue, stand-down without a lock, keep the chain alive across
a lock-acquire error or a sync failure). Same testing approach as
Weave-Ingest's own tests/test_session_cleanup.py: `_acquire_or_renew` is
monkeypatched directly rather than exercising the real Redis lock, and
celery_app.send_task is captured rather than actually dispatched -- no
Redis/broker needed for any test here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models.models import Collection, Document, DocumentStatus
from app.workers import collection_sync_tasks
from app.workers.celery_app import celery_app
from app.workers.collection_sync_tasks import _run_sync, collection_sync_tick
from tests.conftest import TestingSessionLocal


def _db():
    return TestingSessionLocal()


def _cleanup():
    db = _db()
    try:
        db.query(Collection).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


def _make_document(
    *, status: DocumentStatus, index_attempts: int, updated_at: datetime, embedding_attempts: int = 0,
) -> Document:
    db = _db()
    try:
        document = Document(
            source_job_id=str(uuid.uuid4()),
            content_sha256='a' * 64,
            engine='paddleocr',
            processed_at=updated_at,
            status=status,
            index_attempts=index_attempts,
            embedding_attempts=embedding_attempts,
            updated_at=updated_at,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _around():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture()
def sent(monkeypatch):
    monkeypatch.setattr(collection_sync_tasks, 'SessionLocal', TestingSessionLocal)
    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []))))
    return captured


class _FakeResponse:
    def __init__(self, status_code: int, json_body=None) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


# --- _run_sync ----------------------------------------------------------------------


def test_run_sync_upserts_into_the_shared_test_db(sent, monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    payload = [{'slug': 'handbuch', 'name': 'Handbuch', 'description': None, 'read_teams': []}]
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        count = _run_sync()

    assert count == 1
    db = _db()
    try:
        assert db.get(Collection, 'handbuch') is not None
    finally:
        db.close()


# --- collection_sync_tick control flow ------------------------------------------------


def test_tick_syncs_and_reenqueues_with_the_held_token(sent, monkeypatch):
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    assert len(sent) == 1
    name, args = sent[0]
    assert name == collection_sync_tasks.TICK_TASK_NAME
    assert args == ['token-xyz']


def test_tick_stands_down_without_reenqueuing_when_lock_unavailable(sent, monkeypatch):
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: None)

    collection_sync_tick(None)

    assert sent == []


def test_tick_still_reenqueues_next_tick_when_lock_acquire_raises(sent, monkeypatch):
    """A Redis error out of _acquire_or_renew (e.g. a connection blip) must
    not kill the self-re-enqueuing chain: the next tick is still sent, with
    the SAME token this execution was called with."""

    def _boom(_lock_token):
        raise Exception('redis blip')

    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', _boom)

    collection_sync_tick('token-abc')

    assert len(sent) == 1
    name, args = sent[0]
    assert name == collection_sync_tasks.TICK_TASK_NAME
    assert args == ['token-abc']


def test_tick_still_reenqueues_when_the_sync_itself_raises(sent, monkeypatch):
    """A failure inside the sync (e.g. Weave-Ingest unreachable) must not
    kill the chain either -- the tick logs and re-enqueues with the token
    it just acquired/renewed, same discipline as the lock-acquire-raises
    case."""
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')

    def _boom():
        raise Exception('weave-ingest unreachable')

    monkeypatch.setattr(collection_sync_tasks, '_run_sync', _boom)

    collection_sync_tick(None)

    assert len(sent) == 1
    name, args = sent[0]
    assert name == collection_sync_tasks.TICK_TASK_NAME
    assert args == ['token-xyz']


def test_tick_task_name_is_namespaced_under_weave_knowledge():
    """Must fall under the 'weave.knowledge.*' task_routes wildcard (see
    app/workers/celery_app.py) so it lands on this service's own queue,
    same discipline as INDEX_TASK_NAME."""
    assert collection_sync_tasks.TICK_TASK_NAME == 'weave.knowledge.collection_sync_tick'


# --- SH-01: renewal failure reclaims an orphaned lock instead of stranding the chain --------


def test_tick_reacquires_and_reenqueues_when_lock_was_lost_and_free(sent, monkeypatch):
    """A renewal failure alone must not end the chain: if the lock key is
    simply gone (expired, Redis restart) rather than held by another chain,
    a fresh NX acquire must succeed and the chain continues with the new
    token."""
    monkeypatch.setattr(collection_sync_tasks, '_renew_sync_lock', lambda token: False)
    monkeypatch.setattr(collection_sync_tasks, '_try_acquire_sync_lock', lambda: 'new-token')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick('stale-token')

    assert len(sent) == 1
    name, args = sent[0]
    assert name == collection_sync_tasks.TICK_TASK_NAME
    assert args == ['new-token']


def test_tick_stands_down_when_lock_lost_to_another_holder(sent, monkeypatch):
    """A renewal failure whose fresh acquire ALSO fails means another chain
    genuinely holds the lock -- must stand down, no re-enqueue."""
    monkeypatch.setattr(collection_sync_tasks, '_renew_sync_lock', lambda token: False)
    monkeypatch.setattr(collection_sync_tasks, '_try_acquire_sync_lock', lambda: None)

    collection_sync_tick('stale-token')

    assert sent == []


def test_tick_retries_reenqueue_on_transient_send_failure(sent, monkeypatch):
    """A transient send_task failure while re-enqueuing the next tick must
    be retried (bounded), not left to kill the chain."""
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    monkeypatch.setattr(collection_sync_tasks.time, 'sleep', lambda seconds: None)

    calls = {'n': 0}

    def flaky_send(name, args=None, **kw):
        calls['n'] += 1
        if calls['n'] < 2:
            raise Exception('broker hiccup')
        sent.append((name, list(args or [])))

    monkeypatch.setattr(celery_app, 'send_task', flaky_send)

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    assert calls['n'] == 2
    assert len(sent) == 1
    name, args = sent[0]
    assert name == collection_sync_tasks.TICK_TASK_NAME
    assert args == ['token-xyz']


def test_tick_reenqueue_gives_up_after_max_attempts_without_raising(sent, monkeypatch):
    """After exhausting the bounded retries, the tick must log and return
    normally rather than raise (a raised exception here would crash-loop
    the Celery task redelivery instead of just ending the chain)."""
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    monkeypatch.setattr(collection_sync_tasks.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: (_ for _ in ()).throw(Exception('down')))

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)  # must not raise

    assert sent == []


# --- SH-03: periodic re-drive of stalled index retries ------------------------------------


def test_redrive_resends_a_due_retry_eligible_document(sent, monkeypatch):
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=1000)
    stalled = _make_document(status=DocumentStatus.PENDING, index_attempts=1, updated_at=old_enough)
    _make_document(status=DocumentStatus.PENDING, index_attempts=1, updated_at=datetime.now(timezone.utc))  # too recent
    _make_document(status=DocumentStatus.PENDING, index_attempts=0, updated_at=old_enough)  # never tried yet
    _make_document(status=DocumentStatus.FAILED, index_attempts=5, updated_at=old_enough)  # already given up

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [args for name, args in sent if name == 'weave.knowledge.index_document']
    assert index_sends == [[str(stalled.id)]]


def test_redrive_skips_document_already_at_max_attempts(sent, monkeypatch):
    from app.workers import tasks as tasks_module

    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    _make_document(status=DocumentStatus.PENDING, index_attempts=tasks_module._MAX_ATTEMPTS, updated_at=old_enough)

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [args for name, args in sent if name == 'weave.knowledge.index_document']
    assert index_sends == []


# --- Incident 2026-09-22: restart recovery re-drives an embedding-stalled retry --


def test_redrive_covers_a_stalled_embedding_retry_after_a_restart(sent, monkeypatch):
    """A document stuck retrying the EMBED step (not just the markdown-fetch
    step) must also be re-driven -- e.g. after the knowledge worker pod
    itself was OOMKilled/restarted mid-backoff, this is what resumes it
    without waiting for the original retry's own (possibly lost) send_task."""
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    stalled = _make_document(
        status=DocumentStatus.PENDING, index_attempts=0, embedding_attempts=1, updated_at=old_enough,
    )
    # Too recent -- its own scheduled retry may still be in flight.
    _make_document(
        status=DocumentStatus.PENDING, index_attempts=0, embedding_attempts=1, updated_at=datetime.now(timezone.utc),
    )

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [args for name, args in sent if name == 'weave.knowledge.index_document']
    assert index_sends == [[str(stalled.id)]]


def test_redrive_covers_a_stalled_reindex_embedding_retry_with_reindex_flag(monkeypatch):
    """A stalled REINDEX request (status stays INDEXED while
    embedding_attempts>0 -- see app/workers/tasks.py's _run_pipeline reindex-
    aware skip check) must be redriven WITH reindex=True, or the redriven
    task would just no-op against the already-INDEXED document."""
    monkeypatch.setattr(collection_sync_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    captured: list[tuple[str, list, dict]] = []
    monkeypatch.setattr(
        celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []), kw)),
    )

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    stalled = _make_document(
        status=DocumentStatus.INDEXED, index_attempts=0, embedding_attempts=1, updated_at=old_enough,
    )

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [(args, kw) for name, args, kw in captured if name == 'weave.knowledge.index_document']
    assert index_sends == [([str(stalled.id)], {'kwargs': {'reindex': True}})]


def test_redrive_covers_a_stalled_reindex_fetch_retry_with_reindex_flag(monkeypatch):
    """A stalled REINDEX request whose failure is at the markdown-FETCH step
    (not the embed step) also stays at status=INDEXED with index_attempts>0
    -- reindex=True skips _run_pipeline's early-return, so a
    TransientFetchError there increments index_attempts, not
    embedding_attempts, while status never leaves INDEXED. None of the
    other three passes match this (status, counter) combination, so
    without this pass such a stalled reindex is invisible to the redrive
    and, if its own retry send_task is also lost (the pod-restart scenario
    the whole redrive mechanism exists for), gets stuck forever -- never
    retried, never marked FAILED."""
    monkeypatch.setattr(collection_sync_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    captured: list[tuple[str, list, dict]] = []
    monkeypatch.setattr(
        celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []), kw)),
    )

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    stalled = _make_document(
        status=DocumentStatus.INDEXED, index_attempts=1, embedding_attempts=0, updated_at=old_enough,
    )

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [(args, kw) for name, args, kw in captured if name == 'weave.knowledge.index_document']
    assert index_sends == [([str(stalled.id)], {'kwargs': {'reindex': True}})]


def test_redrive_ignores_a_document_that_indexed_successfully_after_a_transient_retry(monkeypatch):
    """A document that survived a transient embedding retry and then
    indexed successfully has embedding_attempts reset to 0 by
    app/workers/tasks.py's _run_pipeline (see that reset's own comment).
    Pass 3 of _redrive_stalled_index_retries (status=INDEXED,
    counter=embedding_attempts) must NOT mistake this healthy, settled
    document for a stalled reindex retry -- otherwise collection_sync_tick
    would spuriously and repeatedly fire index_document.delay(reindex=True)
    for it forever, reproducing the uncontrolled embedding load the
    2026-09-22 incident fix exists to prevent."""
    monkeypatch.setattr(collection_sync_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(collection_sync_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')

    captured: list[tuple[str, list, dict]] = []
    monkeypatch.setattr(
        celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []), kw)),
    )

    old_enough = datetime.now(timezone.utc) - timedelta(seconds=10_000)
    # embedding_attempts=0 is exactly the state a document is left in after
    # a successful embed, even if it needed a transient retry along the way.
    _make_document(status=DocumentStatus.INDEXED, index_attempts=0, embedding_attempts=0, updated_at=old_enough)

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        collection_sync_tick(None)

    index_sends = [(args, kw) for name, args, kw in captured if name == 'weave.knowledge.index_document']
    assert index_sends == []
