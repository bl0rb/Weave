from tests.conftest import client


def test_health_returns_healthy_without_auth():
    # /health carries no Authorization header at all -- it must still
    # succeed, since a liveness/readiness probe has no service token to
    # present (see app/main.py's healthcheck docstring/comment).
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'healthy'}
