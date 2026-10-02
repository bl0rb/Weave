"""FEATURE 1 (content hash + document versioning) and FEATURE 2 (JSON export)
API tests.

Real cookie-based logins via login_as/create_test_user (same idioms as
test_job_authz.py) rather than the get_current_user bypass test_api.py uses:
predecessor lookup (_find_predecessor_job) and the /versions and
export.json owner/team fields are computed against actual owner_id/team_id
rows, so they need real persisted users to mean anything.
"""

import pytest
from sqlalchemy import select

from app.models.models import Job, JobStatus, Team, User
from app.services.security import rate_limiter
from conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # /auth/login is rate-limited per client host, and TestClient always
    # presents as "testclient" -- shared bucket across every test unless
    # reset per test (see test_job_authz.py's identical fixture).
    rate_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    from app.api import routes
    from app.core.config import settings

    settings.worker_tmp_dir = tmp_path / 'work'
    # Uploads under test never reach a real worker; process_job.delay is a
    # no-op so jobs stay PENDING unless a test flips status itself.
    monkeypatch.setattr(routes.process_job, 'delay', lambda *args, **kwargs: None)
    yield


def _create_team(name: str) -> str:
    db = TestingSessionLocal()
    try:
        team = Team(name=name)
        db.add(team)
        db.commit()
        db.refresh(team)
        return team.id
    finally:
        db.close()


def _set_user_team(username: str, team_id: str | None) -> None:
    """Direct DB flip of a user's team, simulating an admin removing/moving
    someone after the fact (used to construct visibility changing between
    when a document version was linked and when it's later read back)."""
    db = TestingSessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == username))
        user.team_id = team_id
        db.commit()
    finally:
        db.close()


def _mark_finished(job_id: str, markdown: str) -> None:
    db = TestingSessionLocal()
    try:
        job = db.get(Job, job_id)
        job.status = JobStatus.FINISHED
        job.result_markdown = markdown
        db.commit()
    finally:
        db.close()


# --- FEATURE 1: content hash + document versioning ---------------------------

def test_reupload_with_modified_content_creates_version_two():
    create_test_user(username='ver-modify-user', email='ver-modify-user@example.com')
    client = login_as('ver-modify-user')

    first = client.post(
        '/api/v1/upload',
        files={'file': ('report.pdf', b'%PDF-version-one', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert first.status_code == 200
    job_v1 = first.json()['job_id']

    second = client.post(
        '/api/v1/upload',
        files={'file': ('report.pdf', b'%PDF-version-two-modified', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert second.status_code == 200
    job_v2 = second.json()['job_id']
    assert job_v2 != job_v1

    v1_detail = client.get(f'/api/v1/jobs/{job_v1}').json()
    assert v1_detail['document_version'] == 1
    assert v1_detail['previous_job_id'] is None
    assert v1_detail['content_sha256']

    v2_detail = client.get(f'/api/v1/jobs/{job_v2}').json()
    assert v2_detail['document_version'] == 2
    assert v2_detail['previous_job_id'] == job_v1
    assert v2_detail['content_sha256'] != v1_detail['content_sha256']


def test_identical_reupload_returns_409_and_creates_no_new_job():
    create_test_user(username='ver-dup-user', email='ver-dup-user@example.com')
    client = login_as('ver-dup-user')

    first = client.post(
        '/api/v1/upload',
        files={'file': ('dup.pdf', b'%PDF-identical-bytes', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert first.status_code == 200
    job_id = first.json()['job_id']

    before_count = len(client.get('/api/v1/jobs').json()['items'])

    duplicate = client.post(
        '/api/v1/upload',
        files={'file': ('dup.pdf', b'%PDF-identical-bytes', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body['duplicate_of'] == job_id
    assert body['existing_version'] == 1
    assert isinstance(body['detail'], str)
    assert 'version 1' in body['detail']

    after_count = len(client.get('/api/v1/jobs').json()['items'])
    assert after_count == before_count


def _space(client, name: str, **extra) -> str:
    resp = client.post(
        '/api/v1/collections', json={'name': name, 'description': 'Test purpose', 'visibility': 'restricted', **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()['collection_id']


def _space_upload(client, collection_id: str, filename: str, content: bytes):
    return client.post(
        f'/api/v1/collections/{collection_id}/upload', files={'file': (filename, content, 'application/pdf')}
    )


def _job_row(job_id: str) -> Job:
    db = TestingSessionLocal()
    try:
        return db.get(Job, job_id)
    finally:
        db.close()


def test_version_chain_never_crosses_knowledge_spaces():
    """ADR 0008: a same-named document in a space the uploader has no role
    on is neither a predecessor (Knowledge would supersede it on release)
    nor a duplicate (the 409 would disclose its job id)."""
    team_id = _create_team('ver-space-team')
    create_test_user(username='ver-space-v', email='ver-space-v@example.com', team_id=team_id)
    create_test_user(username='ver-space-u', email='ver-space-u@example.com', team_id=team_id)
    owner_client = login_as('ver-space-v')
    uploader_client = login_as('ver-space-u')

    foreign_space = _space(owner_client, 'Ver Space Foreign')
    foreign = _space_upload(owner_client, foreign_space, 'Richtlinie.pdf', b'%PDF-foreign')
    assert foreign.status_code == 200, foreign.text
    foreign_job = foreign.json()['job_id']
    assert uploader_client.get(f'/api/v1/jobs/{foreign_job}').status_code == 404

    own_space = _space(uploader_client, 'Ver Space Own')
    same_bytes = _space_upload(uploader_client, own_space, 'Richtlinie.pdf', b'%PDF-foreign')
    assert same_bytes.status_code == 200, same_bytes.text
    assert 'duplicate_of' not in same_bytes.json()
    same_bytes_row = _job_row(same_bytes.json()['job_id'])
    assert same_bytes_row.previous_job_id is None
    assert same_bytes_row.document_version == 1

    # The same person uploading the same name into a second space of theirs.
    changed = _space_upload(owner_client, _space(owner_client, 'Ver Space Second'), 'Richtlinie.pdf', b'%PDF-changed')
    assert changed.status_code == 200, changed.text
    assert _job_row(changed.json()['job_id']).previous_job_id is None

    # A plain upload never chains onto a space document either.
    plain = owner_client.post(
        '/api/v1/upload',
        files={'file': ('Richtlinie.pdf', b'%PDF-plain', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    assert plain.status_code == 200, plain.text
    assert _job_row(plain.json()['job_id']).previous_job_id is None


def test_version_chain_follows_space_roles_not_the_uploaders_team():
    create_test_user(username='ver-role-owner', email='ver-role-owner@example.com')
    member = create_test_user(username='ver-role-member', email='ver-role-member@example.com')
    owner_client = login_as('ver-role-owner')
    member_client = login_as('ver-role-member')
    space = _space(owner_client, 'Ver Role Space', grants=[{'user_id': member.id, 'role': 'member'}])

    first = _space_upload(owner_client, space, 'Handbuch.pdf', b'%PDF-v1')
    assert first.status_code == 200, first.text
    second = _space_upload(member_client, space, 'Handbuch.pdf', b'%PDF-v2')
    assert second.status_code == 200, second.text
    second_row = _job_row(second.json()['job_id'])
    assert second_row.previous_job_id == first.json()['job_id']
    assert second_row.document_version == 2

    duplicate = _space_upload(member_client, space, 'Handbuch.pdf', b'%PDF-v2')
    assert duplicate.status_code == 409
    assert duplicate.json()['duplicate_of'] == second.json()['job_id']


def test_versions_endpoint_shape_newest_first_and_is_current():
    create_test_user(username='ver-shape-user', email='ver-shape-user@example.com')
    client = login_as('ver-shape-user')

    v1 = client.post(
        '/api/v1/upload',
        files={'file': ('shape.pdf', b'%PDF-shape-v1', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']
    v2 = client.post(
        '/api/v1/upload',
        files={'file': ('shape.pdf', b'%PDF-shape-v2', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']

    resp = client.get(f'/api/v1/jobs/{v1}/versions')
    assert resp.status_code == 200
    items = resp.json()['items']

    assert [item['document_version'] for item in items] == [2, 1]
    assert [item['job_id'] for item in items] == [v2, v1]
    assert items[0]['is_current'] is True
    assert items[1]['is_current'] is False
    assert items[0]['uploaded_by'] == 'ver-shape-user'
    assert set(items[0].keys()) == {
        'job_id', 'document_version', 'content_sha256', 'status', 'created_at', 'uploaded_by', 'is_current',
    }


def test_versions_endpoint_team_visibility():
    team_id = _create_team('ver-versions-team')
    owner = create_test_user(username='ver-team-owner', email='ver-team-owner@example.com', team_id=team_id)
    create_test_user(username='ver-teammate', email='ver-teammate@example.com', team_id=team_id)
    create_test_user(username='ver-outsider', email='ver-outsider@example.com')

    owner_client = login_as('ver-team-owner')
    v1 = owner_client.post(
        '/api/v1/upload',
        files={'file': ('teamdoc.pdf', b'%PDF-team-v1', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']
    v2 = owner_client.post(
        '/api/v1/upload',
        files={'file': ('teamdoc.pdf', b'%PDF-team-v2', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']

    teammate_client = login_as('ver-teammate')
    teammate_resp = teammate_client.get(f'/api/v1/jobs/{v1}/versions')
    assert teammate_resp.status_code == 200
    teammate_versions = {item['document_version'] for item in teammate_resp.json()['items']}
    assert teammate_versions == {1, 2}

    outsider_client = login_as('ver-outsider')
    assert outsider_client.get(f'/api/v1/jobs/{v1}/versions').status_code == 404
    assert outsider_client.get(f'/api/v1/jobs/{v2}/versions').status_code == 404

    del owner  # only needed to own the jobs above


def test_versions_endpoint_is_current_computed_over_visible_slice_only():
    """A caller who can see an older version but not the newest one (e.g. a
    user who owns v1 but has since left the team that owns v2) must not be
    able to tell, from the response, that an invisible newer version exists.
    Their newest VISIBLE entry should be flagged is_current -- not silently
    marked stale, which would leak the existence of a version they can't
    see."""
    team_id = _create_team('ver-leaver-team')
    create_test_user(username='ver-leaver', email='ver-leaver@example.com', team_id=team_id)
    create_test_user(username='ver-remaining-member', email='ver-remaining-member@example.com', team_id=team_id)

    leaver_client = login_as('ver-leaver')
    v1 = leaver_client.post(
        '/api/v1/upload',
        files={'file': ('leavesdoc.pdf', b'%PDF-leaver-v1', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']

    # A teammate (still on the team, so they can see v1) uploads a new
    # version of the same document.
    remaining_client = login_as('ver-remaining-member')
    v2 = remaining_client.post(
        '/api/v1/upload',
        files={'file': ('leavesdoc.pdf', b'%PDF-leaver-v2', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    ).json()['job_id']

    # Sanity check while both are still on the team: the full chain is
    # visible, and v2 is correctly flagged current.
    full_resp = remaining_client.get(f'/api/v1/jobs/{v2}/versions')
    full_items = full_resp.json()['items']
    assert [item['job_id'] for item in full_items] == [v2, v1]
    assert full_items[0]['is_current'] is True
    assert full_items[1]['is_current'] is False

    # The original uploader now leaves the team -- they still own v1 (so it
    # stays visible to them by ownership) but lose team-based visibility
    # into v2, which they don't own. (This also drops v1 from the
    # remaining member's view, since v1's visibility to them is entirely
    # team-based -- irrelevant to what's under test here.)
    _set_user_team('ver-leaver', None)

    resp = leaver_client.get(f'/api/v1/jobs/{v1}/versions')
    assert resp.status_code == 200
    items = resp.json()['items']

    # Only the visible slice (v1) comes back -- v2 must not be exposed.
    assert [item['job_id'] for item in items] == [v1]
    # And it must be flagged current: it's the newest version this caller
    # can see, regardless of the invisible v2 sitting ahead of it.
    assert items[0]['is_current'] is True


# --- FEATURE 2: JSON export ----------------------------------------------------

def test_export_json_shape():
    create_test_user(username='exp-shape-user', email='exp-shape-user@example.com')
    client = login_as('exp-shape-user')

    upload = client.post(
        '/api/v1/upload',
        files={'file': ('exportme.pdf', b'%PDF-export-shape', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'tags': 'finance'},
    )
    job_id = upload.json()['job_id']
    _mark_finished(job_id, '---\nsource: exportme.pdf\n---\n\nBody text')

    resp = client.get(f'/api/v1/jobs/{job_id}/export.json')
    assert resp.status_code == 200
    assert resp.headers['content-type'].startswith('application/json')
    assert f'{job_id}.json' in resp.headers['content-disposition']

    body = resp.json()
    assert body['schema'] == 'weave-ingest.job-export/1'
    assert body['job']['id'] == job_id
    assert body['job']['status'] == JobStatus.FINISHED.value
    assert body['document']['source_filename'] == 'exportme.pdf'
    assert body['document']['document_version'] == 1
    assert body['document']['previous_job_id'] is None
    assert body['document']['tags'] == ['finance']
    assert body['uploader']['username'] == 'exp-shape-user'
    assert body['markdown'] == '---\nsource: exportme.pdf\n---\n\nBody text'
    assert 'profile_id' in body['processing']
    assert 'quality_gate' in body['processing']


def test_export_json_requires_password_when_job_protected():
    create_test_user(username='exp-pw-user', email='exp-pw-user@example.com')
    client = login_as('exp-pw-user')

    upload = client.post(
        '/api/v1/upload',
        files={'file': ('protected.pdf', b'%PDF-protected', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny', 'password': 'sekret123'},
    )
    job_id = upload.json()['job_id']
    _mark_finished(job_id, '---\nsource: protected.pdf\n---\n\nBody')

    no_password = client.get(f'/api/v1/jobs/{job_id}/export.json')
    assert no_password.status_code == 401

    wrong_password = client.get(f'/api/v1/jobs/{job_id}/export.json', params={'password': 'nope'})
    assert wrong_password.status_code == 401

    ok = client.get(f'/api/v1/jobs/{job_id}/export.json', params={'password': 'sekret123'})
    assert ok.status_code == 200


def test_export_json_conflicts_when_job_not_finished():
    create_test_user(username='exp-unfinished-user', email='exp-unfinished-user@example.com')
    client = login_as('exp-unfinished-user')

    upload = client.post(
        '/api/v1/upload',
        files={'file': ('pending.pdf', b'%PDF-pending', 'application/pdf')},
        data={'profile_id': 'ppocrv6_tiny'},
    )
    job_id = upload.json()['job_id']

    resp = client.get(f'/api/v1/jobs/{job_id}/export.json')
    assert resp.status_code == 409
