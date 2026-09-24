import uuid

import pytest

from app.models.models import DocumentRelease, Job
from app.services.security import rate_limiter
from app.workers import publication_tasks
from tests.conftest import TestingSessionLocal, create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _job


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # /auth/login is rate-limited per client host, and TestClient always
    # presents as "testclient" -- shared bucket across every test unless
    # reset per test (see test_collections_api.py's identical fixture).
    rate_limiter.reset()
    yield


def test_skip_then_unskip_round_trip(monkeypatch):
    user = create_test_user(username=f'skip-{uuid.uuid4().hex[:8]}', email=f'skip-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)

    skipped = authed.post(f'/api/v1/portal/documents/{job.id}/skip')
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()['review_decision'] == 'skipped'

    listing = authed.get('/api/v1/portal/documents', params={'review_only': 'true'}).json()
    assert job.id not in [item['id'] for item in listing['items']]
    skipped_listing = authed.get('/api/v1/portal/documents', params={'review_state': 'skipped'}).json()
    assert job.id in [item['id'] for item in skipped_listing['items']]

    unskipped = authed.post(f'/api/v1/portal/documents/{job.id}/unskip')
    assert unskipped.status_code == 200
    assert unskipped.json()['review_decision'] is None
    listing_after = authed.get('/api/v1/portal/documents', params={'review_only': 'true'}).json()
    assert job.id in [item['id'] for item in listing_after['items']]


def test_review_state_review_excludes_released_job(monkeypatch):
    """Regression: the frontend's 'Zur Prüfung' tab sends review_state=review
    (not the legacy review_only=true) -- a released job must not leak into it."""
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(username=f'revstate-{uuid.uuid4().hex[:8]}', email=f'revstate-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    released = authed.post(f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']})
    assert released.status_code == 202, released.text

    listing = authed.get('/api/v1/portal/documents', params={'review_state': 'review'}).json()
    assert job.id not in [item['id'] for item in listing['items']]


def test_skip_then_bulk_release_excludes_from_skipped_filter(monkeypatch):
    """Regression: skip -> bulk-release must not leave the job listed under
    review_state=skipped once it has an issued release."""
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(username=f'skiprel-{uuid.uuid4().hex[:8]}', email=f'skiprel-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)

    assert authed.post(f'/api/v1/portal/documents/{job.id}/skip').status_code == 200
    response = authed.post('/api/v1/portal/documents/bulk', json={'job_ids': [job.id], 'action': 'release'})
    assert response.status_code == 200, response.text
    assert response.json()['done'] == 1

    skipped_listing = authed.get('/api/v1/portal/documents', params={'review_state': 'skipped'}).json()
    assert job.id not in [item['id'] for item in skipped_listing['items']]


def test_review_only_and_review_state_skipped_combo_does_not_500():
    """Regression: review_state wins over the legacy review_only flag; both
    being present must not double-join DocumentRelease in the same query."""
    user = create_test_user(username=f'combo-{uuid.uuid4().hex[:8]}', email=f'combo-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    assert authed.post(f'/api/v1/portal/documents/{job.id}/skip').status_code == 200

    response = authed.get('/api/v1/portal/documents', params={'review_only': 'true', 'review_state': 'skipped'})
    assert response.status_code == 200, response.text
    assert job.id in [item['id'] for item in response.json()['items']]


def test_skip_forbidden_when_quality_gate_blocks_release():
    owner = create_test_user(username=f'owner-{uuid.uuid4().hex[:8]}', email=f'owner-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(owner.id)
    job = _job(owner.id, collection, quality='block')
    response = login_as(owner.username).post(f'/api/v1/portal/documents/{job.id}/skip')
    assert response.status_code == 403


def test_skip_conflicts_with_existing_release(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(username=f'rel-{uuid.uuid4().hex[:8]}', email=f'rel-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    released = authed.post(f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']})
    assert released.status_code == 202, released.text
    response = authed.post(f'/api/v1/portal/documents/{job.id}/skip')
    assert response.status_code == 409


def test_bulk_release_mixed_permissions_and_quality_c(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    owner = create_test_user(username=f'bulk-owner-{uuid.uuid4().hex[:8]}', email=f'bulk-owner-{uuid.uuid4().hex[:8]}@example.com')
    other = create_test_user(username=f'bulk-other-{uuid.uuid4().hex[:8]}', email=f'bulk-other-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(owner.id)
    other_collection = _collection(other.id)
    good_job = _job(owner.id, collection)
    grade_c_job = _job(owner.id, collection, quality='block')
    with _db() as db:
        stored = db.get(Job, grade_c_job.id)
        stored.processing_info = {**stored.processing_info, 'execution': {'quality_gate': {'grade': 'C', 'recommendation': 'block'}}}
        db.commit()
    foreign_job = _job(other.id, other_collection)

    authed = login_as(owner.username)
    response = authed.post('/api/v1/portal/documents/bulk', json={
        'job_ids': [good_job.id, grade_c_job.id, foreign_job.id],
        'action': 'release',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['done'] == 1
    error_ids = {error['job_id'] for error in body['errors']}
    assert grade_c_job.id in error_ids
    assert foreign_job.id in error_ids

    response2 = authed.post('/api/v1/portal/documents/bulk', json={
        'job_ids': [grade_c_job.id],
        'action': 'release',
        'accept_quality_warnings': True,
    })
    assert response2.status_code == 200
    assert response2.json()['done'] == 1


def test_bulk_delete_mixed_ownership():
    owner = create_test_user(username=f'del-owner-{uuid.uuid4().hex[:8]}', email=f'del-owner-{uuid.uuid4().hex[:8]}@example.com')
    other = create_test_user(username=f'del-other-{uuid.uuid4().hex[:8]}', email=f'del-other-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(owner.id)
    other_collection = _collection(other.id)
    own_job = _job(owner.id, collection)
    foreign_job = _job(other.id, other_collection)

    authed = login_as(owner.username)
    response = authed.post('/api/v1/portal/documents/bulk', json={
        'job_ids': [own_job.id, foreign_job.id],
        'action': 'delete',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['done'] == 1
    assert any(error['job_id'] == foreign_job.id for error in body['errors'])
    with _db() as db:
        assert db.get(Job, own_job.id) is None
        assert db.get(Job, foreign_job.id) is not None


def test_bulk_delete_rejects_password_protected_job():
    """Regression: seeing a password-protected job (visibility) must not let
    bulk delete bypass the password check the single DELETE endpoint applies."""
    owner = create_test_user(username=f'del-pw-{uuid.uuid4().hex[:8]}', email=f'del-pw-{uuid.uuid4().hex[:8]}@example.com')
    collection = _collection(owner.id)
    job = _job(owner.id, collection)
    with _db() as db:
        db.get(Job, job.id).password_hash = 'opaque-password-hash'
        db.commit()

    authed = login_as(owner.username)
    response = authed.post('/api/v1/portal/documents/bulk', json={'job_ids': [job.id], 'action': 'delete'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['done'] == 0
    assert any(error['job_id'] == job.id for error in body['errors'])
    with _db() as db:
        assert db.get(Job, job.id) is not None


def test_bulk_job_ids_over_limit_rejected():
    user = create_test_user(username=f'lim-{uuid.uuid4().hex[:8]}', email=f'lim-{uuid.uuid4().hex[:8]}@example.com')
    authed = login_as(user.username)
    response = authed.post('/api/v1/portal/documents/bulk', json={
        'job_ids': [str(uuid.uuid4()) for _ in range(101)],
        'action': 'delete',
    })
    assert response.status_code == 422
