from sqlalchemy import delete

from app.core.config import settings
from app.models.models import RetrievalProviderConfig, UserRole
from conftest import TestingSessionLocal, create_test_user, login_as


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
