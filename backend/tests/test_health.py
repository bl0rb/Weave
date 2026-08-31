from tests.conftest import client


def test_health_is_unauthenticated_and_reports_healthy():
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'healthy'}
