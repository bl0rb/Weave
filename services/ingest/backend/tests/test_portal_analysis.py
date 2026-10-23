import json
import uuid

import pytest

from app.models.models import ChatProviderConfig, Job
from app.services import safe_fetch as safe_fetch_module
from app.services import security
from tests.test_portal import _collection, _db, _job, create_test_user, login_as


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    security.rate_limiter.reset()
    yield


def _user():
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'analysis-{suffix}', email=f'analysis-{suffix}@example.com')


def _provider(enabled: bool = True):
    with _db() as db:
        db.merge(
            ChatProviderConfig(
                id='default',
                name='Central',
                enabled=enabled,
                base_url='http://llm.internal:8000',
                model='test-model',
                api_key_encrypted=security.encrypt_chat_provider_api_key('k'),
            )
        )
        db.commit()


def _drop_provider():
    with _db() as db:
        row = db.get(ChatProviderConfig, 'default')
        if row is not None:
            db.delete(row)
            db.commit()


@pytest.fixture(autouse=True)
def _clean_provider():
    # The default row is shared state: leave none behind for other modules.
    _drop_provider()
    yield
    _drop_provider()


def _answer(content: str, status_code: int = 200):
    body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
    return safe_fetch_module.SafeFetchResponse(status_code=status_code, headers={}, body=body, final_url='x')


_GOOD = json.dumps(
    {
        'summary': 'Looks fine.',
        'recommendations': [
            {'severity': 'warning', 'action': 'check', 'title': 'Check table', 'detail': 'Row 3 is odd.'},
            {'severity': 'bogus', 'action': 'check', 'title': 'dropped', 'detail': ''},
        ],
    }
)


def _mock_fetch(monkeypatch, *replies):
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(safe_fetch_module, 'safe_fetch', fake)
    return calls


def test_findings_in_detail_skip_malformed():
    user = _user()
    job = _job(user.id, _collection(user.id))
    findings = [{'line': 3, 'column': 4, 'kind': 'gibberish', 'text': 'xx'}, {'line': 'a'}, 'junk']
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.processing_info = {
            **stored.processing_info,
            'execution': {'quality_gate': {'grade': 'B', 'findings': findings}},
        }
        db.commit()
    detail = login_as(user.username).get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['quality']['findings'] == [{'line': 3, 'column': 4, 'kind': 'gibberish', 'text': 'xx'}]


def test_not_configured_409_and_config_flag():
    user = _user()
    job = _job(user.id, _collection(user.id))
    authed = login_as(user.username)
    assert authed.get('/api/v1/portal/config').json()['analysis_configured'] is False
    response = authed.post(f'/api/v1/portal/documents/{job.id}/analysis', json={})
    assert response.status_code == 409
    assert response.json()['detail'] == 'analysis_not_configured'
    _provider(enabled=False)
    assert authed.get('/api/v1/portal/config').json()['analysis_configured'] is False
    _provider()
    assert authed.get('/api/v1/portal/config').json()['analysis_configured'] is True


def test_success_cache_and_refresh(monkeypatch):
    _provider()
    user = _user()
    job = _job(user.id, _collection(user.id))
    calls = _mock_fetch(monkeypatch, _answer(f'```json\n{_GOOD}\n```'))
    authed = login_as(user.username)
    url = f'/api/v1/portal/documents/{job.id}/analysis'
    first = authed.post(url, json={'locale': 'en'})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body['cached'] is False
    assert body['summary'] == 'Looks fine.'
    assert [r['title'] for r in body['recommendations']] == ['Check table']
    assert len(body['markdown_sha256']) == 64
    second = authed.post(url, json={'locale': 'en'}).json()
    assert second['cached'] is True and second['summary'] == 'Looks fine.'
    assert len(calls) == 1
    # another locale is a different cache entry
    assert authed.post(url, json={'locale': 'de'}).json()['cached'] is False
    assert len(calls) == 2
    refreshed = authed.post(url, json={'locale': 'en', 'refresh': True}).json()
    assert refreshed['cached'] is False
    assert len(calls) == 3


@pytest.mark.parametrize(
    'reply',
    [
        safe_fetch_module.SafeFetchError('boom'),
        _answer('{}', status_code=500),
        _answer('not json at all'),
    ],
)
def test_provider_failure_502(monkeypatch, reply):
    _provider()
    user = _user()
    job = _job(user.id, _collection(user.id))
    _mock_fetch(monkeypatch, reply)
    response = login_as(user.username).post(f'/api/v1/portal/documents/{job.id}/analysis', json={})
    assert response.status_code == 502
    assert response.json()['detail'] == 'analysis_failed'
    assert 'llm.internal' not in response.text


def test_invisible_job_404():
    _provider()
    owner = _user()
    job = _job(owner.id, _collection(owner.id))
    other = _user()
    response = login_as(other.username).post(f'/api/v1/portal/documents/{job.id}/analysis', json={})
    assert response.status_code == 404


def test_password_protected_403():
    _provider()
    user = _user()
    job = _job(user.id, _collection(user.id))
    with _db() as db:
        db.get(Job, job.id).password_hash = security.hash_password('pw')
        db.commit()
    response = login_as(user.username).post(f'/api/v1/portal/documents/{job.id}/analysis', json={})
    assert response.status_code == 403
