"""Central administration and Runtime projection for n8n-backed bots."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.models.models import BotTombstone, ManagedBot, Team, UserRole
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _clean_managed_bots():
    rate_limiter.reset()
    with TestingSessionLocal() as db:
        db.execute(delete(ManagedBot))
        db.execute(delete(BotTombstone))
        db.commit()
    yield
    with TestingSessionLocal() as db:
        db.execute(delete(ManagedBot))
        db.execute(delete(BotTombstone))
        db.commit()


def _identity(prefix: str, *, role: UserRole = UserRole.USER):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(
        username=f'{prefix}-{suffix}',
        email=f'{prefix}-{suffix}@example.com',
        role=role,
    )


def _team() -> str:
    name = f'Bot-Team-{uuid.uuid4().hex[:8]}'
    with TestingSessionLocal() as db:
        db.add(Team(name=name))
        db.commit()
    return name


def _scope(admin_client, team_name: str) -> str:
    response = admin_client.post(
        '/api/v1/collections',
        json={'name': f'Bot Wissen {uuid.uuid4().hex[:6]}', 'read_teams': [team_name]},
    )
    assert response.status_code == 200, response.text
    return response.json()['slug']


def _payload(team_name: str, collection_slug: str, **overrides):
    payload = {
        'id': f'service-bot-{uuid.uuid4().hex[:8]}',
        'name': 'Service-Assistent',
        'description': 'Antwortet über einen n8n-Workflow.',
        'enabled': True,
        'webhook_url': 'https://n8n.example.com/webhook/weave-service',
        'streaming': False,
        'auth_token': 'webhook-bearer-secret',
        'timeout_seconds': 90,
        'teams': [team_name],
        'collections': [collection_slug],
        'require_sources': True,
        'no_context_reply': 'Dazu liegen keine belegten Informationen vor.',
    }
    payload.update(overrides)
    return payload


def test_admin_crud_is_redacted_and_runtime_projection_requires_service_token(monkeypatch):
    admin = _identity('bot-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)

    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['id'] == payload['id']
    assert created.json()['has_auth_token'] is True
    assert 'auth_token' not in created.json()
    assert 'webhook-bearer-secret' not in created.text

    listed = admin_client.get('/api/v1/auth/admin/bots')
    assert listed.status_code == 200
    assert [item['id'] for item in listed.json()['items']] == [payload['id']]
    assert 'webhook-bearer-secret' not in listed.text

    with TestingSessionLocal() as db:
        row = db.get(ManagedBot, payload['id'])
        assert row is not None
        assert row.auth_token_encrypted != 'webhook-bearer-secret'
        assert row.updated_by_id == admin.id

    monkeypatch.setattr(settings, 'chat_config_service_token', '')
    assert admin_client.get('/api/v1/internal/bots').status_code == 503
    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    assert admin_client.get('/api/v1/internal/bots', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    internal = admin_client.get(
        '/api/v1/internal/bots',
        headers={'Authorization': 'Bearer runtime-control-token'},
    )
    assert internal.status_code == 200, internal.text
    assert internal.headers['cache-control'] == 'no-store'
    projected = internal.json()['items'][0]
    assert projected['id'] == payload['id']
    assert projected['auth_token'] == 'webhook-bearer-secret'
    assert projected['teams'] == [team_name]
    assert projected['collections'] == [collection_slug]

    update_body = {key: value for key, value in payload.items() if key != 'id'}
    update_body.update({'name': 'Service-Bot 2', 'auth_token': None, 'clear_auth_token': False})
    updated = admin_client.put(f"/api/v1/auth/admin/bots/{payload['id']}", json=update_body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['name'] == 'Service-Bot 2'
    assert updated.json()['has_auth_token'] is True

    deleted = admin_client.delete(f"/api/v1/auth/admin/bots/{payload['id']}")
    assert deleted.status_code == 200
    assert admin_client.get('/api/v1/auth/admin/bots').json()['items'] == []


def test_admin_bot_list_projects_nonempty_runtime_roster(monkeypatch):
    admin = _identity('runtime-list-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    monkeypatch.setattr(settings, 'runtime_api_token', 'runtime-token')
    monkeypatch.setattr(settings, 'runtime_bots_base_url', 'http://runtime.test')
    monkeypatch.setattr(
        'app.api.managed_bots.httpx.get',
        lambda *args, **kwargs: type('Response', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: [{
                'id': 'general-assistant', 'name': 'Allgemeiner Assistent',
                'description': 'YAML bot', 'kind': 'llm',
                'retrieval': {'enabled': True}, 'teams': [], 'collections': [],
            }],
        })(),
    )
    response = admin_client.get('/api/v1/auth/admin/bots')
    assert response.status_code == 200, response.text
    item = next(item for item in response.json()['items'] if item['id'] == 'general-assistant')
    assert item['kind'] == 'llm'
    assert item['source'] == 'runtime'
    assert item['editable'] is True

def test_non_admin_cannot_manage_bots_and_unknown_scope_is_rejected():
    admin = _identity('bot-reference-admin', role=UserRole.ADMIN)
    regular = _identity('bot-reference-user')
    admin_client = login_as(admin.username)
    regular_client = login_as(regular.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)

    assert regular_client.get('/api/v1/auth/admin/bots').status_code == 403
    assert regular_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 403

    bad_team = _payload('Unbekanntes Team', collection_slug)
    rejected_team = admin_client.post('/api/v1/auth/admin/bots', json=bad_team)
    assert rejected_team.status_code == 422
    assert 'Unbekannte Teams' in rejected_team.text

    bad_collection = _payload(team_name, 'nicht-vorhanden')
    rejected_collection = admin_client.post('/api/v1/auth/admin/bots', json=bad_collection)
    assert rejected_collection.status_code == 422
    assert 'Unbekannte Wissensbereiche' in rejected_collection.text


def test_webhook_change_without_a_new_token_clears_the_old_credential():
    admin = _identity('bot-move-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201

    update_body = {key: value for key, value in payload.items() if key != 'id'}
    update_body.update({
        'webhook_url': 'https://n8n.example.com/webhook/weave-service-v2',
        'auth_token': None,
        'clear_auth_token': False,
    })
    moved = admin_client.put(f"/api/v1/auth/admin/bots/{payload['id']}", json=update_body)
    assert moved.status_code == 200, moved.text
    assert moved.json()['has_auth_token'] is False


def test_internal_projection_omits_disabled_bots(monkeypatch):
    admin = _identity('bot-disabled-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug, enabled=False, auth_token=None)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    internal = admin_client.get(
        '/api/v1/internal/bots',
        headers={'Authorization': 'Bearer runtime-control-token'},
    )
    assert internal.status_code == 200
    assert internal.json()['items'] == []
    assert payload['id'] in internal.json()['disabled_ids']


def test_admin_roster_with_eight_bots_uses_one_runtime_request(monkeypatch):
    admin_client = login_as(_identity('many-bots-admin', role=UserRole.ADMIN).username)
    monkeypatch.setattr(settings, 'runtime_api_token', 'runtime-token')
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: [
            {'id': f'sample-{index}', 'name': f'Sample {index}', 'system_prompt': 'Help.',
             'model': {'provider': 'fake'}, 'retrieval': {'enabled': False}}
            for index in range(8)
        ])
    monkeypatch.setattr('app.api.managed_bots.httpx.get', get)
    response = admin_client.get('/api/v1/auth/admin/bots')
    assert response.status_code == 200, response.text
    assert len(response.json()['items']) == 8
    assert len(calls) == 1
    assert calls[0].endswith('/internal/bot-configs')


def test_deleting_a_runtime_bot_persists_suppression_and_hides_it(monkeypatch):
    admin = _identity('runtime-delete-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    runtime_bot = SimpleNamespace(id='general-assistant')
    monkeypatch.setattr('app.api.managed_bots._runtime_bots', lambda: [runtime_bot])

    deleted = admin_client.delete('/api/v1/auth/admin/bots/general-assistant')
    assert deleted.status_code == 200, deleted.text

    listed = admin_client.get('/api/v1/auth/admin/bots')
    assert listed.status_code == 200
    assert listed.json()['items'] == []
    with TestingSessionLocal() as db:
        tombstone = db.get(BotTombstone, 'general-assistant')
        assert tombstone is not None
        assert tombstone.deleted_by_id == admin.id

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    internal = admin_client.get(
        '/api/v1/internal/bots',
        headers={'Authorization': 'Bearer runtime-control-token'},
    )
    assert internal.status_code == 200
    assert internal.json()['items'] == []
    assert internal.json()['disabled_ids'] == ['general-assistant']


def test_recreating_a_deleted_bot_clears_runtime_suppression(monkeypatch):
    admin = _identity('runtime-recreate-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)

    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert admin_client.delete(f"/api/v1/auth/admin/bots/{payload['id']}").status_code == 200
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201

    with TestingSessionLocal() as db:
        assert db.get(BotTombstone, payload['id']) is None
    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    internal = admin_client.get(
        '/api/v1/internal/bots',
        headers={'Authorization': 'Bearer runtime-control-token'},
    )
    assert payload['id'] not in internal.json()['disabled_ids']


@pytest.mark.parametrize('url', [
    'ftp://n8n.example.com/webhook/agent',
    'https://user:password@n8n.example.com/webhook/agent',
    'https://n8n.example.com:invalid/webhook/agent',
    'https://n8n.example.com/webhook/agent#ignored-fragment',
    'https://evil.example\\@n8n.example.com/webhook/agent',
])
def test_managed_bot_rejects_ambiguous_or_non_http_webhook_urls(url):
    admin = _identity('bot-url-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    response = admin_client.post(
        '/api/v1/auth/admin/bots',
        json=_payload(team_name, collection_slug, webhook_url=url, auth_token=None),
    )
    assert response.status_code == 422


def test_llm_bot_without_a_webhook_is_created_instead_of_crashing_the_validator():
    """A missing webhook_url used to raise TypeError inside the field
    validator, and the resulting 500 bypassed CORS -- the browser only ever
    saw "Failed to fetch"."""
    admin = _identity('bot-llm-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    response = admin_client.post(
        '/api/v1/auth/admin/bots',
        json=_payload(
            team_name,
            collection_slug,
            kind='llm',
            webhook_url=None,
            auth_token=None,
            system_prompt='Du antwortest nur mit belegten Quellen.',
        ),
    )

    assert response.status_code == 201, response.text
    assert response.json()['kind'] == 'llm'
    assert response.json()['webhook_url'] is None


def test_n8n_bot_still_requires_a_webhook():
    admin = _identity('bot-n8n-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    response = admin_client.post(
        '/api/v1/auth/admin/bots',
        json=_payload(team_name, collection_slug, webhook_url=None, auth_token=None),
    )

    assert response.status_code == 422


def test_agent_config_round_trips_through_admin_and_internal_projections(monkeypatch):
    """The `agent` block (Weave-Runtime's `BotConfig.agent`) is validated on
    save via `AgentConfig` (rollout plan "Schritt 4 -- Administration und
    Streaming", a field-for-field mirror of Runtime's own model, see
    schemas/managed_bots.py) -- create it, see the CANONICAL (defaults
    filled in) shape echoed back on the admin response, update it, and see
    the update echoed on both the admin AND the internal (Runtime-facing)
    projection."""
    from app.schemas.managed_bots import AgentConfig

    admin = _identity('bot-agent-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    agent_config = {
        'enabled': True,
        'subagents': [
            {'id': 'it-support', 'name': 'IT Support', 'mission': 'Answer IT questions.', 'collections': ['it-docs']}
        ],
    }
    # What the API actually stores/echoes -- the minimal input above, with
    # every Runtime-mirrored default (limits, filters, tools, ...) filled
    # in explicitly, exactly like Runtime's own `AgentConfig.model_dump()`
    # would for the identical input.
    canonical_agent_config = AgentConfig(**agent_config).model_dump()

    payload = _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None,
        system_prompt='Du recherchierst zuerst.', agent=agent_config,
    )

    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['agent'] == canonical_agent_config

    with TestingSessionLocal() as db:
        row = db.get(ManagedBot, payload['id'])
        assert row.agent_config == canonical_agent_config

    updated_agent_config = {**agent_config, 'limits': {'budget_searches': 5}}
    canonical_updated_agent_config = AgentConfig(**updated_agent_config).model_dump()
    update_body = {key: value for key, value in payload.items() if key != 'id'}
    update_body['agent'] = updated_agent_config
    updated = admin_client.put(f"/api/v1/auth/admin/bots/{payload['id']}", json=update_body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['agent'] == canonical_updated_agent_config

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    internal = admin_client.get(
        '/api/v1/internal/bots',
        headers={'Authorization': 'Bearer runtime-control-token'},
    )
    assert internal.status_code == 200, internal.text
    projected = internal.json()['items'][0]
    assert projected['id'] == payload['id']
    assert projected['agent'] == canonical_updated_agent_config


def test_agent_config_rejects_invalid_shape_with_readable_422(monkeypatch):
    """An admin-saved `agent` block that violates one of Runtime's own
    `AgentConfig` rules (here: `enabled: true` with an empty `subagents`
    list) must fail at SAVE time with a readable 422, not silently persist
    and only fail the next time Weave-Runtime loads this bot."""
    admin = _identity('bot-agent-invalid-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    payload = _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None,
        system_prompt='Du recherchierst zuerst.', agent={'enabled': True, 'subagents': []},
    )

    response = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert response.status_code == 422
    assert 'subagents' in response.text.lower()

    with TestingSessionLocal() as db:
        assert db.get(ManagedBot, payload['id']) is None


def test_agent_config_rejects_duplicate_subagent_ids_with_readable_422():
    admin = _identity('bot-agent-dupe-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    payload = _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None,
        system_prompt='Du recherchierst zuerst.',
        agent={
            'enabled': True,
            'subagents': [
                {'id': 'it-support', 'name': 'IT Support', 'mission': 'IT.', 'collections': ['it-docs']},
                {'id': 'it-support', 'name': 'IT Support 2', 'mission': 'IT.', 'collections': ['it-docs']},
            ],
        },
    )

    response = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert response.status_code == 422
    assert 'unique' in response.text.lower()


def test_bot_without_agent_config_projects_none():
    admin = _identity('bot-no-agent-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)

    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['agent'] is None
