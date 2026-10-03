"""Fixed-window rate limiting, keyed per authenticated user.

The counters live in Weave-API's own database (`rate_limit_windows`, see
DatabaseRateLimiter), so every replica charges the same per-user budget --
an in-process counter would hand out one full budget per replica. One
short upsert per rate-limited request; this service has no Redis.
FixedWindowRateLimiter keeps the same logic in process memory for tests
that need an injectable clock.

Fixed-window, not sliding-window/token-bucket: the simplest implementation
that satisfies "at most N requests per rolling wall-clock minute" well
enough here -- its known limitation is up to a 2x burst right across a
window boundary (e.g. the limit's worth of requests in the last second of
one window, immediately followed by another limit's worth in the first
second of the next). Acceptable for a first cut; call out explicitly rather
than silently accepting it as if it were a sliding window.

Keyed per user (not per IP): behind a shared NAT/reverse proxy every caller
would otherwise share one IP-scoped budget, letting one user's traffic
exhaust another's.

The limiter's clock is injectable (`FixedWindowRateLimiter(clock=...)`) so
tests can advance time deterministically without a real sleep -- see
tests/test_ratelimit.py.
"""

import random
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core import db as db_module
from app.core.auth import get_current_user
from app.core.config import settings
from app.models.models import RateLimitWindow, User

_WINDOW_SECONDS = 60.0


@dataclass
class _WindowState:
    window_start: float
    count: int


class FixedWindowRateLimiter:
    """Fixed-window counter per key. `limit` defaults to
    `settings.rate_limit_per_minute`, read at construction time (not on
    every call) so a test can override it per-instance without having to
    mutate the global settings object.
    """

    def __init__(
        self,
        *,
        limit: int | None = None,
        window_seconds: float = _WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit if limit is not None else settings.rate_limit_per_minute
        self._window_seconds = window_seconds
        self._clock = clock
        self._lock = Lock()
        self._state: dict[str, _WindowState] = {}

    def check(self, key: str) -> None:
        """Raise HTTP 429 (with a `Retry-After` header) if `key` has already
        used up its budget in the current window; otherwise count this call
        and return normally."""
        now = self._clock()
        with self._lock:
            state = self._state.get(key)
            if state is None or now - state.window_start >= self._window_seconds:
                self._state[key] = _WindowState(window_start=now, count=1)
                return
            if state.count >= self._limit:
                retry_after = max(0, int(self._window_seconds - (now - state.window_start)) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail='Rate limit exceeded',
                    headers={'Retry-After': str(retry_after)},
                )
            state.count += 1

    def reset(self) -> None:
        """Drop all tracked state -- used between pytest tests so one test's
        request volume never bleeds into the next's budget."""
        with self._lock:
            self._state.clear()


def _too_many_requests(retry_after: float) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail='Rate limit exceeded',
        headers={'Retry-After': str(max(0, int(retry_after)) + 1)},
    )


class DatabaseRateLimiter:
    """Fixed-window counter per key in the `rate_limit_windows` table,
    shared by all replicas. Windows are aligned to wall-clock time (not a
    per-process monotonic clock) so every replica agrees on them."""

    # Old windows are pruned on roughly one call in this many.
    _PRUNE_PROBABILITY = 0.01
    _KEEP_SECONDS = 3600

    def __init__(
        self,
        *,
        limit: int | None = None,
        window_seconds: float = _WINDOW_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limit = limit if limit is not None else settings.rate_limit_per_minute
        self._window_seconds = window_seconds
        self._clock = clock

    def check(self, key: str) -> None:
        now = self._clock()
        window_start = int(now // self._window_seconds * self._window_seconds)
        with db_module.SessionLocal() as db:
            dialect = db.get_bind().dialect.name
            insert = postgresql_insert if dialect == 'postgresql' else sqlite_insert
            statement = (
                insert(RateLimitWindow)
                .values(key=key, window_start=window_start, count=1)
                .on_conflict_do_update(
                    index_elements=['key', 'window_start'],
                    set_={'count': RateLimitWindow.count + 1},
                )
                .returning(RateLimitWindow.count)
            )
            count = db.execute(statement).scalar_one()
            if random.random() < self._PRUNE_PROBABILITY:
                db.execute(delete(RateLimitWindow).where(RateLimitWindow.window_start < window_start - self._KEEP_SECONDS))
            db.commit()
        if count > self._limit:
            raise _too_many_requests(window_start + self._window_seconds - now)

    def reset(self) -> None:
        with db_module.SessionLocal() as db:
            db.execute(delete(RateLimitWindow))
            db.commit()


# Module-level singleton every route dependency below shares. Tests swap
# this attribute out (`monkeypatch.setattr(ratelimit, 'rate_limiter', ...)`)
# with an instance built with an injected clock and/or a tighter limit --
# `enforce_rate_limit` below looks the name up in this module's namespace on
# every call, so it always sees whichever instance currently sits here.
rate_limiter: FixedWindowRateLimiter | DatabaseRateLimiter = DatabaseRateLimiter()


def enforce_rate_limit(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency combining authentication with per-user rate
    limiting: resolves `current_user` (app/core/auth.py) first -- an
    unauthenticated request must never burn budget it has no user to charge
    -- then checks it against the shared `rate_limiter`. Routes that need
    both auth and rate limiting declare only this one dependency (see
    app/api/conversations.py, app/api/bots.py, app/api/chat.py).
    """
    rate_limiter.check(str(user.id))
    return user
