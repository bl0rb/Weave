from tests.conftest import client


def test_health_is_unauthenticated_and_pings_the_db():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'healthy'}
