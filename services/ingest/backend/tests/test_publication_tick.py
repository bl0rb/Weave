"""SH-01: publication_tick's leadership-lock recovery and self-re-enqueue
robustness (app/workers/publication_tasks.py).

publication_tasks._acquire_or_renew talks to Redis directly (no separate
_try_acquire_lock/_renew_lock split like refresh_tasks.py /
session_cleanup_tasks.py), so exercising its lost-token fallback for real
needs a tiny fake Redis client -- same GET/SET-NX-EX/EXPIRE idiom as
tests/conftest.py's _FakeRedis, extended with GET since this lock reads it
directly (the rate limiter that fake backs never does).
"""

from app.workers import publication_tasks
from app.workers.celery_app import celery_app
from app.workers.publication_tasks import _acquire_or_renew, publication_tick


class _FakeLockRedis:
    def __init__(self, value: str | None = None) -> None:
        self._value = value

    def get(self, key):
        return self._value

    def set(self, key, value, nx: bool = False, ex=None):
        if nx and self._value is not None:
            return None
        self._value = value
        return True

    def expire(self, key, seconds):
        return True


def test_acquire_or_renew_reclaims_a_lost_lock_when_nobody_else_holds_it(monkeypatch) -> None:
    """SH-01: the carried token is no longer the current holder (expired,
    Redis restart/eviction) and the key is free -- a fresh NX acquisition
    must succeed, returning a new token rather than None."""
    fake = _FakeLockRedis(value=None)
    monkeypatch.setattr(publication_tasks.Redis, 'from_url', lambda *a, **kw: fake)

    token = _acquire_or_renew('stale-token')

    assert token is not None
    assert token != 'stale-token'
    assert fake.get('worker:portal-publication:tick-lock') == token


def test_acquire_or_renew_stands_down_when_lock_is_lost_to_another_holder(monkeypatch) -> None:
    """SH-01: the carried token is lost AND another replica's chain already
    holds the lock live -- must stand down (None), not steal it."""
    fake = _FakeLockRedis(value='other-token')
    monkeypatch.setattr(publication_tasks.Redis, 'from_url', lambda *a, **kw: fake)

    token = _acquire_or_renew('stale-token')

    assert token is None
    assert fake.get('worker:portal-publication:tick-lock') == 'other-token'


def test_tick_reclaims_a_lost_lock_and_reenqueues_with_a_new_token(monkeypatch) -> None:
    fake = _FakeLockRedis(value=None)
    monkeypatch.setattr(publication_tasks.Redis, 'from_url', lambda *a, **kw: fake)
    reconciled: list[bool] = []
    monkeypatch.setattr(publication_tasks, 'reconcile_due_releases', lambda: reconciled.append(True))
    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []))))

    publication_tick('stale-token')

    assert reconciled == [True]
    assert len(captured) == 1
    name, args = captured[0]
    assert name == 'publication_tick'
    assert args == [fake.get('worker:portal-publication:tick-lock')]
    assert args != ['stale-token']


def test_tick_stands_down_without_reconciling_when_lock_is_lost_to_another_holder(monkeypatch) -> None:
    fake = _FakeLockRedis(value='other-token')
    monkeypatch.setattr(publication_tasks.Redis, 'from_url', lambda *a, **kw: fake)
    reconciled: list[bool] = []
    monkeypatch.setattr(publication_tasks, 'reconcile_due_releases', lambda: reconciled.append(True))
    captured: list[tuple[str, list]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []))))

    publication_tick('stale-token')

    assert reconciled == []
    assert captured == []


def test_tick_reenqueue_retries_a_transient_send_task_failure_and_succeeds(monkeypatch) -> None:
    """SH-01: the self-re-enqueue must survive a transient broker error
    instead of ending the chain on the first failure."""
    monkeypatch.setattr(publication_tasks, '_acquire_or_renew', lambda lock_token: 'token-xyz')
    monkeypatch.setattr(publication_tasks, 'reconcile_due_releases', lambda: None)
    monkeypatch.setattr(publication_tasks.time, 'sleep', lambda seconds: None)

    attempts: list[int] = []

    def _flaky_send_task(name, args=None, **kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise Exception('transient broker error')

    monkeypatch.setattr(celery_app, 'send_task', _flaky_send_task)

    publication_tick(None)

    assert len(attempts) == 3
