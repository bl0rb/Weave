import httpx
import pytest

from app.core.config import settings
from app.models.models import UserRole
from app.services import system_status
from conftest import create_test_user, login_as


def _response(url: str, status_code: int, body=None) -> httpx.Response:
    return httpx.Response(status_code, json=body, request=httpx.Request('GET', url))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    system_status.clear_cache()
    monkeypatch.setattr(system_status, '_worker_check', lambda: ('ok', '1 worker'))
    yield
    system_status.clear_cache()


def _serve(monkeypatch, answers: dict[str, object]) -> list[str]:
    """Fake httpx.get: URL -> (status, body) or an exception to raise."""
    calls: list[str] = []

    def fake_get(url, **_):
        calls.append(url)
        answer = answers.get(url, httpx.ConnectError('refused'))
        if isinstance(answer, Exception):
            raise answer
        return _response(url, *answer)

    monkeypatch.setattr(system_status.httpx, 'get', fake_get)
    return calls


def test_targets_parse_and_skip_malformed(monkeypatch):
    monkeypatch.setattr(settings, 'system_status_targets', ['knowledge=http://k:8000/', 'broken', '=http://x', 'api= '])
    assert system_status.targets() == {'knowledge': 'http://k:8000'}


def test_user_sees_areas_only_and_admin_sees_components(monkeypatch):
    monkeypatch.setattr(settings, 'system_status_targets', [
        'knowledge=http://k:8000', 'retrieval=http://r:8000', 'embeddings=http://e:8000', 'chat=http://c:3000',
    ])
    _serve(monkeypatch, {
        'http://k:8000/ready': (200, {'status': 'ready'}),
        'http://k:8000/workers': (200, {'status': 'ok', 'workers': 2}),
        'http://r:8000/health': (503, {'status': 'unavailable'}),
        'http://e:8000/ready': (503, {'status': 'starting'}),
        'http://c:3000/': (404, None),
    })
    user = create_test_user(username='status-user', email='status-user@example.com')
    admin = create_test_user(username='status-admin', email='status-admin@example.com', role=UserRole.ADMIN)

    public = login_as(user.username).get('/api/v1/system-status')
    assert public.status_code == 200, public.text
    body = public.json()
    assert 'components' not in body and 'http://' not in public.text
    assert body['status'] == 'down'
    assert {area['key']: area['status'] for area in body['areas']} == {
        'portal': 'ok', 'processing': 'degraded', 'chat': 'down',
    }
    assert login_as(user.username).get('/api/v1/auth/admin/system-status').status_code == 403

    detailed = login_as(admin.username).get('/api/v1/auth/admin/system-status').json()
    components = {item['key']: item for item in detailed['components']}
    # Unconfigured components are not part of this installation and not listed.
    assert set(components) == {
        'database', 'ingest-backend', 'broker', 'ingest-worker',
        'knowledge', 'knowledge-worker', 'embeddings', 'retrieval', 'chat',
    }
    assert components['knowledge-worker']['status'] == 'ok'
    assert components['knowledge-worker']['detail'] == '2 worker'
    assert components['knowledge-worker']['target'] == 'http://k:8000'
    assert components['embeddings'] == {**components['embeddings'], 'status': 'degraded', 'detail': 'starting'}
    assert components['retrieval']['status'] == 'down'
    assert components['retrieval']['detail'] == 'unavailable'
    # A frontend only has to answer at all.
    assert components['chat']['status'] == 'ok'


def test_unreachable_service_and_cache(monkeypatch):
    monkeypatch.setattr(settings, 'system_status_targets', ['api=http://a:8000'])
    calls = _serve(monkeypatch, {'http://a:8000/health': httpx.ReadTimeout('slow')})
    admin = create_test_user(username='status-cache', email='status-cache@example.com', role=UserRole.ADMIN)
    client = login_as(admin.username)

    first = client.get('/api/v1/auth/admin/system-status').json()
    assert {item['key']: item['detail'] for item in first['components']}['api'] == 'timeout'
    client.get('/api/v1/auth/admin/system-status')
    assert len(calls) == 1  # served from the cache
    client.get('/api/v1/auth/admin/system-status?refresh=true')
    assert len(calls) == 2


def test_worker_check_reports_missing_workers(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(system_status.celery_app.control, 'ping', lambda timeout: [])
    assert system_status._worker_check() == ('down', 'no worker answered')
    monkeypatch.setattr(system_status.celery_app.control, 'ping', lambda timeout: [{'w1': {'ok': 'pong'}}])
    assert system_status._worker_check() == ('ok', '1 worker')
