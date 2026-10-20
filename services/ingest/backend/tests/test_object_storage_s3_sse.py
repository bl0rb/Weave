"""S3_SSE / S3_KMS_KEY_ID: the encryption headers on the HTTP requests a real
boto3 client sends for every write path in app/services/object_store.py
(put_object, and upload_fileobj below and above the multipart part size).
A before-send hook answers each request locally, so nothing leaves the
process, and records the headers a bucket policy or SCP would check."""

import io
import os

import pytest

from app.core.config import settings
from app.services import object_store

KEY_ARN = 'arn:aws:kms:eu-central-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab'
SSE = 'x-amz-server-side-encryption'
SSE_KEY = 'x-amz-server-side-encryption-aws-kms-key-id'


class _Raw:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, *args, **kwargs):
        yield self._body


_BODIES = {
    'CreateMultipartUpload': b'<InitiateMultipartUploadResult><Bucket>test-bucket</Bucket>'
                             b'<Key>k</Key><UploadId>upload-1</UploadId></InitiateMultipartUploadResult>',
    'CompleteMultipartUpload': b'<CompleteMultipartUploadResult><Bucket>test-bucket</Bucket>'
                               b'<Key>k</Key><ETag>"etag"</ETag></CompleteMultipartUploadResult>',
}


@pytest.fixture
def requests(monkeypatch):
    """(operation, headers) of every request, in send order."""
    import boto3
    from botocore.awsrequest import AWSResponse

    sent: list[tuple[str, dict]] = []

    def respond(request, event_name, **kwargs):
        operation = event_name.rsplit('.', 1)[-1]
        headers = {name.lower(): value for name, value in request.headers.items()}
        sent.append((operation, {name: v.decode() if isinstance(v, bytes) else v for name, v in headers.items()}))
        body = _BODIES.get(operation, b'')
        return AWSResponse(request.url, 200, {'ETag': '"etag"'}, _Raw(body))

    client = boto3.client(
        's3', region_name='eu-central-1', aws_access_key_id='test', aws_secret_access_key='test',
    )
    client.meta.events.register('before-send.s3', respond)
    monkeypatch.setattr(object_store, '_client', (os.getpid(), client))
    monkeypatch.setattr(settings, 's3_bucket', 'test-bucket')
    monkeypatch.setattr(settings, 'object_chunk_bytes', 64 * 1024)
    return sent


def _part_bytes() -> int:
    return object_store._transfer_config().multipart_chunksize


def _write(path: str, size: int) -> None:
    data = os.urandom(size)
    if path == 'put_object':
        object_store._s3_put('weave-test/objects/k', data, 'application/pdf')
    else:
        object_store.s3_upload_file(io.BytesIO(data), 'weave-test/objects/k', 'application/pdf')


WRITES = [
    pytest.param('put_object', 1000, ['PutObject'], id='put_object'),
    pytest.param('upload_fileobj', 1000, ['PutObject'], id='upload_fileobj-small'),
    pytest.param(
        'upload_fileobj', None,
        ['CreateMultipartUpload', 'UploadPart', 'UploadPart', 'CompleteMultipartUpload'],
        id='upload_fileobj-multipart',
    ),
]


@pytest.mark.parametrize('path, size, operations', WRITES)
def test_kms_headers_on_every_write(requests, monkeypatch, path, size, operations) -> None:
    monkeypatch.setattr(settings, 's3_sse', 'aws:kms')
    monkeypatch.setattr(settings, 's3_kms_key_id', KEY_ARN)

    _write(path, size or _part_bytes() + 1)  # None: one byte over the part size, two parts

    assert sorted(op for op, _ in requests) == sorted(operations)
    for operation, headers in requests:
        if operation in ('PutObject', 'CreateMultipartUpload'):
            # The request that creates the object carries both headers ...
            assert headers[SSE] == 'aws:kms'
            assert headers[SSE_KEY] == KEY_ARN
            assert headers['content-type'] == 'application/pdf'
        else:
            # ... parts and completion inherit them (S3 rejects them there).
            assert SSE not in headers and SSE_KEY not in headers


@pytest.mark.parametrize('path, size, operations', WRITES)
def test_without_settings_no_encryption_headers(requests, path, size, operations) -> None:
    assert settings.s3_sse == '' and settings.s3_kms_key_id == ''  # 0.7.11 behaviour

    _write(path, size or _part_bytes() + 1)

    assert sorted(op for op, _ in requests) == sorted(operations)
    for _operation, headers in requests:
        assert SSE not in headers and SSE_KEY not in headers


def test_sse_without_key_id_uses_bucket_managed_key(requests, monkeypatch) -> None:
    monkeypatch.setattr(settings, 's3_sse', 'aws:kms')

    _write('put_object', 10)

    [(_op, headers)] = requests
    assert headers[SSE] == 'aws:kms' and SSE_KEY not in headers


def test_key_id_without_kms_is_refused(requests, monkeypatch) -> None:
    monkeypatch.setattr(settings, 's3_kms_key_id', KEY_ARN)

    with pytest.raises(object_store.ObjectStorageConfigError, match='S3_SSE=aws:kms'):
        _write('put_object', 10)
    assert requests == []
