from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_db
from app.main import app
from tests.conftest import client


def test_health_returns_healthy():
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'healthy'}


def test_readiness_ok():
    resp = client.get('/ready')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ready'}


def test_readiness_reports_database_failure():
    # AV-03: a dead DB must flip readiness without touching liveness.
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise SQLAlchemyError('db is down')

    def _broken_get_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_get_db
    try:
        resp = client.get('/ready')
    finally:
        from tests.conftest import override_get_db

        app.dependency_overrides[get_db] = override_get_db
    assert resp.status_code == 503
    assert resp.json()['reason'] == 'database'


def test_workers_counts_ping_replies(monkeypatch):
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, 'ping', lambda timeout: [{'w1': {'ok': 'pong'}}, {'w2': {'ok': 'pong'}}])
    resp = client.get('/workers')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok', 'workers': 2}

    monkeypatch.setattr(celery_app.control, 'ping', lambda timeout: [])
    resp = client.get('/workers')
    assert resp.status_code == 503
    assert resp.json() == {'status': 'unavailable', 'workers': 0}
