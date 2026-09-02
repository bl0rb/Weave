"""Collection assignment contract for the first Confluence knowledge portal."""

import uuid
import hashlib
from datetime import datetime, timezone

import yaml
from sqlalchemy import select

from app.api import import_routes
from app.models.models import (
    Collection,
    ImportAuthType,
    ImportRun,
    ImportRunStatus,
    ImportSource,
    Job,
    JobArtifact,
    UserRole,
)
from app.services import security
from app.services.confluence import AttachmentMeta, ConfluenceError, Page, PageContext
from app.services.publications import build_release_payload, canonical_snapshot
from app.workers import import_tasks, refresh_tasks
from app.workers.celery_app import celery_app
from app.workers.import_tasks import import_confluence
from tests.conftest import TestingSessionLocal, client, create_test_user, login_as


BASE_URL = 'https://acme.example.com'
PNG = b'\x89PNG\r\n\x1a\nportal'
PDF = b'%PDF-portal'


def _db():
    return TestingSessionLocal()


def _user(prefix: str, *, role: UserRole = UserRole.USER):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(
        username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', role=role
    )


def _source(owner_id: str, *, refresh_enabled: bool = False) -> ImportSource:
    db = _db()
    try:
        source = ImportSource(
            owner_id=owner_id,
            name='Portal Confluence',
            base_url=BASE_URL,
            server_kind='cloud',
            api_base_path='/wiki/api/v2',
            auth_type=ImportAuthType.CLOUD_BASIC,
            auth_username='portal@example.com',
            credential_encrypted=security.encrypt_import_credential('portal-token'),
            refresh_enabled=refresh_enabled,
            refresh_interval_seconds=900 if refresh_enabled else None,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        db.expunge(source)
        return source
    finally:
        db.close()


def _collection(owner_id: str, *, slug: str = 'portal-docs', name: str = 'Portal Docs') -> Collection:
    db = _db()
    try:
        collection = Collection(owner_id=owner_id, slug=f'{slug}-{uuid.uuid4().hex[:6]}', name=name)
        db.add(collection)
        db.commit()
        db.refresh(collection)
        db.expunge(collection)
        return collection
    finally:
        db.close()


def _frontmatter(markdown: str) -> dict:
    end = markdown.find('\n---\n', 4)
    return yaml.safe_load(markdown[4:end + 1])


class _FakeClient:
    def __init__(self) -> None:
        self.page = Page(
            id='portal-page',
            title='Portal Page',
            version=1,
            html='<h1>Portal</h1><p>Knowledge portal content.</p>',
            url=f'{BASE_URL}/wiki/spaces/PORTAL/pages/portal-page/Portal-Page',
        )
        self.attachment = AttachmentMeta(
            id='portal-attachment',
            filename='guide.pdf',
            media_type='application/pdf',
            size_bytes=len(PDF),
            download_url=f'{BASE_URL}/download/attachments/portal-page/guide.pdf',
            page_id=self.page.id,
            _fetch=lambda: PDF,
        )

    def fetch_page(self, page_id: str) -> Page:
        assert page_id == self.page.id
        return self.page

    def fetch_context(self, page_id: str) -> PageContext:
        return PageContext(space_key='PORTAL', ancestor_titles=[])

    def iter_children(self, page_id: str):
        return iter(())

    def iter_attachments(self, page_id: str):
        return iter((self.attachment,))

    def resolve_space_root(self, space_key: str) -> str:  # pragma: no cover
        raise ConfluenceError('not used')


def _run(owner_id: str, source_id: str, collection: Collection, *, refresh: bool = False) -> str:
    db = _db()
    try:
        run = ImportRun(
            source_id=source_id,
            owner_id=owner_id,
            kind='confluence',
            scope_type='page',
            scope_value='portal-page',
            options={
                'max_pages': 10,
                'max_depth': 0,
                'include_attachments': True,
                'ocr_attachments': True,
                'ocr_profile_id': None,
                'folder': '',
                'subfolder': '',
                'tags': [],
                'email': '',
                'collection_id': collection.id,
                'collection_slug': collection.slug,
                'collection_name': collection.name,
                'is_refresh': refresh,
            },
            state={'frontier': [['portal-page', 0]], 'visited': {}, 'errors': []},
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def test_create_import_run_denies_foreign_and_normalizes_own_collection(monkeypatch):
    owner = _user('portal-owner')
    foreign = _user('portal-foreign')
    source = _source(owner.id)
    own_collection = _collection(owner.id)
    foreign_collection = _collection(foreign.id, slug='foreign', name='Foreign')
    authed = login_as(owner.username)
    monkeypatch.setattr(import_routes.celery_app, 'send_task', lambda *args, **kwargs: None)

    denied = authed.post(
        '/api/v1/import/runs',
        json={
            'source_id': source.id,
            'scope': {'type': 'page', 'value': '123'},
            'options': {'collection_id': foreign_collection.id},
        },
    )
    assert denied.status_code == 404

    created = authed.post(
        '/api/v1/import/runs',
        json={
            'source_id': source.id,
            'scope': {'type': 'page', 'value': '123'},
            'options': {
                'collection_id': own_collection.id,
                'collection_slug': 'forged-slug',
                'collection_name': 'Forged Name',
            },
        },
    )
    assert created.status_code == 201, created.text
    db = _db()
    try:
        run = db.get(ImportRun, created.json()['id'])
        assert run.options['collection_id'] == own_collection.id
        assert run.options['collection_slug'] == own_collection.slug
        assert run.options['collection_name'] == own_collection.name
    finally:
        db.close()


def test_page_and_attachment_jobs_carry_collection_metadata_and_frontmatter(monkeypatch):
    owner = _user('portal-worker')
    source = _source(owner.id)
    collection = _collection(owner.id)
    run_id = _run(owner.id, source.id, collection)
    sent: list[tuple[str, list]] = []
    fake_client = _FakeClient()
    monkeypatch.setattr(import_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(import_tasks, 'create_client', lambda **kwargs: fake_client)
    monkeypatch.setattr(
        celery_app, 'send_task', lambda name, args=None, **kwargs: sent.append((name, list(args or [])))
    )

    import_confluence(run_id, 0)

    db = _db()
    try:
        jobs = db.scalars(select(Job).where(Job.import_run_id == run_id)).all()
        page_job = next(job for job in jobs if job.processing_info['settings']['mode'] == 'import')
        attachment_job = next(
            job for job in jobs if job.processing_info['settings']['mode'] == 'import_attachment'
        )
        for job in jobs:
            settings = job.processing_info['settings']
            assert settings['collection_id'] == collection.id
            assert settings['collection_slug'] == collection.slug
            assert settings['collection_name'] == collection.name
        page_meta = _frontmatter(page_job.result_markdown)
        assert page_meta['collection'] == collection.slug
        assert page_meta['collection_name'] == collection.name
        expected_page_hash = hashlib.sha256(fake_client.page.html.encode('utf-8')).hexdigest()
        assert page_job.content_sha256 == expected_page_hash
        snapshot, snapshot_hash, canonical_meta = canonical_snapshot(db, page_job, collection)
        release_payload = build_release_payload(page_job, 'release-id', snapshot_hash, canonical_meta)
        assert hashlib.sha256(snapshot.encode('utf-8')).hexdigest() == snapshot_hash
        assert release_payload['content_sha256'] == expected_page_hash
        assert release_payload['engine'] == 'confluence-import'
        assert release_payload['frontmatter']['collection'] == collection.slug
        assert release_payload['frontmatter']['collection_name'] == collection.name
        artifact = db.scalars(select(JobArtifact).where(JobArtifact.job_id == page_job.id)).one()
        assert artifact.filename == 'guide.pdf'
        assert artifact.sha256 == hashlib.sha256(PDF).hexdigest()
        assert attachment_job.content_sha256 == hashlib.sha256(PDF).hexdigest()
        assert sent
        assert all(name == 'process_job' for name, _args in sent)
        assert all(args == [attachment_job.id, None, 'import_attachment', '', None] for _name, args in sent)
    finally:
        db.close()


def test_refresh_preserves_collection_and_fails_closed_when_deleted(monkeypatch):
    owner = _user('portal-refresh')
    source = _source(owner.id, refresh_enabled=True)
    collection = _collection(owner.id)
    db = _db()
    try:
        monkeypatch.setattr(refresh_tasks, 'SessionLocal', TestingSessionLocal)
        prior = ImportRun(
            source_id=source.id,
            owner_id=owner.id,
            kind='confluence',
            status=ImportRunStatus.FINISHED,
            scope_type='page',
            scope_value='portal-page',
            options={
                'max_pages': 10,
                'max_depth': 0,
                'include_attachments': True,
                'collection_id': collection.id,
                'collection_slug': collection.slug,
                'collection_name': collection.name,
            },
            state={'frontier': [], 'visited': {}, 'errors': []},
            finished_at=datetime.now(timezone.utc),
        )
        db.add(prior)
        db.commit()
        sent: list[tuple[str, list]] = []
        monkeypatch.setattr(
            refresh_tasks.celery_app,
            'send_task',
            lambda name, args=None, **kwargs: sent.append((name, list(args or []))),
        )
        assert refresh_tasks._start_refresh_run(db, db.get(ImportSource, source.id)) is True
        refresh_run = db.scalar(
            select(ImportRun)
            .where(ImportRun.source_id == source.id)
            .where(ImportRun.id != prior.id)
        )
        assert refresh_run.options['collection_id'] == collection.id
        assert refresh_run.options['collection_slug'] == collection.slug
        assert sent == [('import_confluence', [refresh_run.id, 0])]

        # An assigned collection disappearing must stop dispatch at the
        # scheduler boundary; no unassigned replacement run is allowed.
        refresh_run.status = ImportRunStatus.FINISHED
        refresh_run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.delete(collection)
        db.commit()
        sent.clear()
        db.get(ImportSource, source.id).last_refresh_at = None
        db.commit()
        refresh_tasks._dispatch_due_refreshes()
        assert sent == []
        source_row = db.get(ImportSource, source.id)
        assert 'refusing an unassigned refresh' in source_row.last_refresh_error
    finally:
        db.close()
