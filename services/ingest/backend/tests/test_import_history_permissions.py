from app.models.models import Collection, ImportRun, ImportRunStatus, Team, UserRole
from tests.test_import_api import _db, _make_run, _make_source, _make_team, _user
from conftest import login_as


def test_completed_import_is_visible_to_collection_contributor_without_credential_control():
    owner = _user('history-owner')
    team_id = _make_team('history-contributors')
    contributor = _user('history-contributor', team_id=team_id)
    outsider = _user('history-outsider')
    source = _make_source(owner.id, server_kind='cloud')
    run = _make_run(owner_id=owner.id, source_id=source.id, status=ImportRunStatus.FINISHED)
    with _db() as db:
        collection = Collection(owner_id=owner.id, slug=f'history-{run.id}', name='Existing import', read_teams=[db.get(Team, team_id).name])
        db.add(collection)
        db.flush()
        db.get(ImportRun, run.id).options = {'collection_id': collection.id}
        db.commit()
    client = login_as(contributor.username)
    listed = client.get('/api/v1/import/runs')
    assert listed.status_code == 200, listed.text
    assert run.id in {item['id'] for item in listed.json()['items']}
    detail = client.get(f'/api/v1/import/runs/{run.id}')
    assert detail.status_code == 200, detail.text
    assert detail.json()['can_edit'] is False
    assert detail.json()['can_sync'] is False
    assert client.post(f'/api/v1/import/runs/{run.id}/sync').status_code == 403
    assert login_as(outsider.username).get(f'/api/v1/import/runs/{run.id}').status_code == 404


def test_admin_can_manage_completed_import_without_reading_personal_credentials(monkeypatch):
    owner = _user('history-managed-owner')
    admin = _user('history-admin', role=UserRole.ADMIN)
    source = _make_source(owner.id, server_kind='cloud')
    run = _make_run(owner_id=owner.id, source_id=source.id, status=ImportRunStatus.FINISHED)
    calls = []
    def start_refresh(db, selected_source, *, template):
        calls.append((selected_source.id, selected_source.owner_id, template.id))
        return template
    monkeypatch.setattr('app.workers.refresh_tasks._start_refresh_run', start_refresh)
    client = login_as(admin.username)
    listing = client.get('/api/v1/import/runs').json()['items']
    item = next(item for item in listing if item['id'] == run.id)
    assert item['can_edit'] is True
    assert item['can_sync'] is True
    detail = client.get(f'/api/v1/import/runs/{run.id}')
    assert detail.status_code == 200
    assert detail.json()['can_edit'] is True
    assert detail.json()['can_sync'] is True
    assert 'credential' not in detail.text
    assert source.id not in {item['id'] for item in client.get('/api/v1/import/sources').json()['items']}
    response = client.post(f'/api/v1/import/runs/{run.id}/sync')
    assert response.status_code == 202, response.text
    assert calls == [(source.id, owner.id, run.id)]
