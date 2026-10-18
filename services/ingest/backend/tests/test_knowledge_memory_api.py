"""Administration › KI › Wissensgedächtnis (ADR-0009): settings, the
internal projections for Knowledge and Runtime, and the metrics/rebuild
proxies to Knowledge (mocked at httpx)."""

from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy import delete

from app.core.config import settings
from app.models.models import KnowledgeMemoryConfig, UserRole
from conftest import TestingSessionLocal, create_test_user, login_as

PATH = '/api/v1/auth/admin/knowledge-memory'
INTERNAL = {'Authorization': 'Bearer knowledge-secret'}


def _clear() -> None:
    with TestingSessionLocal() as db:
        db.execute(delete(KnowledgeMemoryConfig))
        db.commit()


def _admin(prefix: str):
    return login_as(create_test_user(username=prefix, email=f'{prefix}@example.com', role=UserRole.ADMIN).username)


def test_settings_start_unmanaged_and_are_saved(monkeypatch):
    _clear()
    admin = _admin('memory-admin')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    initial = admin.get(PATH).json()
    assert (initial['mode'], initial['managed']) == ('off', False)
    assert admin.get('/api/v1/internal/knowledge-memory', headers=INTERNAL).json()['managed'] is False
    assert admin.get('/api/v1/internal/chat-provider', headers=INTERNAL).json()['knowledge_memory_mode'] is None

    saved = admin.put(PATH, json={'mode': 'llm', 'llm_endpoint': 'default', 'debounce_seconds': 60,
                                  'max_documents_per_run': 10, 'max_input_chars': 8000})
    assert saved.status_code == 200, saved.text
    assert saved.json()['managed'] is True

    internal = admin.get('/api/v1/internal/knowledge-memory', headers=INTERNAL)
    assert internal.headers['cache-control'] == 'no-store'
    assert internal.json() == {'mode': 'llm', 'llm_endpoint': 'default', 'debounce_seconds': 60,
                               'max_documents_per_run': 10, 'max_input_chars': 8000, 'managed': True}
    # Runtime gets the mode with the chat-provider snapshot it already fetches per turn.
    assert admin.get('/api/v1/internal/chat-provider', headers=INTERNAL).json()['knowledge_memory_mode'] == 'llm'


def test_deleting_the_saving_admin_keeps_the_settings_managed(monkeypatch):
    _clear()
    admin = _admin('memory-gone-admin')
    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    admin.put(PATH, json={'mode': 'llm', 'llm_endpoint': 'default', 'debounce_seconds': 60,
                          'max_documents_per_run': 10, 'max_input_chars': 8000})
    with TestingSessionLocal() as db:  # what ON DELETE SET NULL leaves behind
        db.get(KnowledgeMemoryConfig, 'default').updated_by_id = None
        db.commit()
    assert admin.get(PATH).json()['managed'] is True
    assert admin.get('/api/v1/internal/knowledge-memory', headers=INTERNAL).json()['mode'] == 'llm'
    assert admin.get('/api/v1/internal/chat-provider', headers=INTERNAL).json()['knowledge_memory_mode'] == 'llm'


def test_invalid_settings_are_refused():
    _clear()
    admin = _admin('memory-invalid-admin')
    assert admin.put(PATH, json={'mode': 'immer'}).status_code == 422
    assert admin.put(PATH, json={'mode': 'llm', 'llm_endpoint': 'gibt-es-nicht'}).status_code == 422
    assert admin.put(PATH, json={'mode': 'llm', 'max_documents_per_run': 0}).status_code == 422


def test_only_admins_and_the_service_token_get_in(monkeypatch):
    _clear()
    editor = login_as(create_test_user(username='memory-editor', email='memory-editor@example.com', role=UserRole.BOT_ADMIN).username)
    assert editor.get(PATH).status_code == 403
    assert editor.get('/api/v1/admin/knowledge-memory/status').status_code == 403
    monkeypatch.setattr(settings, 'chat_config_service_token', 'knowledge-secret')
    assert editor.get('/api/v1/internal/knowledge-memory', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_metrics_and_rebuild_are_proxied_to_knowledge(monkeypatch):
    admin = _admin('memory-metrics-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')
    assert admin.get('/api/v1/admin/knowledge-memory/status').status_code == 503

    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'http://knowledge.local')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'hook-secret')
    status = MagicMock(status_code=200)
    status.json.return_value = {'mode': 'extractive', 'documents': 3, 'summarized': 2, 'collections': []}
    with patch('app.api.knowledge_memory.httpx.request', return_value=status) as request:
        assert admin.get('/api/v1/admin/knowledge-memory/status').json()['summarized'] == 2
    method, url = request.call_args.args
    assert (method, url) == ('GET', 'http://knowledge.local/api/v1/internal/memory/status')
    assert request.call_args.kwargs['headers'] == {'X-Weave-Reindex-Token': 'hook-secret'}

    queued = MagicMock(status_code=200)
    queued.json.return_value = {'queued': 4}
    with patch('app.api.knowledge_memory.httpx.request', return_value=queued) as request:
        assert admin.post('/api/v1/admin/knowledge-memory/rebuild', json={'collection': 'personal'}).json() == {'queued': 4}
    assert request.call_args.kwargs['json'] == {'collection': 'personal'}

    with patch('app.api.knowledge_memory.httpx.request', side_effect=httpx.ConnectError('down')):
        assert admin.post('/api/v1/admin/knowledge-memory/rebuild', json={}).status_code == 503
