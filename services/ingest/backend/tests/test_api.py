import io
import uuid
import zipfile
from pathlib import Path

import pytest
import redis as redis_lib
from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_user
from app.api.routes import _JOB_LIST_PAGE_LIMIT_MAX
from app.database.session import get_db
from app.main import app
from app.models.models import (
    Collection, Job, JobMarkdownVersion, JobStatus, StoredObject, Team, User, UserRole, VlConnection, WebhookConnection,
)
from app.services import security
from conftest import TestingSessionLocal, client, override_get_db, stored_bytes, stored_upload

# These tests predate the Step 2 auth work and exercise business logic that
# doesn't care about *who* is calling -- Step 3 is what adds per-row
# visibility scoping on top of the plain "is there a session" gate added in
# Step 2. Bypass the gate here with a fixed admin identity rather than
# threading a real login through every one of these tests; test_auth_api.py
# is what actually exercises the cookie/session machinery.
_TEST_ADMIN_USER = User(
    id='test-admin-bypass',
    username='test-admin-bypass',
    email='test-admin-bypass@example.com',
    role=UserRole.ADMIN,
    is_active=True,
)


def _ensure_bypass_user_row() -> None:
    """The bypass identity above is only a detached object, but every Job
    these tests create stores it in jobs.owner_id -- a real FOREIGN KEY.
    SQLite only enforces it because conftest pins PRAGMA foreign_keys=ON (as
    PostgreSQL always does), so the row has to actually exist."""
    with TestingSessionLocal() as db:
        if db.get(User, _TEST_ADMIN_USER.id) is None:
            db.add(
                User(
                    id=_TEST_ADMIN_USER.id,
                    username=_TEST_ADMIN_USER.username,
                    email=_TEST_ADMIN_USER.email,
                    role=UserRole.ADMIN,
                    is_active=True,
                )
            )
            db.commit()


@pytest.fixture(autouse=True)
def _bypass_auth():
    _ensure_bypass_user_row()
    app.dependency_overrides[get_current_user] = lambda: _TEST_ADMIN_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _stored_object_count() -> int:
    with TestingSessionLocal() as db:
        return db.query(StoredObject).count()


def test_healthcheck():
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'healthy'}


def test_readiness_ok():
    response = client.get('/api/v1/ready')
    assert response.status_code == 200
    assert response.json() == {'status': 'ready'}


def test_readiness_reports_database_failure(monkeypatch):
    # AV-03: a dead DB must flip readiness without touching liveness. The
    # route now takes its session via Depends(get_db) like every other
    # route, so the failure is injected through the same dependency
    # override the fixtures already use (conftest.override_get_db) instead
    # of monkeypatching module-level SessionLocal.
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise SQLAlchemyError('db is down')

    def _broken_get_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_get_db
    try:
        response = client.get('/api/v1/ready')
    finally:
        # Restore conftest's shared override rather than popping it, since
        # that override is process-wide for the rest of the test session.
        app.dependency_overrides[get_db] = override_get_db
    assert response.status_code == 503
    assert response.json()['reason'] == 'database'


def test_readiness_reports_broker_failure(monkeypatch):
    # AV-03: a dead broker must also flip readiness (DB stays healthy here).
    def _broken_ping():
        raise redis_lib.RedisError('broker is down')

    import app.main as main_module

    fake_client = type('FakeClient', (), {'ping': staticmethod(_broken_ping)})()
    monkeypatch.setattr(main_module, '_rate_limit_redis', lambda: fake_client)
    response = client.get('/api/v1/ready')
    assert response.status_code == 503
    assert response.json()['reason'] == 'broker'


def test_upload_rejects_unsupported_type():
    response = client.post(
        '/api/v1/upload',
        files={'file': ('malware.exe', b'x', 'application/octet-stream')},
    )
    assert response.status_code == 400


def test_upload_creates_job(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    called = {}

    def fake_delay(
        job_id: str,
        profile_id: str | None = None,
        mode: str | None = None,
        email: str | None = None,
        department: str | None = None,
    ):
        called['job_id'] = job_id
        called['profile_id'] = profile_id
        called['mode'] = mode
        called['email'] = email
        called['department'] = department

    monkeypatch.setattr(routes.process_job, 'delay', fake_delay)

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document.pdf', b'%PDF-sample', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'email': 'single@example.com', 'tags': 'finance, invoices'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == JobStatus.PENDING.value
    assert 'job_id' in payload
    assert called['job_id'] == payload['job_id']
    assert called['profile_id'] == 'ppocrv6_tiny'
    assert called['mode'] == 'single'
    assert called['email'] == 'single@example.com'

    db = TestingSessionLocal()
    job = db.get(Job, payload['job_id'])
    assert job is not None
    assert job.upload_path.startswith('inbox/')
    assert stored_bytes(job.upload_object_id) == b'%PDF-sample'
    assert job.upload_mime_type == 'application/pdf'
    assert job.upload_size_bytes == len(b'%PDF-sample')
    assert sorted(tag.name for tag in job.tags) == ['finance', 'invoices']
    db.close()


def _make_vl_connection(*, name: str = 'Upload VL', enabled: bool = True) -> VlConnection:
    db = TestingSessionLocal()
    try:
        connection = VlConnection(
            name=name,
            base_url='https://vl.example.com',
            model='vl-model',
            api_key_encrypted=security.encrypt_vl_api_key('secret-key'),
            system_prompt='',
            enabled=enabled,
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        db.expunge(connection)
        return connection
    finally:
        db.close()


def test_upload_with_vl_profile_creates_job_with_vl_settings_and_dispatches_openai_vision(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    connection = _make_vl_connection(name='Prod Vision')

    called = {}
    monkeypatch.setattr(
        routes.process_job,
        'delay',
        lambda job_id, profile_id=None, mode=None, email=None, department=None: called.update(
            job_id=job_id, profile_id=profile_id
        ),
    )

    response = client.post(
        '/api/v1/upload',
        # Distinct filename/content: this shared-DB test module never resets
        # between tests, and _find_predecessor_job's duplicate-content check
        # is keyed on (visible-to-user, filename, sha256) -- reusing
        # 'document.pdf' / b'%PDF-sample' here would 409 against
        # test_upload_creates_job's job instead of creating a new one.
        files={'file': ('document-vl.pdf', b'%PDF-vl-upload-sample', 'application/pdf')},
        data={'profile_id': f'vl:{connection.id}', 'email': 'vl-upload@example.com'},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    # Dispatched with the real pipeline id, never the raw 'vl:<connection_id>'
    # display value -- see paddle_service.effective_pipeline_profile_id.
    assert called['profile_id'] == 'openai_vision'

    db = TestingSessionLocal()
    try:
        job = db.get(Job, payload['job_id'])
        settings_info = job.processing_info['settings']
        assert settings_info['profile_id'] == f'vl:{connection.id}'
        assert settings_info['vl_connection_id'] == connection.id
        assert settings_info['variant_label'] == 'Prod Vision'
    finally:
        db.close()


def test_upload_with_unknown_vl_profile_is_422_and_creates_no_job(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    objects_before = _stored_object_count()

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: delayed.append(args))

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document.pdf', b'%PDF-sample', 'application/pdf')},
        data={'profile_id': 'vl:does-not-exist'},
    )
    assert response.status_code == 422
    assert response.json()['detail'] == "Unknown profile 'vl:does-not-exist'"
    assert delayed == []
    # Nothing stored for a request rejected before create_job_from_upload runs.
    assert _stored_object_count() == objects_before


def _make_webhook_connection(owner_id: str, *, name: str = 'Upload Webhook', enabled: bool = True) -> WebhookConnection:
    db = TestingSessionLocal()
    try:
        connection = WebhookConnection(
            owner_id=owner_id,
            name=name,
            url='https://n8n.example.com/webhook/upload-test',
            events=['job.finished'],
            enabled=enabled,
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        db.expunge(connection)
        return connection
    finally:
        db.close()


def _make_other_user_id() -> str:
    """A second, real user row (FK-enforced -- see _ensure_bypass_user_row's
    docstring above) to own a webhook connection that _TEST_ADMIN_USER must
    not be able to configure a job with."""
    db = TestingSessionLocal()
    try:
        other_id = 'test-webhook-stranger'
        if db.get(User, other_id) is None:
            db.add(User(
                id=other_id, username='test-webhook-stranger', email='test-webhook-stranger@example.com',
                role=UserRole.USER, is_active=True,
            ))
            db.commit()
        return other_id
    finally:
        db.close()


def test_upload_with_webhook_connection_sets_job_setting(monkeypatch, tmp_path):
    # Regression for the opt-in webhook rewrite: POST /upload's
    # webhook_connection_id, once validated as the caller's own enabled
    # connection, must land in job.processing_info['settings'] -- that's
    # what app/workers/webhook_tasks.py's dispatch_job_event now reads to
    # decide whether/where to deliver job.finished/job.failed.
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    connection = _make_webhook_connection(_TEST_ADMIN_USER.id)

    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: None)

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document-webhook.pdf', b'%PDF-webhook-upload-sample', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'webhook_connection_id': connection.id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    db = TestingSessionLocal()
    try:
        job = db.get(Job, payload['job_id'])
        assert job.processing_info['settings']['webhook_connection_id'] == connection.id
    finally:
        db.close()


def test_upload_with_unknown_webhook_connection_is_422_and_creates_no_job(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    objects_before = _stored_object_count()

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: delayed.append(args))

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document-webhook-bad.pdf', b'%PDF-webhook-bad-sample', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'webhook_connection_id': 'does-not-exist'},
    )
    assert response.status_code == 422
    assert response.json()['detail'] == 'Unknown webhook connection'
    assert delayed == []
    # Nothing stored, same as the vl: 422 above.
    assert _stored_object_count() == objects_before


def test_upload_with_foreign_webhook_connection_is_422_no_existence_leak(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    other_owner_id = _make_other_user_id()
    foreign_connection = _make_webhook_connection(other_owner_id, name='Someone else’s')

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: delayed.append(args))

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document-webhook-foreign.pdf', b'%PDF-webhook-foreign-sample', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'webhook_connection_id': foreign_connection.id},
    )
    assert response.status_code == 422
    # Same message as an unknown id -- a foreign connection id must not be
    # distinguishable from a nonexistent one (see
    # routes._validated_webhook_connection's docstring).
    assert response.json()['detail'] == 'Unknown webhook connection'
    assert delayed == []


def test_upload_allows_missing_email(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    called = {}

    def fake_delay(
        job_id: str,
        profile_id: str | None = None,
        mode: str | None = None,
        email: str | None = None,
        department: str | None = None,
    ):
        called['email'] = email

    monkeypatch.setattr(routes.process_job, 'delay', fake_delay)

    response = client.post(
        '/api/v1/upload',
        # Distinct filename from test_upload_creates_job: same admin-bypass
        # owner, so an identical (filename, content) pair here would be
        # flagged as a duplicate re-upload (see FEATURE 1 versioning) and
        # 409 instead of exercising this test's actual intent.
        files={'file': ('document-no-email.pdf', b'%PDF-sample', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'tags': 'draft'},
    )
    assert response.status_code == 200
    assert called['email'] == ''


def test_eml_upload_creates_job_like_normal_document(monkeypatch, tmp_path):
    """Test that .eml files can be uploaded through the normal upload flow and create a Job."""
    from app.api import routes
    from app.core.config import settings
    from email.mime.text import MIMEText

    settings.worker_tmp_dir = tmp_path / 'work'

    # Create a simple .eml file
    msg = MIMEText('Email body content', 'plain')
    msg['Subject'] = 'Test Email'
    msg['From'] = 'sender@example.com'
    msg['To'] = 'recipient@example.com'
    eml_content = msg.as_bytes()

    called = {}

    def fake_delay(
        job_id: str,
        profile_id: str | None = None,
        mode: str | None = None,
        email: str | None = None,
        department: str | None = None,
    ):
        called['job_id'] = job_id
        called['profile_id'] = profile_id
        called['mode'] = mode
        called['email'] = email
        called['department'] = department

    monkeypatch.setattr(routes.process_job, 'delay', fake_delay)

    response = client.post(
        '/api/v1/upload',
        files={'file': ('message.eml', eml_content, 'message/rfc822')},
        data={'profile_id': 'ppocrv6_tiny', 'email': 'test@example.com', 'tags': 'inbox'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == JobStatus.PENDING.value
    assert 'job_id' in payload
    assert called['job_id'] == payload['job_id']
    assert called['profile_id'] == 'ppocrv6_tiny'
    assert called['mode'] == 'single'
    assert called['email'] == 'test@example.com'

    db = TestingSessionLocal()
    job = db.get(Job, payload['job_id'])
    assert job is not None
    assert job.original_filename == 'message.eml'
    assert job.upload_path.startswith('inbox/')
    assert stored_bytes(job.upload_object_id) == eml_content
    assert job.upload_mime_type == 'message/rfc822'
    assert job.upload_size_bytes == len(eml_content)
    assert sorted(tag.name for tag in job.tags) == ['inbox']
    db.close()


def test_collection_flow(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    delayed: list[dict[str, str | None]] = []

    def fake_delay(
        job_id: str,
        profile_id: str | None = None,
        mode: str | None = None,
        email: str | None = None,
        department: str | None = None,
    ):
        delayed.append(
            {
                'job_id': job_id,
                'profile_id': profile_id,
                'mode': mode,
                'email': email,
                'department': department,
            }
        )

    monkeypatch.setattr(routes.process_job, 'delay', fake_delay)

    create_resp = client.post(
        '/api/v1/collections',
        json={'description': 'Test purpose', 'folder': 'accounts', 'subfolder': '2026'},
    )
    assert create_resp.status_code == 200
    collection_id = create_resp.json()['collection_id']

    upload_resp = client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('document-a.pdf', b'%PDF-sample', 'application/pdf')},
    )
    assert upload_resp.status_code == 200
    job_id = upload_resp.json()['job_id']

    db = TestingSessionLocal()
    collection_job = db.get(Job, job_id)
    assert collection_job is not None
    assert collection_job.upload_path.startswith('accounts/2026/')
    db.close()

    upload_resp_2 = client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('document-b.pdf', b'%PDF-sample-2', 'application/pdf')},
    )
    assert upload_resp_2.status_code == 200
    job_id_2 = upload_resp_2.json()['job_id']

    start_resp = client.post(
        f'/api/v1/collections/{collection_id}/start',
        json={'profile_id': 'ppocrv6_medium'},
    )
    assert start_resp.status_code == 200
    assert start_resp.json()['started_jobs'] == 2
    # _lock_jobs deliberately sorts UUIDs to acquire database locks in a
    # stable order; random UUID order is unrelated to upload order.
    assert {entry['job_id'] for entry in delayed} == {job_id, job_id_2}
    assert all(entry['profile_id'] == 'ppocrv6_medium' for entry in delayed)
    assert all(entry['mode'] == 'collection' for entry in delayed)
    assert all(entry['email'] == '' for entry in delayed)
    assert all(entry['department'] == '' for entry in delayed)


def test_collection_start_with_vl_profile_sets_vl_settings_and_dispatches_openai_vision(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    connection = _make_vl_connection(name='Collection Vision')

    delayed: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        routes.process_job,
        'delay',
        lambda job_id, profile_id=None, mode=None, email=None, department=None: delayed.append(
            {'job_id': job_id, 'profile_id': profile_id}
        ),
    )

    create_resp = client.post('/api/v1/collections', json={'description': 'Test purpose', 'folder': 'vl-accounts', 'subfolder': '2026'})
    collection_id = create_resp.json()['collection_id']
    upload_resp = client.post(
        f'/api/v1/collections/{collection_id}/upload',
        # Distinct filename/content -- see the comment on the /upload vl:
        # test above (same shared-DB duplicate-content hazard).
        files={'file': ('document-vl-collection.pdf', b'%PDF-vl-collection-sample', 'application/pdf')},
    )
    job_id = upload_resp.json()['job_id']

    start_resp = client.post(
        f'/api/v1/collections/{collection_id}/start',
        json={'profile_id': f'vl:{connection.id}'},
    )
    assert start_resp.status_code == 200, start_resp.text
    assert start_resp.json()['started_jobs'] == 1
    # Dispatched with the real pipeline id, never the raw 'vl:<connection_id>'
    # display value -- see paddle_service.effective_pipeline_profile_id.
    assert delayed == [{'job_id': job_id, 'profile_id': 'openai_vision'}]

    db = TestingSessionLocal()
    try:
        settings_info = db.get(Job, job_id).processing_info['settings']
        assert settings_info['profile_id'] == f'vl:{connection.id}'
        assert settings_info['vl_connection_id'] == connection.id
        assert settings_info['variant_label'] == 'Collection Vision'
    finally:
        db.close()


def test_collection_start_with_unknown_vl_profile_is_422_and_starts_nothing(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: delayed.append(args))

    create_resp = client.post('/api/v1/collections', json={'description': 'Test purpose', 'folder': 'vl-bad', 'subfolder': '2026'})
    collection_id = create_resp.json()['collection_id']
    client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('document-vl-bad.pdf', b'%PDF-vl-bad-sample', 'application/pdf')},
    )

    start_resp = client.post(
        f'/api/v1/collections/{collection_id}/start',
        json={'profile_id': 'vl:does-not-exist'},
    )
    assert start_resp.status_code == 422
    assert start_resp.json()['detail'] == "Unknown profile 'vl:does-not-exist'"
    assert delayed == []


def test_collection_persists_in_db_across_sessions(tmp_path):
    """Step 4 ride-along: collections used to live in an in-memory
    `_COLLECTIONS` dict in app/api/routes.py, which meant a second replica
    (or a restart) could never see a collection created on another pod. They
    are now a real `collections` table row, so a brand new SQLAlchemy
    session -- standing in here for "a different backend replica" -- must
    see exactly what was written, independent of the session/identity map
    that created it.
    """
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    create_resp = client.post(
        '/api/v1/collections',
        json={'description': 'Test purpose', 'email': 'ops@example.com', 'department': 'finance', 'folder': 'audits', 'subfolder': '2026-q1'},
    )
    assert create_resp.status_code == 200
    collection_id = create_resp.json()['collection_id']

    # A fresh session with nothing in its identity map -- if this were still
    # the old in-memory dict, a different process wouldn't have it at all;
    # here it must be a durable row read straight from the DB.
    fresh_session = TestingSessionLocal()
    try:
        row = fresh_session.get(Collection, collection_id)
        assert row is not None
        assert row.email == 'ops@example.com'
        assert row.department == 'finance'
        assert row.folder == 'audits'
        assert row.subfolder == '2026-q1'
        assert row.created_by_id == _TEST_ADMIN_USER.id
        assert [(grant.user_id, grant.role.value) for grant in row.grants] == [(_TEST_ADMIN_USER.id, 'owner')]
    finally:
        fresh_session.close()

    # And the API itself, which pulls a brand new session per-request via
    # get_db (see conftest.override_get_db), still resolves it too.
    get_resp = client.get(f'/api/v1/collections/{collection_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['collection_id'] == collection_id
    assert get_resp.json()['job_ids'] == []


def test_create_collection_generates_unique_slug_from_name():
    """POST /collections without an explicit slug derives one from `name`
    (lowercased, non-alnum runs collapsed to hyphens) and de-duplicates a
    second collection whose name yields the same slug via a numeric suffix
    rather than failing outright -- see routes._unique_collection_slug (an
    identical name is a 409 instead, see the duplicate-name check)."""
    resp1 = client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Kundenservice 2026!'})
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1['slug'] == 'kundenservice-2026'
    assert body1['name'] == 'Kundenservice 2026!'

    resp2 = client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Kundenservice 2026?'})
    assert resp2.status_code == 200
    assert resp2.json()['slug'] == 'kundenservice-2026-2'


def test_create_collection_without_name_defaults_name_and_slug_from_folder():
    resp = client.post('/api/v1/collections', json={'description': 'Test purpose', 'folder': 'Audits 2026'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['slug'] == 'audits-2026'
    assert body['name'] == 'audits-2026'


def test_create_collection_with_explicit_slug_validates_format_and_uniqueness():
    bad = client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Finance', 'slug': 'Not A Slug!'})
    assert bad.status_code == 422

    ok = client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Finance', 'slug': 'finance-eu'})
    assert ok.status_code == 200
    assert ok.json()['slug'] == 'finance-eu'

    dup = client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Finance Duplicate', 'slug': 'finance-eu'})
    assert dup.status_code == 409


def test_create_collection_persists_description_and_team_grants():
    db = TestingSessionLocal()
    try:
        teams = [Team(name=f'legal-{uuid.uuid4().hex[:6]}'), Team(name=f'compliance-{uuid.uuid4().hex[:6]}')]
        db.add_all(teams)
        db.commit()
        team_ids = [team.id for team in teams]
    finally:
        db.close()
    resp = client.post(
        '/api/v1/collections',
        json={
            'name': 'Legal',
            'description': 'Legal department documents',
            'grants': [{'team_id': team_id, 'role': 'reader'} for team_id in team_ids],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['description'] == 'Legal department documents'
    assert {grant['team_id'] for grant in body['grants'] if grant['team_id']} == set(team_ids)

    db = TestingSessionLocal()
    try:
        row = db.get(Collection, body['collection_id'])
        assert {grant.team_id for grant in row.grants if grant.team_id} == set(team_ids)
        assert row.description == 'Legal department documents'
    finally:
        db.close()


def test_markdown_browser_lists_files(tmp_path):
    """DB-derived: the browser tree is built from Job rows with
    result_markdown, not from anything on disk. Field-for-field the response
    shape (path/filename/folder/size_bytes/updated_at) matches what the old
    filesystem-scanning handler produced.
    """
    db = TestingSessionLocal()
    db.query(Job).filter(Job.id.in_(['job-1', 'job-2'])).delete(synchronize_session=False)
    db.commit()  # release the write lock before stored_upload()
    db.add_all(
        [
            Job(
                id='job-1',
                original_filename='single.pdf',
                upload_path=str(tmp_path / 'single.pdf'),
                upload_object_id=stored_upload(b's'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
                result_markdown='# single',
                # No folder/subfolder recorded -> synthesized under 'inbox'.
            ),
            Job(
                id='job-2',
                original_filename='collection.pdf',
                upload_path=str(tmp_path / 'collection.pdf'),
                upload_object_id=stored_upload(b'c'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
                result_markdown='# collection',
                processing_info={'settings': {'folder': 'collections', 'subfolder': 'collection-1'}},
            ),
        ]
    )
    db.commit()
    db.close()

    list_resp = client.get('/api/v1/markdown-files')
    assert list_resp.status_code == 200
    payload = list_resp.json()
    items_by_path = {item['path']: item for item in payload['items']}
    assert 'inbox/job-1/job-1.md' in items_by_path
    assert 'collections/collection-1/job-2/job-2.md' in items_by_path

    entry_one = items_by_path['inbox/job-1/job-1.md']
    assert entry_one['filename'] == 'job-1.md'
    assert entry_one['folder'] == 'inbox/job-1'
    assert entry_one['size_bytes'] == len('# single'.encode('utf-8'))
    assert 'updated_at' in entry_one

    entry_two = items_by_path['collections/collection-1/job-2/job-2.md']
    assert entry_two['filename'] == 'job-2.md'
    assert entry_two['folder'] == 'collections/collection-1/job-2'
    assert entry_two['size_bytes'] == len('# collection'.encode('utf-8'))

    file_resp = client.get('/api/v1/markdown-files/inbox/job-1/job-1.md')
    assert file_resp.status_code == 200
    assert file_resp.text == '# single'

    nested_resp = client.get('/api/v1/markdown-files/collections/collection-1/job-2/job-2.md')
    assert nested_resp.status_code == 200
    assert nested_resp.text == '# collection'


def test_markdown_browser_and_folder_delete_respect_job_passwords(tmp_path):
    """Download and single/bulk delete ask for a protected job's password;
    the markdown browser and the folder-wide delete cannot, so they leave
    protected jobs alone instead of bypassing it."""
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    db = TestingSessionLocal()
    db.add(
        Job(
            id='job-protected-md',
            original_filename='secret.pdf',
            upload_path=str(tmp_path / 'secret.pdf'),
            status=JobStatus.FINISHED,
            result_markdown='# secret',
            password_hash=security.hash_password('pw-secret'),
            processing_info={'settings': {'folder': 'protected-folder'}},
        )
    )
    db.commit()
    db.close()

    listing = client.get('/api/v1/markdown-files')
    assert listing.status_code == 200
    assert all('job-protected-md' not in item['path'] for item in listing.json()['items'])
    assert client.get('/api/v1/markdown-files/protected-folder/job-protected-md/job-protected-md.md').status_code == 404

    refused = client.delete('/api/v1/folders/protected-folder')
    assert refused.status_code == 409
    db = TestingSessionLocal()
    try:
        assert db.get(Job, 'job-protected-md') is not None
    finally:
        db.close()


def test_search_filters_by_name_and_tag(tmp_path):
    db = TestingSessionLocal()
    job_one = Job(
        id='search-1',
        original_filename='Invoice_April.pdf',
        upload_path=str(tmp_path / 'invoice.pdf'),
        upload_object_id=stored_upload(b'1'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
    )
    job_two = Job(
        id='search-2',
        original_filename='Receipt_May.pdf',
        upload_path=str(tmp_path / 'receipt.pdf'),
        upload_object_id=stored_upload(b'2'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
    )
    db.add_all([job_one, job_two])
    db.commit()

    from app.api import routes

    tag = routes.Tag(name='search-finance')
    job_one.tags.append(tag)
    db.add(tag)
    db.commit()
    db.close()

    search_resp = client.get('/api/v1/search?q=invoice&tag=search-finance')
    assert search_resp.status_code == 200
    body = search_resp.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == 'search-1'

    jobs_resp = client.get('/api/v1/jobs?tag=search-finance')
    assert jobs_resp.status_code == 200
    assert any(item['id'] == 'search-1' for item in jobs_resp.json()['items'])

    running_job = Job(
        id='search-3',
        original_filename='Running.pdf',
        upload_path=str(tmp_path / 'running.pdf'),
        upload_object_id=stored_upload(b'3'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.RUNNING,
    )
    db = TestingSessionLocal()
    db.add(running_job)
    db.commit()
    db.close()

    running_resp = client.get('/api/v1/jobs?status=RUNNING')
    assert running_resp.status_code == 200
    assert all(item['status'] == JobStatus.RUNNING.value for item in running_resp.json()['items'])


def test_dashboard_stats_aggregate(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.models.models import Tag

    stats_db = tmp_path / 'stats.db'
    stats_db.write_bytes(b'stats')
    monkeypatch.setattr(settings, 'database_url', f'sqlite:///{stats_db}')
    settings.worker_tmp_dir = tmp_path / 'work'

    db = TestingSessionLocal()
    db.query(Job).delete()
    db.commit()  # release the write lock before stored_upload()
    db.query(Tag).delete()
    db.commit()

    finished = Job(
        id='stats-finished',
        original_filename='finished.pdf',
        upload_path=str(tmp_path / 'finished.pdf'),
        upload_object_id=stored_upload(b'1'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        processing_info={'execution': {'page_count': 7}},
    )
    failed = Job(
        id='stats-failed',
        original_filename='failed.pdf',
        upload_path=str(tmp_path / 'failed.pdf'),
        upload_object_id=stored_upload(b'2'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FAILED,
    )
    db.add_all([finished, failed])
    db.commit()
    db.close()

    response = client.get('/api/v1/stats')
    assert response.status_code == 200
    payload = response.json()
    assert payload['processed_documents'] == 1
    assert payload['processed_pages'] == 7
    assert payload['errors'] == 1
    assert isinstance(payload['database_size_bytes'], int)


def test_save_markdown_creates_new_version(tmp_path):
    db = TestingSessionLocal()
    result_file = tmp_path / 'result.md'
    result_file.write_text('---\nsource: "x"\n---\n\n# done', encoding='utf-8')
    job = Job(
        id='job-save',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        processing_info={},
    )
    db.add(job)
    db.commit()
    db.close()

    save_resp = client.put(
        '/api/v1/jobs/job-save/save',
        json={'markdown': '---\nsource: "x"\nmode: "single"\nemail: "x@y.com"\n---\n\n# edited'},
    )
    assert save_resp.status_code == 200
    body = save_resp.json()
    assert body['version'] == 1

    preview_resp = client.get('/api/v1/jobs/job-save/preview')
    assert preview_resp.status_code == 200
    assert '# edited' in preview_resp.text

    db = TestingSessionLocal()
    saved = db.get(Job, 'job-save')
    assert saved is not None
    assert saved.result_markdown is not None and '# edited' in saved.result_markdown
    db.close()


def test_save_markdown_recomputes_stale_quality_gate(tmp_path):
    """A manual edit invalidates the OCR-time quality gate; saving must
    recompute it against the new markdown so review-UI badges/filters stop
    reflecting the original (now-overwritten) OCR output."""
    db = TestingSessionLocal()
    result_file = tmp_path / 'result.md'
    result_file.write_text('---\nsource: "x"\n---\n\n# done', encoding='utf-8')
    job = Job(
        id='job-save-quality',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        processing_info={
            'execution': {
                'quality_gate': {
                    'grade': 'C',
                    'score': 0.1,
                    'recommendation': 'block',
                    'signals': {},
                    'issues': ['stale'],
                },
            },
        },
    )
    db.add(job)
    db.commit()
    db.close()

    save_resp = client.put(
        '/api/v1/jobs/job-save-quality/save',
        json={'markdown': '---\nsource: "x"\nmode: "single"\nemail: "x@y.com"\n---\n\n# edited'},
    )
    assert save_resp.status_code == 200

    db = TestingSessionLocal()
    saved = db.get(Job, 'job-save-quality')
    assert saved is not None
    quality_gate = saved.processing_info['execution']['quality_gate']
    assert quality_gate != {
        'grade': 'C',
        'score': 0.1,
        'recommendation': 'block',
        'signals': {},
        'issues': ['stale'],
    }
    assert quality_gate['grade'] in ('A', 'B', 'C')
    db.close()


def test_save_markdown_response_path_is_null_and_no_disk_file_written(tmp_path):
    """Editor saves no longer write '.v{n}.md' files to disk; the response
    keeps its 'path' field (backward-compatible shape) but the value is now
    always null.
    """
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    db = TestingSessionLocal()
    job = Job(
        id='job-save-nodisk',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='---\nsource: "x"\n---\n\n# done',
        processing_info={},
    )
    db.add(job)
    db.commit()
    db.close()

    save_resp = client.put(
        '/api/v1/jobs/job-save-nodisk/save',
        json={'markdown': '---\nsource: "x"\n---\n\n# edited once'},
    )
    assert save_resp.status_code == 200
    body = save_resp.json()
    assert body['version'] == 1
    assert body['path'] is None
    assert not list(tmp_path.rglob('*.md'))


def test_save_markdown_creates_version_row_per_save(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-save-versions',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='---\nsource: "x"\n---\n\n# done',
        processing_info={},
    )
    db.add(job)
    db.commit()
    db.close()

    first_resp = client.put(
        '/api/v1/jobs/job-save-versions/save',
        json={'markdown': '---\nsource: "x"\n---\n\n# first edit'},
    )
    assert first_resp.status_code == 200
    assert first_resp.json()['version'] == 1

    second_resp = client.put(
        '/api/v1/jobs/job-save-versions/save',
        json={'markdown': '---\nsource: "x"\n---\n\n# second edit'},
    )
    assert second_resp.status_code == 200
    assert second_resp.json()['version'] == 2

    db = TestingSessionLocal()
    rows = (
        db.query(JobMarkdownVersion)
        .filter(JobMarkdownVersion.job_id == 'job-save-versions')
        .order_by(JobMarkdownVersion.version)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].version == 1
    assert '# first edit' in rows[0].content
    assert rows[1].version == 2
    assert '# second edit' in rows[1].content

    saved_job = db.get(Job, 'job-save-versions')
    assert saved_job is not None
    assert saved_job.result_markdown is not None and '# second edit' in saved_job.result_markdown
    db.close()


def test_job_markdown_versions_cascade_delete_with_job(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-save-cascade',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='---\nsource: "x"\n---\n\n# done',
        processing_info={},
    )
    db.add(job)
    db.commit()
    db.close()

    for markdown in ('---\nsource: "x"\n---\n\n# v1', '---\nsource: "x"\n---\n\n# v2'):
        resp = client.put('/api/v1/jobs/job-save-cascade/save', json={'markdown': markdown})
        assert resp.status_code == 200

    db = TestingSessionLocal()
    assert db.query(JobMarkdownVersion).filter(JobMarkdownVersion.job_id == 'job-save-cascade').count() == 2
    db.close()

    delete_resp = client.delete('/api/v1/jobs/job-save-cascade')
    assert delete_resp.status_code == 200

    db = TestingSessionLocal()
    assert db.get(Job, 'job-save-cascade') is None
    assert db.query(JobMarkdownVersion).filter(JobMarkdownVersion.job_id == 'job-save-cascade').count() == 0
    db.close()


def test_list_and_download(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-1',
        original_filename='a.pdf',
        upload_path=str(tmp_path / 'a.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='# done',
    )
    db.add(job)
    db.commit()
    db.close()

    list_resp = client.get('/api/v1/jobs')
    assert list_resp.status_code == 200
    assert any(item['id'] == 'job-1' for item in list_resp.json()['items'])

    dl_resp = client.get('/api/v1/jobs/job-1/download')
    assert dl_resp.status_code == 200
    assert dl_resp.headers['content-type'].startswith('text/markdown')


def test_restart_pending_jobs(monkeypatch, tmp_path):
    from app.api import routes

    db = TestingSessionLocal()
    db.add_all(
        [
            Job(
                id='job-pending-restart',
                original_filename='pending.pdf',
                upload_path=str(tmp_path / 'pending.pdf'),
                upload_object_id=stored_upload(b'p'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.PENDING,
                processing_info={'settings': {'profile_id': 'ppocrv6_small', 'mode': 'single'}},
            ),
            Job(
                id='job-finished-ignore',
                original_filename='finished.pdf',
                upload_path=str(tmp_path / 'finished.pdf'),
                upload_object_id=stored_upload(b'f'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
            ),
        ]
    )
    db.commit()
    db.close()

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args: delayed.append(args))

    response = client.post('/api/v1/jobs/restart-pending')
    assert response.status_code == 200
    payload = response.json()
    assert payload['pending_jobs'] >= 1
    assert payload['queued_jobs'] >= 1
    assert any(entry[0] == 'job-pending-restart' for entry in delayed)


def test_restart_pending_requeues_only_running_jobs_whose_worker_is_gone(monkeypatch, tmp_path):
    # By identity (heartbeat), never by count: a live job must survive even
    # when nothing else tells the API which jobs are really running.
    from datetime import datetime, timedelta, timezone

    from app.api import routes
    from app.core.config import settings as app_settings

    now = datetime.now(timezone.utc)
    db = TestingSessionLocal()
    db.query(Job).filter(Job.status == JobStatus.RUNNING).delete()
    db.commit()  # release the write lock before stored_upload()
    for job_id, heartbeat_at in (
        ('restart-live', now),
        ('restart-lost', now - timedelta(seconds=app_settings.job_stale_seconds + 10)),
    ):
        db.add(Job(
            id=job_id,
            original_filename=f'{job_id}.pdf',
            upload_path=str(tmp_path / f'{job_id}.pdf'),
            upload_object_id=stored_upload(b'r'),
            upload_mime_type='application/pdf',
            upload_size_bytes=1,
            status=JobStatus.RUNNING,
            heartbeat_at=heartbeat_at,
            claim_token=f'{job_id}-token',
        ))
    db.commit()
    db.close()

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args: delayed.append(args))

    response = client.post('/api/v1/jobs/restart-pending')

    assert response.status_code == 200
    assert response.json()['recovered_running'] == 1
    assert 'restart-live' not in [entry[0] for entry in delayed]
    assert 'restart-lost' in [entry[0] for entry in delayed]
    db = TestingSessionLocal()
    assert db.get(Job, 'restart-live').status == JobStatus.RUNNING
    lost = db.get(Job, 'restart-lost')
    assert lost.status == JobStatus.PENDING
    assert lost.claim_token is None
    db.close()


def test_delete_job(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-delete',
        original_filename='x.pdf',
        upload_path='inbox/job-delete/job-delete.pdf',
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
    )
    db.add(job)
    db.commit()
    db.close()

    resp = client.delete('/api/v1/jobs/job-delete')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'deleted'
    with TestingSessionLocal() as db:
        assert db.get(Job, 'job-delete') is None


def test_delete_folder_removes_jobs(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-folder',
        original_filename='q2-report.pdf',
        upload_path='finance/q2/job-folder/job-folder.pdf',
        upload_object_id=stored_upload(b'pdf'),
        upload_mime_type='application/pdf',
        upload_size_bytes=3,
        status=JobStatus.FINISHED,
        processing_info={'settings': {'folder': 'finance', 'subfolder': 'q2', 'storage_folder': 'finance/q2/job-folder'}},
    )
    db.add(job)
    db.commit()
    db.close()

    response = client.delete('/api/v1/folders/finance/q2')
    assert response.status_code == 200
    payload = response.json()
    assert payload['path'] == 'finance/q2'
    assert payload['deleted_jobs'] == 1
    with TestingSessionLocal() as db:
        assert db.get(Job, 'job-folder') is None


def test_download_folder_markdown_zip_recursive_finished_only(tmp_path):
    db = TestingSessionLocal()
    db.add_all(
        [
            Job(
                id='job-a',
                original_filename='report-a.pdf',
                result_markdown='# finished a',
                upload_path=str(tmp_path / 'a.pdf'),
                upload_object_id=stored_upload(b'a'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
                processing_info={'settings': {'folder': 'finance', 'subfolder': 'q2', 'storage_folder': 'finance/q2/job-a'}},
            ),
            Job(
                id='job-b',
                original_filename='report-b.pdf',
                result_markdown='# finished b',
                upload_path=str(tmp_path / 'b.pdf'),
                upload_object_id=stored_upload(b'b'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
                processing_info={'settings': {'folder': 'finance', 'subfolder': 'q2/sub', 'storage_folder': 'finance/q2/sub/job-b'}},
            ),
            Job(
                id='job-c',
                original_filename='report-c.pdf',
                result_markdown='# failed c',
                upload_path=str(tmp_path / 'c.pdf'),
                upload_object_id=stored_upload(b'c'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FAILED,
                processing_info={'settings': {'folder': 'finance', 'subfolder': 'q2', 'storage_folder': 'finance/q2/job-c'}},
            ),
        ]
    )
    db.commit()
    db.close()

    response = client.get('/api/v1/folders/finance/q2/download')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/zip')

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = sorted(archive.namelist())
    assert len(names) == 2
    assert any(name.endswith('report-a-job-a.md') for name in names)
    assert any(name.endswith('report-b-job-b.md') for name in names)
    assert all('job-c' not in name for name in names)


def test_download_markdown_serves_from_db(tmp_path):
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'

    db = TestingSessionLocal()
    job = Job(
        id='job-db-download',
        original_filename='db-only.pdf',
        upload_path=str(tmp_path / 'missing-upload.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='# from database',
    )
    db.add(job)
    db.commit()
    db.close()

    response = client.get('/api/v1/jobs/job-db-download/download')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/markdown')
    assert response.headers['content-disposition'] == 'attachment; filename="job-db-download.md"'
    assert response.text == '# from database'


def test_download_folder_markdown_serves_from_db(tmp_path):
    db = TestingSessionLocal()
    job = Job(
        id='job-zip-db',
        original_filename='zip-db.pdf',
        upload_path=str(tmp_path / 'missing-upload.pdf'),
        upload_object_id=stored_upload(b'x'),
        upload_mime_type='application/pdf',
        upload_size_bytes=1,
        status=JobStatus.FINISHED,
        result_markdown='# zip content from db',
        processing_info={
            'settings': {
                'folder': 'finance',
                'subfolder': 'db-only',
                'storage_folder': 'finance/db-only/job-zip-db',
            }
        },
    )
    db.add(job)
    db.commit()
    db.close()

    response = client.get('/api/v1/folders/finance/db-only/download')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/zip')

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert len(names) == 1
    assert archive.read(names[0]).decode('utf-8') == '# zip content from db'


def test_jobs_pagination_limit_offset(tmp_path):
    db = TestingSessionLocal()
    db.query(Job).filter(Job.id.like('page-%')).delete(synchronize_session=False)
    db.commit()
    db.add_all(
        [
            Job(
                id=f'page-{i}',
                original_filename=f'page-{i}.pdf',
                upload_path=str(tmp_path / f'page-{i}.pdf'),
                upload_object_id=stored_upload(b'x'),
                upload_mime_type='application/pdf',
                upload_size_bytes=1,
                status=JobStatus.FINISHED,
            )
            for i in range(5)
        ]
    )
    db.commit()
    db.close()

    unbounded_resp = client.get('/api/v1/jobs?q=page-')
    assert unbounded_resp.status_code == 200
    assert len(unbounded_resp.json()['items']) == 5

    page_one_resp = client.get('/api/v1/jobs?q=page-&limit=2')
    assert page_one_resp.status_code == 200
    page_one_items = page_one_resp.json()['items']
    assert len(page_one_items) == 2

    page_two_resp = client.get('/api/v1/jobs?q=page-&limit=2&offset=2')
    assert page_two_resp.status_code == 200
    page_two_items = page_two_resp.json()['items']
    assert len(page_two_items) == 2

    page_one_ids = {item['id'] for item in page_one_items}
    page_two_ids = {item['id'] for item in page_two_items}
    assert page_one_ids.isdisjoint(page_two_ids)

    assert client.get('/api/v1/jobs?limit=-1').status_code == 422
    assert client.get('/api/v1/jobs?offset=-1').status_code == 422
    assert client.get(f'/api/v1/jobs?limit={_JOB_LIST_PAGE_LIMIT_MAX + 1}').status_code == 422

    search_page_resp = client.get('/api/v1/search?q=page-&limit=2')
    assert search_page_resp.status_code == 200
    search_page = search_page_resp.json()
    assert len(search_page['items']) == 2
    # With pagination active, total must be the full match count, not the page size.
    assert search_page['total'] == 5

    search_unbounded = client.get('/api/v1/search?q=page-').json()
    assert len(search_unbounded['items']) == 5
    assert search_unbounded['total'] == 5


def test_list_jobs_defers_blob_columns(tmp_path):
    """Regression test: GET /jobs (and /search) must not eagerly load the
    result_markdown column for rows it merely lists.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.api.routes import _job_query

    db = TestingSessionLocal()
    db.query(Job).filter(Job.id == 'job-defer-check').delete()
    db.commit()
    db.add(
        Job(
            id='job-defer-check',
            original_filename='defer-check.pdf',
            upload_path=str(tmp_path / 'defer-check.pdf'),
            upload_object_id=stored_upload(b'x' * 1000),
            upload_mime_type='application/pdf',
            upload_size_bytes=1000,
            status=JobStatus.FINISHED,
            result_markdown='# some markdown content',
        )
    )
    db.commit()
    db.close()

    query_db = TestingSessionLocal()
    jobs = _job_query(query_db, _TEST_ADMIN_USER)
    target = next(job for job in jobs if job.id == 'job-defer-check')
    unloaded = sa_inspect(target).unloaded
    assert 'result_markdown' in unloaded
    query_db.close()


def test_deferred_blob_column_raises_after_session_close(tmp_path):
    """Proves the DetachedInstanceError trap is real for this ORM config:
    a column deferred via query .options() has never been loaded into the
    instance's __dict__, so touching it after the owning session is closed
    must fail loudly instead of silently returning None or stale data. Any
    route that queries with _JOB_BLOB_DEFER_OPTIONS and then reads that attribute after its `db` dependency has been torn
    down would hit exactly this.
    """
    from sqlalchemy import select
    from sqlalchemy.orm.exc import DetachedInstanceError

    from app.api.routes import _JOB_BLOB_DEFER_OPTIONS

    db = TestingSessionLocal()
    db.query(Job).filter(Job.id == 'job-detached-check').delete()
    db.commit()
    db.add(
        Job(
            id='job-detached-check',
            original_filename='detached-check.pdf',
            upload_path=str(tmp_path / 'detached-check.pdf'),
            upload_object_id=stored_upload(b'x' * 1000),
            upload_mime_type='application/pdf',
            upload_size_bytes=1000,
            status=JobStatus.FINISHED,
            result_markdown='# detached check',
        )
    )
    db.commit()
    db.close()

    query_db = TestingSessionLocal()
    job = query_db.scalars(select(Job).where(Job.id == 'job-detached-check').options(*_JOB_BLOB_DEFER_OPTIONS)).one()
    query_db.close()

    with pytest.raises(DetachedInstanceError):
        job.result_markdown  # noqa: B018 - intentional attribute access to trigger the lazy load


def test_listing_and_admin_endpoints_survive_populated_blob_columns(monkeypatch, tmp_path):
    """End-to-end regression: with a stored upload and result_markdown both
    populated (the realistic post-migration shape, not NULL legacy rows),
    every endpoint that lists/administers jobs via the deferred-blob query
    options must still return 200 through the full FastAPI dependency
    lifecycle (session opened by Depends(get_db), closed only after the
    response body has been serialized). A regression here would surface as
    a 500 from DetachedInstanceError, not a wrong value.
    """
    from app.core.config import settings
    from app.api import routes

    settings.worker_tmp_dir = tmp_path / 'work'

    big_markdown = '# heading\n' + ('lorem ipsum ' * 500)
    db = TestingSessionLocal()
    db.query(Job).filter(Job.id == 'job-populated-blobs').delete()
    db.commit()
    job = Job(
        id='job-populated-blobs',
        original_filename='populated-blobs.pdf',
        upload_path=str(tmp_path / 'populated-blobs.pdf'),
        upload_object_id=stored_upload(b'\x89PNG' * 500),
        upload_mime_type='application/pdf',
        upload_size_bytes=2000,
        status=JobStatus.FINISHED,
        result_markdown=big_markdown,
        processing_info={
            'settings': {'folder': 'blob-check', 'subfolder': '', 'storage_folder': 'blob-check/job-populated-blobs'},
            'execution': {'page_count': 3},
        },
    )
    db.add(job)
    db.commit()
    db.close()

    jobs_resp = client.get('/api/v1/jobs?q=populated-blobs')
    assert jobs_resp.status_code == 200
    jobs_items = jobs_resp.json()['items']
    assert any(item['id'] == 'job-populated-blobs' for item in jobs_items)
    assert all('result_markdown' not in item and 'upload_content' not in item for item in jobs_items)

    search_resp = client.get('/api/v1/search?q=populated-blobs')
    assert search_resp.status_code == 200
    assert any(item['id'] == 'job-populated-blobs' for item in search_resp.json()['items'])

    stats_resp = client.get('/api/v1/stats')
    assert stats_resp.status_code == 200
    assert stats_resp.json()['processed_pages'] >= 3

    delayed: list[tuple] = []
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args: delayed.append(args))

    restart_resp = client.post('/api/v1/folders/blob-check/restart')
    assert restart_resp.status_code == 200
    assert restart_resp.json()['restarted_jobs'] == 1

    verify_db = TestingSessionLocal()
    refreshed = verify_db.get(Job, 'job-populated-blobs')
    assert refreshed is not None
    assert refreshed.status == JobStatus.PENDING
    # _delete_job_outputs clears result_markdown as part of the restart
    # path; confirm the write-only access on a deferred attribute landed
    # correctly rather than silently no-op'ing or raising.
    assert refreshed.result_markdown is None
    verify_db.close()

    delete_resp = client.delete('/api/v1/folders/blob-check')
    assert delete_resp.status_code == 200
    assert delete_resp.json()['deleted_jobs'] == 1


def test_update_paddle_settings(monkeypatch):
    from app.services import paddle_service

    monkeypatch.setattr(paddle_service, '_runtime_capability', lambda: {
        'torch_available': True,
        'cuda_available': False,
        'selected_device': 'cpu',
        'platform': 'linux-aarch64',
        'no_cuda_reason': 'CPU-only torch installed or no NVIDIA GPU present on this host',
    })

    payload = {
        'default_profile': 'ppocrv6_tiny',
        'timeout_seconds': 300,
    }
    response = client.put('/api/v1/paddle/settings', json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body['default_profile'] == 'ppocrv6_tiny'


def test_paddle_status_reports_queue_when_probe_degraded(monkeypatch, tmp_path):
    from app.api import routes

    db = TestingSessionLocal()
    db.add(
        Job(
            id='queue-pending',
            original_filename='queued.pdf',
            upload_path=str(tmp_path / 'queued.pdf'),
            upload_object_id=stored_upload(b'q'),
            upload_mime_type='application/pdf',
            upload_size_bytes=1,
            status=JobStatus.PENDING,
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(routes, 'get_paddle_status', lambda: ('stopped', 'Worker unavailable or Paddle probe timed out', None))

    response = client.get('/api/v1/paddle/status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'running'
    assert payload['queue_total'] >= 1
    assert payload['pending_jobs'] >= 1


def test_worker_restart_requeues_running_jobs(monkeypatch, tmp_path):
    # SH-02: startup recovery must only reset a RUNNING job whose updated_at
    # is old enough that it cannot still be genuinely executing (older than
    # the hard time limit + margin) -- a live job with a recent updated_at
    # must be left alone, or a routine rolling restart duplicates OCR work.
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings as app_settings
    from app.workers import tasks
    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)

    stale_updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=app_settings.celery_task_time_limit_seconds, minutes=10
    )

    db = TestingSessionLocal()
    db.query(Job).filter(Job.status == JobStatus.RUNNING).delete()
    db.commit()
    db.add(
        Job(
            id='job-running-restart',
            original_filename='restart.pdf',
            upload_path=str(tmp_path / 'restart.pdf'),
            upload_object_id=stored_upload(b'r'),
            upload_mime_type='application/pdf',
            upload_size_bytes=1,
            status=JobStatus.RUNNING,
            updated_at=stale_updated_at,
            processing_info={
                'settings': {
                    'profile_id': 'ppocrv6_medium',
                    'mode': 'collection',
                    'email': 'ops@example.com',
                    'department': 'ops',
                },
                'execution': {'status': 'running'},
            },
        )
    )
    db.add(
        Job(
            id='job-running-still-live',
            original_filename='live.pdf',
            upload_path=str(tmp_path / 'live.pdf'),
            upload_object_id=stored_upload(b'l'),
            upload_mime_type='application/pdf',
            upload_size_bytes=1,
            status=JobStatus.RUNNING,
            updated_at=datetime.now(timezone.utc),
            processing_info={
                'settings': {
                    'profile_id': 'ppocrv6_medium',
                    'mode': 'collection',
                    'email': 'ops@example.com',
                    'department': 'ops',
                },
                'execution': {'status': 'running'},
            },
        )
    )
    db.commit()
    db.close()

    delayed: list[tuple] = []
    monkeypatch.setattr(tasks.process_job, 'delay', lambda *args: delayed.append(args))

    restarted = tasks.requeue_running_jobs_after_restart()
    assert restarted >= 1
    assert delayed
    queued_map = {entry[0]: entry for entry in delayed}
    assert 'job-running-restart' in queued_map
    assert queued_map['job-running-restart'][1] == 'ppocrv6_medium'
    assert queued_map['job-running-restart'][2] == 'collection'
    # The live job must not be reset/requeued -- a second worker picking it
    # up would duplicate the still-running OCR work.
    assert 'job-running-still-live' not in queued_map

    db = TestingSessionLocal()
    live_job = db.get(Job, 'job-running-still-live')
    assert live_job is not None
    assert live_job.status == JobStatus.RUNNING
    db.close()

    db = TestingSessionLocal()
    job = db.get(Job, 'job-running-restart')
    assert job is not None
    assert job.status == JobStatus.PENDING
    db.close()


def test_upload_rejects_oversize_file_without_partial_remnant(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, 'max_upload_bytes', 5)
    objects_before = _stored_object_count()

    response = client.post(
        '/api/v1/upload',
        files={'file': ('document.pdf', b'%PDF-well-over-the-limit', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'email': 'oversize@example.com'},
    )
    assert response.status_code == 413
    assert _stored_object_count() == objects_before


def test_inspect_upload_rejects_oversize_after_multiple_chunks(monkeypatch, tmp_path):
    """The 413 in inspect_upload is crossed on a later 1MB read, not the
    first. Exercised directly against UploadFile, bypassing HTTP multipart
    overhead; the size check runs before anything is stored.
    """
    from app.core.config import settings
    from app.services import storage

    chunk_size = 1024 * 1024
    # Limit sits inside the *second* chunk.
    monkeypatch.setattr(settings, 'max_upload_bytes', int(chunk_size * 1.5))

    data = b'A' * (chunk_size * 3)
    upload = UploadFile(file=io.BytesIO(data), filename='huge.pdf')
    upload.headers = {'content-type': 'application/pdf'}

    with pytest.raises(HTTPException) as exc_info:
        storage.inspect_upload(upload)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == 'File too large'


def test_create_folder_only_validates_the_path(tmp_path):
    # Folders exist only as the folder/subfolder of their jobs.
    response = client.post(
        '/api/v1/folders',
        json={'folder': 'finance', 'subfolder': 'q3'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['path'] == 'finance/q3'
    assert not list(tmp_path.iterdir())


def test_process_job_works_in_a_private_scratch_dir_and_removes_it(monkeypatch, tmp_path):
    """The worker copies the stored original into a per-task scratch dir,
    writes the result to the database only, and removes the dir afterwards."""
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(settings, 'worker_tmp_dir', tmp_path / 'work')

    db = TestingSessionLocal()
    db.query(Job).filter(Job.id == 'job-retry').delete()
    db.commit()
    db.add(
        Job(
            id='job-retry',
            original_filename='job-retry.pdf',
            upload_path='inbox/job-retry/job-retry.pdf',
            upload_object_id=stored_upload(b'%PDF-1.4 fake upload content'),
            upload_mime_type='application/pdf',
            upload_size_bytes=len(b'%PDF-1.4 fake upload content'),
            status=JobStatus.PENDING,
            processing_info={'settings': {'storage_folder': 'inbox/job-retry'}},
        )
    )
    db.commit()
    db.close()

    seen: dict = {}

    def convert(path, *args, **kwargs):
        seen['path'] = Path(path)
        seen['bytes'] = Path(path).read_bytes()
        return '# fresh result from this run', {'page_count': 1}

    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', convert)

    tasks.process_job('job-retry')

    assert seen['bytes'] == b'%PDF-1.4 fake upload content'
    assert seen['path'].suffix == '.pdf'
    assert seen['path'].parent.parent == tmp_path / 'work'
    assert not seen['path'].parent.exists()
    db = TestingSessionLocal()
    job = db.get(Job, 'job-retry')
    assert job is not None
    assert job.status == JobStatus.FINISHED
    assert job.result_markdown == '# fresh result from this run'
    assert job.error_message is None
    db.close()


def test_process_job_with_vl_settings_shape_forwards_vl_override(monkeypatch, tmp_path):
    """Worker-integration slice for the 'vl:<connection_id>' profile
    contract (AUFGABE 5d): a job whose settings carry the shape
    upload_document/restart_job now write (vl_connection_id, dispatched
    with the real pipeline id 'openai_vision' -- see
    paddle_service.effective_pipeline_profile_id) reaches
    convert_to_markdown_with_details with the same vl_override shape the
    benchmark variant path already exercises (tasks.py ~335), unmodified.
    """
    from app.core.config import settings
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    settings.worker_tmp_dir = tmp_path / 'work'

    connection = _make_vl_connection(name='Worker Path Vision')

    db = TestingSessionLocal()
    db.query(Job).filter(Job.id == 'job-vl-single').delete()
    db.commit()
    db.add(
        Job(
            id='job-vl-single',
            original_filename='job-vl-single.pdf',
            upload_path='inbox/job-vl-single/job-vl-single.pdf',
            upload_object_id=stored_upload(b'%PDF-1.4 fake upload content'),
            upload_mime_type='application/pdf',
            upload_size_bytes=len(b'%PDF-1.4 fake upload content'),
            status=JobStatus.PENDING,
            processing_info={
                'settings': {
                    'storage_folder': 'inbox/job-vl-single',
                    'profile_id': f'vl:{connection.id}',
                    'vl_connection_id': connection.id,
                    'variant_label': connection.name,
                }
            },
        )
    )
    db.commit()
    db.close()

    seen_calls = []
    monkeypatch.setattr(
        tasks,
        'convert_to_markdown_with_details',
        lambda *args, **kwargs: (seen_calls.append(kwargs) or ('# vl result', {'page_count': 1})),
    )

    # Real pipeline id, as effective_pipeline_profile_id / the benchmark
    # variant spec would dispatch -- never the raw 'vl:<connection_id>'.
    tasks.process_job('job-vl-single', 'openai_vision', 'single', '', None)

    assert len(seen_calls) == 1
    vl_override = seen_calls[0]['vl_override']
    assert vl_override['name'] == 'Worker Path Vision'
    assert vl_override['base_url'] == connection.base_url
    assert vl_override['model'] == connection.model

    db = TestingSessionLocal()
    try:
        job = db.get(Job, 'job-vl-single')
        assert job.status == JobStatus.FINISHED
        assert job.result_markdown == '# vl result'
        # The RUNNING transition rewrites settings from the task parameter
        # (the real pipeline id) -- it must NOT clobber the vl: selection,
        # which is the job's user-facing profile identity (jobs table,
        # detail page, restart audit). Regression: the first cut stored
        # 'openai_vision' here after the job ran.
        post_settings = job.processing_info['settings']
        assert post_settings['profile_id'] == f'vl:{connection.id}'
        assert post_settings['requested_profile_id'] == f'vl:{connection.id}'
        assert post_settings['vl_connection_id'] == connection.id
    finally:
        db.close()
