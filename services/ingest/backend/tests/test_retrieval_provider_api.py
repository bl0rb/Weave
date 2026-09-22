import httpx
from sqlalchemy import delete

from app.core.config import settings
from app.models.models import RetrievalProviderConfig, UserRole
from conftest import TestingSessionLocal, create_test_user, login_as

import app.api.retrieval_provider as retrieval_provider_module


def _clear() -> None:
    with TestingSessionLocal() as db:
        db.execute(delete(RetrievalProviderConfig))
        db.commit()


def _admin(prefix: str):
    user = create_test_user(username=prefix, email=f'{prefix}@example.com', role=UserRole.ADMIN)
    return login_as(user.username), user


def test_internal_projection_requires_service_token_and_does_not_crash(monkeypatch):
    """Regression: internal_retrieval_provider() referenced an undefined
    `effective` name (NameError -> 500) instead of computing it via
    _effective(row). Exercises the exact reproduction from the bug report:
    an authenticated internal call must return 200, never crash."""
    _clear()
    admin, _ = _admin('retrieval-config-admin')
    admin.put('/api/v1/auth/admin/retrieval-provider', json={'embedding_provider': 'fake', 'rerank_provider': 'none'})

    monkeypatch.setattr(settings, 'chat_config_service_token', '')
    assert admin.get('/api/v1/internal/retrieval-provider').status_code == 503
    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    assert admin.get('/api/v1/internal/retrieval-provider', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    internal = admin.get('/api/v1/internal/retrieval-provider', headers={'Authorization': 'Bearer knowledge-secret'})
    assert internal.status_code == 200, internal.text
    assert internal.headers['cache-control'] == 'no-store'
    assert internal.json()['embedding_provider'] == 'fake'


def test_internal_projection_falls_back_to_env_when_no_admin_override_exists(monkeypatch):
    _clear()
    admin, _ = _admin('retrieval-config-fallback-admin')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    monkeypatch.setattr(settings, 'embedding_provider', 'openai')
    monkeypatch.setattr(settings, 'embedding_model', 'intfloat/multilingual-e5-small')

    internal = admin.get('/api/v1/internal/retrieval-provider', headers={'Authorization': 'Bearer knowledge-secret'})
    assert internal.status_code == 200, internal.text
    assert internal.json()['embedding_provider'] == 'openai'
    assert internal.json()['embedding_model'] == 'intfloat/multilingual-e5-small'


def test_admin_override_wins_over_env_in_internal_projection(monkeypatch):
    _clear()
    admin, _ = _admin('retrieval-config-override-admin')
    monkeypatch.setattr(settings, 'embedding_provider', 'openai')
    saved = admin.put('/api/v1/auth/admin/retrieval-provider', json={'embedding_provider': 'fake', 'rerank_provider': 'none'})
    assert saved.status_code == 200, saved.text

    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    internal = admin.get('/api/v1/internal/retrieval-provider', headers={'Authorization': 'Bearer knowledge-secret'})
    assert internal.json()['embedding_provider'] == 'fake'


# --- Index-Wartung: 'Vektoren neu berechnen' ----------------------------

def _mock_httpx(monkeypatch, *, post=None, get=None):
    if post is not None:
        monkeypatch.setattr(retrieval_provider_module.httpx, 'post', post)
    if get is not None:
        monkeypatch.setattr(retrieval_provider_module.httpx, 'get', get)


class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError('error', request=httpx.Request('POST', 'http://x'), response=httpx.Response(self.status_code))

    def json(self) -> dict:
        return self._json_body


def test_reindex_vectors_starts_knowledge_reindex(monkeypatch):
    admin, _ = _admin('reindex-vectors-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'reindex-secret')

    calls = []

    def fake_post(url, headers=None, timeout=None):
        calls.append((url, headers))
        return _FakeResponse({'status': 'started', 'task_id': 'task-123'})

    _mock_httpx(monkeypatch, post=fake_post)

    response = admin.post('/api/v1/admin/retrieval-provider/reindex')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {'started': True, 'task_id': 'task-123'}
    assert calls == [('https://knowledge.example/api/v1/internal/reindex', {'X-Weave-Reindex-Token': 'reindex-secret'})]


def test_reindex_vectors_503_when_knowledge_unreachable(monkeypatch):
    admin, _ = _admin('reindex-vectors-down-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'reindex-secret')

    def fake_post(url, headers=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    _mock_httpx(monkeypatch, post=fake_post)

    response = admin.post('/api/v1/admin/retrieval-provider/reindex')
    assert response.status_code == 503, response.text


def test_reindex_vectors_503_when_knowledge_url_unset(monkeypatch):
    admin, _ = _admin('reindex-vectors-unset-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')

    response = admin.post('/api/v1/admin/retrieval-provider/reindex')
    assert response.status_code == 503, response.text


def test_reindex_vectors_denies_non_admin():
    unique = 'reindex-vectors-user'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.USER)
    client = login_as(user.username)
    response = client.post('/api/v1/admin/retrieval-provider/reindex')
    assert response.status_code == 403


def test_reindex_vectors_status_proxies_knowledge(monkeypatch):
    admin, _ = _admin('reindex-status-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'reindex-secret')

    def fake_get(url, headers=None, timeout=None):
        assert url == 'https://knowledge.example/api/v1/internal/reindex/status'
        assert headers == {'X-Weave-Reindex-Token': 'reindex-secret'}
        return _FakeResponse({'total': 5, 'by_status': {'indexed': 5}, 'newest_updated_at': None})

    _mock_httpx(monkeypatch, get=fake_get)

    response = admin.get('/api/v1/admin/retrieval-provider/reindex-status')
    assert response.status_code == 200, response.text
    assert response.json() == {'total': 5, 'by_status': {'indexed': 5}, 'newest_updated_at': None}


def test_reindex_vectors_status_503_when_knowledge_down(monkeypatch):
    admin, _ = _admin('reindex-status-down-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'reindex-secret')

    def fake_get(url, headers=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    _mock_httpx(monkeypatch, get=fake_get)

    response = admin.get('/api/v1/admin/retrieval-provider/reindex-status')
    assert response.status_code == 503, response.text
