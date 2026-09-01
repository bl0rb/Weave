"""Verifies app/main.py's actual startup sequence: the model loads on a
background thread (never blocking ASGI startup or /health), and /health +
/v1/embeddings correctly reflect the cold -> warm transition. Uses a fake
`build_embedder` (monkeypatched onto app.main's own module reference, see
that module's docstring for why it looks the function up fresh on every
call rather than capturing it as a default argument) with an artificial
delay -- long enough to reliably observe the "still starting" window, short
enough to keep this test fast.
"""

import time

from fastapi.testclient import TestClient

import app.main as main_module
from app.services.encoder import EncodeResult
from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, reset_encoder_state

_FAKE_LOAD_DELAY_SECONDS = 0.3


class _SlowFakeEmbedder:
    model_name = FAKE_MODEL_NAME
    dimension = 384
    threads = 1

    def encode(self, texts, *, input_type):
        return EncodeResult(vectors=[[0.0] * self.dimension for _ in texts], token_counts=[1 for _ in texts])


def _slow_fake_build_embedder(settings):
    time.sleep(_FAKE_LOAD_DELAY_SECONDS)
    return _SlowFakeEmbedder()


def test_startup_does_not_block_and_health_transitions_from_starting_to_ok(monkeypatch):
    reset_encoder_state()
    monkeypatch.setattr(main_module.encoder_module, 'build_embedder', _slow_fake_build_embedder)

    entered_at = time.monotonic()
    with TestClient(main_module.app) as test_client:
        entry_elapsed = time.monotonic() - entered_at
        # Entering the lifespan context returns almost immediately -- if
        # app/main.py's lifespan awaited build_embedder() inline instead of
        # handing it to a background thread, this would take at least
        # _FAKE_LOAD_DELAY_SECONDS.
        assert entry_elapsed < _FAKE_LOAD_DELAY_SECONDS / 2

        immediate_health = test_client.get('/health')
        assert immediate_health.status_code == 200
        immediate_body = immediate_health.json()
        assert immediate_body['warm'] is False
        assert immediate_body['status'] == 'starting'

        immediate_embeddings = test_client.post(
            '/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'}
        )
        assert immediate_embeddings.status_code == 503

        deadline = time.monotonic() + 5.0
        warm = False
        while time.monotonic() < deadline:
            if test_client.get('/health').json()['warm']:
                warm = True
                break
            time.sleep(0.02)
        assert warm, 'background loader never finished within the test deadline'

        warm_health = test_client.get('/health').json()
        assert warm_health['status'] == 'ok'
        assert warm_health['dimension'] == 384

        ready_embeddings = test_client.post(
            '/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'}
        )
        assert ready_embeddings.status_code == 200

    reset_encoder_state()


def test_startup_failure_is_reported_as_error_status(monkeypatch):
    reset_encoder_state()

    def _failing_build_embedder(settings):
        raise RuntimeError('simulated model load failure')

    monkeypatch.setattr(main_module.encoder_module, 'build_embedder', _failing_build_embedder)

    with TestClient(main_module.app) as test_client:
        deadline = time.monotonic() + 5.0
        body = test_client.get('/health').json()
        while time.monotonic() < deadline and body['status'] == 'starting':
            time.sleep(0.02)
            body = test_client.get('/health').json()

        assert body['status'] == 'error'
        assert body['warm'] is False

    reset_encoder_state()
