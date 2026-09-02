import hashlib
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.api.portal_management import _build_admin_collections_query
from app.models.models import (
    BenchmarkRun,
    Collection,
    DocumentRelease,
    ImportRun,
    ImportRunStatus,
    Job,
    JobStatus,
    UserRole,
)
from tests.conftest import TestingSessionLocal, create_test_user, login_as


@pytest.fixture
def admin_collection_statement():
    """Reusable statement for a later live PostgreSQL session fixture."""
    return _build_admin_collections_query()


def _user(prefix: str, *, role: UserRole = UserRole.USER, team_id: str | None = None):
    token = uuid.uuid4().hex[:10]
    return create_test_user(
        username=f'{prefix}-{token}',
        email=f'{prefix}-{token}@example.com',
        role=role,
        team_id=team_id,
    )


def _collection(owner_id: str | None, *, name: str, description: str | None = None) -> Collection:
    db = TestingSessionLocal()
    try:
        collection = Collection(
            owner_id=owner_id,
            slug=f'{name.lower()}-{uuid.uuid4().hex[:10]}',
            name=name,
            description=description,
            read_teams=['support'],
        )
        db.add(collection)
        db.commit()
        db.refresh(collection)
        db.expunge(collection)
        return collection
    finally:
        db.close()


def _job(
    owner_id: str | None,
    *,
    filename: str,
    collection_id: str | None = None,
    status: JobStatus = JobStatus.FINISHED,
    recommendation: str | None = 'allow',
    grade: str | None = 'A',
    import_run_id: str | None = None,
    benchmark_run_id: str | None = None,
    markdown: str | None = '---\ntitle: test\n---\n\n# Test\n',
) -> Job:
    db = TestingSessionLocal()
    try:
        settings = {'collection_id': collection_id} if collection_id is not None else {}
        job = Job(
            original_filename=filename,
            upload_path=f'/tmp/{filename}',
            result_markdown=markdown,
            status=status,
            owner_id=owner_id,
            import_run_id=import_run_id,
            benchmark_run_id=benchmark_run_id,
            processing_info={
                'settings': settings,
                'execution': {'quality_gate': {'grade': grade, 'recommendation': recommendation}},
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job
    finally:
        db.close()


def _import_run(owner_id: str, status: ImportRunStatus) -> ImportRun:
    db = TestingSessionLocal()
    try:
        run = ImportRun(
            owner_id=owner_id,
            kind='confluence',
            scope_type='page',
            scope_value=f'portal-management-{uuid.uuid4().hex}',
            status=status,
            options={},
            state={},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run
    finally:
        db.close()


def _release(job_id: str, owner_id: str, *, status: str = 'failed') -> DocumentRelease:
    db = TestingSessionLocal()
    try:
        release = DocumentRelease(
            job_id=job_id,
            owner_id=owner_id,
            markdown_snapshot='# released',
            markdown_sha256=hashlib.sha256(b'# released').hexdigest(),
            payload={'event': 'document.released'},
            status=status,
        )
        db.add(release)
        db.commit()
        db.refresh(release)
        db.expunge(release)
        return release
    finally:
        db.close()


def test_admin_collection_inventory_includes_empty_rows_and_sql_counts():
    owner = _user('portal-management-owner')
    admin = _user('portal-management-admin', role=UserRole.ADMIN)
    marker = uuid.uuid4().hex[:10]
    collection = _collection(owner.id, name=f'Management {marker}', description=f'Indexed {marker}')
    empty = _collection(None, name=f'Empty {marker}')

    _job(owner.id, filename=f'{marker}-pending.pdf', collection_id=collection.id, status=JobStatus.PENDING)
    _job(owner.id, filename=f'{marker}-running.pdf', collection_id=collection.id, status=JobStatus.RUNNING)
    _job(owner.id, filename=f'{marker}-review.pdf', collection_id=collection.id)
    _job(
        owner.id,
        filename=f'{marker}-blocked.pdf',
        collection_id=collection.id,
        recommendation='block',
    )
    running_import = _import_run(owner.id, ImportRunStatus.RUNNING)
    _job(owner.id, filename=f'{marker}-import.pdf', collection_id=collection.id, import_run_id=running_import.id)
    _job(owner.id, filename=f'{marker}-failed.pdf', collection_id=collection.id, status=JobStatus.FAILED)
    released_job = _job(owner.id, filename=f'{marker}-released.pdf', collection_id=collection.id)
    _release(released_job.id, owner.id, status='failed')

    benchmark_db = TestingSessionLocal()
    try:
        benchmark = BenchmarkRun(
            owner_id=owner.id,
            original_filename=f'{marker}-benchmark.pdf',
            content_sha256=hashlib.sha256(marker.encode()).hexdigest(),
        )
        benchmark_db.add(benchmark)
        benchmark_db.commit()
        benchmark_db.refresh(benchmark)
        benchmark_id = benchmark.id
    finally:
        benchmark_db.close()
    _job(owner.id, filename=f'{marker}-benchmark.pdf', collection_id=collection.id, benchmark_run_id=benchmark_id)

    assert login_as(owner.username).get('/api/v1/portal/admin/collections').status_code == 403
    response = login_as(admin.username).get('/api/v1/portal/admin/collections', params={'q': marker})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 2
    by_name = {item['name']: item for item in body['items']}
    assert by_name[f'Empty {marker}']['document_count'] == 0
    assert by_name[f'Empty {marker}']['owner'] is None
    item = by_name[f'Management {marker}']
    assert item['owner'] == {'id': owner.id, 'username': owner.username}
    assert item['document_count'] == 7
    assert item['pending_count'] == 1
    assert item['running_count'] == 1
    assert item['review_count'] == 1
    assert item['failed_count'] == 1
    assert item['released_count'] == 1


def test_admin_collection_statement_is_standalone_and_postgres_ready(admin_collection_statement):
    """The statement is executable directly by a future live-PG fixture.

    The endpoint test above executes it on SQLite; this additionally checks
    the PostgreSQL rendering and guards that JSON read_teams is selected, but
    never appears in a GROUP BY clause.
    """
    statement = admin_collection_statement
    db = TestingSessionLocal()
    try:
        db.execute(statement).all()
    finally:
        db.close()

    compiled = str(statement.compile(dialect=postgresql.dialect()))
    group_by_sql = compiled.lower().split('group by', 1)[-1] if 'group by' in compiled.lower() else ''
    assert 'read_teams' not in group_by_sql
    assert 'job_collection_aggregates' in compiled


def test_activity_applies_visibility_before_counts_and_status_pagination():
    user = _user('portal-activity-user')
    outsider = _user('portal-activity-outsider')
    admin = _user('portal-activity-admin', role=UserRole.ADMIN)
    marker = uuid.uuid4().hex[:10]
    collection = _collection(user.id, name=f'Activity {marker}')
    pending = _job(user.id, filename=f'{marker}-pending.pdf', collection_id=collection.id, status=JobStatus.PENDING)
    running = _job(user.id, filename=f'{marker}-running.pdf', collection_id=collection.id, status=JobStatus.RUNNING)
    finished = _job(user.id, filename=f'{marker}-finished.pdf', collection_id=collection.id)
    failed = _job(user.id, filename=f'{marker}-failed.pdf', collection_id=collection.id, status=JobStatus.FAILED)
    _job(outsider.id, filename=f'{marker}-outsider.pdf', collection_id=collection.id)
    legacy = _job(None, filename=f'{marker}-legacy.pdf')

    response = login_as(user.username).get('/api/v1/portal/activity', params={'q': marker, 'status': 'FINISHED', 'limit': 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == finished.id
    assert body['items'][0]['collection_id'] == collection.id
    assert body['items'][0]['collection_name'] == collection.name
    assert body['items'][0]['import_run_id'] is None
    assert body['items'][0]['release_status'] is None
    assert body['items'][0]['quality_grade'] == 'A'
    assert body['items'][0]['quality_recommendation'] == 'allow'
    assert body['counts'] == {'pending': 1, 'running': 1, 'finished': 1, 'failed': 1}

    page = login_as(user.username).get('/api/v1/portal/activity', params={'q': marker, 'limit': 2, 'offset': 1})
    assert page.status_code == 200, page.text
    assert page.json()['total'] == 4
    assert len(page.json()['items']) == 2
    assert {item['id'] for item in page.json()['items']} == {finished.id, running.id}
    assert pending.id not in {item['id'] for item in page.json()['items']}
    assert failed.id not in {item['id'] for item in page.json()['items']}

    admin_body = login_as(admin.username).get('/api/v1/portal/activity', params={'q': marker, 'limit': 20}).json()
    assert admin_body['total'] == 6
    assert legacy.id in {item['id'] for item in admin_body['items']}
    legacy_item = next(item for item in admin_body['items'] if item['id'] == legacy.id)
    assert legacy_item['collection_id'] is None
    assert legacy_item['collection_name'] is None
    assert {item['id'] for item in admin_body['items']} >= {pending.id, finished.id, failed.id}


def test_activity_serializes_import_enum_and_release_status_and_nulls():
    user = _user('portal-activity-contract')
    marker = uuid.uuid4().hex[:10]
    run = _import_run(user.id, ImportRunStatus.FINISHED)
    imported = _job(
        user.id,
        filename=f'{marker}-imported.pdf',
        import_run_id=run.id,
    )
    _release(imported.id, user.id, status='sent')

    body = login_as(user.username).get('/api/v1/portal/activity', params={'q': marker}).json()
    assert body['total'] == 1
    assert body['items'] == [
        {
            'id': imported.id,
            'original_filename': f'{marker}-imported.pdf',
            'status': 'FINISHED',
            'created_at': body['items'][0]['created_at'],
            'updated_at': body['items'][0]['updated_at'],
            'collection_id': None,
            'collection_name': None,
            'import_run_id': run.id,
            'import_status': 'finished',
            'release_status': 'sent',
            'quality_grade': 'A',
            'quality_recommendation': 'allow',
        }
    ]
