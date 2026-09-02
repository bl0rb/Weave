from unittest.mock import Mock, patch

import httpx
import pytest

from app.core.config import settings
from app.services.chat_config_client import ChatConfigUnavailable, fetch_chat_provider
from app.services.chat_config_client import ChatProviderSnapshot
from tests.conftest import AUTH_HEADERS, client


def test_unconfigured_client_preserves_standalone_runtime_behavior(monkeypatch):
    monkeypatch.setattr(settings, 'chat_config_base_url', '')
    monkeypatch.setattr(settings, 'chat_config_service_token', '')
    assert fetch_chat_provider() is None


def test_fetches_a_fresh_authenticated_snapshot(monkeypatch):
    monkeypatch.setattr(settings, 'chat_config_base_url', 'http://ingest:8000')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'shared-token')
    response = Mock(status_code=200)
    response.json.return_value = {
        'configured': True, 'enabled': True, 'base_url': 'https://llm.example/v1',
        'model': 'enterprise-chat', 'api_key': 'provider-key', 'timeout_seconds': 42,
        'temperature': 0.2,
    }
    with patch('app.services.chat_config_client.httpx.get', return_value=response) as get:
        snapshot = fetch_chat_provider()
    assert snapshot and snapshot.model == 'enterprise-chat'
    assert snapshot.api_key == 'provider-key'
    assert get.call_args.kwargs['headers'] == {'Authorization': 'Bearer shared-token'}


@pytest.mark.parametrize('failure', ['partial', 'network', 'status', 'shape'])
def test_central_config_fails_closed(monkeypatch, failure):
    monkeypatch.setattr(settings, 'chat_config_base_url', 'http://ingest:8000')
    monkeypatch.setattr(settings, 'chat_config_service_token', '' if failure == 'partial' else 'token')
    if failure == 'network':
        mocked = patch('app.services.chat_config_client.httpx.get', side_effect=httpx.ConnectError('down'))
    else:
        response = Mock(status_code=503 if failure == 'status' else 200)
        response.json.return_value = {} if failure == 'shape' else {'enabled': False}
        mocked = patch('app.services.chat_config_client.httpx.get', return_value=response)
    with mocked:
        with pytest.raises(ChatConfigUnavailable):
            fetch_chat_provider()


def test_direct_chat_turn_uses_central_model_and_endpoint(monkeypatch):
    monkeypatch.setattr(
        'app.services.chat.fetch_chat_provider',
        lambda: ChatProviderSnapshot(
            enabled=True,
            base_url='https://central.example/v1',
            model='central-model',
            api_key='central-key',
            timeout_seconds=33,
            temperature=0.1,
        ),
    )
    response = Mock(status_code=200, text='')
    response.json.return_value = {
        'choices': [{'message': {'content': 'Zentrale Antwort'}}],
        'model': 'central-model',
    }
    with patch('app.services.llm.httpx.post', return_value=response) as post:
        result = client.post(
            '/internal/chat',
            headers=AUTH_HEADERS,
            json={'bot_id': 'general-assistant', 'message': 'Hallo'},
        )
    assert result.status_code == 200, result.text
    assert result.json()['answer'] == 'Zentrale Antwort'
    assert result.json()['trace']['model'] == 'central-model'
    assert post.call_args.args[0] == 'https://central.example/v1/chat/completions'
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer central-key'
    assert post.call_args.kwargs['json']['temperature'] == 0.1
