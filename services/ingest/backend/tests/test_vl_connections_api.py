"""VL connection API tests: admin VL-connection CRUD, the user-facing VL
connections list, capabilities listing, and worker behaviour for jobs whose
VL connection is missing or disabled.

Real cookie-based logins (create_test_user/login_as, same idioms as
test_versioning_api.py/test_import_api.py) because owner/team visibility and
predecessor lookup join against real users/jobs rows.
"""

import uuid

import pytest

from app.models.models import (
    Job,
    JobStatus,
    UserRole,
    VlConnection,
)
from app.services import security
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as, stored_upload


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    # Benchmark jobs under test never reach a real worker; process_job.delay
    # is a no-op, so jobs stay PENDING unless a test flips status itself.
    # routes.process_job and benchmarks.process_job are the SAME imported
    # Celery task object, so patching .delay here covers both call sites.
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: None)
    yield


def _db():
    return TestingSessionLocal()


def _user(prefix: str, **kwargs):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', **kwargs)


def _make_vl_connection(
    *,
    name: str = 'Test VL Connection',
    model: str = 'vl-model',
    enabled: bool = True,
    base_url: str = 'https://vl.example.com',
    api_key: str = 'super-secret-key',
    system_prompt: str = '',
) -> VlConnection:
    db = _db()
    try:
        connection = VlConnection(
            name=name,
            base_url=base_url,
            model=model,
            api_key_encrypted=security.encrypt_vl_api_key(api_key),
            system_prompt=system_prompt,
            enabled=enabled,
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        db.expunge(connection)
        return connection
    finally:
        db.close()


def test_admin_can_create_list_and_never_see_api_key():
    admin_user = _user('vladmin', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)

    create_resp = admin.post(
        '/api/v1/auth/admin/vl-connections',
        json={
            'name': 'Internal vLLM',
            'base_url': 'https://vllm.internal.example.com:8000/',
            'model': 'qwen2-vl-7b',
            'api_key': 'sk-internal-abc123',
            'system_prompt': 'Extract markdown.',
            'enabled': True,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert 'api_key' not in body
    assert body['has_api_key'] is True
    assert body['base_url'] == 'https://vllm.internal.example.com:8000'  # trailing slash stripped
    connection_id = body['id']

    list_resp = admin.get('/api/v1/auth/admin/vl-connections')
    assert list_resp.status_code == 200
    items = list_resp.json()['items']
    assert any(item['id'] == connection_id for item in items)
    assert all('api_key' not in item for item in items)


def test_admin_vl_connection_update_keeps_key_on_blank_omit():
    admin_user = _user('vlupd', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)
    connection = _make_vl_connection(name='Original Name', api_key='original-key-value')

    update_resp = admin.put(
        f'/api/v1/auth/admin/vl-connections/{connection.id}',
        json={'name': 'Renamed Connection'},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()['name'] == 'Renamed Connection'
    assert update_resp.json()['has_api_key'] is True

    db = _db()
    try:
        refreshed = db.get(VlConnection, connection.id)
        assert security.decrypt_vl_api_key(refreshed.api_key_encrypted) == 'original-key-value'
    finally:
        db.close()


def test_admin_vl_connection_delete():
    admin_user = _user('vldel', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)
    connection = _make_vl_connection()

    resp = admin.delete(f'/api/v1/auth/admin/vl-connections/{connection.id}')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'deleted'}

    db = _db()
    try:
        assert db.get(VlConnection, connection.id) is None
    finally:
        db.close()


def test_admin_vl_connection_delete_while_referenced_by_pending_job_degrades_gracefully():
    """Deleting a connection referenced by a still-pending job must
    not error here (Job only holds a loose vl_connection_id string, not a
    FK) -- the job later fails on its own with a graceful message; see
    test_process_job_fails_gracefully_when_vl_connection_missing below."""
    admin_user = _user('vldelrefadmin', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)

    connection = _make_vl_connection()
    job_id = f'job-vldelref-{uuid.uuid4().hex[:8]}'
    _make_pending_job_with_settings(job_id, f'{job_id}.pdf', {
        'profile_id': f'vl:{connection.id}',
        'vl_connection_id': connection.id,
    })

    resp = admin.delete(f'/api/v1/auth/admin/vl-connections/{connection.id}')
    assert resp.status_code == 200

    db = _db()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.PENDING  # untouched -- no cascade, no integrity error
        info = job.processing_info
        assert info['settings']['vl_connection_id'] == connection.id
    finally:
        db.close()


def test_non_admin_cannot_manage_vl_connections():
    user = _user('vlnonadmin', role=UserRole.USER)
    client = login_as(user.username)

    assert client.get('/api/v1/auth/admin/vl-connections').status_code == 403
    assert client.post(
        '/api/v1/auth/admin/vl-connections',
        json={'name': 'x', 'base_url': 'https://x.example.com', 'model': 'm', 'api_key': 'k'},
    ).status_code == 403


def test_admin_vl_connection_base_url_validation():
    admin_user = _user('vlbadurl', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)

    resp = admin.post(
        '/api/v1/auth/admin/vl-connections',
        json={'name': 'x', 'base_url': 'ftp://bad.example.com', 'model': 'm', 'api_key': 'k'},
    )
    assert resp.status_code == 422


def test_admin_test_vl_connection_mocked(monkeypatch):
    admin_user = _user('vltest', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)
    connection = _make_vl_connection()

    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.paddle_service,
        'test_vl_connection',
        lambda base_url, model, api_key, system_prompt, timeout_seconds=20.0: {
            'ok': True, 'detail': 'Connected', 'latency_ms': 42,
        },
    )

    resp = admin.post(f'/api/v1/auth/admin/vl-connections/{connection.id}/test')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True, 'detail': 'Connected', 'latency_ms': 42}


def test_admin_test_vl_connection_decrypt_failure_returns_ok_false_null_latency(monkeypatch):
    admin_user = _user('vldecrypt', role=UserRole.ADMIN)
    admin = login_as(admin_user.username)
    connection = _make_vl_connection()

    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module,
        'decrypt_vl_api_key',
        lambda ciphertext: (_ for _ in ()).throw(ValueError('bad key')),
    )

    resp = admin.post(f'/api/v1/auth/admin/vl-connections/{connection.id}/test')
    assert resp.status_code == 200
    body = resp.json()
    assert body['ok'] is False
    assert body['latency_ms'] is None
    assert 'decrypted' in body['detail']


# --- User: VL connections (read-only, enabled only) ----------------------------

def test_user_vl_connections_only_enabled_and_minimal_shape():
    user = _user('vlpublic', role=UserRole.USER)
    client = login_as(user.username)
    enabled = _make_vl_connection(name='Enabled Conn', model='model-a', enabled=True)
    _make_vl_connection(name='Disabled Conn', model='model-b', enabled=False)

    resp = client.get('/api/v1/vl-connections')
    assert resp.status_code == 200
    items = resp.json()['items']
    ids = {item['id'] for item in items}
    assert enabled.id in ids
    assert all(set(item.keys()) == {'id', 'name', 'model'} for item in items)
    names = [item['name'] for item in items]
    assert 'Disabled Conn' not in names


def test_paddle_capabilities_lists_enabled_vl_connections_after_static_profiles():
    user = _user('vlcaps', role=UserRole.USER)
    client = login_as(user.username)
    enabled = _make_vl_connection(name='Enabled Vision', model='gpt-4o', enabled=True)
    _make_vl_connection(name='Disabled Vision', model='gpt-4o-mini', enabled=False)

    resp = client.get('/api/v1/paddle/capabilities')
    assert resp.status_code == 200
    profiles = resp.json()['profiles']

    static_profiles = [p for p in profiles if p['kind'] == 'ocr']
    vl_profiles = [p for p in profiles if p['kind'] == 'vl']
    # Static presets first, dynamic VL entries appended after -- see
    # paddle_service.get_paddle_capabilities.
    assert profiles == static_profiles + vl_profiles
    assert any(p['value'] == 'ppocrv6_tiny' for p in static_profiles)

    vl_values = {p['value'] for p in vl_profiles}
    assert f'vl:{enabled.id}' in vl_values
    assert all('Disabled Vision' != p['label'].removeprefix('VL: ') for p in vl_profiles)
    enabled_entry = next(p for p in vl_profiles if p['value'] == f'vl:{enabled.id}')
    assert enabled_entry['label'] == 'VL: Enabled Vision'
    assert enabled_entry['description'] == 'gpt-4o — vision-language connection'


# --- Benchmark create validation ------------------------------------------------

def _make_pending_job_with_settings(job_id: str, upload_path, settings_extra: dict) -> None:
    db = TestingSessionLocal()
    try:
        db.query(Job).filter(Job.id == job_id).delete()
        db.commit()
        db.add(
            Job(
                id=job_id,
                original_filename=f'{job_id}.pdf',
                upload_path=str(upload_path),
                upload_object_id=stored_upload(b'%PDF-1.4 fake upload content'),
                upload_mime_type='application/pdf',
                upload_size_bytes=len(b'%PDF-1.4 fake upload content'),
                status=JobStatus.PENDING,
                processing_info={'settings': settings_extra},
            )
        )
        db.commit()
    finally:
        db.close()


def test_process_job_fails_gracefully_when_vl_connection_missing(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    settings.worker_tmp_dir = tmp_path / 'work'

    upload_path = 'benchmarks/run-x/job-vl-missing/job-vl-missing.pdf'

    _make_pending_job_with_settings('job-vl-missing', upload_path, {
        'storage_folder': 'benchmarks/run-x/job-vl-missing',
        'vl_connection_id': str(uuid.uuid4()),  # never created -> missing
    })

    tasks.process_job('job-vl-missing', 'openai_vision', 'benchmark', '', None)

    db = TestingSessionLocal()
    try:
        job = db.get(Job, 'job-vl-missing')
        assert job.status == JobStatus.FAILED
        assert job.error_message == 'VL connection is no longer available'
        assert job.processing_info['execution']['status'] == 'failed'
    finally:
        db.close()


def test_process_job_fails_gracefully_when_vl_connection_disabled(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    settings.worker_tmp_dir = tmp_path / 'work'

    connection = _make_vl_connection(enabled=False)

    upload_path = 'benchmarks/run-y/job-vl-disabled/job-vl-disabled.pdf'

    _make_pending_job_with_settings('job-vl-disabled', upload_path, {
        'storage_folder': 'benchmarks/run-y/job-vl-disabled',
        'vl_connection_id': connection.id,
    })

    tasks.process_job('job-vl-disabled', 'openai_vision', 'benchmark', '', None)

    db = TestingSessionLocal()
    try:
        job = db.get(Job, 'job-vl-disabled')
        assert job.status == JobStatus.FAILED
        assert job.error_message == 'VL connection is no longer available'
    finally:
        db.close()
