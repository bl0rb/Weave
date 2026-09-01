"""app/core/ratelimit.py:FixedWindowRateLimiter and the enforce_rate_limit
dependency, exercised through GET /v1/bots (any authenticated+rate-limited
route works; bots is the simplest one needing no request body or seeded
conversation of its own) -- with the Weave-Runtime call itself stubbed out
so rate-limiting is the only thing under test.

Every test builds its own `FixedWindowRateLimiter` with an injected
`_FakeClock` and monkeypatches it in as `app.core.ratelimit.rate_limiter`
(reverted automatically by pytest's `monkeypatch` at teardown) -- this
advances "time" deterministically, with no real `time.sleep`.
"""

import pytest

from app.core import ratelimit
from app.core.ratelimit import FixedWindowRateLimiter
from tests.conftest import auth_headers, client, make_user_with_token


class _FakeClock:
    """A monotonic-clock stand-in a test can advance on demand."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture(autouse=True)
def _stub_runtime_bots(monkeypatch):
    # Patched where it's looked up (app.api.bots), not where it's defined
    # (app.services.runtime_client) -- `from ... import list_bots` already
    # bound the name into app.api.bots's own module namespace by the time
    # this fixture runs, so patching the origin module wouldn't be seen.
    monkeypatch.setattr('app.api.bots.list_bots', lambda: [])


def test_requests_within_limit_all_succeed(db_session, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=3, clock=clock))

    _, raw_token = make_user_with_token(db_session, username='bob-limit-ok')
    headers = auth_headers(raw_token)

    for _ in range(3):
        assert client.get('/v1/bots', headers=headers).status_code == 200


def test_exceeding_the_window_returns_429_with_retry_after(db_session, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=2, clock=clock))

    _, raw_token = make_user_with_token(db_session, username='bob-limit-exceed')
    headers = auth_headers(raw_token)

    assert client.get('/v1/bots', headers=headers).status_code == 200
    assert client.get('/v1/bots', headers=headers).status_code == 200

    response = client.get('/v1/bots', headers=headers)
    assert response.status_code == 429
    assert 'Retry-After' in response.headers
    assert int(response.headers['Retry-After']) > 0


def test_new_window_resets_the_budget(db_session, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1, clock=clock, window_seconds=60))

    _, raw_token = make_user_with_token(db_session, username='bob-limit-window')
    headers = auth_headers(raw_token)

    assert client.get('/v1/bots', headers=headers).status_code == 200
    assert client.get('/v1/bots', headers=headers).status_code == 429

    clock.advance(61)  # past the 60s window
    assert client.get('/v1/bots', headers=headers).status_code == 200


def test_budget_is_tracked_per_user_not_globally(db_session, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ratelimit, 'rate_limiter', FixedWindowRateLimiter(limit=1, clock=clock))

    _, token_a = make_user_with_token(db_session, username='carol')
    _, token_b = make_user_with_token(db_session, username='dave')

    assert client.get('/v1/bots', headers=auth_headers(token_a)).status_code == 200
    # carol's own budget is now exhausted for this window...
    assert client.get('/v1/bots', headers=auth_headers(token_a)).status_code == 429
    # ...but dave has his own, untouched budget.
    assert client.get('/v1/bots', headers=auth_headers(token_b)).status_code == 200
