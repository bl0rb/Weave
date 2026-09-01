"""Auth cases for the fail-closed require_api_token dependency
(app/core/auth.py), exercised through both routes it guards. /health is
verified separately (test_health.py) to need NO auth at all."""

from app.core.config import settings
from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, client, install_fake_embedder


def test_unconfigured_token_returns_503(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_api_token', '')
    install_fake_embedder()

    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'})
    assert resp.status_code == 503

    resp = client.get('/v1/models', headers=AUTH_HEADERS)
    assert resp.status_code == 503


def test_missing_authorization_header_returns_401():
    install_fake_embedder()
    resp = client.post('/v1/embeddings', json={'model': FAKE_MODEL_NAME, 'input': 'x'})
    assert resp.status_code == 401


def test_wrong_token_returns_401():
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings',
        headers={'Authorization': 'Bearer wrong-token'},
        json={'model': FAKE_MODEL_NAME, 'input': 'x'},
    )
    assert resp.status_code == 401


def test_malformed_authorization_header_returns_401():
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings',
        headers={'Authorization': 'not-a-bearer-header'},
        json={'model': FAKE_MODEL_NAME, 'input': 'x'},
    )
    assert resp.status_code == 401


def test_correct_token_succeeds():
    install_fake_embedder()
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'})
    assert resp.status_code == 200
