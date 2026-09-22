"""EMBEDDINGS_MAX_CONCURRENT_REQUESTS / EMBEDDINGS_QUEUE_TIMEOUT_SECONDS
enforcement (2026-09-22 OOM incident): at most N encodes run at once, and a
request that can't get a slot within the queue timeout gets a 503 with
Retry-After rather than piling up behind an already-saturated encoder.

Uses `with TestClient(app) as test_client:` (like test_startup.py), not the
bare module-level `client` from conftest -- concurrency across real OS
threads needs the requests to share a single event loop (the portal that
context entry sets up), which the bare client's per-call portal doesn't
give: asyncio.Semaphore is bound to whichever loop first acquires it, so a
fresh loop per request would make it useless as a cross-request bound,
exactly like the real single-loop uvicorn process this guards. The real
`build_embedder` is monkeypatched (like test_startup.py) to hand back our
slow fake directly, so entering the context never touches the network.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.services.encoder import EncodeResult
from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, reset_encoder_state


class _SlowFakeEmbedder:
    """Deterministic fake whose encode() sleeps and tracks peak concurrency
    -- safe to sleep here since encode() runs inside run_in_threadpool's
    worker thread, never the event loop."""

    model_name = FAKE_MODEL_NAME
    dimension = 8
    threads = 1

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._lock = threading.Lock()
        self._current = 0
        self.peak_concurrency = 0

    def encode(self, texts: list[str], *, input_type: str) -> EncodeResult:
        with self._lock:
            self._current += 1
            self.peak_concurrency = max(self.peak_concurrency, self._current)
        try:
            time.sleep(self.delay)
            return EncodeResult(vectors=[[0.0] * self.dimension for _ in texts], token_counts=[1 for _ in texts])
        finally:
            with self._lock:
                self._current -= 1


def _post(test_client: TestClient):
    return test_client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': ['a']})


def _run_with_slow_embedder(monkeypatch, delay: float) -> tuple[TestClient, _SlowFakeEmbedder]:
    """Boots the app with a monkeypatched build_embedder so lifespan's
    background loader installs our slow fake instead of downloading the
    real model, then waits for it to go warm."""
    reset_encoder_state()
    monkeypatch.setattr(main_module, '_encode_semaphore', None)  # force a limiter bound to this test's own loop
    fake = _SlowFakeEmbedder(delay)
    monkeypatch.setattr(main_module.encoder_module, 'build_embedder', lambda settings: fake)

    test_client = TestClient(main_module.app)
    test_client.__enter__()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not test_client.get('/health').json()['warm']:
        time.sleep(0.02)
    return test_client, fake


def test_concurrent_requests_never_overlap_encoder_beyond_limit(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_concurrent_requests', 2)
    monkeypatch.setattr(settings, 'embeddings_queue_timeout_seconds', 10)
    test_client, fake = _run_with_slow_embedder(monkeypatch, delay=0.2)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: _post(test_client), range(4)))
    finally:
        test_client.__exit__(None, None, None)
        reset_encoder_state()

    assert all(resp.status_code == 200 for resp in responses)
    assert fake.peak_concurrency <= 2


def test_queue_timeout_returns_503_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_concurrent_requests', 1)
    monkeypatch.setattr(settings, 'embeddings_queue_timeout_seconds', 0.05)
    test_client, _fake = _run_with_slow_embedder(monkeypatch, delay=0.5)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_post, test_client) for _ in range(2)]
            responses = [future.result() for future in futures]
    finally:
        test_client.__exit__(None, None, None)
        reset_encoder_state()

    statuses = sorted(resp.status_code for resp in responses)
    assert statuses == [200, 503]
    overflow = next(resp for resp in responses if resp.status_code == 503)
    assert overflow.headers.get('Retry-After') == '5'
    assert 'embeddings busy' in overflow.json()['detail']


def test_health_stays_responsive_while_encoder_is_busy(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_concurrent_requests', 1)
    monkeypatch.setattr(settings, 'embeddings_queue_timeout_seconds', 10)
    test_client, _fake = _run_with_slow_embedder(monkeypatch, delay=0.3)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_post, test_client)
            time.sleep(0.05)  # let the busy request grab the only slot
            health_resp = test_client.get('/health')
            future.result()
    finally:
        test_client.__exit__(None, None, None)
        reset_encoder_state()

    assert health_resp.status_code == 200
