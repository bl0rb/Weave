"""EMBEDDINGS_MAX_INPUTS enforcement: exceeding it is a 413 (a size limit
on the request), not a 400/422 (a malformed request)."""

from app.core.config import settings
from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, client, install_fake_embedder


def test_input_within_limit_succeeds(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_inputs', 3)
    install_fake_embedder()
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': ['a', 'b', 'c']})
    assert resp.status_code == 200


def test_input_exceeding_limit_returns_413(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_inputs', 3)
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': ['a', 'b', 'c', 'd']}
    )
    assert resp.status_code == 413


def test_single_string_input_never_hits_the_list_limit(monkeypatch):
    monkeypatch.setattr(settings, 'embeddings_max_inputs', 1)
    install_fake_embedder()
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'a single string'})
    assert resp.status_code == 200
