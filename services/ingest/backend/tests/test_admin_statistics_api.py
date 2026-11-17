"""GET /api/v1/admin/statistics (app/api/admin_statistics.py).

The test database is shared by the whole suite, so the counts are checked
as the difference between a response before and after adding rows.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

import app.api.admin_statistics as admin_statistics_module
from app.core.config import settings
from app.main import app
from app.models.models import (
    Collection,
    CollectionVisibility,
    DocumentRelease,
    Job,
    JobStatus,
    KnowledgeWithdrawal,
    ReviewTask,
    Team,
    TopicSubscription,
    UserRole,
)
from app.services import object_store
from app.services.security import rate_limiter
from conftest import BROWSER_HEADERS, TestingSessionLocal, create_test_user, login_as

URL = '/api/v1/admin/statistics'

KNOWLEDGE_STATS = {
    'documents': {
        'total': 7, 'by_status': {'pending': 1, 'indexed': 4, 'blocked': 0, 'superseded': 1, 'failed': 1},
    },
    'chunks': {'total': 120, 'embedded': 118, 'avg_chars': 812.5},
    'embedding_models': [{'model': 'e5-small', 'chunks': 100}, {'model': 'fake-embed', 'chunks': 18}],
    'collections': {'total': 3, 'with_documents': 2},
    'memory': {'documents': 4, 'collections': 2},
    'vector_backend': 'pgvector',
    'storage': {'documents_bytes': 1000, 'chunks_bytes': 5000, 'memory_bytes': 200, 'total_bytes': 6200},
    'generated_at': '2026-11-17T10:00:00Z',
    'something_new': {'ignored': True},
}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The endpoint is rate limited per client; the suite shares one
    limiter, so leave the budget to the modules that run after this one."""
    rate_limiter.reset()
    yield
    rate_limiter.reset()


class _FakeResponse:
    def __init__(self, json_body, status_code: int = 200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                'error', request=httpx.Request('GET', 'http://x'), response=httpx.Response(self.status_code)
            )

    def json(self):
        return self._json_body


def _admin(prefix: str) -> TestClient:
    user = create_test_user(username=prefix, email=f'{prefix}@example.com', role=UserRole.ADMIN)
    return login_as(user.username)


def _unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')


_created_jobs: list[str] = []


def _job(db, *, status=JobStatus.FINISHED, previous=None, version=1, created_at=None, **fields) -> Job:
    job = Job(
        original_filename='doc.pdf', upload_path='uploads/doc.pdf', status=status,
        previous_job_id=previous.id if previous is not None else None, document_version=version, **fields,
    )
    if created_at is not None:
        job.created_at = created_at
    db.add(job)
    db.flush()
    _created_jobs.append(job.id)
    return job


@pytest.fixture
def cleanup_jobs():
    """Releases RESTRICT job deletes; later modules wipe the jobs table."""
    yield
    with TestingSessionLocal() as db:
        db.execute(delete(DocumentRelease).where(DocumentRelease.job_id.in_(_created_jobs)))
        db.execute(delete(KnowledgeWithdrawal).where(KnowledgeWithdrawal.job_id.in_(_created_jobs)))
        db.execute(update(Job).where(Job.id.in_(_created_jobs)).values(previous_job_id=None))
        db.execute(delete(Job).where(Job.id.in_(_created_jobs)))
        db.commit()
    _created_jobs.clear()


def _release(db, job: Job, status: str = 'sent') -> None:
    db.add(DocumentRelease(
        job_id=job.id, markdown_snapshot='snap', markdown_sha256='a' * 64,
        payload={'event': 'document.released', 'job_id': job.id}, status=status,
    ))
    db.flush()


def test_requires_admin(monkeypatch):
    _unconfigured(monkeypatch)
    assert TestClient(app, headers=BROWSER_HEADERS).get(URL).status_code == 401
    member = create_test_user(username='stats-member', email='stats-member@example.com')
    assert login_as(member.username).get(URL).status_code == 403


def test_counts_are_aggregated(monkeypatch, cleanup_jobs):
    _unconfigured(monkeypatch)
    admin = _admin('stats-admin')
    before = admin.get(URL)
    assert before.status_code == 200, before.text
    before = before.json()

    suffix = uuid.uuid4().hex[:8]
    with TestingSessionLocal() as db:
        # Chain A: v1 released, v2 released (current) -> 1 document, 1 released.
        a1 = _job(db)
        _release(db, a1)
        a2 = _job(db, previous=a1, version=2)
        _release(db, a2)
        # Chain B: released but withdrawn -> not released.
        b1 = _job(db)
        _release(db, b1)
        db.add(KnowledgeWithdrawal(job_id=b1.id, status='sent'))
        # Chain C: v1 released (still current), v2 in processing.
        c1 = _job(db)
        _release(db, c1)
        _job(db, status=JobStatus.RUNNING, previous=c1, version=2)
        # Chain D: v1 released, v2 failed (never released), v3 released ->
        # only v3 counts (v1 is superseded across the unreleased v2).
        d1 = _job(db)
        _release(db, d1)
        d2 = _job(db, status=JobStatus.FAILED, previous=d1, version=2)
        d3 = _job(db, previous=d2, version=3)
        _release(db, d3)
        # Chain E: release whose delivery to Knowledge failed -> not released
        # (the portal's in_chat counts only 'sent').
        e1 = _job(db)
        _release(db, e1, status='failed')
        # Single failed, pending and old documents.
        _job(db, status=JobStatus.FAILED)
        _job(db, status=JobStatus.PENDING)
        _job(db, created_at=datetime.now(timezone.utc) - timedelta(days=45))

        object_store.put_bytes(db, b'x' * 10)
        object_store.put_bytes(db, b'y' * 32)

        public = Collection(slug=f'stats-public-{suffix}', name='Öffentlich', visibility=CollectionVisibility.PUBLIC)
        restricted = Collection(slug=f'stats-restricted-{suffix}', name='Eingeschränkt')
        db.add_all([public, restricted])
        db.add(Team(name=f'stats-team-{suffix}'))
        db.flush()
        db.add_all([
            ReviewTask(collection_id=public.id, kind='knowledge_gap', title='Lücke', cluster_key=f'a-{suffix}'),
            ReviewTask(collection_id=public.id, kind='knowledge_gap', title='Lücke', cluster_key=f'b-{suffix}', status='in_progress'),
            ReviewTask(collection_id=public.id, kind='knowledge_gap', title='Lücke', cluster_key=f'c-{suffix}', status='done'),
        ])
        db.commit()

    inactive = create_test_user(username=f'stats-off-{suffix}', email=f'stats-off-{suffix}@example.com', is_active=False)
    create_test_user(username=f'stats-adm-{suffix}', email=f'stats-adm-{suffix}@example.com', role=UserRole.ADMIN)
    with TestingSessionLocal() as db:
        db.add(TopicSubscription(user_id=inactive.id, collection_id=public.id, label='Thema'))
        db.commit()

    response = admin.get(URL)
    assert response.status_code == 200, response.text
    after = response.json()

    def delta(*path):
        old, new = before, after
        for key in path:
            old, new = old[key], new[key]
        return new - old

    assert delta('documents', 'total') == 8
    assert delta('documents', 'versions') == 12
    assert delta('documents', 'by_status', 'FINISHED') == 8
    assert delta('documents', 'by_status', 'RUNNING') == 1
    assert delta('documents', 'by_status', 'PENDING') == 1
    assert delta('documents', 'by_status', 'FAILED') == 2
    assert delta('documents', 'released') == 3
    assert delta('documents', 'uploaded_last_30_days') == 11

    assert delta('storage', 'objects') == 2
    assert delta('storage', 'bytes') == 42
    assert after['storage']['objects'] == sum(item['objects'] for item in after['storage']['by_backend'].values())

    assert delta('collections', 'total') == 2
    assert delta('collections', 'public') == 1
    assert delta('collections', 'restricted') == 1
    assert delta('users', 'total') == 2
    assert delta('users', 'active') == 1
    assert delta('users', 'admins') == 1
    assert delta('teams') == 1
    assert delta('review_tasks', 'open') == 1
    assert delta('review_tasks', 'in_progress') == 1
    assert delta('subscriptions') == 1

    assert after['knowledge'] is None
    assert after['knowledge_error'] == 'Der Wissensdienst ist nicht konfiguriert.'
    assert after['generated_at']


def test_knowledge_stats_are_proxied(monkeypatch):
    admin = _admin('stats-knowledge-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example/')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'reindex-secret')
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers, timeout))
        return _FakeResponse(KNOWLEDGE_STATS)

    monkeypatch.setattr(admin_statistics_module.httpx, 'get', fake_get)

    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [('https://knowledge.example/api/v1/internal/stats', {'X-Weave-Reindex-Token': 'reindex-secret'}, 5)]
    assert body['knowledge_error'] is None
    knowledge = body['knowledge']
    assert knowledge['documents']['by_status']['indexed'] == 4
    assert knowledge['chunks'] == {'total': 120, 'embedded': 118, 'avg_chars': 812.5}
    assert knowledge['embedding_models'][0] == {'model': 'e5-small', 'chunks': 100}
    assert knowledge['vector_backend'] == 'pgvector'
    assert knowledge['storage']['total_bytes'] == 6200
    assert 'something_new' not in knowledge


def test_knowledge_partial_response_defaults(monkeypatch):
    admin = _admin('stats-knowledge-partial-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    partial = {
        'documents': {'total': 2}, 'chunks': {'total': 3}, 'collections': {}, 'memory': {},
        'vector_backend': 'sqlite', 'storage': None,
    }
    monkeypatch.setattr(
        admin_statistics_module.httpx, 'get', lambda url, headers=None, timeout=None: _FakeResponse(partial),
    )

    body = admin.get(URL).json()
    assert body['knowledge_error'] is None
    assert body['knowledge']['chunks'] == {'total': 3, 'embedded': 0, 'avg_chars': None}
    assert body['knowledge']['documents']['by_status']['failed'] == 0
    assert body['knowledge']['embedding_models'] == []
    assert body['knowledge']['storage'] is None


def test_knowledge_foreign_body_is_an_error(monkeypatch):
    """A 200 body without the statistics sections (wrong service, gateway
    page) is no statistics: knowledge_error, not a row of zeros."""
    admin = _admin('stats-knowledge-foreign-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    for foreign in ({}, {'detail': 'Not Found'}, {'chunks': {'total': 3}}):
        monkeypatch.setattr(
            admin_statistics_module.httpx, 'get', lambda url, headers=None, timeout=None, body=foreign: _FakeResponse(body),
        )
        response = admin.get(URL)
        assert response.status_code == 200, response.text
        assert response.json()['knowledge'] is None, foreign
        assert response.json()['knowledge_error'] == 'Die Statistik des Wissensdienstes konnte nicht abgerufen werden.'


def test_knowledge_malformed_base_url_does_not_fail_the_endpoint(monkeypatch):
    """httpx.InvalidURL is no httpx.HTTPError; real httpx.get, no mock."""
    admin = _admin('stats-knowledge-badurl-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'http://knowledge:80O0')

    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()['knowledge'] is None
    assert response.json()['knowledge_error'] == 'Die Statistik des Wissensdienstes konnte nicht abgerufen werden.'


def test_knowledge_unreachable_does_not_fail_the_endpoint(monkeypatch):
    admin = _admin('stats-knowledge-down-admin')
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')

    def refused(url, headers=None, timeout=None):
        raise httpx.ConnectError('connection refused')

    monkeypatch.setattr(admin_statistics_module.httpx, 'get', refused)
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()['knowledge'] is None
    assert response.json()['knowledge_error'] == 'Die Statistik des Wissensdienstes konnte nicht abgerufen werden.'

    for broken in (_FakeResponse({}, status_code=403), _FakeResponse(['not', 'an', 'object'])):
        monkeypatch.setattr(admin_statistics_module.httpx, 'get', lambda url, headers=None, timeout=None, r=broken: r)
        response = admin.get(URL)
        assert response.status_code == 200, response.text
        assert response.json()['knowledge'] is None
        assert response.json()['knowledge_error']
