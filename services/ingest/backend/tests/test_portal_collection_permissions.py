"""Existing shared collection documents use the same rights on every surface."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.api.routes import _collection_control_job_filter
from app.models.models import Job, User, user_teams
from tests.conftest import create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _job, _team


@pytest.mark.parametrize('role', ['member', 'reader'])
def test_shared_collection_job_visibility_and_release_match_membership(monkeypatch, role):
    _configure(monkeypatch)
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda *_: None)
    suffix = uuid.uuid4().hex[:8]
    team = _team('shared-legacy')
    owner = create_test_user(username=f'owner-{suffix}', email=f'owner-{suffix}@example.com')
    user = create_test_user(username=f'contributor-{suffix}', email=f'contributor-{suffix}@example.com', team_id=team.id)
    with _db() as db:
        db.execute(user_teams.insert().values(user_id=user.id, team_id=team.id, role=role))
        db.commit()
    collection = _collection(owner.id, read_teams=[team.name])
    job = _job(owner.id, collection)
    authed = login_as(user.username)
    area = authed.get(f'/api/v1/collections/{collection.id}')
    assert area.status_code == 200
    assert area.json()['can_upload'] is (role == 'member')
    assert (job.id in area.json()['job_ids']) is (role == 'member')
    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id})
    assert listing.status_code == 200, listing.text
    assert listing.json()['total'] == (1 if role == 'member' else 0)
    preview = authed.get(f'/api/v1/portal/documents/{job.id}')
    if role == 'reader':
        assert preview.status_code == 404
        assert authed.put(f'/api/v1/jobs/{job.id}/save', json={'markdown': job.result_markdown}).status_code == 404
        assert authed.post(f'/api/v1/portal/collections/{collection.id}/release-all', json={}).status_code == 403
    else:
        assert listing.json()['items'][0]['can_release'] is True
        assert preview.status_code == 200, preview.text
        assert preview.json()['can_release'] is True
        released = authed.post(f'/api/v1/portal/collections/{collection.id}/release-all', json={})
        assert released.status_code == 202, released.text
        assert released.json()['released'] == 1


def test_postgres_collection_predicate_declares_json_value_column():
    """PostgreSQL requires the derived column alias that SQLite rejects."""
    from types import SimpleNamespace

    class Database:
        bind = SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, statement):
            return SimpleNamespace(all=lambda: [('team', 'member')])

        def scalars(self, statement):
            return SimpleNamespace(all=lambda: ['Service'])

    user = User(id='user', team_id='team')
    expression = _collection_control_job_filter(Database(), user)
    sql = str(select(Job.id).where(expression).compile(dialect=postgresql.dialect()))
    assert 'json_array_elements_text' in sql
    assert 'AS job_collection_read_team(value)' in sql
