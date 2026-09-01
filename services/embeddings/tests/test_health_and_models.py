from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, client, install_fake_embedder


def test_health_requires_no_auth_and_reports_cold_state():
    # No Authorization header at all, and no install_fake_embedder() call --
    # /health must still answer 200 (see app/main.py's module docstring for
    # why an unauthenticated, always-200 health endpoint matters for a
    # container healthcheck during first-run model download).
    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'starting'
    assert body['warm'] is False
    assert body['dimension'] is None
    assert body['model'] == FAKE_MODEL_NAME
    assert isinstance(body['threads'], int) and body['threads'] >= 1


def test_health_reports_warm_state_once_model_is_loaded():
    install_fake_embedder(dimension=384, threads=4)
    resp = client.get('/health')
    body = resp.json()
    assert body['status'] == 'ok'
    assert body['warm'] is True
    assert body['dimension'] == 384
    assert body['threads'] == 4


def test_models_endpoint_requires_auth():
    resp = client.get('/v1/models')
    assert resp.status_code == 401


def test_models_endpoint_lists_configured_model():
    resp = client.get('/v1/models', headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body['object'] == 'list'
    assert body['data'] == [{'id': FAKE_MODEL_NAME, 'object': 'model', 'owned_by': 'weave-tools'}]
