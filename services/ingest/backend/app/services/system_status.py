"""System status for the portal sidebar and the admin page.

Weave-Ingest probes the platform over the network it already has -- the
services' health endpoints, its own database and broker, a Celery ping to
its workers -- instead of docker.sock/kubectl, which the Kubernetes
deployment has no portable equivalent for (same reasoning as the worker log
capture). One probe round is cached for a few seconds, so every open
sidebar polling never turns into a probe storm against the other services.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.security import _rate_limit_redis
from app.workers.celery_app import celery_app

Status = Literal['ok', 'degraded', 'down']
Area = Literal['portal', 'processing', 'chat']

AREAS: tuple[Area, ...] = ('portal', 'processing', 'chat')
_RANK = {'ok': 0, 'degraded': 1, 'down': 2}
_CACHE_SECONDS = 15.0
_HTTP_TIMEOUT = httpx.Timeout(2.0, connect=1.0)


@dataclass(frozen=True)
class HttpComponent:
    key: str
    area: Area
    # Health path on the target; None only checks reachability (any HTTP
    # answer below 500 -- frontends and the MCP endpoint have no health route).
    path: str | None
    # Probed through another component's address (the Knowledge worker has
    # no port of its own; Weave-Knowledge answers for it).
    via: str | None = None


# Display order on the admin page, after the fixed checks below.
HTTP_COMPONENTS: tuple[HttpComponent, ...] = (
    HttpComponent('ingest-frontend', 'portal', None),
    HttpComponent('knowledge', 'processing', '/ready'),
    HttpComponent('knowledge-worker', 'processing', '/workers', via='knowledge'),
    HttpComponent('embeddings', 'processing', '/ready'),
    HttpComponent('retrieval', 'chat', '/health'),
    HttpComponent('reranker', 'chat', '/health'),
    HttpComponent('runtime', 'chat', '/health'),
    HttpComponent('api', 'chat', '/health'),
    HttpComponent('tools', 'chat', '/health'),
    HttpComponent('tools-mcp', 'chat', None),
    HttpComponent('chat', 'chat', None),
)


@dataclass
class ComponentStatus:
    key: str
    area: Area
    status: Status
    latency_ms: int | None = None
    detail: str | None = None
    target: str | None = None


def targets() -> dict[str, str]:
    """``system_status_targets`` as component -> base URL; malformed entries are skipped."""
    parsed: dict[str, str] = {}
    for entry in settings.system_status_targets:
        key, sep, url = entry.partition('=')
        if sep and key.strip() and url.strip():
            parsed[key.strip()] = url.strip().rstrip('/')
    return parsed


def _timed(check: Callable[[], tuple[Status, str | None]]) -> tuple[Status, str | None, int]:
    started = time.monotonic()
    try:
        status, detail = check()
    except Exception as exc:  # a probe must never take the status page down with it
        status, detail = 'down', type(exc).__name__
    return status, detail, int((time.monotonic() - started) * 1000)


def _http_check(url: str, path: str | None) -> tuple[Status, str | None]:
    try:
        response = httpx.get(f'{url}{path or "/"}', timeout=_HTTP_TIMEOUT, follow_redirects=False)
    except httpx.TimeoutException:
        return 'down', 'timeout'
    except httpx.HTTPError:
        return 'down', 'unreachable'
    if path is None:
        return ('ok', None) if response.status_code < 500 else ('down', f'HTTP {response.status_code}')
    try:
        body = response.json()
    except ValueError:
        body = {}
    reported = body.get('status') if isinstance(body, dict) else None
    workers = body.get('workers') if isinstance(body, dict) else None
    if reported == 'starting':
        return 'degraded', 'starting'
    if response.is_success and reported != 'error':
        return 'ok', f'{workers} worker' if isinstance(workers, int) else None
    return 'down', reported if isinstance(reported, str) and reported not in ('ok', 'healthy') else f'HTTP {response.status_code}'


def _database_check(db: Session) -> tuple[Status, str | None]:
    db.execute(text('SELECT 1'))
    return 'ok', None


def _broker_check() -> tuple[Status, str | None]:
    _rate_limit_redis().ping()
    return 'ok', None


def _worker_check() -> tuple[Status, str | None]:
    replies = celery_app.control.ping(timeout=1.0) or []
    return ('ok', f'{len(replies)} worker') if replies else ('down', 'no worker answered')


def _collect(db: Session) -> dict:
    configured = targets()
    # The database check runs on the request's session, in this thread.
    results: list[ComponentStatus] = []
    status, detail, latency = _timed(lambda: _database_check(db))
    results.append(ComponentStatus('database', 'portal', status, latency, detail))
    results.append(ComponentStatus('ingest-backend', 'portal', 'ok', 0))

    jobs: list[tuple[ComponentStatus, Callable[[], tuple[Status, str | None]]]] = [
        (ComponentStatus('broker', 'portal', 'down'), _broker_check),
        (ComponentStatus('ingest-worker', 'processing', 'down'), _worker_check),
    ]
    for component in HTTP_COMPONENTS:
        url = configured.get(component.via or component.key)
        if not url:
            continue
        jobs.append((
            ComponentStatus(component.key, component.area, 'down', target=url),
            lambda url=url, path=component.path: _http_check(url, path),
        ))
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        outcomes = list(pool.map(lambda job: _timed(job[1]), jobs))
    for (entry, _), (status, detail, latency) in zip(jobs, outcomes):
        entry.status, entry.detail, entry.latency_ms = status, detail, latency
        results.append(entry)

    areas = {area: _worst(entry.status for entry in results if entry.area == area) for area in AREAS}
    return {
        'status': _worst(areas.values()),
        'checked_at': datetime.now(timezone.utc),
        'areas': [{'key': area, 'status': status} for area, status in areas.items()],
        'components': results,
    }


def _worst(statuses) -> Status:
    return max(statuses, key=_RANK.__getitem__, default='ok')


_lock = threading.Lock()
_cached: tuple[float, dict] | None = None


def system_status(db: Session, *, refresh: bool = False) -> dict:
    """The latest probe round, at most ``_CACHE_SECONDS`` old unless ``refresh``.

    The lock is held while probing on purpose: concurrent callers wait for
    the one round in flight instead of starting their own.
    """
    global _cached
    with _lock:
        if not refresh and _cached is not None and time.monotonic() - _cached[0] < _CACHE_SECONDS:
            return _cached[1]
        result = _collect(db)
        _cached = (time.monotonic(), result)
        return result


def clear_cache() -> None:
    global _cached
    with _lock:
        _cached = None
