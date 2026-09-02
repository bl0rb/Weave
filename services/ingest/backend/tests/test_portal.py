import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import yaml
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Collection, DocumentRelease, ImportRun, ImportRunStatus, Job, JobStatus, Team, UserRole
from app.workers import publication_tasks
from tests.conftest import TestingSessionLocal, client, create_test_user, login_as


def _db():
    return TestingSessionLocal()


def _team(name: str) -> Team:
    db = _db()
    try:
        value = Team(name=f'{name}-{uuid.uuid4().hex[:8]}')
        db.add(value)
        db.commit()
        db.refresh(value)
        db.expunge(value)
        return value
    finally:
        db.close()


def _collection(owner_id: str, *, read_teams: list[str] | None = None) -> Collection:
    db = _db()
    try:
        value = Collection(
            owner_id=owner_id,
            slug=f'portal-{uuid.uuid4().hex[:12]}',
            name='Portal collection',
            read_teams=read_teams or [],
        )
        db.add(value)
        db.commit()
        db.refresh(value)
        db.expunge(value)
        return value
    finally:
        db.close()


def _job(
    owner_id: str,
    collection: Collection,
    *,
    markdown: str | None = None,
    quality: str = 'allow',
    import_run_id: str | None = None,
) -> Job:
    db = _db()
    try:
        value = Job(
            original_filename='guide.pdf',
            upload_path='/tmp/guide.pdf',
            status=JobStatus.FINISHED,
            owner_id=owner_id,
            import_run_id=import_run_id,
            content_sha256=hashlib.sha256(b'portal-upload').hexdigest(),
            result_markdown=markdown or '---\ncollection: edited\nengine: test\nprocessed_at: now\n---\n\n# Guide\n',
            processing_info={
                'settings': {'collection_id': collection.id},
                'execution': {'quality_gate': {'grade': 'A', 'recommendation': quality}},
            },
        )
        db.add(value)
        db.commit()
        db.refresh(value)
        db.expunge(value)
        return value
    finally:
        db.close()


def _import_run(status: ImportRunStatus) -> ImportRun:
    db = _db()
    try:
        value = ImportRun(
            kind='confluence',
            scope_type='page',
            scope_value='portal-page',
            status=status,
            options={},
            state={},
        )
        db.add(value)
        db.commit()
        db.refresh(value)
        db.expunge(value)
        return value
    finally:
        db.close()


def _configure(monkeypatch):
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'secret')


def test_portal_config_returns_authenticated_team_name(monkeypatch):
    team = _team('Support')
    user = create_test_user(
        username=f'portal-config-{uuid.uuid4().hex[:8]}',
        email=f'portal-config-{uuid.uuid4().hex[:8]}@example.com',
        team_id=team.id,
    )
    response = login_as(user.username).get('/api/v1/portal/config')
    assert response.status_code == 200
    assert response.json() == {'publication_configured': False, 'team_name': team.name}


def test_portal_preview_hash_canonicalizes_authoritative_collection_and_release(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-owner-{uuid.uuid4().hex[:8]}',
        email=f'portal-owner-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)

    preview = authed.get(f'/api/v1/portal/documents/{job.id}')
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert 'collection: edited' not in body['markdown']
    assert f'collection: {collection.slug}' in body['markdown']
    assert body['markdown_sha256'] == hashlib.sha256(body['markdown'].encode()).hexdigest()

    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': body['markdown_sha256']},
    )
    assert released.status_code == 202, released.text
    release_id = released.json()['id']
    downloaded = authed.get(f'/api/v1/portal/releases/{release_id}/download')
    assert downloaded.status_code == 200
    assert downloaded.text == body['markdown']

    db = _db()
    try:
        db.get(Job, job.id).result_markdown = body['markdown'] + '\nEdited after approval\n'
        db.commit()
    finally:
        db.close()

    frozen_preview = authed.get(f'/api/v1/portal/documents/{job.id}')
    assert frozen_preview.status_code == 200
    assert frozen_preview.json()['markdown'] == body['markdown']
    assert frozen_preview.json()['markdown_sha256'] == body['markdown_sha256']

    db = _db()
    try:
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.id == release_id))
        assert release is not None
        assert release.payload['event'] == 'document.released'
        assert release.payload['release_id'] == release_id
    finally:
        db.close()


def test_portal_markdown_download_uses_frozen_release_and_safe_headers(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-download-{uuid.uuid4().hex[:8]}',
        email=f'portal-download-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(user.id, collection, markdown='---\nengine: test\n---\n\n# Frozen export\n')
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    assert authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    ).status_code == 202

    db = _db()
    try:
        stored = db.get(Job, job.id)
        stored.original_filename = '../Team Richtlinie?.pdf'
        stored.result_markdown = '---\nengine: test\n---\n\n# Changed later\n'
        db.commit()
    finally:
        db.close()

    response = authed.get(f'/api/v1/portal/documents/{job.id}/markdown')
    assert response.status_code == 200, response.text
    assert response.text == preview['markdown']
    assert 'Team Richtlinie_.md' in response.headers['content-disposition']
    assert '../' not in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_portal_collection_zip_contains_only_visible_ready_unprotected_markdown(monkeypatch):
    _configure(monkeypatch)
    team = _team('Portal export')
    owner = create_test_user(
        username=f'portal-export-owner-{uuid.uuid4().hex[:8]}',
        email=f'portal-export-owner-{uuid.uuid4().hex[:8]}@example.com',
        team_id=team.id,
    )
    outsider = create_test_user(
        username=f'portal-export-outsider-{uuid.uuid4().hex[:8]}',
        email=f'portal-export-outsider-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(owner.id)
    frozen = _job(owner.id, collection, markdown='---\nengine: test\n---\n\n# Frozen marker\n')
    current = _job(owner.id, collection, markdown='---\nengine: test\n---\n\n# Current marker\n')
    protected = _job(owner.id, collection, markdown='---\nengine: test\n---\n\n# Protected marker\n')
    hidden = _job(outsider.id, collection, markdown='---\nengine: test\n---\n\n# Hidden marker\n')
    pending = _job(owner.id, collection, markdown='---\nengine: test\n---\n\n# Pending marker\n')
    other_collection = _collection(owner.id)
    other = _job(owner.id, other_collection, markdown='---\nengine: test\n---\n\n# Other marker\n')

    authed = login_as(owner.username)
    preview = authed.get(f'/api/v1/portal/documents/{frozen.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    assert authed.post(
        f'/api/v1/portal/documents/{frozen.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    ).status_code == 202

    db = _db()
    try:
        db.get(Job, frozen.id).original_filename = '../Handbuch.pdf'
        db.get(Job, frozen.id).result_markdown = '---\nengine: test\n---\n\n# Later edit marker\n'
        db.get(Job, current.id).original_filename = 'Handbuch.PDF'
        db.get(Job, protected.id).password_hash = 'opaque-password-hash'
        db.get(Job, pending.id).status = JobStatus.PENDING
        db.commit()
    finally:
        db.close()

    response = authed.get(f'/api/v1/portal/collections/{collection.id}/markdown.zip')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    assert response.headers['cache-control'] == 'private, no-store'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        contents = '\n'.join(archive.read(name).decode() for name in names)
    assert len(names) == 2
    assert len({name.casefold() for name in names}) == 2
    assert all('/' not in name and '\\' not in name and name.endswith('.md') for name in names)
    assert '# Frozen marker' in contents
    assert '# Current marker' in contents
    assert 'Later edit marker' not in contents
    assert 'Protected marker' not in contents
    assert 'Hidden marker' not in contents
    assert 'Pending marker' not in contents
    assert 'Other marker' not in contents


def test_portal_collection_zip_hides_foreign_collection_and_rejects_empty_export():
    owner = create_test_user(
        username=f'portal-empty-owner-{uuid.uuid4().hex[:8]}',
        email=f'portal-empty-owner-{uuid.uuid4().hex[:8]}@example.com',
    )
    outsider = create_test_user(
        username=f'portal-empty-outsider-{uuid.uuid4().hex[:8]}',
        email=f'portal-empty-outsider-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(owner.id)
    assert login_as(outsider.username).get(
        f'/api/v1/portal/collections/{collection.id}/markdown.zip'
    ).status_code == 404
    empty = login_as(owner.username).get(f'/api/v1/portal/collections/{collection.id}/markdown.zip')
    assert empty.status_code == 404


def test_portal_release_normalizes_unquoted_yaml_datetime_before_json_persistence(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-date-{uuid.uuid4().hex[:8]}', email=f'portal-date-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    job = _job(
        user.id,
        collection,
        markdown='---\nprocessed_at: 2026-09-01T12:00:00Z\nnested:\n  happened: 2026-09-01\nengine: test\n---\n\n# Date\n',
    )
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}')
    assert preview.status_code == 200, preview.text
    preview_markdown = preview.json()['markdown']
    frontmatter_end = preview_markdown.find('\n---\n', 4)
    preview_frontmatter = yaml.safe_load(preview_markdown[4:frontmatter_end + 1])
    assert preview_frontmatter['processed_at'] == '2026-09-01T12:00:00+00:00'
    assert isinstance(preview_frontmatter['processed_at'], str)
    assert preview_frontmatter['nested']['happened'] == '2026-09-01'
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview.json()['markdown_sha256']},
    )
    assert released.status_code == 202, released.text
    db = _db()
    try:
        release = db.get(DocumentRelease, released.json()['id'])
        assert release.payload['frontmatter']['processed_at'] == '2026-09-01T12:00:00+00:00'
        assert release.payload['frontmatter']['nested']['happened'] == '2026-09-01'
    finally:
        db.close()


def test_portal_release_rejects_non_json_yaml_shape(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-yaml-{uuid.uuid4().hex[:8]}', email=f'portal-yaml-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    job = _job(user.id, collection, markdown='---\nvalues: !!set {one: null}\nengine: test\n---\n\n# Shape\n')
    authed = login_as(user.username)
    response = authed.get(f'/api/v1/portal/documents/{job.id}')
    assert response.status_code == 409


def test_portal_release_requires_control_and_stale_preview_is_rejected(monkeypatch):
    _configure(monkeypatch)
    team = _team('Portal shared')
    owner = create_test_user(
        username=f'portal-owner-{uuid.uuid4().hex[:8]}', email=f'portal-owner-{uuid.uuid4().hex[:8]}@example.com', team_id=team.id
    )
    teammate = create_test_user(
        username=f'portal-teammate-{uuid.uuid4().hex[:8]}', email=f'portal-teammate-{uuid.uuid4().hex[:8]}@example.com', team_id=team.id
    )
    collection = _collection(owner.id)
    job = _job(owner.id, collection)
    owner_client = login_as(owner.username)
    preview = owner_client.get(f'/api/v1/portal/documents/{job.id}').json()

    denied = login_as(teammate.username).post(
        f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']}
    )
    assert denied.status_code == 403

    db = _db()
    try:
        db.get(Job, job.id).result_markdown += 'changed\n'
        db.commit()
    finally:
        db.close()
    stale = owner_client.post(
        f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']}
    )
    assert stale.status_code == 409


def test_portal_release_waits_for_import_run_finalization(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-import-gate-{uuid.uuid4().hex[:8]}',
        email=f'portal-import-gate-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    clients = login_as(user.username)
    jobs: dict[ImportRunStatus, Job] = {}
    for run_status in (ImportRunStatus.RUNNING, ImportRunStatus.CANCELLED, ImportRunStatus.FAILED, ImportRunStatus.FINISHED):
        run = _import_run(run_status)
        jobs[run_status] = _job(user.id, collection, import_run_id=run.id)

    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    for run_status, job in jobs.items():
        preview = clients.get(f'/api/v1/portal/documents/{job.id}')
        assert preview.status_code == 200, preview.text
        assert preview.json()['can_release'] is (run_status == ImportRunStatus.FINISHED)
        response = clients.post(
            f'/api/v1/portal/documents/{job.id}/release',
            json={'markdown_sha256': preview.json()['markdown_sha256']},
        )
        assert response.status_code == (202 if run_status == ImportRunStatus.FINISHED else 409), response.text

    listed = clients.get('/api/v1/portal/documents', params={'limit': 20})
    assert listed.status_code == 200, listed.text
    by_id = {item['id']: item for item in listed.json()['items']}
    assert all(by_id[job.id]['can_release'] is (run_status == ImportRunStatus.FINISHED) for run_status, job in jobs.items())


def test_collection_upload_and_start_require_collection_control(monkeypatch):
    _configure(monkeypatch)
    team = _team('Portal collection control')
    owner = create_test_user(
        username=f'portal-collection-owner-{uuid.uuid4().hex[:8]}',
        email=f'portal-collection-owner-{uuid.uuid4().hex[:8]}@example.com',
        team_id=team.id,
    )
    teammate = create_test_user(
        username=f'portal-collection-teammate-{uuid.uuid4().hex[:8]}',
        email=f'portal-collection-teammate-{uuid.uuid4().hex[:8]}@example.com',
        team_id=team.id,
    )
    admin = create_test_user(
        username=f'portal-collection-admin-{uuid.uuid4().hex[:8]}',
        email=f'portal-collection-admin-{uuid.uuid4().hex[:8]}@example.com',
        role=UserRole.ADMIN,
    )
    collection = _collection(owner.id)
    teammate_client = login_as(teammate.username)
    assert teammate_client.get(f'/api/v1/collections/{collection.id}').json()['can_manage'] is False
    assert login_as(owner.username).get(f'/api/v1/collections/{collection.id}').json()['can_manage'] is True
    assert login_as(admin.username).get(f'/api/v1/collections/{collection.id}').json()['can_manage'] is True
    teammate_client = login_as(teammate.username)
    denied_upload = teammate_client.post(
        f'/api/v1/collections/{collection.id}/upload',
        files={'file': ('teammate.pdf', b'%PDF-teammate', 'application/pdf')},
    )
    assert denied_upload.status_code == 403
    denied_start = teammate_client.post(
        f'/api/v1/collections/{collection.id}/start',
        json={'profile_id': 'ppocrv6_tiny'},
    )
    assert denied_start.status_code == 403

    db = _db()
    try:
        assert db.query(Job).filter(Job.owner_id == teammate.id).count() == 0
    finally:
        db.close()

    owner_client = login_as(owner.username)
    created = owner_client.post(
        f'/api/v1/collections/{collection.id}/upload',
        files={'file': ('owner.pdf', b'%PDF-owner', 'application/pdf')},
    )
    assert created.status_code == 200, created.text
    process_calls: list[str] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda job_id, *args: process_calls.append(job_id))
    started = login_as(admin.username).post(
        f'/api/v1/collections/{collection.id}/start',
        json={'profile_id': 'ppocrv6_tiny'},
    )
    assert started.status_code == 200, started.text
    assert started.json()['started_jobs'] == 1
    assert process_calls == [created.json()['job_id']]


def test_issued_release_blocks_save_restart_and_collection_start_preflight(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-immutable-{uuid.uuid4().hex[:8]}',
        email=f'portal-immutable-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    released = authed.post(
        f'/api/v1/portal/documents/{job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    )
    assert released.status_code == 202, released.text

    saved = authed.put(
        f'/api/v1/jobs/{job.id}/save',
        json={'markdown': preview['markdown'] + '\nchanged\n'},
    )
    assert saved.status_code == 409, saved.text
    restarted = authed.post(f'/api/v1/jobs/{job.id}/restart', json={})
    assert restarted.status_code == 409, restarted.text

    process_calls: list[str] = []
    monkeypatch.setattr('app.api.routes.process_job.delay', lambda job_id, *args: process_calls.append(job_id))
    started = authed.post(
        f'/api/v1/collections/{collection.id}/start',
        json={'profile_id': 'ppocrv6_tiny'},
    )
    assert started.status_code == 409, started.text
    assert process_calls == []


def test_portal_review_only_filters_finished_unreleased_and_paginates(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-review-{uuid.uuid4().hex[:8]}', email=f'portal-review-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    released_job = _job(user.id, collection)
    blocked_job = _job(user.id, collection, quality='block')
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{released_job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    assert authed.post(
        f'/api/v1/portal/documents/{released_job.id}/release',
        json={'markdown_sha256': preview['markdown_sha256']},
    ).status_code == 202

    response = authed.get('/api/v1/portal/documents', params={'review_only': 'true', 'limit': 1})
    assert response.status_code == 200
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['id'] == blocked_job.id
    assert response.json()['items'][0]['can_release'] is False


def test_portal_retry_handles_sqlite_naive_lease_timestamp(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-lease-{uuid.uuid4().hex[:8]}', email=f'portal-lease-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    release = authed.post(
        f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']}
    ).json()

    db = _db()
    try:
        row = db.get(DocumentRelease, release['id'])
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    retried = authed.post(f"/api/v1/portal/releases/{release['id']}/retry")
    assert retried.status_code == 200, retried.text


def test_issued_release_blocks_job_deletion_with_conflict(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-delete-{uuid.uuid4().hex[:8]}', email=f'portal-delete-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    assert authed.post(
        f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']}
    ).status_code == 202
    deleted = authed.delete(f'/api/v1/jobs/{job.id}')
    assert deleted.status_code == 409, deleted.text


def test_failed_release_delivery_can_be_reconciled_and_download_stays_snapshot(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-worker-{uuid.uuid4().hex[:8]}', email=f'portal-worker-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda release_id: None)
    response = authed.post(
        f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']}
    )
    release_id = response.json()['id']
    monkeypatch.setattr(publication_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(publication_tasks, 'send_webhook_request', lambda *args, **kwargs: (503, 'temporary'))
    monkeypatch.setattr(publication_tasks.celery_app, 'send_task', lambda *args, **kwargs: None)
    publication_tasks.deliver_release.run(release_id)
    db = _db()
    try:
        release = db.get(DocumentRelease, release_id)
        assert release.status == 'pending'
        assert release.attempts == 1
    finally:
        db.close()

    monkeypatch.setattr(publication_tasks, 'send_webhook_request', lambda *args, **kwargs: (204, None))
    db = _db()
    try:
        db.get(DocumentRelease, release_id).next_attempt_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    publication_tasks.deliver_release.run(release_id)
    db = _db()
    try:
        assert db.get(DocumentRelease, release_id).status == 'sent'
    finally:
        db.close()
