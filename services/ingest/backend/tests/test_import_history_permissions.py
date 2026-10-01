from sqlalchemy import delete

from app.models.models import (
    Collection,
    CollectionGrant,
    CollectionRole,
    DocumentRelease,
    ImportRun,
    ImportRunStatus,
    Job,
    KnowledgeWithdrawal,
    Team,
    UserRole,
)
from app.services.security import hash_password
from app.workers.celery_app import celery_app
from tests.test_import_api import _db, _make_job, _make_run, _make_source, _make_team, _user
from conftest import add_legacy_collection, login_as


def test_completed_import_is_visible_to_collection_contributor_without_credential_control():
    owner = _user('history-owner')
    team_id = _make_team('history-contributors')
    contributor = _user('history-contributor', team_id=team_id)
    outsider = _user('history-outsider')
    source = _make_source(owner.id, server_kind='cloud')
    run = _make_run(owner_id=owner.id, source_id=source.id, status=ImportRunStatus.FINISHED)
    with _db() as db:
        collection = add_legacy_collection(
            db, owner_id=owner.id, slug=f'history-{run.id}', name='Existing import', read_teams=[db.get(Team, team_id).name]
        )
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


def _space_import(importer_id: str, space_owner_id: str) -> tuple[str, str, str]:
    """A finished import of `importer_id`, a member (not the owner) of the
    target space. Returns (collection id, run id, page job id)."""
    source = _make_source(importer_id, server_kind='cloud')
    run = _make_run(owner_id=importer_id, source_id=source.id, status=ImportRunStatus.FINISHED)
    with _db() as db:
        collection = add_legacy_collection(db, owner_id=space_owner_id, slug=f'import-space-{run.id}', name='HR')
        db.add(CollectionGrant(collection_id=collection.id, user_id=importer_id, role=CollectionRole.MEMBER))
        db.get(ImportRun, run.id).options = {'collection_id': collection.id}
        db.commit()
        collection_id = collection.id
    job = _make_job(
        owner_id=importer_id, filename='kuendigung-mustermann.md', import_run_id=run.id,
        processing_info={'settings': {'mode': 'import', 'collection_id': collection_id}},
    )
    return collection_id, run.id, job.id


def test_space_import_follows_space_roles_not_the_run_owner_or_teammates():
    team_id = _make_team('space-import-team')
    importer = _user('space-importer', team_id=team_id)
    teammate = _user('space-teammate', team_id=team_id)
    space_owner = _user('space-import-owner')
    collection_id, run_id, job_id = _space_import(importer.id, space_owner.id)

    # A teammate without a grant sees neither the run nor its page titles.
    teammate_client = login_as(teammate.username)
    assert run_id not in {item['id'] for item in teammate_client.get('/api/v1/import/runs').json()['items']}
    assert teammate_client.get(f'/api/v1/import/runs/{run_id}').status_code == 404

    # The space owner sees it with its documents and may withdraw them, but
    # does not control someone else's run.
    owner_client = login_as(space_owner.username)
    detail = owner_client.get(f'/api/v1/import/runs/{run_id}')
    assert detail.status_code == 200, detail.text
    assert [job['id'] for job in detail.json()['jobs']] == [job_id]
    assert detail.json()['can_remove_missing'] is True
    assert owner_client.delete(f'/api/v1/import/runs/{run_id}?delete_jobs=true').status_code == 403

    # Revoking the importer's grant takes effect at once, run ownership notwithstanding.
    with _db() as db:
        db.execute(delete(CollectionGrant).where(
            CollectionGrant.collection_id == collection_id, CollectionGrant.user_id == importer.id,
        ))
        db.commit()
    importer_client = login_as(importer.username)
    assert run_id not in {item['id'] for item in importer_client.get('/api/v1/import/runs').json()['items']}
    assert importer_client.get(f'/api/v1/import/runs/{run_id}').status_code == 404
    assert importer_client.post(f'/api/v1/import/runs/{run_id}/cancel').status_code == 404
    assert importer_client.delete(f'/api/v1/import/runs/{run_id}?delete_jobs=true').status_code == 404
    with _db() as db:
        assert db.get(ImportRun, run_id) is not None
        assert db.get(Job, job_id) is not None


def test_deleting_a_run_with_its_documents_follows_the_document_delete_rules(monkeypatch):
    importer = _user('space-run-deleter')
    space_owner = _user('space-run-delete-owner')
    collection_id, run_id, job_id = _space_import(importer.id, space_owner.id)
    protected = _make_job(
        owner_id=importer.id, filename='geheim.md', import_run_id=run_id, password_hash=hash_password('pw'),
        processing_info={'settings': {'mode': 'import', 'collection_id': collection_id}},
    )
    with _db() as db:
        db.add(DocumentRelease(
            job_id=job_id, owner_id=importer.id, markdown_snapshot='# HR', markdown_sha256='0' * 64,
            payload={}, status='sent',
        ))
        db.commit()
    sent = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kwargs: sent.append((name, args)))
    client = login_as(importer.username)
    url = f'/api/v1/import/runs/{run_id}?delete_jobs=true'

    # A password-protected document needs its own, password-checked delete.
    assert client.delete(url).status_code == 409
    with _db() as db:
        assert db.get(Job, protected.id) is not None and db.get(Job, job_id) is not None
        db.delete(db.get(Job, protected.id))
        db.commit()

    # The released document is withdrawn from Knowledge on the way out.
    response = client.delete(url)
    assert response.status_code == 200, response.text
    assert response.json() == {'id': run_id, 'deleted_jobs': 1}
    with _db() as db:
        assert db.get(Job, job_id) is None
        assert db.get(KnowledgeWithdrawal, job_id) is not None
    assert sent == [('deliver_knowledge_withdrawal', [job_id])]
