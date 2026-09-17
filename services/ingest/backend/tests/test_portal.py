import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import yaml
from sqlalchemy import select

from app.core.config import settings
from app.models.models import (
    Collection,
    DocumentRelease,
    ImportAuthType,
    ImportPageState,
    ImportRun,
    ImportRunStatus,
    ImportSource,
    Job,
    JobStatus,
    MailMessage,
    Team,
    UserRole,
    user_teams,
)
from app.services import security
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
    mail_message_id: str | None = None,
) -> Job:
    db = _db()
    try:
        value = Job(
            original_filename='guide.pdf',
            upload_path='/tmp/guide.pdf',
            status=JobStatus.FINISHED,
            owner_id=owner_id,
            import_run_id=import_run_id,
            mail_message_id=mail_message_id,
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


def _import_source(owner_id: str) -> ImportSource:
    db = _db()
    try:
        source = ImportSource(
            owner_id=owner_id,
            name='Portal Confluence',
            base_url='https://portal.example.atlassian.net',
            server_kind='cloud',
            api_base_path='/wiki/api/v2',
            auth_type=ImportAuthType.CLOUD_BASIC,
            auth_username='portal@example.com',
            credential_encrypted=security.encrypt_import_credential('portal-token'),
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        db.expunge(source)
        return source
    finally:
        db.close()


def _import_page_state(source_id: str, job_id: str, *, title: str = 'Portal Page', url: str = 'https://portal.example.atlassian.net/wiki/spaces/X/pages/1') -> ImportPageState:
    db = _db()
    try:
        page = ImportPageState(
            source_id=source_id,
            page_id=f'page-{uuid.uuid4().hex[:8]}',
            page_version=1,
            job_id=job_id,
            title=title,
            url=url,
        )
        db.add(page)
        db.commit()
        db.refresh(page)
        db.expunge(page)
        return page
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


def test_grade_c_requires_confirmation_and_preserves_quality(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    suffix = uuid.uuid4().hex[:8]
    user = create_test_user(username=f'quality-{suffix}', email=f'quality-{suffix}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection, quality='block')
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.processing_info = {**stored.processing_info, 'execution': {'quality_gate': {'grade': 'C', 'recommendation': 'block'}}}
        db.commit()
    authed = login_as(user.username)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert preview['can_release'] is True
    payload = {'markdown_sha256': preview['markdown_sha256']}
    url = f'/api/v1/portal/documents/{job.id}/release'
    assert authed.post(url, json=payload).status_code == 409
    released = authed.post(url, json={**payload, 'accept_quality_warning': True})
    assert released.status_code == 202, released.text
    with _db() as db:
        event = db.get(DocumentRelease, released.json()['id']).payload
        assert event['quality_override'] is True
        assert event['quality']['grade'] == 'C'
        assert event['quality']['recommendation'] == 'block'


def test_portal_config_returns_authenticated_team_name(monkeypatch):
    team = _team('Support')
    user = create_test_user(
        username=f'portal-config-{uuid.uuid4().hex[:8]}',
        email=f'portal-config-{uuid.uuid4().hex[:8]}@example.com',
        team_id=team.id,
    )
    response = login_as(user.username).get('/api/v1/portal/config')
    assert response.status_code == 200
    assert response.json() == {'publication_configured': False, 'team_name': team.name, 'team_names': [team.name]}


def test_portal_config_lists_every_team_membership_not_only_the_primary(monkeypatch):
    primary = _team('Support')
    secondary = _team('Sales')
    user = create_test_user(
        username=f'portal-config-multi-{uuid.uuid4().hex[:8]}',
        email=f'portal-config-multi-{uuid.uuid4().hex[:8]}@example.com',
        team_id=primary.id,
    )
    with _db() as db:
        db.execute(user_teams.insert().values(user_id=user.id, team_id=secondary.id, role='member'))
        db.commit()
    response = login_as(user.username).get('/api/v1/portal/config')
    assert response.status_code == 200
    assert response.json()['team_name'] == primary.name
    assert sorted(response.json()['team_names']) == sorted([primary.name, secondary.name])


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
    assert released.json()['released_by'] == user.username
    release_id = released.json()['id']
    downloaded = authed.get(f'/api/v1/portal/releases/{release_id}/download')
    assert downloaded.status_code == 200
    assert downloaded.text == body['markdown']

    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', 'knowledge-test-credential')
    service_headers = {'Authorization': 'Bearer knowledge-test-credential'}
    snapshot_url = f'/api/v1/portal/releases/{release_id}/download'
    assert client.get(snapshot_url, headers=service_headers).text == body['markdown']
    assert client.get('/api/v1/collections/registry', headers=service_headers).status_code == 200
    for forbidden_url in ('/api/v1/auth/admin/users', '/api/v1/jobs', f'/api/v1/portal/documents/{job.id}'):
        assert client.get(forbidden_url, headers=service_headers).status_code == 401
    for protected_url in (snapshot_url, '/api/v1/collections/registry'):
        assert client.get(protected_url, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', '')
    assert client.get('/api/v1/collections/registry', headers=service_headers).status_code == 401

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
    contributed = _job(outsider.id, collection, markdown='---\nengine: test\n---\n\n# Contributed marker\n')
    hidden = _job(outsider.id, _collection(outsider.id), markdown='---\nengine: test\n---\n\n# Hidden marker\n')
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
    assert len(names) == 3
    assert len({name.casefold() for name in names}) == 3
    assert all('/' not in name and '\\' not in name and name.endswith('.md') for name in names)
    assert '# Frozen marker' in contents
    assert '# Current marker' in contents
    assert '# Contributed marker' in contents
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
    # Keep this negative case explicit: a missing row for the legacy primary
    # team is now treated as the historical default member role.
    with _db() as db:
        db.execute(user_teams.insert().values(user_id=teammate.id, team_id=team.id, role='reader'))
        db.commit()
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
    # This test covers an explicitly read-only membership. A legacy
    # ``users.team_id`` without a corresponding row is covered separately by
    # the collection compatibility regressions and remains an implicit member.
    with _db() as db:
        db.execute(user_teams.insert().values(user_id=teammate.id, team_id=team.id, role='reader'))
        db.commit()
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


def test_portal_documents_quality_grade_filter(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-grade-{uuid.uuid4().hex[:8]}', email=f'portal-grade-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    graded_job = _job(user.id, collection)
    ungraded_job = _job(user.id, collection)
    db = _db()
    try:
        row = db.get(Job, ungraded_job.id)
        row.processing_info = {**row.processing_info, 'execution': {}}
        db.add(row)
        db.commit()
    finally:
        db.close()
    authed = login_as(user.username)

    response = authed.get('/api/v1/portal/documents', params={'quality_grade': 'a'})
    assert response.status_code == 200
    assert {item['id'] for item in response.json()['items']} == {graded_job.id}

    response = authed.get('/api/v1/portal/documents', params={'quality_grade': 'none'})
    assert response.status_code == 200
    assert {item['id'] for item in response.json()['items']} == {ungraded_job.id}


def test_portal_document_detail_quality_and_missing_reason(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-quality-{uuid.uuid4().hex[:8]}', email=f'portal-quality-{uuid.uuid4().hex[:8]}@example.com'
    )
    collection = _collection(user.id)
    graded_job = _job(user.id, collection)
    ungraded_job = _job(user.id, collection)
    db = _db()
    try:
        row = db.get(Job, ungraded_job.id)
        row.processing_info = {**row.processing_info, 'execution': {}}
        db.add(row)
        db.commit()
    finally:
        db.close()
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{graded_job.id}').json()
    assert detail['quality']['grade'] == 'A'
    assert detail['quality']['recommendation'] == 'allow'
    assert detail['quality_missing_reason'] is None

    detail = authed.get(f'/api/v1/portal/documents/{ungraded_job.id}').json()
    assert detail['quality'] is None
    assert detail['quality_missing_reason'] == 'legacy'


def test_portal_document_detail_missing_reason_import_without_gate(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-quality-import-{uuid.uuid4().hex[:8]}',
        email=f'portal-quality-import-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    run = _import_run(ImportRunStatus.FINISHED)
    job = _job(user.id, collection, import_run_id=run.id)
    db = _db()
    try:
        row = db.get(Job, job.id)
        row.processing_info = {**row.processing_info, 'execution': {}}
        db.add(row)
        db.commit()
    finally:
        db.close()
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['quality'] is None
    assert detail['quality_missing_reason'] == 'import_without_gate'


def test_portal_document_detail_missing_reason_not_finished_and_failed(monkeypatch):
    _configure(monkeypatch)
    user = create_test_user(
        username=f'portal-quality-status-{uuid.uuid4().hex[:8]}',
        email=f'portal-quality-status-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    pending_job = _job(user.id, collection)
    failed_job = _job(user.id, collection)
    db = _db()
    try:
        pending_row = db.get(Job, pending_job.id)
        pending_row.status = JobStatus.PENDING
        pending_row.processing_info = {**pending_row.processing_info, 'execution': {}}
        failed_row = db.get(Job, failed_job.id)
        failed_row.status = JobStatus.FAILED
        failed_row.processing_info = {**failed_row.processing_info, 'execution': {}}
        db.add(pending_row)
        db.add(failed_row)
        db.commit()
    finally:
        db.close()
    authed = login_as(user.username)

    pending_detail = authed.get(f'/api/v1/portal/documents/{pending_job.id}').json()
    assert pending_detail['quality'] is None
    assert pending_detail['quality_missing_reason'] == 'not_finished'

    failed_detail = authed.get(f'/api/v1/portal/documents/{failed_job.id}').json()
    assert failed_detail['quality'] is None
    assert failed_detail['quality_missing_reason'] == 'failed'


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


def test_portal_document_source_reflects_upload_folder(monkeypatch):
    user = create_test_user(
        username=f'portal-source-{uuid.uuid4().hex[:8]}',
        email=f'portal-source-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    job = _job(user.id, collection)
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.processing_info = {
            **stored.processing_info,
            'settings': {**stored.processing_info['settings'], 'folder': 'Kunden', 'subfolder': 'Vertraege'},
        }
        db.commit()
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['source'] == {'kind': 'upload', 'label': 'Hochgeladen', 'path': 'Kunden/Vertraege', 'url': None}

    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id}).json()
    item = next(item for item in listing['items'] if item['id'] == job.id)
    assert item['source']['kind'] == 'upload'
    assert item['source']['path'] == 'Kunden/Vertraege'


def test_portal_document_source_reflects_confluence_page(monkeypatch):
    user = create_test_user(
        username=f'portal-confluence-{uuid.uuid4().hex[:8]}',
        email=f'portal-confluence-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    run = _import_run(ImportRunStatus.FINISHED)
    job = _job(user.id, collection, import_run_id=run.id)
    source = _import_source(user.id)
    page = _import_page_state(source.id, job.id, title='Betriebshandbuch', url='https://portal.example.atlassian.net/wiki/spaces/X/pages/42')
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['source'] == {'kind': 'confluence', 'label': 'Betriebshandbuch', 'path': None, 'url': page.url}

    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id}).json()
    item = next(item for item in listing['items'] if item['id'] == job.id)
    assert item['source'] == {'kind': 'confluence', 'label': 'Betriebshandbuch', 'path': None, 'url': page.url}


def test_portal_document_source_reflects_mail_attachment(monkeypatch):
    user = create_test_user(
        username=f'portal-mail-{uuid.uuid4().hex[:8]}',
        email=f'portal-mail-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    db = _db()
    try:
        mail = MailMessage(
            owner_id=user.id,
            content_sha256=hashlib.sha256(b'portal-mail').hexdigest(),
            subject='Rechnung 2026',
            from_address='buchhaltung@example.com',
            raw_content=b'raw',
            raw_size_bytes=3,
        )
        db.add(mail)
        db.commit()
        db.refresh(mail)
        mail_id = mail.id
    finally:
        db.close()
    job = _job(user.id, collection, mail_message_id=mail_id)
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['source'] == {
        'kind': 'mail',
        'label': 'Rechnung 2026 (buchhaltung@example.com)',
        'path': None,
        'url': None,
    }

    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id}).json()
    item = next(item for item in listing['items'] if item['id'] == job.id)
    assert item['source']['kind'] == 'mail'


def test_portal_document_source_confluence_job_without_page_state_is_not_upload(monkeypatch):
    """A Job superseded by a Confluence refresh loses its ImportPageState row
    (job_id there points at the newest import) but must not be reported as a
    plain upload -- see the source-of-truth regression this guards."""
    user = create_test_user(
        username=f'portal-stale-{uuid.uuid4().hex[:8]}',
        email=f'portal-stale-{uuid.uuid4().hex[:8]}@example.com',
    )
    collection = _collection(user.id)
    run = _import_run(ImportRunStatus.FINISHED)
    job = _job(user.id, collection, import_run_id=run.id)
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.processing_info = {
            **stored.processing_info,
            'settings': {
                **stored.processing_info['settings'],
                'import': {'source_page_id': '1', 'source_page_version': 1, 'source_url': 'https://portal.example.atlassian.net/wiki/spaces/X/pages/99'},
            },
        }
        db.commit()
    # No ImportPageState row is created for this job -- it was overwritten
    # to point at a newer job by a subsequent refresh/re-import.
    authed = login_as(user.username)

    detail = authed.get(f'/api/v1/portal/documents/{job.id}').json()
    assert detail['source']['kind'] != 'upload'
    assert detail['source']['kind'] == 'confluence'
    assert detail['source']['url'] == 'https://portal.example.atlassian.net/wiki/spaces/X/pages/99'

    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id}).json()
    item = next(item for item in listing['items'] if item['id'] == job.id)
    assert item['source']['kind'] != 'upload'
