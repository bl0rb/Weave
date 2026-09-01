"""Contract tests for POST /rerank against app.schemas.rerank's shape --
every field checked here is checked against the EXACT form Weave-
Retrieval's HttpReranker (backend/app/services/reranker.py in that repo)
sends and parses; see README.md for the full field-by-field trace.

reranker_model.score() is monkeypatched in every test below (the `client`
fixture from conftest.py only marks the model warm, never loads it) -- no
real model runs in this file. tests/test_rerank_content.py is the one file
that runs the actual BAAI/bge-reranker-v2-m3 model.
"""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.model import reranker_model
from tests.conftest import AUTH_HEADERS


# --- response shape / sorting / top_n --------------------------------------

def test_health_reports_expected_fields(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert set(response.json().keys()) == {'status', 'model', 'threads', 'warm', 'max_documents'}


def test_health_does_not_require_auth(client):
    # No Authorization header at all -- GET /health is exempt on purpose,
    # see app/main.py.
    response = client.get('/health')
    assert response.status_code == 200


def test_rerank_response_shape(client, monkeypatch):
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.1, 0.9, 0.5])
    response = client.post(
        '/rerank',
        json={'model': 'BAAI/bge-reranker-v2-m3', 'query': 'q', 'documents': ['a', 'b', 'c']},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {'results'}
    for item in body['results']:
        assert set(item.keys()) == {'index', 'relevance_score'}


def test_rerank_sorts_descending_by_score(client, monkeypatch):
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.1, 0.9, 0.5])
    response = client.post('/rerank', json={'query': 'q', 'documents': ['a', 'b', 'c']}, headers=AUTH_HEADERS)
    body = response.json()
    assert [r['index'] for r in body['results']] == [1, 2, 0]
    assert [r['relevance_score'] for r in body['results']] == [0.9, 0.5, 0.1]


def test_rerank_respects_top_n(client, monkeypatch):
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.1, 0.9, 0.5])
    response = client.post(
        '/rerank', json={'query': 'q', 'documents': ['a', 'b', 'c'], 'top_n': 2}, headers=AUTH_HEADERS
    )
    body = response.json()
    assert len(body['results']) == 2
    assert [r['index'] for r in body['results']] == [1, 2]


def test_rerank_default_top_n_returns_every_document(client, monkeypatch):
    # HttpReranker (Weave-Retrieval) always sends top_n=len(documents)
    # explicitly and requires exactly that many results back -- but a
    # direct Cohere-style caller may omit top_n entirely, which must
    # behave the same way (rank ALL documents, drop none).
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.1, 0.9, 0.5, 0.2])
    response = client.post('/rerank', json={'query': 'q', 'documents': ['a', 'b', 'c', 'd']}, headers=AUTH_HEADERS)
    assert len(response.json()['results']) == 4


def test_rerank_top_n_larger_than_documents_is_clamped(client, monkeypatch):
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.3, 0.7])
    response = client.post(
        '/rerank', json={'query': 'q', 'documents': ['a', 'b'], 'top_n': 50}, headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert len(response.json()['results']) == 2


def test_rerank_unknown_model_field_is_logged_not_rejected(client, monkeypatch, caplog):
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.5])
    with caplog.at_level('WARNING', logger='app.api.rerank'):
        response = client.post(
            '/rerank', json={'model': 'some-other-model', 'query': 'q', 'documents': ['a']}, headers=AUTH_HEADERS
        )
    assert response.status_code == 200
    assert any('some-other-model' in record.getMessage() for record in caplog.records)


# --- guardrails -------------------------------------------------------------

def test_rerank_over_max_documents_returns_413(client, monkeypatch):
    monkeypatch.setattr(settings, 'reranker_max_documents', 5)
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.0] * len(documents))
    response = client.post('/rerank', json={'query': 'q', 'documents': ['x'] * 6}, headers=AUTH_HEADERS)
    assert response.status_code == 413


def test_rerank_at_max_documents_is_allowed(client, monkeypatch):
    monkeypatch.setattr(settings, 'reranker_max_documents', 5)
    monkeypatch.setattr(reranker_model, 'score', lambda query, documents: [0.0] * len(documents))
    response = client.post('/rerank', json={'query': 'q', 'documents': ['x'] * 5}, headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_rerank_empty_documents_returns_422(client):
    response = client.post('/rerank', json={'query': 'q', 'documents': []}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_rerank_empty_query_returns_422(client):
    response = client.post('/rerank', json={'query': '', 'documents': ['a']}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_rerank_missing_documents_field_returns_422(client):
    response = client.post('/rerank', json={'query': 'q'}, headers=AUTH_HEADERS)
    assert response.status_code == 422


# --- auth ---------------------------------------------------------------

def test_rerank_missing_auth_header_returns_401(client):
    response = client.post('/rerank', json={'query': 'q', 'documents': ['a']})
    assert response.status_code == 401


def test_rerank_wrong_token_returns_401(client):
    response = client.post(
        '/rerank', json={'query': 'q', 'documents': ['a']}, headers={'Authorization': 'Bearer wrong'}
    )
    assert response.status_code == 401


def test_rerank_malformed_auth_header_returns_401(client):
    response = client.post(
        '/rerank', json={'query': 'q', 'documents': ['a']}, headers={'Authorization': 'test-token'}
    )
    assert response.status_code == 401


def test_rerank_unconfigured_token_returns_503(monkeypatch):
    # Deliberately does NOT use the `client` fixture -- that fixture sets a
    # token; this test needs the opposite (the fail-closed default with no
    # RERANKER_API_TOKEN configured at all), so it builds its own
    # TestClient directly.
    monkeypatch.setattr(settings, 'reranker_api_token', '')
    was_warm = reranker_model.warm
    reranker_model._ready.set()
    try:
        response = TestClient(app).post(
            '/rerank', json={'query': 'q', 'documents': ['a']}, headers=AUTH_HEADERS
        )
    finally:
        if not was_warm:
            reranker_model._ready.clear()
    assert response.status_code == 503


def test_rerank_unconfigured_token_takes_precedence_over_bad_auth_header(monkeypatch):
    # Even a request with NO Authorization header at all still gets the
    # "service unconfigured" 503, not a 401 -- 401 would wrongly suggest
    # that presenting SOME credential could ever succeed here.
    monkeypatch.setattr(settings, 'reranker_api_token', '')
    response = TestClient(app).post('/rerank', json={'query': 'q', 'documents': ['a']})
    assert response.status_code == 503


# --- model still loading --------------------------------------------------

def test_rerank_returns_503_while_model_still_loading(monkeypatch):
    # `warm` is a read-only property backed by `_ready` (a threading.Event)
    # -- clearing that event is how a test (or a real not-yet-loaded
    # process) makes `warm` false, since the property itself has no
    # setter to monkeypatch directly.
    monkeypatch.setattr(settings, 'reranker_api_token', 'test-token')
    was_warm = reranker_model.warm
    reranker_model._ready.clear()
    monkeypatch.setattr(reranker_model, 'wait_until_warm', lambda timeout: False)
    try:
        response = TestClient(app).post('/rerank', json={'query': 'q', 'documents': ['a']}, headers=AUTH_HEADERS)
    finally:
        if was_warm:
            reranker_model._ready.set()
    assert response.status_code == 503


def test_health_reports_warm_false_while_loading(monkeypatch):
    was_warm = reranker_model.warm
    reranker_model._ready.clear()
    try:
        response = TestClient(app).get('/health')
    finally:
        if was_warm:
            reranker_model._ready.set()
    assert response.json()['warm'] is False


def test_bearer_comparison_is_constant_time():
    # Regression: a plain `!=` short-circuits at the first differing byte,
    # leaking through response latency how much of a guess was correct.
    import inspect

    from app.api import deps

    source = inspect.getsource(deps.require_service_token)
    assert 'compare_digest' in source
    # Ignore comment lines: the comment above the check explains the very
    # `!=` pattern being avoided, so a naive substring search finds it there.
    code = '\n'.join(
        line for line in source.splitlines() if not line.lstrip().startswith('#')
    )
    assert '!=' not in code
