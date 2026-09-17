import hashlib
import uuid

from app.core.config import settings
from app.models.models import JobArtifact
from app.workers import publication_tasks
from tests.conftest import TestingSessionLocal, client, create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _job


def _artifact(job_id: str, *, filename: str = 'diagram.png', content: bytes = b'fake-png-bytes') -> JobArtifact:
    db = TestingSessionLocal()
    try:
        value = JobArtifact(
            job_id=job_id,
            kind='image',
            filename=filename,
            content_type='image/png',
            content=content,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        db.add(value)
        db.commit()
        db.refresh(value)
        db.expunge(value)
        return value
    finally:
        db.close()


def test_release_rewrites_relative_artifact_image_links_but_keeps_digest_of_original(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(
        username=f'portal-img-{uuid.uuid4().hex[:8]}',
        email=f'portal-img-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(
        user.id,
        collection,
        markdown='---\nengine: test\n---\n\n# Guide\n\n![Diagram](artifacts/diagram.png)\n',
    )
    _artifact(job.id)
    authed = login_as(user.username)

    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert 'artifacts/diagram.png' in preview['markdown']

    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    )
    assert released.status_code == 202, released.text
    release_id = released.json()['id']

    # The digest served back must still match the ORIGINAL (relative) snapshot's hash.
    downloaded = authed.get(f'/api/v1/portal/releases/{release_id}/download')
    assert downloaded.status_code == 200
    expected_url = f'{settings.public_api_url.rstrip("/")}/api/v1/portal/releases/{release_id}/artifacts/diagram.png'
    assert f'![Diagram]({expected_url})' in downloaded.text
    assert '](artifacts/diagram.png)' not in downloaded.text
    assert hashlib.sha256(preview['markdown'].encode()).hexdigest() == preview['markdown_sha256']


def test_release_event_payload_digest_matches_downloaded_markdown_bytes(monkeypatch):
    # Regression: Knowledge's fetch_released_markdown() verifies the downloaded
    # markdown body against payload['markdown_sha256']. That digest must match the
    # POST-rewrite bytes actually served, not the pre-rewrite canonical snapshot.
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(
        username=f'portal-img3-{uuid.uuid4().hex[:8]}',
        email=f'portal-img3-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(
        user.id,
        collection,
        markdown='---\nengine: test\n---\n\n# Guide\n\n![Diagram](artifacts/diagram.png)\n',
    )
    _artifact(job.id)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    )
    assert released.status_code == 202, released.text
    release_id = released.json()['id']

    downloaded = authed.get(f'/api/v1/portal/releases/{release_id}/download')
    assert downloaded.status_code == 200
    served_digest = hashlib.sha256(downloaded.content).hexdigest()

    from app.models.models import DocumentRelease

    db = TestingSessionLocal()
    try:
        release = db.get(DocumentRelease, release_id)
        payload_digest = release.payload['markdown_sha256']
    finally:
        db.close()

    assert payload_digest == served_digest


def test_release_artifact_endpoint_serves_bytes_and_404s_for_foreign_filename(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    user = create_test_user(
        username=f'portal-img2-{uuid.uuid4().hex[:8]}',
        email=f'portal-img2-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(
        user.id,
        collection,
        markdown='---\nengine: test\n---\n\n# Guide\n\n![Diagram](artifacts/diagram.png)\n',
    )
    artifact = _artifact(job.id)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    )
    release_id = released.json()['id']

    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', 'knowledge-test-credential')
    service_headers = {'Authorization': 'Bearer knowledge-test-credential'}
    url = f'/api/v1/portal/releases/{release_id}/artifacts/{artifact.filename}'
    response = client.get(url, headers=service_headers)
    assert response.status_code == 200
    assert response.content == b'fake-png-bytes'
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['x-content-type-options'] == 'nosniff'

    missing = client.get(f'/api/v1/portal/releases/{release_id}/artifacts/does-not-exist.png', headers=service_headers)
    assert missing.status_code == 404

    assert client.get(url, headers={'Authorization': 'Bearer wrong'}).status_code == 401
