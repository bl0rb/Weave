"""Collections contract tests: the slug/name/description/read_teams fields
added on top of the pre-existing collections table (see app/models/models.py's
Collection docstring and README.md's "Collections" section), GET /collections
visibility, PATCH ownership, the GET /collections/registry sync endpoint for
Weave-Knowledge, and the full create -> upload -> process -> frontmatter
chain that stamps a collection's slug/name into every document processed
through it.

Real cookie-based logins (create_test_user/login_as, same idioms as
test_import_api.py/test_benchmarks_api.py) because collection visibility
(_visible_collection_filter/_owner_visible) joins against the real users
table, exactly like the job/run/benchmark authz tests. test_api.py's
collection tests keep using its admin-bypass fixture for everything that
doesn't need real per-user authz (create-time slug/description/read_teams
persistence); this file is only for what does.
"""

from io import BytesIO
import uuid
from unittest.mock import patch

import pytest
import yaml

from app.models.models import ImportRun, ImportRunStatus, Job, JobStatus, ManagedBot, Team, UserRole
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # /auth/login is rate-limited per client host, and TestClient always
    # presents as "testclient" -- shared bucket across every test unless
    # reset per test (see test_versioning_api.py's identical fixture).
    rate_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.uploads_dir = tmp_path / 'uploads'
    settings.results_dir = tmp_path / 'results'
    # Real processing is opted into per-test below; by default /start is a
    # no-op so tests that don't need it stay fast and worker-free.
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
        lambda *args, **kwargs: None,
    )
    yield


def _user(prefix: str, **kwargs):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', **kwargs)


def _make_team(name_prefix: str) -> str:
    db = TestingSessionLocal()
    try:
        team = Team(name=f'{name_prefix}-{uuid.uuid4().hex[:8]}')
        db.add(team)
        db.commit()
        db.refresh(team)
        return team.id
    finally:
        db.close()


def test_collections_visibility_and_patch_control_matrix():
    """own + current-teammates' + admin-all for reads (GET /collections,
    GET /collections/{id}), same rule as GET /jobs; PATCH additionally
    requires ownership or admin -- read is not control, same split as
    import_routes._require_run_control/benchmarks._require_benchmark_control."""
    team_id = _make_team('coll-team')
    owner = _user('coll-owner', team_id=team_id)
    teammate = _user('coll-teammate', team_id=team_id)
    outsider = _user('coll-outsider')
    admin = _user('coll-admin', role=UserRole.ADMIN)

    owner_client = login_as(owner.username)
    create_resp = owner_client.post('/api/v1/collections', json={'name': 'Team Docs'})
    assert create_resp.status_code == 200
    collection_id = create_resp.json()['collection_id']

    teammate_client = login_as(teammate.username)
    assert collection_id in {c['collection_id'] for c in teammate_client.get('/api/v1/collections').json()['items']}
    assert teammate_client.get(f'/api/v1/collections/{collection_id}').status_code == 200
    forbidden = teammate_client.patch(f'/api/v1/collections/{collection_id}', json={'name': 'Hacked'})
    assert forbidden.status_code == 403

    outsider_client = login_as(outsider.username)
    assert collection_id not in {c['collection_id'] for c in outsider_client.get('/api/v1/collections').json()['items']}
    assert outsider_client.get(f'/api/v1/collections/{collection_id}').status_code == 404
    assert outsider_client.patch(f'/api/v1/collections/{collection_id}', json={'name': 'Hacked'}).status_code == 404

    admin_client = login_as(admin.username)
    assert collection_id in {c['collection_id'] for c in admin_client.get('/api/v1/collections').json()['items']}
    admin_patch = admin_client.patch(f'/api/v1/collections/{collection_id}', json={'name': 'Renamed by Admin'})
    assert admin_patch.status_code == 200
    assert admin_patch.json()['name'] == 'Renamed by Admin'

    owner_patch = owner_client.patch(f'/api/v1/collections/{collection_id}', json={'read_teams': ['ops']})
    assert owner_patch.status_code == 200
    assert owner_patch.json()['read_teams'] == ['ops']
    # CollectionUpdateRequest carries no `slug` field -- it never changes.
    assert owner_patch.json()['slug'] == admin_patch.json()['slug']


def test_empty_collection_can_be_deleted_only_by_owner_or_admin():
    team_id = _make_team('coll-delete-team')
    owner = _user('coll-delete-owner', team_id=team_id)
    teammate = _user('coll-delete-teammate', team_id=team_id)
    outsider = _user('coll-delete-outsider')

    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Leerer Bereich'})
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    # Visibility is not control: a teammate may read the card but not rename
    # or delete it. A completely unrelated caller still gets the endpoint's
    # non-enumerating 404 response.
    assert login_as(teammate.username).delete(f'/api/v1/collections/{collection_id}').status_code == 403
    assert login_as(outsider.username).delete(f'/api/v1/collections/{collection_id}').status_code == 404

    deleted = owner_client.delete(f'/api/v1/collections/{collection_id}')
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {'status': 'deleted'}
    assert owner_client.get(f'/api/v1/collections/{collection_id}').status_code == 404


def test_collection_delete_never_cascades_documents_or_races_an_active_import():
    owner = _user('coll-delete-protected-owner')
    owner_client = login_as(owner.username)

    with_document = owner_client.post('/api/v1/collections', json={'name': 'Bereich mit Dokument'})
    assert with_document.status_code == 200, with_document.text
    document_collection_id = with_document.json()['collection_id']
    db = TestingSessionLocal()
    try:
        db.add(Job(
            original_filename='behalten.pdf',
            upload_path='/tmp/behalten.pdf',
            owner_id=owner.id,
            processing_info={'settings': {'collection_id': document_collection_id}},
        ))
        db.commit()
    finally:
        db.close()

    blocked_document = owner_client.delete(f'/api/v1/collections/{document_collection_id}')
    assert blocked_document.status_code == 409
    assert 'enthält noch Dokumente' in blocked_document.json()['detail']
    assert owner_client.get(f'/api/v1/collections/{document_collection_id}').status_code == 200

    with_import = owner_client.post('/api/v1/collections', json={'name': 'Bereich mit Import'})
    assert with_import.status_code == 200, with_import.text
    import_collection_id = with_import.json()['collection_id']
    db = TestingSessionLocal()
    try:
        db.add(ImportRun(
            owner_id=owner.id,
            kind='confluence',
            status=ImportRunStatus.RUNNING,
            scope_type='page',
            scope_value='12345',
            options={'collection_id': import_collection_id},
        ))
        db.commit()
    finally:
        db.close()

    blocked_import = owner_client.delete(f'/api/v1/collections/{import_collection_id}')
    assert blocked_import.status_code == 409
    assert 'läuft noch ein Import' in blocked_import.json()['detail']
    assert owner_client.get(f'/api/v1/collections/{import_collection_id}').status_code == 200

    assigned = owner_client.post('/api/v1/collections', json={'name': 'Bereich für Bot'})
    assert assigned.status_code == 200, assigned.text
    bot_collection_id = assigned.json()['collection_id']
    bot_collection_slug = assigned.json()['slug']
    db = TestingSessionLocal()
    try:
        db.add(ManagedBot(
            id=f'collection-guard-{uuid.uuid4().hex[:8]}',
            name='Collection Guard',
            webhook_url='https://n8n.example.com/webhook/guard',
            teams=[],
            collections=[bot_collection_slug],
            updated_by_id=owner.id,
        ))
        db.commit()
    finally:
        db.close()

    blocked_bot = owner_client.delete(f'/api/v1/collections/{bot_collection_id}')
    assert blocked_bot.status_code == 409
    assert 'noch einem Bot zugeordnet' in blocked_bot.json()['detail']
    assert owner_client.get(f'/api/v1/collections/{bot_collection_id}').status_code == 200


def test_collections_registry_lists_every_collection_unfiltered_by_visibility():
    """GET /collections/registry is the Weave-Knowledge sync source: unlike
    GET /collections it is NOT scoped to the caller's own visibility -- an
    unrelated admin's token still sees every collection's ACL metadata, and
    the payload carries nothing document-shaped. Admin-only (see the
    dedicated 403 test below), so the "unrelated caller" here must itself be
    an admin -- exactly the account shape README.md now documents Weave-
    Knowledge's sync token as needing."""
    owner = _user('registry-owner')
    admin = _user('registry-admin', role=UserRole.ADMIN)

    owner_client = login_as(owner.username)
    create_resp = owner_client.post(
        '/api/v1/collections',
        json={'name': 'Registry Sample', 'slug': f'registry-sample-{uuid.uuid4().hex[:8]}', 'read_teams': ['ops']},
    )
    assert create_resp.status_code == 200
    slug = create_resp.json()['slug']

    admin_client = login_as(admin.username)
    registry_resp = admin_client.get('/api/v1/collections/registry')
    assert registry_resp.status_code == 200
    items = registry_resp.json()['items']
    entry = next(item for item in items if item['slug'] == slug)
    assert entry == {'slug': slug, 'name': 'Registry Sample', 'description': None, 'read_teams': ['ops']}
    assert set(entry.keys()) == {'slug', 'name', 'description', 'read_teams'}


def test_collections_registry_rejects_non_admin():
    """FIX (information leak): GET /collections/registry hands back every
    collection's slug + full read_teams ACL in one call -- the complete
    cross-team access map of the system. Any authenticated user (owner,
    teammate, or a total outsider) being able to enumerate that is itself
    the leak the endpoint must not have, regardless of what the caller can
    otherwise see via GET /collections."""
    owner = _user('registry-reject-owner')
    outsider = _user('registry-reject-outsider')

    owner_client = login_as(owner.username)
    create_resp = owner_client.post('/api/v1/collections', json={'name': 'Owner Only Collection'})
    assert create_resp.status_code == 200

    assert owner_client.get('/api/v1/collections/registry').status_code == 403
    outsider_client = login_as(outsider.username)
    assert outsider_client.get('/api/v1/collections/registry').status_code == 403



# --- Dedicated Collection-registry notification -----------------------------

def test_create_collection_notifies_knowledge_without_external_webhook_payload(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: True)
    owner = _user('coll-notify-create')
    owner_client = login_as(owner.username)
    with patch.object(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
    ) as notify:
        response = owner_client.post(
            '/api/v1/collections',
            json={'name': 'Knowledge notification', 'read_teams': ['ops']},
        )

    assert response.status_code == 200, response.text
    notify.assert_called_once_with(response.json()['slug'])


def test_patch_collection_notifies_knowledge_with_stable_slug(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: True)
    owner = _user('coll-notify-patch')
    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'name': 'Before'})
    assert created.status_code == 200, created.text

    with patch.object(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
    ) as notify:
        response = owner_client.patch(
            f"/api/v1/collections/{created.json()['collection_id']}",
            json={'name': 'After', 'read_teams': ['legal']},
        )

    assert response.status_code == 200, response.text
    notify.assert_called_once_with(created.json()['slug'])


def test_collection_notification_enqueue_failure_does_not_rollback_create(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: True)
    owner = _user('coll-notify-failure')
    owner_client = login_as(owner.username)
    monkeypatch.setattr(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
        lambda *_: (_ for _ in ()).throw(RuntimeError('broker unavailable')),
    )

    response = owner_client.post('/api/v1/collections', json={'name': 'Still committed'})
    assert response.status_code == 200, response.text
    assert owner_client.get(f"/api/v1/collections/{response.json()['collection_id']}").status_code == 200


def test_collection_change_skips_broker_when_knowledge_is_not_configured(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: False)
    owner = _user('coll-notify-unconfigured')
    owner_client = login_as(owner.username)

    with patch.object(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
    ) as notify:
        response = owner_client.post('/api/v1/collections', json={'name': 'No Knowledge channel'})

    assert response.status_code == 200, response.text
    notify.assert_not_called()


def _minimal_text_pdf_bytes(text: str) -> bytes:
    """Hand-built, dependency-free single-page PDF with a real, extractable
    content stream -- copied from tests/test_frontmatter_contract.py's
    identical helper (self-contained per this suite's one-helper-per-file
    convention) since reportlab et al. aren't in the pinned requirements and
    PdfReader.extract_text() needs a genuine content stream. Verified
    against the pinned pypdf==6.16.1.
    """
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    stream = f'BT /F1 12 Tf 20 150 Td ({text}) Tj ET'.encode()
    objects.append(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream')

    buf = BytesIO()
    buf.write(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f'{index} 0 obj\n'.encode())
        buf.write(obj)
        buf.write(b'\nendobj\n')
    xref_offset = buf.tell()
    count = len(objects) + 1
    buf.write(f'xref\n0 {count}\n'.encode())
    buf.write(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        buf.write(f'{offset:010d} 00000 n \n'.encode())
    buf.write(f'trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF'.encode())
    return buf.getvalue()


def test_full_chain_collection_create_upload_process_frontmatter_has_collection_fields(monkeypatch):
    """The chain the task asks to be walked end to end: POST /collections ->
    POST /collections/{id}/upload -> POST /collections/{id}/start ->
    processing_info.settings -> app/workers/tasks.py's metadata dict ->
    _build_rag_frontmatter -> the job's actual result_markdown frontmatter
    carries `collection` (the slug) and `collection_name`.

    Runs the REAL pypdf-fallback conversion (PaddleOCR reported unavailable,
    same technique as test_frontmatter_contract.py) rather than mocking
    convert_to_markdown_with_details -- nothing about the metadata-to-
    frontmatter wiring is faked.
    """
    from app.api import routes
    from app.services import paddle_service
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(paddle_service, '_paddleocr_available', lambda: False)
    # Run the real task body synchronously instead of handing it to Celery --
    # same object process_job.delay is normally called on (routes.py imports
    # the identical task instance), so this also affects tasks.process_job.
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: tasks.process_job(*args, **kwargs))

    owner = _user('chain-owner')
    owner_client = login_as(owner.username)

    create_resp = owner_client.post('/api/v1/collections', json={'name': 'Chain Collection'})
    assert create_resp.status_code == 200
    collection_body = create_resp.json()
    collection_id = collection_body['collection_id']
    slug = collection_body['slug']
    name = collection_body['name']

    upload_resp = owner_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('chain-doc.pdf', _minimal_text_pdf_bytes('Full chain contract test document.'), 'application/pdf')},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    job_id = upload_resp.json()['job_id']

    start_resp = owner_client.post(
        f'/api/v1/collections/{collection_id}/start',
        json={'profile_id': 'ppocrv6_tiny'},
    )
    assert start_resp.status_code == 200, start_resp.text
    assert start_resp.json()['started_jobs'] == 1

    db = TestingSessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.FINISHED, job.error_message
        markdown = job.result_markdown
    finally:
        db.close()

    assert markdown.startswith('---\n'), 'result must open with a YAML frontmatter block'
    end = markdown.index('\n---\n', 4)
    frontmatter = yaml.safe_load(markdown[4:end + 1])
    assert frontmatter['mode'] == 'collection'
    assert frontmatter['collection'] == slug
    assert frontmatter['collection_name'] == name


def test_single_upload_frontmatter_never_carries_collection_fields(monkeypatch):
    """Regression guard for the other half of the contract: a job never
    routed through a collection must not carry `collection`/`collection_name`
    at all (not even as empty strings) -- see _build_rag_frontmatter's
    `if metadata.get('collection_slug'): ...` guards."""
    from app.api import routes
    from app.services import paddle_service
    from app.workers import tasks

    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(paddle_service, '_paddleocr_available', lambda: False)
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: tasks.process_job(*args, **kwargs))

    owner = _user('single-owner')
    owner_client = login_as(owner.username)

    upload_resp = owner_client.post(
        '/api/v1/upload',
        files={'file': ('single-doc.pdf', _minimal_text_pdf_bytes('Single upload, no collection.'), 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    job_id = upload_resp.json()['job_id']

    db = TestingSessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.FINISHED, job.error_message
        markdown = job.result_markdown
    finally:
        db.close()

    end = markdown.index('\n---\n', 4)
    frontmatter = yaml.safe_load(markdown[4:end + 1])
    assert frontmatter['mode'] == 'single'
    assert 'collection' not in frontmatter
    assert 'collection_name' not in frontmatter
