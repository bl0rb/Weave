"""Central administration and Runtime projection for n8n-backed bots."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select

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


def _team_id(team_name: str) -> str:
    """The team's id; an unknown name is passed through as an unknown id."""
    with TestingSessionLocal() as db:
        return db.scalar(select(Team.id).where(Team.name == team_name)) or team_name


def _scope(admin_client, team_name: str) -> str:
    response = admin_client.post(
        '/api/v1/collections',
        json={
            'description': 'Test purpose',
            'name': f'Bot Wissen {uuid.uuid4().hex[:6]}',
            'grants': [{'team_id': _team_id(team_name), 'role': 'reader'}],
        },
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
        'grants': [{'team_id': _team_id(team_name), 'role': 'user'}],
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
    assert projected['users'] == []
    assert projected['public'] is False
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


def test_unreachable_runtime_roster_is_logged(monkeypatch, caplog):
    """Callers like the space-deletion guard then only know Ingest's own
    bots; an operator must be able to see that."""
    from app.api.managed_bots import _runtime_bots

    monkeypatch.setattr(settings, 'runtime_api_token', 'runtime-token')
    monkeypatch.setattr(settings, 'runtime_bots_base_url', 'http://runtime.test')

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError('runtime down')

    monkeypatch.setattr('app.api.managed_bots.httpx.get', unreachable)
    with caplog.at_level('WARNING', logger='app.api.managed_bots'):
        assert _runtime_bots() == []
    assert 'Runtime bot roster unavailable' in caplog.text


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
                'permissions': {'teams': ['legal-unbekannt']},
            }],
        })(),
    )
    response = admin_client.get('/api/v1/auth/admin/bots')
    assert response.status_code == 200, response.text
    item = next(item for item in response.json()['items'] if item['id'] == 'general-assistant')
    assert item['kind'] == 'llm'
    assert item['source'] == 'runtime'
    assert item['editable'] is True
    # A team Ingest doesn't know is still shown, so the bot isn't listed as closed.
    assert item['public'] is False
    assert [(grant['team_id'], grant['name']) for grant in item['grants']] == [(None, 'legal-unbekannt')]

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
    assert 'Unbekannte Personen oder Teams' in rejected_team.text

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
    monkeypatch.setattr('app.api.managed_bots._runtime_bots', lambda *args: [runtime_bot])

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
            {'id': 'it-support', 'name': 'IT Support', 'mission': 'Answer IT questions.', 'collections': [collection_slug]}
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


def test_agent_config_rejects_what_runtime_would_drop():
    """Runtime skips a managed bot its own BotConfig rejects, so a subagent
    model without declared tool support and a subagent space that does not
    exist must fail at save time instead."""
    admin_client = login_as(_identity('bot-agent-runtime-admin', role=UserRole.ADMIN).username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    def agent(**subagent):
        return {'enabled': True, 'subagents': [{
            'id': 'vertrag', 'name': 'Vertrag', 'mission': 'Verträge.', 'collections': [collection_slug], **subagent,
        }]}

    no_tools = _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None, system_prompt='Recherchiere.',
        agent=agent(model={'provider': 'openai', 'model': 'gpt-4o'}),
    )
    response = admin_client.post('/api/v1/auth/admin/bots', json=no_tools)
    assert response.status_code == 422
    assert 'supports_tools' in response.text
    declared = {**no_tools, 'agent': agent(model={'provider': 'openai', 'model': 'gpt-4o', 'supports_tools': True})}
    assert admin_client.post('/api/v1/auth/admin/bots', json=declared).status_code == 201

    unknown_space = _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None, system_prompt='Recherchiere.',
        agent=agent(collections=['gibt-es-nicht']),
    )
    response = admin_client.post('/api/v1/auth/admin/bots', json=unknown_space)
    assert response.status_code == 422
    assert 'gibt-es-nicht' in response.text

    n8n_agent = _payload(team_name, collection_slug, kind='n8n', agent=agent())
    response = admin_client.post('/api/v1/auth/admin/bots', json=n8n_agent)
    assert response.status_code == 422
    assert 'n8n' in response.text


def test_bot_without_agent_config_projects_none():
    admin = _identity('bot-no-agent-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    payload = _payload(team_name, collection_slug)

    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['agent'] is None



def test_new_bot_leaves_out_documents_without_a_space_unless_asked(monkeypatch):
    """ADR 0008 addendum (F41): legacy documents without a space have no
    grants, so a new bot leaves them out by default; an admin can still opt
    in explicitly, and the stored choice reaches Runtime unchanged."""
    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    admin = _identity('bot-uncollected-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)

    llm = dict(kind='llm', webhook_url=None, auth_token=None, system_prompt='Nur mit belegten Quellen antworten.')
    default_bot = admin_client.post('/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, **llm))
    opted_in = admin_client.post(
        '/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, include_uncollected=True, **llm),
    )
    assert default_bot.status_code == 201, default_bot.text
    assert opted_in.status_code == 201, opted_in.text
    assert default_bot.json()['include_uncollected'] is False
    assert opted_in.json()['include_uncollected'] is True

    internal = admin_client.get('/api/v1/internal/bots', headers={'Authorization': 'Bearer runtime-control-token'})
    items = {item['id']: item for item in internal.json()['items']}
    assert items[default_bot.json()['id']]['include_uncollected'] is False
    assert items[opted_in.json()['id']]['include_uncollected'] is True


def test_llm_bot_endpoints_are_validated_and_projected(monkeypatch):
    from app.models.models import ChatProviderConfig

    admin = _identity('bot-endpoint-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    with TestingSessionLocal() as db:
        if db.get(ChatProviderConfig, 'tools-llm') is None:
            db.add(ChatProviderConfig(id='tools-llm', enabled=True, base_url='https://tools.example', model='tool-chat'))
            db.commit()
    llm = dict(kind='llm', webhook_url=None, auth_token=None, system_prompt='Antworte knapp.')

    unknown = admin_client.post('/api/v1/auth/admin/bots', json=_payload(
        team_name, collection_slug, **llm, llm_endpoint='missing-llm',
    ))
    assert unknown.status_code == 422
    assert 'missing-llm' in unknown.text
    n8n = admin_client.post('/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, llm_endpoint='tools-llm'))
    assert n8n.status_code == 422

    created = admin_client.post('/api/v1/auth/admin/bots', json=_payload(
        team_name, collection_slug, **llm, llm_endpoint='tools-llm', llm_endpoints=['default', '*', ' default '],
    ))
    assert created.status_code == 201, created.text
    assert created.json()['llm_endpoint'] == 'tools-llm'
    assert created.json()['llm_endpoints'] == ['default', '*']

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-secret')
    internal = admin_client.get('/api/v1/internal/bots', headers={'Authorization': 'Bearer runtime-secret'})
    projected = next(item for item in internal.json()['items'] if item['id'] == created.json()['id'])
    assert projected['llm_endpoint'] == 'tools-llm'
    assert projected['llm_endpoints'] == ['default', '*']
    assert admin_client.delete('/api/v1/auth/admin/chat-provider/endpoints/tools-llm').status_code == 409



def test_subagent_endpoint_must_exist_and_support_tools(monkeypatch):
    from app.models.models import ChatProviderConfig

    admin = _identity('bot-subagent-endpoint-admin', role=UserRole.ADMIN)
    admin_client = login_as(admin.username)
    team_name = _team()
    collection_slug = _scope(admin_client, team_name)
    with TestingSessionLocal() as db:
        for endpoint_id, tools in (('agent-llm', True), ('plain-llm', False)):
            if db.get(ChatProviderConfig, endpoint_id) is None:
                db.add(ChatProviderConfig(
                    id=endpoint_id, enabled=True, base_url='https://llm.example', model='m', supports_tools=tools,
                ))
        db.commit()

    def agent(endpoint):
        return {'enabled': True, 'subagents': [{
            'id': 'research', 'name': 'Research', 'mission': 'Recherchiere.', 'collections': [collection_slug],
            'endpoint': endpoint,
        }]}

    llm = dict(kind='llm', webhook_url=None, auth_token=None, system_prompt='Antworte knapp.')
    plain = admin_client.post('/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, **llm, agent=agent('plain-llm')))
    assert plain.status_code == 422
    assert 'plain-llm' in plain.text
    missing = admin_client.post('/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, **llm, agent=agent('missing-llm')))
    assert missing.status_code == 422
    created = admin_client.post('/api/v1/auth/admin/bots', json=_payload(team_name, collection_slug, **llm, agent=agent('agent-llm')))
    assert created.status_code == 201, created.text
    assert created.json()['agent']['subagents'][0]['endpoint'] == 'agent-llm'


# --- Owners and users (ADR 0008) ----------------------------------------------

def _owned_bot(admin_client, owner, collection_slug: str, **overrides) -> dict:
    payload = _payload(_team(), collection_slug, **overrides)
    payload['grants'] = [{'user_id': owner.id, 'role': 'owner'}]
    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def test_projection_names_every_granted_person_and_team(monkeypatch):
    admin_client = login_as(_identity('grant-projection-admin', role=UserRole.ADMIN).username)
    owner = _identity('grant-projection-owner')
    user = _identity('grant-projection-user')
    team_name = _team()
    payload = _payload(team_name, _scope(admin_client, team_name))
    payload['grants'] = [
        {'user_id': owner.id, 'role': 'owner'},
        {'user_id': user.id, 'role': 'user'},
        {'team_id': _team_id(team_name), 'role': 'user'},
    ]
    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert [(grant['user_id'], grant['role']) for grant in created.json()['grants'] if grant['user_id']][0] == (owner.id, 'owner')
    assert admin_client.post('/api/v1/auth/admin/bots', json={
        **payload, 'id': f'team-owner-{uuid.uuid4().hex[:6]}', 'grants': [{'team_id': _team_id(team_name), 'role': 'owner'}],
    }).status_code == 422

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = admin_client.get('/api/v1/internal/bots', headers={'Authorization': 'Bearer runtime-control-token'}).json()['items'][0]
    assert projected['teams'] == [team_name]
    assert projected['users'] == sorted([owner.id, user.id])
    assert projected['public'] is False


def test_owner_lists_and_maintains_content_and_users_but_not_the_connection(monkeypatch):
    admin_client = login_as(_identity('bot-owner-admin', role=UserRole.ADMIN).username)
    owner = _identity('bot-owner')
    colleague = _identity('bot-owner-colleague')
    team_name = _team()
    bot = _owned_bot(admin_client, owner, _scope(admin_client, team_name))

    owner_client = login_as(owner.username)
    listed = owner_client.get('/api/v1/bots')
    assert listed.status_code == 200, listed.text
    assert [item['id'] for item in listed.json()['items']] == [bot['id']]
    assert 'webhook_url' not in listed.json()['items'][0]
    assert login_as(colleague.username).get('/api/v1/bots').json()['items'] == []
    assert login_as(colleague.username).patch(f"/api/v1/bots/{bot['id']}", json={'description': 'x'}).status_code == 404

    updated = owner_client.patch(f"/api/v1/bots/{bot['id']}", json={
        'description': 'Beantwortet Fragen zum Service.',
        'public': True,
        'grants': [{'user_id': colleague.id, 'role': 'user'}],
    })
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body['description'] == 'Beantwortet Fragen zum Service.'
    assert body['public'] is True
    assert [(grant['user_id'], grant['role']) for grant in body['grants']] == [(owner.id, 'owner'), (colleague.id, 'user')]
    # Owners are set by an administrator.
    promoted = owner_client.patch(f"/api/v1/bots/{bot['id']}", json={'grants': [{'user_id': colleague.id, 'role': 'owner'}]})
    assert promoted.status_code == 422
    # A bot owner may look people up to share the bot.
    assert owner_client.get('/api/v1/directory/users').status_code == 200


def test_owner_can_only_attach_knowledge_spaces_they_can_read():
    admin_client = login_as(_identity('bot-scope-admin', role=UserRole.ADMIN).username)
    owner = _identity('bot-scope-owner')
    team_name = _team()
    attached = _scope(admin_client, team_name)
    bot = _owned_bot(admin_client, owner, attached)
    foreign = admin_client.post('/api/v1/collections', json={
        'name': f'Fremd {uuid.uuid4().hex[:8]}', 'description': 'Test purpose', 'visibility': 'restricted',
    }).json()['slug']
    own = login_as(owner.username).post('/api/v1/collections', json={'name': f'Eigen {uuid.uuid4().hex[:8]}', 'description': 'Test purpose'}).json()['slug']

    owner_client = login_as(owner.username)
    denied = owner_client.patch(f"/api/v1/bots/{bot['id']}", json={'collections': [attached, foreign]})
    assert denied.status_code == 403
    assert foreign in denied.json()['detail']
    # A space an administrator attached may stay even if the owner can't read it.
    allowed = owner_client.patch(f"/api/v1/bots/{bot['id']}", json={'collections': [attached, own]})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['collections'] == [attached, own]
    # Emptying the list would mean "every space the asker can read".
    emptied = owner_client.patch(f"/api/v1/bots/{bot['id']}", json={'collections': []})
    assert emptied.status_code == 403
    assert owner_client.get('/api/v1/bots').json()['items'][0]['collections'] == [attached, own]
