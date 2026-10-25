"""Export/import of the portable bot document (contracts/bot-document.md)
on the admin bot API: `GET /api/v1/auth/admin/bots/{id}/export` and
`POST /api/v1/auth/admin/bots/import`. Helpers and the managed-bot cleanup
fixture are shared with test_managed_bots_api.py."""

import json
import uuid

import yaml

from app.core.config import settings
from app.models.models import ManagedBot, UserRole
from app.services.bot_document import row_to_document
from conftest import TestingSessionLocal, login_as
from test_managed_bots_api import _clean_managed_bots, _identity, _payload, _scope, _team  # noqa: F401

INTERNAL = {'Authorization': 'Bearer runtime-control-token'}


def _import(client, content: bytes, *, dry_run=True, on_conflict='fail', bot_id=None, filename='bot.bot.yaml'):
    data = {'dry_run': 'true' if dry_run else 'false', 'on_conflict': on_conflict}
    if bot_id is not None:
        data['bot_id'] = bot_id
    return client.post('/api/v1/auth/admin/bots/import', files={'file': (filename, content, 'application/yaml')}, data=data)


def _setup(prefix: str):
    admin_client = login_as(_identity(prefix, role=UserRole.ADMIN).username)
    team_name = _team()
    return admin_client, team_name, _scope(admin_client, team_name)


def _llm_payload(team_name, collection_slug, **overrides):
    return _payload(
        team_name, collection_slug, kind='llm', webhook_url=None, auth_token=None, streaming=False,
        system_prompt='Antworte nur mit Belegen.', retrieval_enabled=True, temperature=0.3,
        min_score=0.2, query_rewrite=True,
        config_extras={'clarify_max_options': 5, 'clarify_question': 'Welches Dokument meinst du?'},
        agent={'enabled': True, 'subagents': [
            {'id': 'vertrag', 'name': 'Vertrag', 'mission': 'Verträge.', 'collections': [collection_slug]},
        ]},
        **overrides,
    )


def test_export_n8n_bot_as_yaml_and_json_hides_secret_and_grants():
    admin_client, team_name, collection_slug = _setup('export-admin')
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201

    response = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export?format=yaml")
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/yaml')
    assert f'filename="{payload["id"]}.bot.yaml"' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    assert 'webhook-bearer-secret' not in response.text
    doc = yaml.safe_load(response.text)
    assert doc['schema_version'] == 1
    assert doc['id'] == payload['id']
    assert doc['model'] == {'provider': 'n8n', 'model': 'n8n-agent-flow', 'endpoints': []}
    assert doc['n8n']['auth_token'] == '${WEAVE_SECRET}'
    assert doc['n8n']['webhook_url'] == payload['webhook_url']
    assert doc['n8n']['timeout_seconds'] == 90
    assert doc['permissions'] == {'public': False}
    assert doc['retrieval']['collections'] == [collection_slug]
    assert doc['guard']['clarify_max_options'] == 3  # Runtime default, written out in full

    as_json = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export?format=json")
    assert as_json.status_code == 200
    assert as_json.headers['content-type'].startswith('application/json')
    assert f'filename="{payload["id"]}.bot.json"' in as_json.headers['content-disposition']
    assert json.loads(as_json.text) == doc
    assert admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export?format=xml").status_code == 422
    assert admin_client.get('/api/v1/auth/admin/bots/gibt-es-nicht/export').status_code == 404


def test_export_dry_run_apply_round_trips_an_llm_bot(monkeypatch):
    admin_client, team_name, collection_slug = _setup('roundtrip-admin')
    payload = _llm_payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201, payload
    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content
    assert yaml.safe_load(exported)['guard']['clarify_max_options'] == 5

    dry = _import(admin_client, exported, bot_id='kopie-des-bots')
    assert dry.status_code == 200, dry.text
    report = dry.json()
    assert report['action'] == 'create' and report['conflict'] is None and report['applied'] is False
    assert report['errors'] == []
    assert report['bot_id'] == 'kopie-des-bots' and report['kind'] == 'llm' and report['schema_version'] == 1
    assert [item['code'] for item in report['warnings']] == ['permissions_reset']
    assert admin_client.get('/api/v1/auth/admin/bots/kopie-des-bots/export').status_code == 404

    applied = _import(admin_client, exported, dry_run=False, bot_id='kopie-des-bots')
    assert applied.status_code == 200, applied.text
    assert applied.json()['applied'] is True
    assert applied.json()['bot']['id'] == 'kopie-des-bots'
    assert applied.json()['bot']['grants'] == []
    assert applied.json()['bot']['config_extras'] == {'clarify_max_options': 5, 'clarify_question': 'Welches Dokument meinst du?'}

    with TestingSessionLocal() as db:
        original = row_to_document(db.get(ManagedBot, payload['id'])).model_dump()
        copy = row_to_document(db.get(ManagedBot, 'kopie-des-bots')).model_dump()
    assert copy == {**original, 'id': 'kopie-des-bots'}

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = {item['id']: item for item in admin_client.get('/api/v1/internal/bots', headers=INTERNAL).json()['items']}
    assert projected['kopie-des-bots']['clarify_max_options'] == 5
    assert projected['kopie-des-bots']['clarify_question'] == 'Welches Dokument meinst du?'
    assert projected['kopie-des-bots']['clarify_ambiguous_margin'] is None
    assert projected['kopie-des-bots']['agent']['subagents'][0]['id'] == 'vertrag'


def test_conflict_fails_with_409_and_overwrite_keeps_the_stored_token(monkeypatch):
    admin_client, team_name, collection_slug = _setup('conflict-admin')
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201
    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content

    dry = _import(admin_client, exported)
    assert dry.status_code == 200
    assert dry.json()['action'] == 'blocked' and dry.json()['conflict'] == 'managed'
    assert any('existiert bereits' in error for error in dry.json()['errors'])

    blocked = _import(admin_client, exported, dry_run=False)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['action'] == 'blocked' and blocked.json()['applied'] is False

    renamed = yaml.safe_load(exported)
    renamed['name'] = 'Service-Assistent (importiert)'
    overwritten = _import(admin_client, yaml.safe_dump(renamed).encode(), dry_run=False, on_conflict='overwrite')
    assert overwritten.status_code == 200, overwritten.text
    report = overwritten.json()
    assert report['action'] == 'overwrite' and report['applied'] is True
    assert report['bot']['name'] == 'Service-Assistent (importiert)'
    assert report['bot']['has_auth_token'] is True
    secret = next(item for item in report['warnings'] if item['code'] == 'missing_secret')
    assert 'bleibt erhalten' in secret['detail']

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = admin_client.get('/api/v1/internal/bots', headers=INTERNAL).json()['items'][0]
    assert projected['auth_token'] == 'webhook-bearer-secret'
    assert projected['name'] == 'Service-Assistent (importiert)'


def test_unknown_collection_and_unknown_schema_version_block_the_import():
    admin_client, team_name, collection_slug = _setup('blocked-admin')
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201
    doc = yaml.safe_load(admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content)

    unknown = {**doc, 'id': 'neuer-bot', 'retrieval': {**doc['retrieval'], 'collections': ['gibt-es-nicht']}}
    dry = _import(admin_client, yaml.safe_dump(unknown).encode())
    assert dry.status_code == 200
    assert dry.json()['action'] == 'blocked'
    assert any('Unbekannte Wissensbereiche: gibt-es-nicht' in error for error in dry.json()['errors'])
    assert _import(admin_client, yaml.safe_dump(unknown).encode(), dry_run=False).status_code == 422

    future = _import(admin_client, yaml.safe_dump({**doc, 'id': 'neuer-bot', 'schema_version': 2}).encode())
    assert future.json()['action'] == 'blocked'
    assert 'schema_version' in future.json()['errors'][0]

    malformed = _import(admin_client, b'- nur: eine Liste', filename='liste.yaml')
    assert malformed.json()['action'] == 'blocked' and 'liste.yaml' in malformed.json()['errors'][0]

    stray = _import(admin_client, yaml.safe_dump({**doc, 'id': 'neuer-bot', 'unbekannt': 1}).encode())
    assert stray.json()['action'] == 'blocked' and 'unbekannt' in stray.json()['errors'][0]
    assert admin_client.get('/api/v1/auth/admin/bots').json()['items'][0]['id'] == payload['id']


def test_runtime_yaml_bot_is_exportable_and_counts_as_conflict(monkeypatch):
    admin_client, _team_name, collection_slug = _setup('runtime-export-admin')
    monkeypatch.setattr(settings, 'runtime_api_token', 'runtime-token')
    monkeypatch.setattr(settings, 'runtime_bots_base_url', 'http://runtime.test')
    runtime_bot = {
        'schema_version': 1, 'id': 'legal-support', 'name': 'Legal Support', 'description': None,
        'model': {'provider': 'n8n', 'model': 'n8n-agent-flow', 'temperature': None, 'supports_tools': None,
                  'endpoint': None, 'endpoints': []},
        'system_prompt': 'Du führst Anfragen über den konfigurierten n8n-Workflow aus.',
        'retrieval': {'enabled': False, 'filters': {}, 'top_k': 20, 'final_k': 5, 'rerank': True,
                      'query_rewrite': None, 'collections': [collection_slug], 'include_uncollected': True},
        'permissions': {'teams': ['legal', 'management'], 'users': ['user-1'], 'public': None},
        'guard': {'require_sources': True, 'no_context_reply': 'Keine Belege.'},
        'n8n': {'webhook_url': 'https://n8n.example.test/webhook/legal', 'auth_token': '**********',
                'streaming': False, 'timeout_seconds': 120},
        'agent': None,
    }
    monkeypatch.setattr(
        'app.api.managed_bots.httpx.get',
        lambda *args, **kwargs: type('Response', (), {
            'status_code': 200, 'raise_for_status': lambda self: None, 'json': lambda self: [runtime_bot],
        })(),
    )
    response = admin_client.get('/api/v1/auth/admin/bots/legal-support/export')
    assert response.status_code == 200, response.text
    doc = yaml.safe_load(response.text)
    assert doc['n8n']['auth_token'] == '${WEAVE_SECRET}'
    assert doc['permissions'] == {}
    assert '**********' not in response.text and 'legal' not in str(doc['permissions'])

    dry = _import(admin_client, response.content)
    assert dry.status_code == 200
    assert dry.json()['conflict'] == 'runtime' and dry.json()['action'] == 'blocked'
    overwrite = _import(admin_client, response.content, on_conflict='overwrite')
    assert overwrite.json()['action'] == 'overwrite' and overwrite.json()['errors'] == []
    # A hand-written YAML bot with teams but no `public` is closed in Runtime; it stays closed here.
    secret = next(item for item in overwrite.json()['warnings'] if item['code'] == 'missing_secret')
    assert 'hinterlegt werden' in secret['detail']
    assert any(item['code'] == 'permissions_reset' for item in overwrite.json()['warnings'])


def test_config_extras_are_optional_and_round_trip_through_the_admin_api(monkeypatch):
    admin_client, team_name, collection_slug = _setup('extras-admin')
    plain = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=plain).status_code == 201
    created = admin_client.post('/api/v1/auth/admin/bots', json=_llm_payload(team_name, collection_slug))
    assert created.status_code == 201, created.text
    assert created.json()['config_extras'] == {'clarify_max_options': 5, 'clarify_question': 'Welches Dokument meinst du?'}
    listed = {item['id']: item for item in admin_client.get('/api/v1/auth/admin/bots').json()['items']}
    assert listed[plain['id']]['config_extras'] is None

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = {item['id']: item for item in admin_client.get('/api/v1/internal/bots', headers=INTERNAL).json()['items']}
    assert projected[created.json()['id']]['clarify_max_options'] == 5
    assert projected[plain['id']]['clarify_max_options'] is None
    assert projected[plain['id']]['clarify_question'] is None

    update_body = {key: value for key, value in _llm_payload(team_name, collection_slug).items() if key != 'id'}
    update_body['config_extras'] = {'clarify_max_options': 9}
    assert admin_client.put(f"/api/v1/auth/admin/bots/{created.json()['id']}", json=update_body).status_code == 422
    update_body['config_extras'] = None
    updated = admin_client.put(f"/api/v1/auth/admin/bots/{created.json()['id']}", json=update_body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['config_extras'] is None


def test_chat_options_project_to_runtime_and_round_trip_through_the_bot_document(monkeypatch):
    admin_client, team_name, collection_slug = _setup('chat-options-admin')
    plain = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=plain).status_code == 201
    payload = _llm_payload(team_name, collection_slug, show_sources=False, disclaimer='  Testsystem – keine verbindlichen Aussagen ')
    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['show_sources'] is False
    assert created.json()['disclaimer'] == 'Testsystem – keine verbindlichen Aussagen'

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = {item['id']: item for item in admin_client.get('/api/v1/internal/bots', headers=INTERNAL).json()['items']}
    assert projected[payload['id']]['show_sources'] is False
    assert projected[payload['id']]['disclaimer'] == 'Testsystem – keine verbindlichen Aussagen'
    assert projected[plain['id']]['show_sources'] is True
    assert projected[plain['id']]['disclaimer'] is None

    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content
    doc = yaml.safe_load(exported)
    assert doc['show_sources'] is False
    assert doc['disclaimer'] == 'Testsystem – keine verbindlichen Aussagen'
    applied = _import(admin_client, exported, dry_run=False, bot_id='kopie-mit-hinweis')
    assert applied.status_code == 200, applied.text
    assert applied.json()['bot']['show_sources'] is False
    assert applied.json()['bot']['disclaimer'] == 'Testsystem – keine verbindlichen Aussagen'

    update_body = {key: value for key, value in payload.items() if key != 'id'}
    update_body['disclaimer'] = 'x' * 501
    assert admin_client.put(f"/api/v1/auth/admin/bots/{payload['id']}", json=update_body).status_code == 422
    update_body.update(show_sources=True, disclaimer='   ')
    updated = admin_client.put(f"/api/v1/auth/admin/bots/{payload['id']}", json=update_body)
    assert updated.status_code == 200, updated.text
    assert updated.json()['show_sources'] is True and updated.json()['disclaimer'] is None


_INTAKE = {
    'question': 'Um welche Beschäftigungsart geht es?', 'options': ['Vollzeit', 'Teilzeit'],
    'allow_free_text': False, 'label': 'Beschäftigungsart',
}
_TOPICS = [{'name': 'Politische Themen', 'description': 'Parteien, Wahlen', 'keywords': ['partei']}]


def test_intake_and_topic_block_project_to_runtime_and_round_trip_through_the_bot_document(monkeypatch):
    admin_client, team_name, collection_slug = _setup('intake-admin')
    plain = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=plain).status_code == 201
    payload = _llm_payload(
        team_name, collection_slug, intake=_INTAKE, blocked_topics=_TOPICS, blocked_topic_reply='  Dazu nichts. ',
    )
    created = admin_client.post('/api/v1/auth/admin/bots', json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['intake'] == _INTAKE
    assert created.json()['blocked_topics'] == _TOPICS
    assert created.json()['blocked_topic_reply'] == 'Dazu nichts.'

    monkeypatch.setattr(settings, 'chat_config_service_token', 'runtime-control-token')
    projected = {item['id']: item for item in admin_client.get('/api/v1/internal/bots', headers=INTERNAL).json()['items']}
    assert projected[payload['id']]['intake'] == _INTAKE
    assert projected[payload['id']]['blocked_topics'] == _TOPICS
    assert projected[payload['id']]['blocked_topic_reply'] == 'Dazu nichts.'
    assert projected[plain['id']]['intake'] is None and projected[plain['id']]['blocked_topics'] == []

    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content
    doc = yaml.safe_load(exported)
    assert doc['intake'] == _INTAKE
    assert doc['guard']['blocked_topics'] == _TOPICS and doc['guard']['blocked_topic_reply'] == 'Dazu nichts.'
    applied = _import(admin_client, exported, dry_run=False, bot_id='kopie-mit-intake')
    assert applied.status_code == 200, applied.text
    assert applied.json()['bot']['intake'] == _INTAKE
    assert applied.json()['bot']['blocked_topics'] == _TOPICS

    plain_doc = yaml.safe_load(admin_client.get(f"/api/v1/auth/admin/bots/{plain['id']}/export").content)
    assert plain_doc.get('intake') is None and plain_doc['guard'].get('blocked_topics', []) == []

    assert admin_client.post('/api/v1/auth/admin/bots', json={**plain, 'id': 'n8n-mit-intake', 'intake': _INTAKE}).status_code == 422
    invalid = {**_INTAKE, 'options': [], 'allow_free_text': False}
    assert admin_client.post('/api/v1/auth/admin/bots', json={**payload, 'id': 'kaputt', 'intake': invalid}).status_code == 422


def test_import_without_dry_run_field_only_reports_and_creates_nothing():
    admin_client, team_name, collection_slug = _setup('nodryrun-admin')
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201
    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content
    for data in ({'bot_id': 'ohne-dry-run'}, {'bot_id': 'ohne-dry-run', 'dry_run': ''}):
        response = admin_client.post(
            '/api/v1/auth/admin/bots/import', files={'file': ('bot.bot.yaml', exported, 'application/yaml')}, data=data,
        )
        assert response.status_code == 200, response.text
        assert response.json()['action'] == 'create' and response.json()['applied'] is False
    with TestingSessionLocal() as db:
        assert db.get(ManagedBot, 'ohne-dry-run') is None


def test_bot_id_override_must_be_a_slug_and_unique_ids_are_normalised():
    admin_client, team_name, collection_slug = _setup('override-admin')
    payload = _payload(team_name, collection_slug)
    assert admin_client.post('/api/v1/auth/admin/bots', json=payload).status_code == 201
    exported = admin_client.get(f"/api/v1/auth/admin/bots/{payload['id']}/export").content
    bad = _import(admin_client, exported, bot_id='Nicht Gültig')
    assert bad.json()['action'] == 'blocked' and 'bot_id' in bad.json()['errors'][0]
    upper = _import(admin_client, exported, bot_id='  Neuer-Bot ')
    assert upper.json()['bot_id'] == 'neuer-bot' and upper.json()['action'] == 'create'
    assert uuid.UUID is not None  # keeps the shared import pattern explicit
