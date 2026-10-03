"""Deleting and removing things under ADR 0008: people, teams, documents
and whole knowledge spaces."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import routes
from app.models.models import (
    BotGrant,
    BotRole,
    BotTombstone,
    Collection,
    DocumentRelease,
    Job,
    JobStatus,
    KnowledgeWithdrawal,
    ManagedBot,
    TechnicalIdentity,
    Team,
    UserRole,
    user_teams,
)
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    rate_limiter.reset()
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    dispatched: list[str] = []
    monkeypatch.setattr(routes, 'dispatch_withdrawals', dispatched.extend)
    monkeypatch.setattr('app.api.portal.dispatch_withdrawals', dispatched.extend)
    monkeypatch.setattr(routes.publication_tasks.notify_collection_registry_changed, 'delay', lambda *a, **k: None)
    yield dispatched


def _user(prefix: str, **kwargs):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', **kwargs)


def _team(prefix: str) -> str:
    with TestingSessionLocal() as db:
        team = Team(name=f'{prefix}-{uuid.uuid4().hex[:8]}')
        db.add(team)
        db.commit()
        return team.id


def _space(client, name: str, **extra) -> dict:
    created = client.post('/api/v1/collections', json={'name': name, 'description': 'Test purpose', **extra})
    assert created.status_code == 200, created.text
    return created.json()


def _job(
    owner_id: str, collection_id: str, *, released: bool = False, password_hash: str | None = None,
    status: JobStatus = JobStatus.FINISHED,
) -> str:
    with TestingSessionLocal() as db:
        job = Job(
            original_filename=f'{uuid.uuid4().hex[:6]}.pdf', upload_path='/tmp/none.pdf', owner_id=owner_id,
            status=status, result_markdown='# Doc\n', password_hash=password_hash,
            processing_info={'settings': {'collection_id': collection_id}},
        )
        db.add(job)
        db.flush()
        if released:
            db.add(DocumentRelease(
                job_id=job.id, owner_id=owner_id, markdown_snapshot='# Doc\n', markdown_sha256='0' * 64, payload={}, status='sent',
            ))
        db.commit()
        return job.id


# --- documents -----------------------------------------------------------------

def test_readers_and_removed_members_can_neither_see_nor_delete_documents():
    team_id = _team('lc-docs')
    owner = _user('lc-docs-owner', team_id=team_id)
    reader = _user('lc-docs-reader', team_id=team_id)
    member = _user('lc-docs-member')
    with TestingSessionLocal() as db:
        db.execute(user_teams.insert().values(user_id=reader.id, team_id=team_id, role='reader'))
        db.commit()
    owner_client = login_as(owner.username)
    space = _space(owner_client, 'Docs', visibility='restricted', grants=[
        {'team_id': team_id, 'role': 'member'}, {'user_id': member.id, 'role': 'member'},
    ])
    # The team reader's teammate uploaded it -- that no longer grants access.
    job_id = _job(owner.id, space['collection_id'])
    assert login_as(reader.username).delete(f'/api/v1/jobs/{job_id}').status_code == 404

    member_client = login_as(member.username)
    own_upload = _job(member.id, space['collection_id'])
    assert member_client.get(f'/api/v1/jobs/{own_upload}').status_code == 200
    removed = owner_client.patch(f"/api/v1/collections/{space['collection_id']}", json={'grants': [
        {'user_id': owner.id, 'role': 'owner'}, {'team_id': team_id, 'role': 'member'},
    ]})
    assert removed.status_code == 200, removed.text
    # Losing the grant also ends access to one's own uploads there.
    assert member_client.get(f'/api/v1/jobs/{own_upload}').status_code == 404
    assert member_client.delete(f'/api/v1/jobs/{own_upload}').status_code == 404


def test_released_document_is_withdrawn_when_deleted(_isolated):
    owner = _user('lc-release-owner')
    owner_client = login_as(owner.username)
    space = _space(owner_client, 'Released')
    job_id = _job(owner.id, space['collection_id'], released=True)

    assert owner_client.delete(f'/api/v1/jobs/{job_id}').status_code == 409
    deleted = owner_client.delete(f'/api/v1/jobs/{job_id}', params={'withdraw': True})
    assert deleted.status_code == 200, deleted.text
    assert _isolated == [job_id]
    with TestingSessionLocal() as db:
        assert db.get(Job, job_id) is None
        assert db.get(KnowledgeWithdrawal, job_id).status == 'pending'
        assert db.scalar(select(DocumentRelease.id).where(DocumentRelease.job_id == job_id)) is None


def test_bulk_delete_withdraws_released_documents_only_when_asked(_isolated):
    owner = _user('lc-bulk-owner')
    owner_client = login_as(owner.username)
    space = _space(owner_client, 'Bulk')
    released = _job(owner.id, space['collection_id'], released=True)
    draft = _job(owner.id, space['collection_id'])

    refused = owner_client.post('/api/v1/portal/documents/bulk', json={'job_ids': [released, draft], 'action': 'delete'})
    assert refused.json()['done'] == 1
    assert [error['job_id'] for error in refused.json()['errors']] == [released]
    withdrawn = owner_client.post('/api/v1/portal/documents/bulk', json={
        'job_ids': [released], 'action': 'delete', 'withdraw_released': True,
    })
    assert withdrawn.json() == {'done': 1, 'errors': []}
    assert _isolated == [released]


# --- knowledge spaces ------------------------------------------------------------

def test_owner_deletes_a_space_with_all_its_content_after_confirming_its_name(_isolated):
    owner = _user('lc-space-owner')
    member = _user('lc-space-member')
    owner_client = login_as(owner.username)
    space = _space(owner_client, 'Mit Inhalt', grants=[{'user_id': member.id, 'role': 'member'}])
    released = _job(owner.id, space['collection_id'], released=True)
    draft = _job(member.id, space['collection_id'])
    url = f"/api/v1/collections/{space['collection_id']}"

    assert owner_client.delete(url).status_code == 409
    assert login_as(member.username).delete(url, params={'with_content': True, 'confirm_name': 'Mit Inhalt'}).status_code == 403
    assert owner_client.delete(url, params={'with_content': True, 'confirm_name': 'Falsch'}).status_code == 422
    deleted = owner_client.delete(url, params={'with_content': True, 'confirm_name': 'Mit Inhalt'})
    assert deleted.status_code == 200, deleted.text
    assert _isolated == [released]
    with TestingSessionLocal() as db:
        assert db.get(Collection, space['collection_id']) is None
        assert db.get(Job, released) is None and db.get(Job, draft) is None


def test_deleting_legacy_documents_withdraws_them_from_knowledge(_isolated, monkeypatch):
    """Jobs indexed by the pre-release document.processed workflow have no
    DocumentRelease, yet may sit in Knowledge -- deleting them (alone, per
    folder or with their space) must withdraw them too, also after a restart
    left them unfinished."""
    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: True)
    owner = _user('lc-legacy-owner')
    owner_client = login_as(owner.username)
    space = _space(owner_client, 'Altbestand')
    single = _job(owner.id, space['collection_id'])
    assert owner_client.delete(f'/api/v1/jobs/{single}').status_code == 200
    assert _isolated == [single]
    restarted = _job(owner.id, space['collection_id'], status=JobStatus.FAILED)
    assert owner_client.delete(f'/api/v1/jobs/{restarted}').status_code == 200
    assert _isolated == [single, restarted]

    with_space = _job(owner.id, space['collection_id'], status=JobStatus.PENDING)
    deleted = owner_client.delete(
        f"/api/v1/collections/{space['collection_id']}", params={'with_content': True, 'confirm_name': 'Altbestand'}
    )
    assert deleted.status_code == 200, deleted.text
    assert _isolated == [single, restarted, with_space]
    with TestingSessionLocal() as db:
        assert db.get(KnowledgeWithdrawal, single).status == 'pending'
        assert db.get(KnowledgeWithdrawal, restarted).status == 'pending'
        assert db.get(KnowledgeWithdrawal, with_space).status == 'pending'


def test_a_deleted_spaces_slug_is_never_handed_out_again(_isolated):
    owner_client = login_as(_user('lc-slug-owner').username)
    first = _space(owner_client, f'HR intern {uuid.uuid4().hex[:6]}')
    assert owner_client.delete(f"/api/v1/collections/{first['collection_id']}").status_code == 200

    other_client = login_as(_user('lc-slug-other').username)
    explicit = other_client.post(
        '/api/v1/collections', json={'name': 'Neu', 'description': 'Test purpose', 'slug': first['slug']}
    )
    assert explicit.status_code == 409
    derived = _space(other_client, first['name'])
    assert derived['slug'] == f"{first['slug']}-2"


def test_space_deletion_counts_subagent_and_runtime_bot_assignments(monkeypatch):
    owner_client = login_as(_user('lc-botref-owner').username)
    for_subagent = _space(owner_client, 'Für Subagent')
    with TestingSessionLocal() as db:
        db.add(ManagedBot(
            id=f'agent-{uuid.uuid4().hex[:6]}', name='Agent', kind='llm', collections=[],
            agent_config={'enabled': True, 'subagents': [{'id': 'vertrag', 'collections': [for_subagent['slug']]}]},
        ))
        db.commit()
    blocked = owner_client.delete(f"/api/v1/collections/{for_subagent['collection_id']}")
    assert blocked.status_code == 409
    assert 'Bot' in blocked.json()['detail']

    for_yaml = _space(owner_client, 'Für YAML-Bot')
    yaml_bot = SimpleNamespace(id=f'yaml-{uuid.uuid4().hex[:6]}', collections=[for_yaml['slug']], agent=None)
    monkeypatch.setattr(routes, '_runtime_bots', lambda db=None: [yaml_bot])
    assert owner_client.delete(f"/api/v1/collections/{for_yaml['collection_id']}").status_code == 409
    # A deleted (tombstoned) YAML bot no longer counts.
    with TestingSessionLocal() as db:
        db.add(BotTombstone(id=yaml_bot.id))
        db.commit()
    assert owner_client.delete(f"/api/v1/collections/{for_yaml['collection_id']}").status_code == 200
    with TestingSessionLocal() as db:
        for row in db.scalars(select(ManagedBot).where(ManagedBot.name == 'Agent')).all():
            db.delete(row)
        db.delete(db.get(BotTombstone, yaml_bot.id))
        db.commit()


def test_space_deletion_refuses_password_protected_documents_and_identity_references():
    owner = _user('lc-guard-owner')
    owner_client = login_as(owner.username)
    protected = _space(owner_client, 'Geschützt')
    _job(owner.id, protected['collection_id'], password_hash='x')
    refused = owner_client.delete(
        f"/api/v1/collections/{protected['collection_id']}", params={'with_content': True, 'confirm_name': 'Geschützt'}
    )
    assert refused.status_code == 409
    assert 'passwortgeschützte' in refused.json()['detail']

    referenced = _space(owner_client, 'Für Identität')
    with TestingSessionLocal() as db:
        db.add(TechnicalIdentity(
            name=f'mcp-{uuid.uuid4().hex[:6]}', allowed_collections=[referenced['slug']],
            token_hash=uuid.uuid4().hex, token_prefix='wv_test',
        ))
        db.commit()
    blocked = owner_client.delete(f"/api/v1/collections/{referenced['collection_id']}")
    assert blocked.status_code == 409
    assert 'technischen Identität' in blocked.json()['detail']
    # Other test modules count the identities they create.
    with TestingSessionLocal() as db:
        for identity in db.scalars(select(TechnicalIdentity).where(TechnicalIdentity.token_prefix == 'wv_test')).all():
            db.delete(identity)
        db.commit()


# --- people and teams ------------------------------------------------------------

def test_deleting_the_last_owner_needs_a_successor_who_takes_over_every_owner_role():
    admin_client = login_as(_user('lc-person-admin', role=UserRole.ADMIN).username)
    leaving = _user('lc-person-leaving')
    successor = _user('lc-person-successor')
    co_owner = _user('lc-person-coowner')
    sole = _space(login_as(leaving.username), 'Allein')
    shared = _space(login_as(leaving.username), 'Geteilt', grants=[{'user_id': co_owner.id, 'role': 'owner'}])
    with TestingSessionLocal() as db:
        bot = ManagedBot(id=f'lc-bot-{uuid.uuid4().hex[:6]}', name='Bot', webhook_url='https://n8n.example.com/webhook/x')
        bot.grants.append(BotGrant(user_id=leaving.id, role=BotRole.OWNER))
        db.add(bot)
        db.commit()
        bot_id = bot.id

    ownership = admin_client.get(f'/api/v1/auth/admin/users/{leaving.id}/ownership').json()
    assert {(item['name'], item['sole_owner']) for item in ownership['collections']} == {('Allein', True), ('Geteilt', False)}
    assert [(item['id'], item['sole_owner']) for item in ownership['bots']] == [(bot_id, True)]

    refused = admin_client.delete(f'/api/v1/auth/admin/users/{leaving.id}')
    assert refused.status_code == 409
    assert 'Allein' in refused.json()['detail']
    assert admin_client.delete(f'/api/v1/auth/admin/users/{leaving.id}', params={'successor_id': leaving.id}).status_code == 422
    deleted = admin_client.delete(f'/api/v1/auth/admin/users/{leaving.id}', params={'successor_id': successor.id})
    assert deleted.status_code == 200, deleted.text

    successor_client = login_as(successor.username)
    for space in (sole, shared):
        assert successor_client.get(f"/api/v1/collections/{space['collection_id']}").json()['role'] == 'owner'
    assert [item['id'] for item in successor_client.get('/api/v1/bots').json()['items']] == [bot_id]


def test_deleting_a_person_without_sole_ownership_needs_no_successor():
    admin_client = login_as(_user('lc-plain-admin', role=UserRole.ADMIN).username)
    reader = _user('lc-plain-reader')
    _space(login_as(_user('lc-plain-owner').username), 'Fremd', grants=[{'user_id': reader.id, 'role': 'reader'}])
    assert admin_client.delete(f'/api/v1/auth/admin/users/{reader.id}').status_code == 200


def test_team_usage_lists_what_loses_access_when_the_team_is_deleted():
    admin_client = login_as(_user('lc-team-admin', role=UserRole.ADMIN).username)
    team_id = _team('lc-team')
    _user('lc-team-member', team_id=team_id)
    owner_client = login_as(_user('lc-team-owner').username)
    space = _space(owner_client, 'Teambereich', responsible_team_id=team_id, grants=[{'team_id': team_id, 'role': 'member'}])
    with TestingSessionLocal() as db:
        bot = ManagedBot(id=f'lc-team-bot-{uuid.uuid4().hex[:6]}', name='Teambot', webhook_url='https://n8n.example.com/webhook/x')
        bot.grants.append(BotGrant(team_id=team_id, role=BotRole.USER))
        db.add(bot)
        db.commit()

    usage = admin_client.get(f'/api/v1/auth/admin/teams/{team_id}/usage')
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body['member_count'] == 1
    assert [(item['name'], item['role']) for item in body['collections']] == [('Teambereich', 'member')]
    assert [item['name'] for item in body['responsible_for']] == ['Teambereich']
    assert [item['name'] for item in body['bots']] == ['Teambot']

    assert admin_client.delete(f'/api/v1/auth/admin/teams/{team_id}').status_code == 200
    detail = owner_client.get(f"/api/v1/collections/{space['collection_id']}").json()
    assert detail['responsible_team'] is None
    assert [grant['team_id'] for grant in detail['grants'] if grant['team_id']] == []
