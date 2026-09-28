"""Portal editor: change a document's Markdown and release the change at once."""

import hashlib
import uuid

import yaml
from sqlalchemy import select

from app.models.models import (
    CollectionGrant,
    CollectionRole,
    DocumentRelease,
    ImportPageState,
    ImportRunStatus,
    Job,
    JobArtifact,
    JobMarkdownVersion,
)
from app.workers import publication_tasks
from tests.conftest import create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _import_page_state, _import_run, _import_source, _job


def _user(prefix: str):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com')


def _frontmatter(snapshot: str) -> dict:
    return yaml.safe_load(snapshot[4:snapshot.index('\n---\n', 4)])


def _edit(client, job_id: str, markdown: str, sha: str, **extra):
    return client.post(f'/api/v1/portal/documents/{job_id}/edit', json={'markdown': markdown, 'markdown_sha256': sha, **extra})


def test_unreleased_document_is_edited_in_place_and_released(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    owner = _user('edit-owner')
    collection = _collection(owner.id)
    job = _job(owner.id, collection)
    client = login_as(owner.username)
    preview = client.get(f'/api/v1/portal/documents/{job.id}').json()
    assert preview['can_edit'] is True

    edited = preview['markdown'].replace('# Guide', '# Guide\n\nKorrigierter Absatz.')
    response = _edit(client, job.id, edited, preview['markdown_sha256'])
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body['document_id'], body['document_version']) == (job.id, 1)

    with _db() as db:
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id))
        assert 'Korrigierter Absatz.' in release.markdown_snapshot
        frontmatter = _frontmatter(release.markdown_snapshot)
        assert frontmatter['source_kind'] == 'upload'
        assert frontmatter['uploaded_by'] == owner.username
        assert frontmatter['uploaded_at']
        assert 'source_url' not in frontmatter
        assert db.scalar(select(JobMarkdownVersion.version).where(JobMarkdownVersion.job_id == job.id)) == 1

    source = client.get(f'/api/v1/portal/documents/{job.id}').json()['source']
    assert source['edited_by'] == owner.username and source['edited_at']


def test_released_document_becomes_a_new_version_carrying_its_source(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    owner = _user('edit-wiki')
    collection = _collection(owner.id)
    run = _import_run(ImportRunStatus.FINISHED)
    job = _job(owner.id, collection, import_run_id=run.id)
    page_url = 'https://portal.example.atlassian.net/wiki/spaces/X/pages/42/Reisekosten'
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.processing_info = {
            **stored.processing_info,
            'settings': {**stored.processing_info['settings'], 'import': {'source_url': page_url}},
        }
        stored.result_markdown = stored.result_markdown + '\n![Plan](artifacts/plan.png)\n'
        db.add(JobArtifact(
            job_id=job.id, kind='image', filename='plan.png', content_type='image/png', content=b'png',
            size_bytes=3, sha256=hashlib.sha256(b'png').hexdigest(),
        ))
        db.commit()
    page = _import_page_state(_import_source(owner.id).id, job.id, url=page_url)
    client = login_as(owner.username)
    preview = client.get(f'/api/v1/portal/documents/{job.id}').json()
    first = client.post(f'/api/v1/portal/documents/{job.id}/release', json={'markdown_sha256': preview['markdown_sha256']})
    assert first.status_code == 202, first.text

    released = client.get(f'/api/v1/portal/documents/{job.id}').json()
    assert released['can_edit'] is True
    # The released snapshot links images absolutely; the edit gets them back relative.
    assert '/artifacts/plan.png' in released['markdown'] and '](artifacts/plan.png)' not in released['markdown']
    edited = released['markdown'].replace('# Guide', '# Reisekosten')
    response = _edit(client, job.id, edited, released['markdown_sha256'])
    assert response.status_code == 202, response.text
    new_id = response.json()['document_id']
    assert new_id != job.id and response.json()['document_version'] == 2

    with _db() as db:
        new = db.get(Job, new_id)
        assert (new.previous_job_id, new.import_run_id, new.owner_id) == (job.id, run.id, owner.id)
        assert new.content_sha256 == db.get(Job, job.id).content_sha256
        assert [artifact.filename for artifact in new.artifacts] == ['plan.png']
        assert '](artifacts/plan.png)' in new.result_markdown
        # A later sync chains onto the edit instead of forking from the original.
        assert db.get(ImportPageState, page.id).job_id == new_id
        release = db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == new_id))
        frontmatter = _frontmatter(release.markdown_snapshot)
        assert frontmatter['source_kind'] == 'confluence'
        assert frontmatter['source_url'] == page_url
        assert frontmatter['previous_job_id'] == job.id
        assert frontmatter['uploaded_at'] == _frontmatter(db.scalar(
            select(DocumentRelease.markdown_snapshot).where(DocumentRelease.job_id == job.id)
        ))['uploaded_at']

    # The old version is no longer editable: that would fork the chain.
    old = client.get(f'/api/v1/portal/documents/{job.id}').json()
    assert old['can_edit'] is False
    assert _edit(client, job.id, edited, old['markdown_sha256']).status_code == 409


def test_edit_rejects_stale_preview_invalid_markdown_and_readers(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    owner = _user('edit-guard')
    reader = _user('edit-reader')
    collection = _collection(owner.id)
    with _db() as db:
        db.add(CollectionGrant(collection_id=collection.id, user_id=reader.id, role=CollectionRole.READER))
        db.commit()
    job = _job(owner.id, collection)
    client = login_as(owner.username)
    preview = client.get(f'/api/v1/portal/documents/{job.id}').json()

    assert _edit(client, job.id, preview['markdown'], '0' * 64).status_code == 409
    assert _edit(client, job.id, '# ohne Frontmatter', preview['markdown_sha256']).status_code == 422
    # Readers do not even see unreleased processing results (member filter).
    reader_client = login_as(reader.username)
    assert _edit(reader_client, job.id, preview['markdown'], preview['markdown_sha256']).status_code == 404

    with _db() as db:
        assert db.scalar(select(DocumentRelease).where(DocumentRelease.job_id == job.id)) is None
        assert db.scalar(select(JobMarkdownVersion).where(JobMarkdownVersion.job_id == job.id)) is None


def test_refused_release_stores_nothing(monkeypatch):
    """Save and release share one transaction."""
    _configure(monkeypatch)
    monkeypatch.setattr(publication_tasks.deliver_release, 'delay', lambda *args: None)
    owner = _user('edit-atomic')
    collection = _collection(owner.id)
    job = _job(owner.id, collection)
    monkeypatch.setattr(
        'app.services.markdown_edit.evaluate_document_quality',
        lambda content, field_validation=None: {'grade': 'C', 'recommendation': 'block'},
    )
    client = login_as(owner.username)
    preview = client.get(f'/api/v1/portal/documents/{job.id}').json()
    edited = preview['markdown'] + '\nMehr.\n'

    refused = _edit(client, job.id, edited, preview['markdown_sha256'])
    assert refused.status_code == 409  # grade C needs an explicit confirmation
    with _db() as db:
        assert db.scalar(select(JobMarkdownVersion).where(JobMarkdownVersion.job_id == job.id)) is None
        assert 'Mehr.' not in db.get(Job, job.id).result_markdown

    confirmed = _edit(client, job.id, edited, preview['markdown_sha256'], accept_quality_warning=True)
    assert confirmed.status_code == 202, confirmed.text
