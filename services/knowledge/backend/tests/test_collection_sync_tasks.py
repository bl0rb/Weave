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

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models.models import Collection
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
        db.commit()
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
