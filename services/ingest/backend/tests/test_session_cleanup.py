"""AUFGABE 2/Punkt 1: the periodic session-cleanup sweep in
app/workers/session_cleanup_tasks.py.

Same testing approach as tests/test_confluence_refresh.py (SessionLocal
monkeypatched to the shared sqlite test DB; celery_app.send_task captured
rather than actually dispatched; the tick's real Redis leadership lock is
not exercised here -- _acquire_or_renew is monkeypatched directly for the
tick-control-flow tests, same as that file's
test_tick_still_reenqueues_next_tick_when_lock_acquire_raises).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import Session as SessionModel
from app.workers import session_cleanup_tasks
from app.workers.celery_app import celery_app
from app.workers.session_cleanup_tasks import delete_expired_sessions, session_cleanup_tick
from tests.conftest import TestingSessionLocal, create_test_user


def _db():
    return TestingSessionLocal()


def _make_owner():
    # Unique per call (the sqlite test DB persists across every test in the
    # session, not just this file), mirroring test_confluence_refresh._make_owner.
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'session-owner-{suffix}', email=f'session-owner-{suffix}@example.com')


def _make_session(owner_id: str, *, expires_at: datetime) -> str:
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        session = SessionModel(
            token_hash=uuid.uuid4().hex,
            user_id=owner_id,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id
    finally:
        db.close()


def _session_exists(session_id: str) -> bool:
    db = _db()
    try:
        return db.get(SessionModel, session_id) is not None
    finally:
        db.close()


@pytest.fixture()
def sent(monkeypatch):
    monkeypatch.setattr(session_cleanup_tasks, 'SessionLocal', TestingSessionLocal)
    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []))))
    return captured


# --- delete_expired_sessions -------------------------------------------------

def test_delete_expired_sessions_removes_only_expired_rows(sent) -> None:
    owner = _make_owner()
    now = datetime.now(timezone.utc)

    expired_id = _make_session(owner.id, expires_at=now - timedelta(days=1))
    active_id = _make_session(owner.id, expires_at=now + timedelta(days=1))

    deleted = delete_expired_sessions()

    assert deleted == 1
    assert not _session_exists(expired_id)
    assert _session_exists(active_id)


def test_delete_expired_sessions_is_a_noop_when_nothing_is_expired(sent) -> None:
    owner = _make_owner()
    now = datetime.now(timezone.utc)
    active_id = _make_session(owner.id, expires_at=now + timedelta(days=1))

    deleted = delete_expired_sessions()

    assert deleted == 0
    assert _session_exists(active_id)


def test_delete_expired_sessions_counts_every_expired_row(sent) -> None:
    owner = _make_owner()
    now = datetime.now(timezone.utc)
    for _ in range(3):
        _make_session(owner.id, expires_at=now - timedelta(seconds=1))

    deleted = delete_expired_sessions()

    assert deleted == 3


# --- session_cleanup_tick control flow --------------------------------------

def test_tick_sweeps_and_reenqueues_with_the_held_token(sent, monkeypatch) -> None:
    monkeypatch.setattr(session_cleanup_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    owner = _make_owner()
    _make_session(owner.id, expires_at=datetime.now(timezone.utc) - timedelta(days=1))

    session_cleanup_tick(None)

    assert len(sent) == 1
    name, args = sent[0]
    assert name == 'session_cleanup_tick'
    assert args == ['token-xyz']


def test_tick_stands_down_without_reenqueuing_when_lock_unavailable(sent, monkeypatch) -> None:
    monkeypatch.setattr(session_cleanup_tasks, '_acquire_or_renew', lambda lock_token: None)

    session_cleanup_tick(None)

    assert sent == []


def test_tick_still_reenqueues_next_tick_when_lock_acquire_raises(sent, monkeypatch) -> None:
    """A Redis error out of _acquire_or_renew (e.g. a connection blip) must
    not kill the self-re-enqueuing chain: the next tick is still sent, with
    the SAME token this execution was called with -- mirrors
    test_confluence_refresh.py's identically-named test for the confluence-
    refresh chain."""

    def _boom(_lock_token):
        raise Exception('redis blip')

    monkeypatch.setattr(session_cleanup_tasks, '_acquire_or_renew', _boom)

    session_cleanup_tick('token-abc')

    assert len(sent) == 1
    name, args = sent[0]
    assert name == 'session_cleanup_tick'
    assert args == ['token-abc']


def test_tick_still_reenqueues_when_the_sweep_itself_raises(sent, monkeypatch) -> None:
    """A failure inside the sweep (e.g. a DB blip) must not kill the chain
    either -- the tick logs and re-enqueues with the token it just
    acquired/renewed, same discipline as the lock-acquire-raises case."""
    monkeypatch.setattr(session_cleanup_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')

    def _boom():
        raise Exception('db blip')

    monkeypatch.setattr(session_cleanup_tasks, 'delete_expired_sessions', _boom)

    session_cleanup_tick(None)

    assert len(sent) == 1
    name, args = sent[0]
    assert name == 'session_cleanup_tick'
    assert args == ['token-xyz']
