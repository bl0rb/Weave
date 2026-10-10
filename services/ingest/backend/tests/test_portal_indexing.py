import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import httpx
import pytest
import redis as redis_lib

from app.core.config import settings
from app.models.models import DocumentRelease, Job, User, UserRole
from app.services import indexing_status, security
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


_REAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(indexing_status, '_RETRY_DELAY_SECONDS', 0)


def _upstream(monkeypatch, references, *, response=None, failure=None, status=200, failing_calls=None):
    calls = []
    real_client = _REAL_CLIENT

    def handle(request):
        calls.append(request)
        if failing_calls is not None and len(calls) > failing_calls:
            failure_now, status_now = None, 200
        else:
            failure_now, status_now = failure, status
        assert str(request.url) == 'https://knowledge.example/api/v1/indexing/status'
        assert json.loads(request.content)['items'] == references
        stamp = request.headers['X-Weave-Status-Timestamp']
        signature = hmac.new(b'secret', b'weave.indexing-status.v1\n' + stamp.encode() + b'\n' + request.content, hashlib.sha256).hexdigest()
        assert request.headers['X-Weave-Status-Signature'] == f'sha256={signature}'
        if failure_now:
            raise failure_now
        body = response if response is not None else {'items': [{**ref, 'state': 'indexed', 'indexed_at': '2026-09-02T11:31:12Z', 'chunk_count': 2} for ref in references]}
        return httpx.Response(status_now, json=body)

    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(indexing_status.httpx, 'Client', factory)
    return calls


def _get(authed, *ids):
    return authed.get('/api/v1/portal/indexing-status', params=[('job_id', value) for value in ids])


def test_admin_diagnostics_uses_stored_release_and_separate_signature(publication, monkeypatch):
    _, reference = publication
    admin = _user(username=f'diagnostics-{uuid.uuid4().hex[:8]}', role=UserRole.ADMIN)
    real_client = httpx.Client

    def handle(request):
        assert str(request.url) == 'https://knowledge.example/api/v1/indexing/diagnostics'
        assert json.loads(request.content) == reference
        stamp = request.headers['X-Weave-Status-Timestamp']
        signature = hmac.new(b'secret', b'weave.indexing-diagnostics.v1\n' + stamp.encode() + b'\n' + request.content, hashlib.sha256).hexdigest()
        assert request.headers['X-Weave-Status-Signature'] == f'sha256={signature}'
        return httpx.Response(200, json={'state': 'failed', 'failure': {'code': 'embedding_dimension_mismatch', 'expected': 1536, 'actual': 384}})

    monkeypatch.setattr(indexing_status.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    response = login_as(admin.username).get(f"/api/v1/portal/documents/{reference['job_id']}/indexing-diagnostics")
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment' in response.headers['content-disposition']
    assert response.json()['release']['id'] == reference['release_id']
    assert response.json()['indexing']['failure']['actual'] == 384
    assert 'private document' not in response.text and 'secret' not in response.text


def test_diagnostics_denies_non_admin_without_contacting_knowledge(publication, monkeypatch):
    user, reference = publication
    calls = _upstream(monkeypatch, [])
    response = login_as(user.username).get(f"/api/v1/portal/documents/{reference['job_id']}/indexing-diagnostics")
    assert response.status_code == 403
    assert calls == []


def test_diagnostics_denies_protected_document(publication, monkeypatch):
    _, reference = publication
    admin = _user(username=f'diagnostics-{uuid.uuid4().hex[:8]}', role=UserRole.ADMIN)
    with TestingSessionLocal() as db:
        db.get(Job, reference['job_id']).password_hash = 'protected'
        db.commit()
    calls = _upstream(monkeypatch, [])
    response = login_as(admin.username).get(f"/api/v1/portal/documents/{reference['job_id']}/indexing-diagnostics")
    assert response.status_code == 404
    assert calls == []


def test_authorized_status_uses_server_release_and_never_exposes_secrets_or_contents(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    calls = _upstream(monkeypatch, [reference])
    response = _get(authed, reference['job_id'])
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['release']['id'] == reference['release_id']
    assert item['indexing'] == {'state': 'indexed', 'indexed_at': '2026-09-02T11:31:12Z', 'chunk_count': 2, 'stale': False, 'checked_at': None, 'retrying': False}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert not any(value in response.text for value in ['private document', 'secret', 'markdown_sha256', 'Signature'])


def test_status_and_diagnostics_compare_the_released_digest_not_the_concurrency_digest(publication, monkeypatch):
    # ST-01: the release payload carries the digest of the image-URL-rewritten
    # snapshot that Knowledge actually downloaded; the row's markdown_sha256 is
    # the pre-rewrite digest used only for the portal's concurrency check.
    user, reference = publication
    with TestingSessionLocal() as db:
        release = db.get(DocumentRelease, reference['release_id'])
        release.payload = {'markdown_sha256': 'c' * 64}
        db.commit()
    expected = {**reference, 'markdown_sha256': 'c' * 64}
    calls = _upstream(monkeypatch, [expected])
    response = _get(login_as(user.username), reference['job_id'])
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['indexing']['state'] == 'indexed'
    assert len(calls) == 1  # _upstream asserts the request carried `expected`, i.e. the payload digest


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
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing'] == {'state': 'unavailable', 'indexed_at': None, 'chunk_count': 0, 'stale': False, 'checked_at': None, 'retrying': False}


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


# --- transient outages: one retry, then 5 minutes of the last confirmed status


def test_one_retry_recovers_a_transient_failure(publication, monkeypatch):
    user, reference = publication
    calls = _upstream(monkeypatch, [reference], failure=httpx.ConnectError('down'), failing_calls=1)
    indexing = _get(login_as(user.username), reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'indexed' and indexing['stale'] is False
    assert len(calls) == 2


def test_server_error_is_retried_once_then_reported_as_retrying(publication, monkeypatch):
    user, reference = publication
    calls = _upstream(monkeypatch, [reference], status=503)
    indexing = _get(login_as(user.username), reference['job_id']).json()['items'][0]['indexing']
    assert indexing == {'state': 'unavailable', 'indexed_at': None, 'chunk_count': 0, 'stale': False, 'checked_at': None, 'retrying': True}
    assert len(calls) == 2


def test_transient_outage_serves_the_last_confirmed_status_as_stale(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    _upstream(monkeypatch, [reference])
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing']['state'] == 'indexed'
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    indexing = _get(authed, reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'indexed' and indexing['chunk_count'] == 2
    assert indexing['stale'] is True and indexing['checked_at']


def test_stale_status_ends_after_the_grace_period(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    _upstream(monkeypatch, [reference])
    _get(authed, reference['job_id'])
    monkeypatch.setattr(indexing_status, 'STALE_GRACE', timedelta(0))
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    indexing = _get(authed, reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'unavailable' and indexing['stale'] is False and indexing['retrying'] is True


def test_long_outage_is_no_longer_retrying(publication, monkeypatch):
    user, reference = publication
    monkeypatch.setattr(indexing_status, 'RETRYING_FOR', timedelta(0))
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    indexing = _get(login_as(user.username), reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'unavailable' and indexing['retrying'] is False


def test_revoked_permission_never_sees_a_stale_status(publication, monkeypatch):
    user, reference = publication
    reader = _user(username=f'reader-{uuid.uuid4().hex[:8]}', team_id=user.team_id)
    authed = login_as(reader.username)
    _upstream(monkeypatch, [reference])
    _get(authed, reference['job_id'])
    with TestingSessionLocal() as db:
        db.get(User, reader.id).team_id = None
        db.commit()
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    assert _get(authed, reference['job_id']).json() == {'items': []}


def test_a_new_release_snapshot_does_not_inherit_a_stale_status(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    _upstream(monkeypatch, [reference])
    _get(authed, reference['job_id'])
    with TestingSessionLocal() as db:
        db.get(DocumentRelease, reference['release_id']).markdown_sha256 = 'c' * 64
        db.commit()
    _upstream(monkeypatch, [{**reference, 'markdown_sha256': 'c' * 64}], failure=httpx.ReadTimeout('busy'))
    indexing = _get(authed, reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'unavailable' and indexing['stale'] is False


def test_a_malformed_answer_clears_the_last_confirmed_status(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    _upstream(monkeypatch, [reference])
    _get(authed, reference['job_id'])
    bad = {**reference, 'state': 'indexed', 'indexed_at': None, 'chunk_count': 2}
    _upstream(monkeypatch, [reference], response={'items': [bad]})
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing']['retrying'] is False
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    indexing = _get(authed, reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'unavailable' and indexing['stale'] is False


def test_last_confirmed_status_is_shared_through_redis_for_the_grace_period(publication, monkeypatch):
    user, reference = publication
    _upstream(monkeypatch, [reference])
    _get(login_as(user.username), reference['job_id'])
    key = f"indexing-status:confirmed:{reference['job_id']}:{reference['release_id']}:{reference['markdown_sha256']}"
    assert 0 < security._rate_limit_redis().ttl(key) <= 300
    assert 'private document' not in security._rate_limit_redis().get(key)


class _BrokenRedis:
    def pipeline(self, transaction=True):
        raise redis_lib.ConnectionError('down')


def test_redis_outage_fails_open_without_grace(publication, monkeypatch):
    user, reference = publication
    authed = login_as(user.username)
    monkeypatch.setattr(security, '_redis_client', _BrokenRedis())
    _upstream(monkeypatch, [reference])
    assert _get(authed, reference['job_id']).json()['items'][0]['indexing']['state'] == 'indexed'
    _upstream(monkeypatch, [reference], failure=httpx.ReadTimeout('busy'))
    indexing = _get(authed, reference['job_id']).json()['items'][0]['indexing']
    assert indexing['state'] == 'unavailable' and indexing['stale'] is False and indexing['retrying'] is False
