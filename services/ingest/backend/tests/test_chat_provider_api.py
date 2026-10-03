from unittest.mock import patch

from sqlalchemy import delete

from app.core.config import settings
from app.models.models import ChatProviderConfig, UserRole
from conftest import TestingSessionLocal, create_test_user, login_as


def _clear() -> None:
    with TestingSessionLocal() as db:
        db.execute(delete(ChatProviderConfig))
        db.commit()


def _admin(prefix: str):
    user = create_test_user(username=prefix, email=f'{prefix}@example.com', role=UserRole.ADMIN)
    return login_as(user.username), user


def test_admin_config_is_redacted_and_internal_projection_requires_service_token(monkeypatch):
    _clear()
    admin, _ = _admin('chat-config-admin')
    saved = admin.put('/api/v1/auth/admin/chat-provider', json={
        'enabled': True,
        'base_url': 'https://llm.example.com/v1/',
        'model': 'enterprise-chat',
        'api_key': 'super-secret-provider-key',
        'timeout_seconds': 45,
        'temperature': 0.3,
        'supports_tools': True,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()['has_api_key'] is True
    assert saved.json()['supports_tools'] is True
    assert 'api_key' not in saved.json()
    assert 'super-secret' not in saved.text

    with TestingSessionLocal() as db:
        row = db.get(ChatProviderConfig, 'default')
        assert row.api_key_encrypted != 'super-secret-provider-key'

    monkeypatch.setattr(settings, 'chat_config_service_token', '')
    assert admin.get('/api/v1/internal/chat-provider').status_code == 503
    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-secret')
    assert admin.get('/api/v1/internal/chat-provider', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    internal = admin.get(
        '/api/v1/internal/chat-provider', headers={'Authorization': 'Bearer runtime-secret'}
    )
    assert internal.status_code == 200
    assert internal.headers['cache-control'] == 'no-store'
    assert internal.json()['api_key'] == 'super-secret-provider-key'
    assert internal.json()['model'] == 'enterprise-chat'
    assert internal.json()['supports_tools'] is True


def test_non_admin_cannot_read_or_change_chat_provider():
    _clear()
    user = create_test_user(username='chat-config-user', email='chat-config-user@example.com')
    client = login_as(user.username)
    assert client.get('/api/v1/auth/admin/chat-provider').status_code == 403
    assert client.put('/api/v1/auth/admin/chat-provider', json={}).status_code == 403


def test_endpoint_change_without_new_key_clears_old_credential():
    _clear()
    admin, _ = _admin('chat-config-move-admin')
    first = admin.put('/api/v1/auth/admin/chat-provider', json={
        'enabled': True, 'base_url': 'https://one.example/v1', 'model': 'model', 'api_key': 'old-key'
    })
    assert first.json()['has_api_key'] is True
    moved = admin.put('/api/v1/auth/admin/chat-provider', json={
        'enabled': True, 'base_url': 'https://two.example/v1', 'model': 'model'
    })
    assert moved.status_code == 200
    assert moved.json()['has_api_key'] is False


def test_connection_test_uses_stored_values_without_exposing_key():
    _clear()
    admin, _ = _admin('chat-config-test-admin')
    admin.put('/api/v1/auth/admin/chat-provider', json={
        'enabled': True, 'base_url': 'https://llm.example/v1', 'model': 'chat', 'api_key': 'stored-key'
    })
    with patch('app.api.chat_provider.test_connection') as probe:
        probe.return_value = {'ok': True, 'detail': 'Verbindung und Modell funktionieren', 'latency_ms': 12}
        response = admin.post('/api/v1/auth/admin/chat-provider/test')
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert probe.call_args.kwargs['api_key'] == 'stored-key'
    assert 'stored-key' not in response.text


def test_enabled_configuration_requires_endpoint_and_model():
    _clear()
    admin, _ = _admin('chat-config-validation-admin')
    response = admin.put('/api/v1/auth/admin/chat-provider', json={'enabled': True})
    assert response.status_code == 422


def test_named_endpoints_are_listed_projected_by_id_and_cataloged_without_keys(monkeypatch):
    _clear()
    admin, _ = _admin('chat-endpoints-admin')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-secret')
    auth = {'Authorization': 'Bearer runtime-secret'}
    assert admin.put('/api/v1/auth/admin/chat-provider', json={
        'enabled': True, 'base_url': 'https://central.example/v1', 'model': 'central-chat',
    }).status_code == 200
    saved = admin.put('/api/v1/auth/admin/chat-provider/endpoints/tools-llm', json={
        'name': 'Tool-Modell', 'enabled': True, 'base_url': 'https://tools.example/v1', 'model': 'tool-chat',
        'api_key': 'tools-secret', 'supports_tools': True,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()['id'] == 'tools-llm'
    assert 'tools-secret' not in saved.text
    assert admin.put('/api/v1/auth/admin/chat-provider/endpoints/Bad_Id', json={}).status_code == 422

    listed = admin.get('/api/v1/auth/admin/chat-provider/endpoints').json()['items']
    assert [item['id'] for item in listed] == ['default', 'tools-llm']

    internal = admin.get('/api/v1/internal/chat-provider', params={'endpoint': 'tools-llm'}, headers=auth).json()
    assert internal['model'] == 'tool-chat'
    assert internal['api_key'] == 'tools-secret'
    assert admin.get('/api/v1/internal/chat-provider', headers=auth).json()['model'] == 'central-chat'
    assert admin.get('/api/v1/internal/chat-provider', params={'endpoint': 'missing'}, headers=auth).json()['configured'] is False

    catalog = admin.get('/api/v1/internal/chat-provider/endpoints', headers=auth)
    assert catalog.status_code == 200
    assert 'tools-secret' not in catalog.text
    assert catalog.json()['items'] == [
        {'id': 'default', 'name': 'central-chat', 'model': 'central-chat', 'supports_tools': False},
        {'id': 'tools-llm', 'name': 'Tool-Modell', 'model': 'tool-chat', 'supports_tools': True},
    ]

    assert admin.delete('/api/v1/auth/admin/chat-provider/endpoints/default').status_code == 409
    assert admin.delete('/api/v1/auth/admin/chat-provider/endpoints/tools-llm').status_code == 204
    assert admin.get('/api/v1/internal/chat-provider', params={'endpoint': 'tools-llm'}, headers=auth).json()['configured'] is False
