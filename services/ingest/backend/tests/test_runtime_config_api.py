"""Admin configuration overview, checks and export (app/api/runtime_config.py)
and backups to S3 (app/api/backup.py's /s3 routes), against an in-memory
stand-in for the boto3 client."""

import time
import uuid
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

import app.api.backup as backup_api
from app.core.config import settings
from app.models.models import BackupRun, BackupRunStatus, UserRole
from app.services import object_store
from conftest import TestingSessionLocal, create_test_user, login_as


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def iter_chunks(self, chunk_size: int = 1024):
        for offset in range(0, len(self._data), chunk_size):
            yield self._data[offset:offset + chunk_size]

    def close(self) -> None:
        pass


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_args: dict = {}

    def head_bucket(self, Bucket):
        if Bucket != 'test-bucket':
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadBucket')

    def put_object(self, Bucket, Key, Body, **extra):
        self.objects[Key] = bytes(Body)
        self.put_args = extra
        return {'ServerSideEncryption': extra.get('ServerSideEncryption')} if extra.get('ServerSideEncryption') else {}

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None, Config=None):
        data = bytearray()
        while piece := Fileobj.read(7000):
            data.extend(piece)
        self.objects[Key] = bytes(data)

    def get_object(self, Bucket, Key, Range=None):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': _Body(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        fake = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                yield {'Contents': [
                    {'Key': key, 'Size': len(data), 'LastModified': datetime.now(timezone.utc)}
                    for key, data in fake.objects.items() if key.startswith(Prefix)
                ]}

        return _Paginator()


@pytest.fixture
def fake_s3(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(settings, 's3_bucket', 'test-bucket')
    monkeypatch.setattr(settings, 's3_secret_access_key', 'running-secret')
    monkeypatch.setattr(settings, 'backup_dir', tmp_path / 'backups')
    monkeypatch.setattr(object_store, 's3_client', lambda: fake)
    monkeypatch.setattr(object_store, 'build_s3_client', lambda **kwargs: fake.__setattr__('built', kwargs) or fake)
    monkeypatch.setattr(backup_api, 'SessionLocal', TestingSessionLocal)
    return fake


def _admin():
    unique = f'config-{uuid.uuid4().hex[:8]}'
    user = create_test_user(username=unique, email=f'{unique}@example.com', role=UserRole.ADMIN)
    return login_as(user.username)


def test_non_admin_cannot_read_config():
    unique = f'config-user-{uuid.uuid4().hex[:8]}'
    user = create_test_user(username=unique, email=f'{unique}@example.com')
    assert login_as(user.username).get('/api/v1/admin/config').status_code == 403


def test_overview_masks_secrets(fake_s3):
    data = _admin().get('/api/v1/admin/config').json()
    by_name = {item['name']: item for item in data['env']}
    assert by_name['S3_SECRET_ACCESS_KEY']['value'] == '••••••••'
    assert by_name['S3_SECRET_ACCESS_KEY']['secret'] is True
    assert by_name['S3_BUCKET'] == {**by_name['S3_BUCKET'], 'value': 'test-bucket', 'customized': True}
    assert by_name['S3_BUCKET']['group'] == 'object_storage'
    assert 'running-secret' not in str(data)
    assert {section['section'] for section in data['manual']} >= {'chat_providers', 'runtime_settings'}
    assert data['checks'] == ['database', 'redis', 's3', 'backup_dir']


def test_env_export_leaves_secrets_out(fake_s3):
    resp = _admin().get('/api/v1/admin/config/export?format=env')
    assert resp.status_code == 200
    assert resp.headers['cache-control'] == 'no-store'
    assert 'S3_BUCKET=test-bucket' in resp.text
    assert '# S3_SECRET_ACCESS_KEY=\n' in resp.text
    assert 'running-secret' not in resp.text
    assert 'running-secret' not in _admin().get('/api/v1/admin/config/export?format=json').text


def test_s3_check_uses_form_values_and_keeps_the_running_secret(fake_s3):
    resp = _admin().post('/api/v1/admin/config/checks/s3', json={
        's3_bucket': 'test-bucket', 's3_endpoint_url': 'http://minio:9000', 's3_secret_access_key': '', 's3_sse': 'AES256',
    })
    result = resp.json()
    assert result['ok'] is True, result
    assert [step['id'] for step in result['steps']] == ['config', 'head_bucket', 'put', 'get', 'delete']
    assert fake_s3.built['endpoint_url'] == 'http://minio:9000'
    assert fake_s3.built['secret_access_key'] == 'running-secret'
    assert fake_s3.put_args['ServerSideEncryption'] == 'AES256'
    assert not fake_s3.objects  # the probe object is gone again


def test_s3_check_reports_the_failing_step(fake_s3):
    result = _admin().post('/api/v1/admin/config/checks/s3', json={'s3_bucket': 'missing'}).json()
    assert result['ok'] is False
    assert result['steps'][-1]['id'] == 'head_bucket'
    assert 'Not Found' in result['steps'][-1]['detail']


def test_database_and_backup_dir_checks(fake_s3):
    admin = _admin()
    assert admin.post('/api/v1/admin/config/checks/database').json()['ok'] is True
    assert admin.post('/api/v1/admin/config/checks/backup_dir').json()['ok'] is True
    assert admin.post('/api/v1/admin/config/checks/nope').status_code == 404


def _wait_for_run(run_id: str) -> BackupRun:
    deadline = time.time() + 10
    while time.time() < deadline:
        with TestingSessionLocal() as db:
            run = db.get(BackupRun, run_id)
            if run.status in (BackupRunStatus.FINISHED, BackupRunStatus.FAILED):
                return run
        time.sleep(0.02)
    raise AssertionError('run did not finish')


def test_backup_to_s3_lists_validates_and_deletes(fake_s3):
    admin = _admin()
    started = admin.post('/api/v1/admin/backup/s3/exports', json={'passphrase': 'a-decent-passphrase-1234'})
    assert started.status_code == 202, started.text
    run = _wait_for_run(started.json()['id'])
    assert run.status == BackupRunStatus.FINISHED, run.error_message
    assert run.report['location'].startswith('s3://test-bucket/weave-ingest/backups/')

    listed = admin.get('/api/v1/admin/backup/s3').json()
    assert listed['configured'] is True
    (archive,) = listed['archives']
    assert fake_s3.objects[archive['key']][:2] == b'\x1f\x8b'
    assert archive['size_bytes'] == run.size_bytes

    wrong = admin.post('/api/v1/admin/backup/s3/imports', json={'key': archive['key'], 'passphrase': 'wrong-passphrase'})
    assert wrong.status_code == 422, wrong.text
    outside = admin.post('/api/v1/admin/backup/s3/imports', json={'key': 'weave-ingest/objects/x', 'passphrase': 'x'})
    assert outside.status_code == 404

    assert admin.delete('/api/v1/admin/backup/s3/archives', params={'key': archive['key']}).status_code == 204
    assert admin.get('/api/v1/admin/backup/s3').json()['archives'] == []


def test_backup_to_s3_needs_a_bucket(monkeypatch):
    monkeypatch.setattr(settings, 's3_bucket', '')
    admin = _admin()
    assert admin.get('/api/v1/admin/backup/s3').json() == {'configured': False, 'location': '', 'archives': []}
    resp = admin.post('/api/v1/admin/backup/s3/exports', json={'passphrase': 'a-decent-passphrase-1234'})
    assert resp.status_code == 409
