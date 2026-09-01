"""In-process fixed-window rate limiting, keyed per authenticated user.

Single-instance limiting: all state lives in a plain dict in THIS process's
memory. That limits traffic hitting one replica only -- a real
multi-replica deployment needs a shared store (Redis INCR + EXPIRE is the
usual choice) to enforce one global budget across replicas; swap in a
Redis-backed implementation behind the same `enforce_rate_limit` dependency
once Weave-API actually runs more than one instance. Fine for this
skeleton, whose whole premise (per the task) is no Celery/Redis yet.

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

import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.config import settings
from app.models.models import User

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


# Module-level singleton every route dependency below shares. Tests swap
# this attribute out (`monkeypatch.setattr(ratelimit, 'rate_limiter', ...)`)
# with an instance built with an injected clock and/or a tighter limit --
# `enforce_rate_limit` below looks the name up in this module's namespace on
# every call, so it always sees whichever instance currently sits here.
rate_limiter = FixedWindowRateLimiter()


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
