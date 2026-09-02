"""Portal collection behavior for ordinary EML uploads.

The test deliberately uses the normal collection upload and job restart
routes. It does not call the retired mail API or create a MailMessage row.
"""

from email.message import EmailMessage
import hashlib
import uuid

from app.core.config import settings
from app.models.models import Job, JobStatus
from app.services.security import rate_limiter
from app.workers import tasks
from conftest import TestingSessionLocal, create_test_user, login_as


def _db():
    return TestingSessionLocal()


def _eml_with_unsupported_attachment(body: str = 'The email body is processed as the document content.') -> bytes:
    message = EmailMessage()
    message['Subject'] = 'Portal EML upload'
    message['From'] = 'sender@example.com'
    message['To'] = 'portal@example.com'
    message['Message-ID'] = f'<{uuid.uuid4().hex}@example.com>'
    message.set_content(body)
    message.add_attachment(
        b'PK\x03\x04 archive bytes',
        maintype='application',
        subtype='zip',
        filename='archive.zip',
    )
    return message.as_bytes()


def _user(prefix: str):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(
        username=f'{prefix}-{suffix}',
        email=f'{prefix}-{suffix}@example.com',
    )


def test_eml_collection_upload_waits_for_manual_restart_and_portal_release(monkeypatch, tmp_path):
    """An EML uses the ordinary collection Job flow and direct EML converter.

    Uploading only persists a pending job. Job restart is the explicit
    processing action, and portal release remains a separate explicit review
    action after processing. The ZIP attachment is reported as skipped by the
    existing EML converter; it does not become a separate mail job.
    """
    from app.api import routes
    from app.core import config
    from app.services import paddle_service

    rate_limiter.reset()
    monkeypatch.setattr(settings, 'uploads_dir', tmp_path / 'uploads')
    monkeypatch.setattr(settings, 'results_dir', tmp_path / 'results')
    monkeypatch.setattr(config.settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(config.settings, 'portal_knowledge_webhook_secret', 'test-secret')

    user = _user('portal-eml')
    portal_client = login_as(user.username)

    collection_response = portal_client.post(
        '/api/v1/collections',
        json={'name': 'Incoming portal mail'},
    )
    assert collection_response.status_code == 200, collection_response.text
    collection = collection_response.json()

    raw_eml = _eml_with_unsupported_attachment()
    dispatched: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: dispatched.append(args))

    upload_response = portal_client.post(
        f"/api/v1/collections/{collection['collection_id']}/upload",
        files={'file': ('incoming.eml', raw_eml, 'message/rfc822')},
    )
    assert upload_response.status_code == 200, upload_response.text
    job_id = upload_response.json()['job_id']
    assert upload_response.json()['status'] == JobStatus.PENDING.value
    assert dispatched == []

    second_upload_response = portal_client.post(
        f"/api/v1/collections/{collection['collection_id']}/upload",
        files={
            'file': (
                'later.eml',
                _eml_with_unsupported_attachment('A later document remains pending.'),
                'message/rfc822',
            )
        },
    )
    assert second_upload_response.status_code == 200, second_upload_response.text
    second_job_id = second_upload_response.json()['job_id']
    assert second_upload_response.json()['status'] == JobStatus.PENDING.value

    db = _db()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.original_filename == 'incoming.eml'
        assert job.upload_content == raw_eml
        assert job.processing_info['settings']['mode'] == 'collection'
        assert job.processing_info['settings']['collection_id'] == collection['collection_id']
    finally:
        db.close()

    # A pending document cannot cross the portal approval boundary.
    before_processing = portal_client.post(
        f'/api/v1/portal/documents/{job_id}/release',
        json={'markdown_sha256': '0' * 64},
    )
    assert before_processing.status_code == 409, before_processing.text
    assert before_processing.json()['detail'] == 'Job is not finished'

    # Restart only the selected job. The portal does not start the rest of the
    # collection as a side effect.
    selected_profile = 'ppocrv6_tiny_structurev3'
    restart_response = portal_client.post(
        f'/api/v1/jobs/{job_id}/restart',
        json={'profile_id': selected_profile},
    )
    assert restart_response.status_code == 200, restart_response.text
    assert restart_response.json()['profile_id'] == selected_profile
    assert len(dispatched) == 1
    assert dispatched[0][0] == job_id
    assert dispatched[0][1] == selected_profile

    # Run the real worker body synchronously. EML conversion takes its direct
    # branch before PaddleOCR availability/model loading is consulted.
    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(
        paddle_service,
        '_paddleocr_available',
        lambda: (_ for _ in ()).throw(AssertionError('EML upload must not probe PaddleOCR')),
    )
    # The API request commits before the worker runs in production. Invoke
    # that captured dispatch after the request so the worker's execution JSON
    # cannot be overwritten by the start request's older ORM snapshot.
    tasks.process_job(*dispatched[0])

    db = _db()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FINISHED, job.error_message
        assert job.processing_info['execution']['engine'] == 'mail-eml'
        assert job.result_markdown is not None
        assert 'The email body is processed as the document content.' in job.result_markdown
        assert '(skipped: archive.zip — unsupported_type)' in job.result_markdown
        assert f"collection: {collection['slug']}" in job.result_markdown
        assert f"collection_name: {collection['name']}" in job.result_markdown
        later_job = db.get(Job, second_job_id)
        assert later_job is not None
        assert later_job.status == JobStatus.PENDING
    finally:
        db.close()

    preview = portal_client.get(f'/api/v1/portal/documents/{job_id}')
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body['can_release'] is True
    assert preview_body['release'] is None

    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    release_response = portal_client.post(
        f'/api/v1/portal/documents/{job_id}/release',
        json={'markdown_sha256': hashlib.sha256(preview_body['markdown'].encode()).hexdigest()},
    )
    assert release_response.status_code == 202, release_response.text
