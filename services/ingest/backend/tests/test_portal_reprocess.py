import uuid

import pytest
from sqlalchemy import select

from app.models.models import DocumentRelease, ImportRunStatus, Job, JobStatus, VlConnection
from app.services import security
from app.services.security import rate_limiter
from tests.conftest import create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _import_run, _job, _team


@pytest.fixture(autouse=True)
def _reset_rate_limiter(monkeypatch):
    rate_limiter.reset()
    monkeypatch.setattr('app.api.portal._active_process_job_ids', lambda: set())
    monkeypatch.setattr('app.api.routes._active_process_job_ids', lambda: set())
    yield


def _user(prefix: str, *, team_id: str | None = None):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(
        username=f'{prefix}-{suffix}',
        email=f'{prefix}-{suffix}@example.com',
        team_id=team_id,
    )


def _set_job(job_id: str, **values) -> None:
    db = _db()
    try:
        job = db.get(Job, job_id)
        for name, value in values.items():
            setattr(job, name, value)
        db.commit()
    finally:
        db.close()


def _preview(client, job: Job) -> dict:
    response = client.get(f'/api/v1/portal/documents/{job.id}')
    assert response.status_code == 200, response.text
    return response.json()


def _job_state(job_id: str) -> tuple[JobStatus, str | None, dict]:
    db = _db()
    try:
        job = db.get(Job, job_id)
        return job.status, job.result_markdown, job.processing_info
    finally:
        db.close()


def test_portal_reprocess_queues_selected_profile_and_clears_previous_result(monkeypatch, tmp_path):
    _configure(monkeypatch)
    result_file = tmp_path / 'draft.md'
    result_file.write_text('Old draft')
    user = _user('portal-reprocess-owner')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    _set_job(
        job.id,
        processing_info={
            'settings': {
                'collection_id': collection.id,
                'profile_id': 'ppocrv6_tiny_structurev3',
                'mode': 'single',
                'email': 'owner@example.com',
                'department': 'legal',
                'folder': 'kept-folder',
            },
            'execution': {'quality_gate': {'grade': 'A', 'recommendation': 'allow'}},
            'editor': {'latest_result_path': str(result_file)},
        },
    )
    client = login_as(user.username)
    preview = _preview(client, job)
    assert preview['profile_id'] == 'ppocrv6_tiny_structurev3'
    assert preview['can_reprocess'] is True

    dispatched: list[tuple] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda *args: dispatched.append(args))
    response = client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': 'ppocrv6_medium_structurev3', 'markdown_sha256': preview['markdown_sha256']},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {'job_id': job.id, 'status': 'queued', 'profile_id': 'ppocrv6_medium_structurev3'}
    assert dispatched == [(job.id, 'ppocrv6_medium_structurev3', 'single', 'owner@example.com', 'legal')]
    status_value, result_markdown, processing_info = _job_state(job.id)
    assert status_value == JobStatus.PENDING
    assert result_markdown is None
    assert processing_info['settings']['collection_id'] == collection.id
    assert processing_info['settings']['folder'] == 'kept-folder'
    assert processing_info['settings']['profile_id'] == 'ppocrv6_medium_structurev3'
    assert db_release(job.id) is None
    assert not result_file.exists()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda *_: None)
    release_url = f'/api/v1/portal/documents/{job.id}/release'
    assert client.post(release_url, json={'markdown_sha256': preview['markdown_sha256']}).status_code == 409

    # Simulate worker completion: only a fresh preview may approve the new draft.
    _set_job(job.id, status=JobStatus.FINISHED, result_markdown=preview['markdown'] + '\nImproved draft\n')
    updated = _preview(client, job)
    assert updated['markdown_sha256'] != preview['markdown_sha256']
    assert client.post(release_url, json={'markdown_sha256': preview['markdown_sha256']}).status_code == 409
    assert client.post(release_url, json={'markdown_sha256': updated['markdown_sha256']}).status_code == 202


def test_portal_reprocess_allows_poor_quality_and_configured_vl_profile(monkeypatch):
    user = _user('portal-reprocess-quality')
    collection = _collection(user.id)
    job = _job(user.id, collection, quality='block')
    connection = _vl_connection()
    client = login_as(user.username)
    preview = _preview(client, job)
    assert preview['can_release'] is False
    assert preview['can_reprocess'] is True

    dispatched: list[tuple] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda *args: dispatched.append(args))
    selected = f'vl:{connection.id}'
    response = client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': selected, 'markdown_sha256': preview['markdown_sha256']},
    )

    assert response.status_code == 202, response.text
    assert response.json()['profile_id'] == selected
    assert dispatched == [(job.id, 'openai_vision', None, None, None)]
    status_value, result_markdown, processing_info = _job_state(job.id)
    assert status_value == JobStatus.PENDING
    assert result_markdown is None
    assert processing_info['settings']['profile_id'] == selected
    assert processing_info['settings']['vl_connection_id'] == connection.id
    assert db_release(job.id) is None


def test_portal_reprocess_allows_finished_attachment_after_import(monkeypatch):
    user = _user('portal-reprocess-attachment')
    collection = _collection(user.id)
    import_run = _import_run(ImportRunStatus.FINISHED)
    job = _job(user.id, collection, import_run_id=import_run.id)
    _set_job(job.id, processing_info={'settings': {'collection_id': collection.id, 'mode': 'import_attachment'}})
    client = login_as(user.username)
    preview = _preview(client, job)

    dispatched: list[tuple] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda *args: dispatched.append(args))
    response = client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': 'ppocrv6_medium_structurev3', 'markdown_sha256': preview['markdown_sha256']},
    )
    assert response.status_code == 202, response.text
    assert dispatched == [(job.id, 'ppocrv6_medium_structurev3', 'import_attachment', None, None)]


def test_portal_reprocess_reader_is_forbidden_and_cross_team_is_hidden():
    team = _team('Portal reprocess authz')
    owner = _user('portal-reprocess-control-owner', team_id=team.id)
    reader = _user('portal-reprocess-reader', team_id=team.id)
    outsider = _user('portal-reprocess-outsider', team_id=_team('Portal reprocess other').id)
    collection = _collection(owner.id)
    job = _job(owner.id, collection)
    owner_client = login_as(owner.username)
    preview = _preview(owner_client, job)

    reader_client = login_as(reader.username)
    reader_detail = reader_client.get(f'/api/v1/portal/documents/{job.id}')
    assert reader_detail.status_code == 200
    assert reader_detail.json()['can_reprocess'] is False
    reader_response = reader_client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': 'ppocrv6_medium_structurev3', 'markdown_sha256': preview['markdown_sha256']},
    )
    assert reader_response.status_code == 403

    outsider_client = login_as(outsider.username)
    assert outsider_client.get(f'/api/v1/portal/documents/{job.id}').status_code == 404
    outsider_response = outsider_client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': 'ppocrv6_medium_structurev3', 'markdown_sha256': preview['markdown_sha256']},
    )
    assert outsider_response.status_code == 404
    assert _job_state(job.id)[0] == JobStatus.FINISHED


def test_portal_reprocess_issued_release_is_blocked_without_mutation(monkeypatch):
    _configure(monkeypatch)
    user = _user('portal-reprocess-release')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    client = login_as(user.username)
    preview = _preview(client, job)
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    release = client.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    )
    assert release.status_code == 202, release.text

    dispatched: list[tuple] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda *args: dispatched.append(args))
    response = client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': 'ppocrv6_medium_structurev3', 'markdown_sha256': preview['markdown_sha256']},
    )
    assert response.status_code == 409
    assert dispatched == []
    status_value, result_markdown, _ = _job_state(job.id)
    assert status_value == JobStatus.FINISHED
    assert result_markdown is not None
    assert db_release(job.id) is not None


@pytest.mark.parametrize('case', ['stale_hash', 'active', 'import_page', 'import_running', 'invalid_profile'])
def test_portal_reprocess_gates_preserve_result_and_do_not_dispatch(monkeypatch, case):
    user = _user(f'portal-reprocess-gate-{case}')
    collection = _collection(user.id)
    import_run_id = None
    if case == 'import_running':
        import_run_id = _import_run(ImportRunStatus.RUNNING).id
    job = _job(user.id, collection, import_run_id=import_run_id)
    if case == 'import_page':
        _set_job(job.id, processing_info={'settings': {'collection_id': collection.id, 'mode': 'import'}})
    client = login_as(user.username)
    preview = _preview(client, job)
    original = _job_state(job.id)
    dispatched: list[tuple] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda *args: dispatched.append(args))
    if case == 'active':
        monkeypatch.setattr('app.api.portal._active_process_job_ids', lambda: {job.id})
    supplied_hash = '0' * 64 if case == 'stale_hash' else preview['markdown_sha256']
    profile_id = 'not-a-real-profile' if case == 'invalid_profile' else 'ppocrv6_medium_structurev3'

    response = client.post(
        f'/api/v1/portal/documents/{job.id}/reprocess',
        json={'profile_id': profile_id, 'markdown_sha256': supplied_hash},
    )

    assert response.status_code == (422 if case == 'invalid_profile' else 409), response.text
    assert dispatched == []
    assert _job_state(job.id) == original


def _vl_connection() -> VlConnection:
    db = _db()
    try:
        connection = VlConnection(
            name='Portal reprocess VL',
            base_url='https://vl.example.com',
            model='vl-model',
            api_key_encrypted=security.encrypt_vl_api_key('secret-key'),
            system_prompt='',
            enabled=True,
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        db.expunge(connection)
        return connection
    finally:
        db.close()


def db_release(job_id: str) -> DocumentRelease | None:
    db = _db()
    try:
        return db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job_id))
    finally:
        db.close()
