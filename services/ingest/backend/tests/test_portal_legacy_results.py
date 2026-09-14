"""Older file-backed job results must support the same portal operations."""

import uuid

import pytest

from app.models.models import DocumentRelease, Job
from tests.conftest import create_test_user, login_as
from tests.test_portal import _collection, _configure, _db, _job


@pytest.mark.parametrize('path_field', ['result_path', 'editor'])
def test_file_backed_result_can_be_listed_exported_and_released(tmp_path, monkeypatch, path_field):
    _configure(monkeypatch)
    monkeypatch.setattr('app.api.portal.publication_tasks.deliver_release.delay', lambda *_: None)
    suffix = uuid.uuid4().hex[:8]
    user = create_test_user(username=f'file-{suffix}', email=f'file-{suffix}@example.com')
    collection = _collection(user.id)
    job = _job(user.id, collection)
    result = tmp_path / 'legacy.md'
    result.write_text(job.result_markdown, encoding='utf-8')
    with _db() as db:
        stored = db.get(Job, job.id)
        stored.result_markdown = None
        if path_field == 'result_path':
            stored.result_path = str(result)
        else:
            stored.processing_info = {**stored.processing_info, 'editor': {'latest_result_path': str(result)}}
        db.commit()

    authed = login_as(user.username)
    listing = authed.get('/api/v1/portal/documents', params={'collection_id': collection.id})
    assert listing.status_code == 200, listing.text
    assert listing.json()['items'][0]['can_release'] is True
    preview = authed.get(f'/api/v1/portal/documents/{job.id}')
    assert preview.status_code == 200, preview.text
    assert preview.json()['can_release'] is True
    download = authed.get(f'/api/v1/portal/documents/{job.id}/markdown')
    assert download.status_code == 200, download.text
    assert '# Guide' in download.text
    released = authed.post(f'/api/v1/portal/collections/{collection.id}/release-all', json={})
    assert released.status_code == 202, released.text
    with _db() as db:
        release = db.query(DocumentRelease).filter_by(job_id=job.id).one()
        assert release.payload['engine'] == 'test'
        assert '# Guide' in release.markdown_snapshot
        assert db.get(Job, job.id).result_markdown is None
