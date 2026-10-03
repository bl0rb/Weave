"""Collections contract tests: the slug/name/description fields and the
grants (ADR 0008) on top of the pre-existing collections table (see app/models/models.py's
Collection docstring and README.md's "Collections" section), GET /collections
visibility, PATCH ownership, the GET /collections/registry sync endpoint for
Weave-Knowledge, and the full create -> upload -> process -> frontmatter
chain that stamps a collection's slug/name into every document processed
through it.

Real cookie-based logins (create_test_user/login_as, same idioms as
test_import_api.py/test_benchmarks_api.py) because collection roles
(app/services/collection_access.py) resolve against the real users and
team memberships, exactly like the job/run/benchmark authz tests.
test_api.py's collection tests keep using its admin-bypass fixture for
everything that doesn't need real per-user authz (create-time
slug/description persistence); this file is only for what does.
"""

from io import BytesIO
import uuid
from unittest.mock import patch

import pytest
import yaml

from app.models.models import ImportRun, ImportRunStatus, Job, JobStatus, ManagedBot, Team, UserRole, user_teams
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

    settings.worker_tmp_dir = tmp_path / 'work'
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


def _create(client, name: str, *, team_id: str | None = None, team_role: str = 'member', **extra):
    """A restricted space, optionally shared with one team -- the shape the
    old implicit "owner's primary team" rule used to give every space."""
    grants = [{'team_id': team_id, 'role': team_role}] if team_id else []
    return client.post(
        '/api/v1/collections',
        json={'name': name, 'description': 'Test purpose', 'visibility': 'restricted', 'grants': grants, **extra},
    )


def test_collections_visibility_and_patch_control_matrix():
    """Grants decide reads (GET /collections, GET /collections/{id}); PATCH
    additionally requires the owner role or admin -- read is not control,
    same split as import_routes._require_run_control/
    benchmarks._require_benchmark_control."""
    team_id = _make_team('coll-team')
    owner = _user('coll-owner', team_id=team_id)
    teammate = _user('coll-teammate', team_id=team_id)
    outsider = _user('coll-outsider')
    admin = _user('coll-admin', role=UserRole.ADMIN)

    owner_client = login_as(owner.username)
    create_resp = _create(owner_client, 'Team Docs', team_id=team_id)
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

    ops_team_id = _make_team('ops')
    owner_patch = owner_client.patch(
        f'/api/v1/collections/{collection_id}',
        json={'grants': [{'user_id': owner.id, 'role': 'owner'}, {'team_id': ops_team_id, 'role': 'reader'}]},
    )
    assert owner_patch.status_code == 200, owner_patch.text
    assert [(g['user_id'], g['team_id'], g['role']) for g in owner_patch.json()['grants']] == [
        (owner.id, None, 'owner'), (None, ops_team_id, 'reader'),
    ]
    # The former team lost its grant with the replace.
    assert teammate_client.get(f'/api/v1/collections/{collection_id}').status_code == 404
    # CollectionUpdateRequest carries no `slug` field -- it never changes.
    assert owner_patch.json()['slug'] == admin_patch.json()['slug']


def test_legacy_primary_team_member_can_add_to_a_collection_after_upgrade():
    """Accounts created before ``user_teams`` existed only have
    ``users.team_id``. They remain members of that primary team, including
    for collection uploads, until an explicit membership role says otherwise.
    """
    team_id = _make_team('legacy-collection-team')
    owner = _user('legacy-collection-owner', team_id=team_id)
    teammate = _user('legacy-collection-teammate', team_id=team_id)

    owner_client = login_as(owner.username)
    created = _create(owner_client, 'Legacy team knowledge', team_id=team_id)
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']

    teammate_client = login_as(teammate.username)
    detail = teammate_client.get(f'/api/v1/collections/{collection_id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['can_upload'] is True

    uploaded = teammate_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('legacy-teammate.pdf', b'%PDF-legacy-teammate', 'application/pdf')},
    )
    assert uploaded.status_code == 200, uploaded.text


def test_explicit_reader_role_still_cannot_add_to_a_collection():
    """The compatibility fallback must not turn an explicit ``reader`` role
    into a writer merely because the account also retains ``team_id``."""
    team_id = _make_team('legacy-reader-team')
    owner = _user('legacy-reader-owner', team_id=team_id)
    reader = _user('legacy-reader-user', team_id=team_id)
    with TestingSessionLocal() as db:
        db.execute(user_teams.insert().values(user_id=reader.id, team_id=team_id, role='reader'))
        db.commit()

    created = _create(login_as(owner.username), 'Reader-only legacy team', team_id=team_id)
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']
    reader_client = login_as(reader.username)
    assert reader_client.get(f'/api/v1/collections/{collection_id}').json()['can_upload'] is False
    denied = reader_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('reader.pdf', b'%PDF-reader', 'application/pdf')},
    )
    assert denied.status_code == 403


def test_legacy_primary_team_member_can_add_to_an_explicitly_shared_collection():
    """The same fallback applies when the collection is owned by another
    team and this legacy account is entitled through a team grant."""
    owner_team_id = _make_team('legacy-shared-owner-team')
    reader_team_id = _make_team('legacy-shared-reader-team')
    owner = _user('legacy-shared-owner', team_id=owner_team_id)
    contributor = _user('legacy-shared-contributor', team_id=reader_team_id)
    created = _create(login_as(owner.username), 'Explicitly shared legacy knowledge', team_id=reader_team_id)
    assert created.status_code == 200, created.text
    collection_id = created.json()['collection_id']
    contributor_client = login_as(contributor.username)
    detail = contributor_client.get(f'/api/v1/collections/{collection_id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['can_upload'] is True
    uploaded = contributor_client.post(
        f'/api/v1/collections/{collection_id}/upload',
        files={'file': ('legacy-shared-contributor.pdf', b'%PDF-legacy-shared-contributor', 'application/pdf')},
    )
    assert uploaded.status_code == 200, uploaded.text


def test_multiple_memberships_grant_reads_without_resharing_owned_collections():
    team_a = _make_team('multi-a')
    team_b = _make_team('multi-b')
    team_c = _make_team('multi-c')
    reader = _user('multi-reader', team_id=team_a)
    owner_b = _user('multi-owner-b', team_id=team_b)
    owner_c = _user('multi-owner-c', team_id=team_c)
    admin_client = login_as(_user('multi-admin', role=UserRole.ADMIN).username)
    reader_client = login_as(reader.username)
    client_b = login_as(owner_b.username)
    own_id = _create(reader_client, 'Owned in A', team_id=team_a).json()['collection_id']
    collection_b = _create(client_b, 'Owned in B', team_id=team_b).json()['collection_id']
    collection_c = _create(login_as(owner_c.username), 'Owned in C', team_id=team_c).json()['collection_id']
    assert reader_client.get(f'/api/v1/collections/{collection_b}').status_code == 404
    updated = admin_client.patch(f'/api/v1/auth/admin/users/{reader.id}', json={'team_ids': [team_a, team_b], 'team_id': team_a, 'team_roles': {team_a: 'reader', team_b: 'reader'}})
    assert updated.status_code == 200, updated.text
    assert set(updated.json()['team_ids']) == {team_a, team_b}
    legacy_update = admin_client.patch(f'/api/v1/auth/admin/users/{reader.id}', json={'team_id': team_a})
    assert set(legacy_update.json()['team_ids']) == {team_a, team_b}
    assert reader_client.get(f'/api/v1/collections/{collection_b}').status_code == 200
    assert reader_client.get(f'/api/v1/collections/{collection_c}').status_code == 404
    assert client_b.get(f'/api/v1/collections/{own_id}').status_code == 404
    assert reader_client.patch(f'/api/v1/collections/{collection_b}', json={'name': 'Not allowed'}).status_code == 403
    revoked = admin_client.patch(f'/api/v1/auth/admin/users/{reader.id}', json={'team_ids': [team_a]})
    assert revoked.status_code == 200
    assert reader_client.get(f'/api/v1/collections/{collection_b}').status_code == 404
    listed = {item['collection_id'] for item in reader_client.get('/api/v1/collections').json()['items']}
    assert own_id in listed
    assert collection_b not in listed
    assert collection_c not in listed


def test_empty_collection_can_be_deleted_only_by_owner_or_admin():
    team_id = _make_team('coll-delete-team')
    owner = _user('coll-delete-owner', team_id=team_id)
    teammate = _user('coll-delete-teammate', team_id=team_id)
    outsider = _user('coll-delete-outsider')

    owner_client = login_as(owner.username)
    created = _create(owner_client, 'Leerer Bereich', team_id=team_id)
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

    with_document = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Bereich mit Dokument'})
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

    with_import = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Bereich mit Import'})
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

    assigned = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Bereich für Bot'})
    assert assigned.status_code == 200, assigned.text
    bot_collection_id = assigned.json()['collection_id']
    bot_collection_slug = assigned.json()['slug']
    db = TestingSessionLocal()
    try:
        db.add(ManagedBot(
            id=f'collection-guard-{uuid.uuid4().hex[:8]}',
            name='Collection Guard',
            webhook_url='https://n8n.example.com/webhook/guard',
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
    team_id = _make_team('ops')
    with TestingSessionLocal() as db:
        team_name = db.get(Team, team_id).name

    owner_client = login_as(owner.username)
    create_resp = _create(
        owner_client, 'Registry Sample', team_id=team_id, team_role='reader', slug=f'registry-sample-{uuid.uuid4().hex[:8]}'
    )
    assert create_resp.status_code == 200
    slug = create_resp.json()['slug']

    admin_client = login_as(admin.username)
    registry_resp = admin_client.get('/api/v1/collections/registry')
    assert registry_resp.status_code == 200
    items = registry_resp.json()['items']
    entry = next(item for item in items if item['slug'] == slug)
    assert entry == {
        'slug': slug,
        'name': 'Registry Sample',
        'description': 'Test purpose',
        'visibility': 'restricted',
        # Computed from the grants: every role includes reading, so the
        # owner is named too.
        'read_teams': [team_name],
        'read_users': [owner.id],
    }
    assert set(entry.keys()) == {'slug', 'name', 'description', 'visibility', 'read_teams', 'read_users'}



def test_collections_registry_jobs_names_the_space_of_each_job(monkeypatch):
    """GET /collections/registry/jobs feeds Weave-Knowledge's
    reconcile-collections (F41): for the job ids the caller sends it names
    the space each job belongs to; jobs without a space, with a deleted
    space or unknown ids are absent. Same reader as the registry: the
    Knowledge service token or an admin, nobody else."""
    from app.core.config import settings
    from conftest import client as anonymous_client

    owner = _user('registry-jobs-owner')
    admin = _user('registry-jobs-admin', role=UserRole.ADMIN)
    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Altbestand Bereich'})
    assert created.status_code == 200, created.text
    collection_id, slug = created.json()['collection_id'], created.json()['slug']

    def _job(settings_info: dict) -> str:
        with TestingSessionLocal() as db:
            job = Job(original_filename='alt.pdf', upload_path='/tmp/alt.pdf', owner_id=owner.id,
                      processing_info={'settings': settings_info})
            db.add(job)
            db.commit()
            return job.id

    in_space = _job({'collection_id': collection_id})
    without_space = _job({})
    deleted_space = _job({'collection_id': str(uuid.uuid4())})
    params = [('job_id', job_id) for job_id in (in_space, without_space, deleted_space, str(uuid.uuid4()))]

    response = login_as(admin.username).get('/api/v1/collections/registry/jobs', params=params)
    assert response.status_code == 200, response.text
    assert response.json() == {'items': [{'job_id': in_space, 'collection': slug, 'collection_name': 'Altbestand Bereich'}]}

    monkeypatch.setattr(settings, 'knowledge_ingest_api_token', 'knowledge-test-credential')
    service_response = anonymous_client.get(
        '/api/v1/collections/registry/jobs', params=params,
        headers={'Authorization': 'Bearer knowledge-test-credential'},
    )
    assert service_response.json() == response.json()

    assert owner_client.get('/api/v1/collections/registry/jobs', params=params).status_code == 403
    too_many = [('job_id', str(uuid.uuid4())) for _ in range(101)]
    assert login_as(admin.username).get('/api/v1/collections/registry/jobs', params=too_many).status_code == 422


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
    create_resp = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Owner Only Collection'})
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
            json={'description': 'Test purpose', 'name': 'Knowledge notification'},
        )

    assert response.status_code == 200, response.text
    notify.assert_called_once_with(response.json()['slug'])


def test_patch_collection_notifies_knowledge_with_stable_slug(monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes.publication_tasks, 'publication_configured', lambda: True)
    legal_team_id = _make_team('legal')
    owner = _user('coll-notify-patch')
    owner_client = login_as(owner.username)
    created = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Before'})
    assert created.status_code == 200, created.text

    with patch.object(
        routes.publication_tasks.notify_collection_registry_changed,
        'delay',
    ) as notify:
        response = owner_client.patch(
            f"/api/v1/collections/{created.json()['collection_id']}",
            json={'name': 'After', 'grants': [{'user_id': owner.id, 'role': 'owner'}, {'team_id': legal_team_id, 'role': 'reader'}]},
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

    response = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Still committed'})
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
        response = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'No Knowledge channel'})

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

    create_resp = owner_client.post('/api/v1/collections', json={'description': 'Test purpose', 'name': 'Chain Collection'})
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
