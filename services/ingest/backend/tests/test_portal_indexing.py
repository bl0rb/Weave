import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from app.core.config import settings
from app.models.models import DocumentRelease, Job, User, UserRole
from app.services import indexing_status
from tests.conftest import TestingSessionLocal, client, create_test_user, login_as
from tests.test_portal import _collection, _configure, _job, _team


def _user(**kwargs):
    return create_test_user(email=f'{uuid.uuid4().hex}@example.test', **kwargs)


@pytest.fixture
def publication(monkeypatch):
    _configure(monkeypatch)
    team = _team('Indexing')
    user = _user(username=f'indexing-{uuid.uuid4().hex[:8]}', team_id=team.id)
    area = _collection(user.id)
    job = _job(user.id, area)
    with TestingSessionLocal() as db:
        release = DocumentRelease(job_id=job.id, owner_id=user.id, markdown_snapshot='private document', markdown_sha256='a' * 64, payload={}, status='sent')
        db.add(release)
        db.commit()
        db.refresh(release)
        reference = {'job_id': job.id, 'release_id': release.id, 'markdown_sha256': release.markdown_sha256}
    return user, reference


def _upstream(monkeypatch, references, *, response=None, failure=None):
    calls = []
    real_client = httpx.Client

    def handle(request):
        calls.append(request)
        assert str(request.url) == 'https://knowledge.example/api/v1/indexing/status'
        assert json.loads(request.content)['items'] == references
        stamp = request.headers['X-Weave-Status-Timestamp']
        signature = hmac.new(b'secret', b'weave.indexing-status.v1\n' + stamp.encode() + b'\n' + request.content, hashlib.sha256).hexdigest()
        assert request.headers['X-Weave-Status-Signature'] == f'sha256={signature}'
        if failure:
            raise failure
        body = response if response is not None else {'items': [{**ref, 'state': 'indexed', 'indexed_at': '2026-09-02T11:31:12Z', 'chunk_count': 2} for ref in references]}
        return httpx.Response(200, json=body)

    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(indexing_status.httpx, 'Client', factory)
    return calls


def _get(authed, *ids):
    return authed.get('/api/v1/portal/indexing-status', params=[('job_id', value) for value in ids])


def test_authorized_status_uses_server_release_and_never_exposes_secrets_or_contents(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    calls = _upstream(monkeypatch, [reference])
    response = _get(authed, reference['job_id'])
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['release']['id'] == reference['release_id']
    assert item['indexing'] == {'state': 'indexed', 'indexed_at': '2026-09-02T11:31:12Z', 'chunk_count': 2}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert not any(value in response.text for value in ['private document', 'secret', 'markdown_sha256', 'Signature'])


def test_unreleased_documents_do_not_call_knowledge(publication, monkeypatch):
    user, _ = publication
    job = _job(user.id, _collection(user.id))
    calls = _upstream(monkeypatch, [])
    response = _get(login_as(user.username), job.id)
    assert response.json() == {'items': [{'job_id': job.id, 'release': None, 'indexing': None}]}
    assert calls == []


def test_unknown_and_other_team_ids_are_indistinguishable_and_never_sent_upstream(publication, monkeypatch):
    _, reference = publication
    outsider = _user(username=f'outsider-{uuid.uuid4().hex[:8]}')
    calls = _upstream(monkeypatch, [])
    authed = login_as(outsider.username)
    assert _get(authed, reference['job_id'], str(uuid.uuid4())).json() == {'items': []}
    assert calls == []


def test_permission_is_rechecked_on_every_poll(publication, monkeypatch):
    user, reference = publication
    reader = _user(username=f'reader-{uuid.uuid4().hex[:8]}', team_id=user.team_id)
    authed = login_as(reader.username)
    calls = _upstream(monkeypatch, [reference])
    assert len(_get(authed, reference['job_id']).json()['items']) == 1
    with TestingSessionLocal() as db:
        db.get(User, reader.id).team_id = None
        db.commit()
    assert _get(authed, reference['job_id']).json() == {'items': []}
    assert len(calls) == 1


def test_admin_can_check_other_owners_but_not_password_protected_documents(publication, monkeypatch):
    _, reference = publication
    admin = _user(username=f'index-admin-{uuid.uuid4().hex[:8]}', role=UserRole.ADMIN)
    authed = login_as(admin.username)
    calls = _upstream(monkeypatch, [reference])
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing']['state'] == 'indexed'
    with TestingSessionLocal() as db:
        db.get(Job, reference['job_id']).password_hash = 'protected'
        db.commit()
    assert _get(authed, reference['job_id']).json() == {'items': []}
    assert len(calls) == 1


@pytest.mark.parametrize('change', [
    {'release_id': str(uuid.uuid4())}, {'markdown_sha256': 'b' * 64},
    {'indexed_at': None}, {'chunk_count': 0}, {'state': 'future-unknown-state'},
])
def test_wrong_or_incomplete_confirmation_is_never_ready(publication, monkeypatch, change):
    user, reference = publication
    authed = login_as(user.username)
    item = {**reference, 'state': 'indexed', 'indexed_at': '2026-09-02T11:31:12Z', 'chunk_count': 2, **change}
    _upstream(monkeypatch, [reference], response={'items': [item]})
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing'] == {'state': 'unavailable', 'indexed_at': None, 'chunk_count': 0}


def test_timeout_is_unavailable_not_indexing_failure(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('secret error detail'))
    response = _get(authed, reference['job_id'])
    assert response.status_code == 200
    assert response.json()['items'][0]['indexing']['state'] == 'unavailable'
    assert 'secret' not in response.text


def test_missing_shared_secret_does_not_send_an_unsigned_request(publication, monkeypatch):
    user, reference = publication
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', '')
    calls = _upstream(monkeypatch, [reference])
    response = _get(login_as(user.username), reference['job_id'])
    assert response.json()['items'][0]['indexing']['state'] == 'unavailable'
    assert calls == []


def test_requires_login_and_bounds_the_requested_page(publication):
    user, reference = publication
    assert _get(client, reference['job_id']).status_code == 401
    authed = login_as(user.username)
    assert _get(authed, *[reference['job_id']] * 51).status_code == 422
    assert _get(authed, 'not-a-uuid').status_code == 422
