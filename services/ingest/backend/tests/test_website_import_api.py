"""Website / intranet connections and the shared source catalog at the API:
kind-bound auth types, admin-only private-target release, the connection
test, website runs (scope 'url' on the connection's host), sync re-runs and
catalog permissions -- listing an entry is open to everyone, managing it is
admin-only, and importing from it still needs a controllable space."""

import uuid

import pytest
from sqlalchemy import select

from app.api import import_routes
from app.models.models import ImportRun, ImportSource, ImportSync, SourceCatalogEntry, UserRole
from app.services import security
from app.services.security import rate_limiter
from tests.conftest import TestingSessionLocal, add_legacy_collection, create_test_user, login_as


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limiter.reset()
    yield


def _db():
    return TestingSessionLocal()


def _user(prefix: str, **kwargs):
    suffix = uuid.uuid4().hex[:8]
    return create_test_user(username=f'{prefix}-{suffix}', email=f'{prefix}-{suffix}@example.com', **kwargs)


def _collection(owner_id: str):
    with _db() as db:
        collection = add_legacy_collection(db, owner_id=owner_id, slug=f'web-{uuid.uuid4().hex[:8]}', name='Web space')
        db.commit()
        return collection.id


def _website_source(client, *, base_url='https://docs.example.com', auth_type='none', credential='', tested=True, **extra):
    resp = client.post('/api/v1/import/sources', json={
        'name': 'Docs', 'kind': 'website', 'base_url': base_url, 'auth_type': auth_type, 'credential': credential, **extra,
    })
    assert resp.status_code == 201, resp.text
    source = resp.json()
    if tested:
        with _db() as db:
            row = db.get(ImportSource, source['id'])
            row.server_kind = 'website'
            db.commit()
    return source


def _catalog_entry(**overrides) -> str:
    with _db() as db:
        entry = SourceCatalogEntry(
            title='VwVfG', description='Verwaltungsverfahrensgesetz', category='legal', publisher='BMJ',
            start_url='https://www.gesetze-im-internet.de/vwvfg/', crawl_scope='prefix', legal_source=True,
            interval_seconds=604800, max_pages=100, **overrides,
        )
        db.add(entry)
        db.commit()
        return entry.id


# --- connections -----------------------------------------------------------------

def test_create_website_source_without_credential_and_kind_bound_auth():
    user = _user('web-src')
    client = login_as(user.username)
    source = _website_source(client, tested=False)
    assert source['kind'] == 'website'
    assert source['auth_type'] == 'none'
    assert source['has_credential'] is False
    assert source['allow_private_target'] is False
    assert 'credential' not in source

    # A Confluence scheme on a website (and vice versa) is refused with a
    # readable message; form logins / SSO never exist as a value at all.
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'website', 'base_url': 'https://h.example.com', 'auth_type': 'pat_bearer', 'credential': 't'})
    assert resp.status_code == 422 and 'not supported for website' in resp.json()['detail']
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'confluence', 'base_url': 'https://h.example.com', 'auth_type': 'bearer', 'credential': 't'})
    assert resp.status_code == 422 and 'not supported for confluence' in resp.json()['detail']
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'website', 'base_url': 'https://h.example.com', 'auth_type': 'form_login', 'credential': 't'})
    assert resp.status_code == 422
    # A credentialed scheme needs a credential; http_basic needs a username.
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'website', 'base_url': 'https://h.example.com', 'auth_type': 'bearer'})
    assert resp.status_code == 422
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'website', 'base_url': 'https://h.example.com', 'auth_type': 'http_basic', 'credential': 'pw'})
    assert resp.status_code == 422
    resp = client.post('/api/v1/import/sources', json={'name': 'x', 'kind': 'website', 'base_url': 'https://h.example.com', 'auth_type': 'http_basic', 'auth_username': 'bob', 'credential': 'pw'})
    assert resp.status_code == 201 and resp.json()['has_credential'] is True and resp.json()['auth_username'] == 'bob'
    # Existing Confluence creation keeps working with the implicit kind.
    resp = client.post('/api/v1/import/sources', json={'name': 'wiki', 'base_url': 'https://wiki.example.com', 'auth_type': 'pat_bearer', 'credential': 'pat'})
    assert resp.status_code == 201 and resp.json()['kind'] == 'confluence'


def test_private_target_release_is_admin_only():
    user = _user('web-priv')
    admin = _user('web-priv-admin', role=UserRole.ADMIN)
    client = login_as(user.username)
    resp = client.post('/api/v1/import/sources', json={'name': 'Intra', 'kind': 'website', 'base_url': 'http://intranet.local', 'auth_type': 'none', 'allow_private_target': True})
    assert resp.status_code == 403
    source = _website_source(client, base_url='http://intranet.local', tested=False)
    resp = client.patch(f"/api/v1/import/sources/{source['id']}", json={'allow_private_target': True})
    assert resp.status_code == 403
    # Admins release their own connections; the flag never applies to Confluence.
    admin_client = login_as(admin.username)
    resp = admin_client.post('/api/v1/import/sources', json={'name': 'Intra', 'kind': 'website', 'base_url': 'http://intranet.local', 'auth_type': 'none', 'allow_private_target': True})
    assert resp.status_code == 201 and resp.json()['allow_private_target'] is True
    resp = admin_client.post('/api/v1/import/sources', json={'name': 'wiki', 'base_url': 'https://wiki.example.com', 'auth_type': 'pat_bearer', 'credential': 'pat', 'allow_private_target': True})
    assert resp.status_code == 422
    with _db() as db:
        row = db.scalar(select(ImportSource).where(ImportSource.owner_id == admin.id, ImportSource.kind == 'website'))
        assert import_routes._private_hosts_for(row) == frozenset({'intranet.local'})
        released_id = row.id
    # The release is bound to the host an admin looked at: an owner who is
    # not an admin cannot retarget it by changing the base URL.
    with _db() as db:
        row = db.get(ImportSource, released_id)
        row.owner_id = user.id
        db.commit()
    resp = client.patch(f'/api/v1/import/sources/{released_id}', json={'base_url': 'http://10.0.0.5:9200'})
    assert resp.status_code == 200 and resp.json()['allow_private_target'] is False
    with _db() as db:
        assert import_routes._private_hosts_for(db.get(ImportSource, released_id)) == frozenset()


def test_website_source_test_sets_server_kind_and_hides_credential(monkeypatch):
    user = _user('web-test')
    client = login_as(user.username)
    source = _website_source(client, auth_type='bearer', credential='secret-tok', tested=False)
    seen: dict = {}

    def fake_probe(base_url, **kwargs):
        seen.update(kwargs, base_url=base_url)
        return True, 'Reachable (HTTP 200)'

    monkeypatch.setattr(import_routes.website, 'probe', fake_probe)
    resp = client.post(f"/api/v1/import/sources/{source['id']}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {'ok': True, 'detail': 'Reachable (HTTP 200)', 'server_kind': 'website'}
    assert seen['credential'] == 'secret-tok' and seen['base_url'] == 'https://docs.example.com'
    with _db() as db:
        row = db.get(ImportSource, source['id'])
        assert row.server_kind == 'website' and row.last_validated_at is not None
        assert security.decrypt_import_credential(row.credential_encrypted) == 'secret-tok'
    listed = client.get('/api/v1/import/sources').json()['items'][0]
    assert listed['server_kind'] == 'website' and 'secret-tok' not in str(listed)


# --- runs / syncs ----------------------------------------------------------------

def test_create_website_run_requires_url_scope_on_connection_host(monkeypatch):
    user = _user('web-run')
    client = login_as(user.username)
    source = _website_source(client)
    collection_id = _collection(user.id)
    entry_id = _catalog_entry()
    sent: list = []
    monkeypatch.setattr(import_routes.celery_app, 'send_task', lambda name, args=None, **kw: sent.append((name, args)))

    resp = client.post('/api/v1/import/runs', json={'source_id': source['id'], 'scope': {'type': 'page', 'value': 'https://docs.example.com/a'}})
    assert resp.status_code == 422
    resp = client.post('/api/v1/import/runs', json={'source_id': source['id'], 'scope': {'type': 'url', 'value': 'https://other.example.com/a'}})
    assert resp.status_code == 422 and 'docs.example.com' in resp.json()['detail']
    resp = client.post('/api/v1/import/runs', json={'source_id': source['id'], 'scope': {'type': 'url', 'value': 'ftp://docs.example.com/a'}})
    assert resp.status_code == 422
    # Another port or scheme is another origin (credential / release binding).
    resp = client.post('/api/v1/import/runs', json={'source_id': source['id'], 'scope': {'type': 'url', 'value': 'https://docs.example.com:8443/a'}})
    assert resp.status_code == 422
    resp = client.post('/api/v1/import/runs', json={'source_id': source['id'], 'scope': {'type': 'url', 'value': 'http://docs.example.com/a'}})
    assert resp.status_code == 422

    resp = client.post('/api/v1/import/runs', json={
        'source_id': source['id'],
        'scope': {'type': 'url', 'value': 'https://Docs.example.com/handbuch/index.html?utm_source=mail#top'},
        'options': {'collection_id': collection_id, 'crawl_scope': 'prefix', 'legal_source': True, 'catalog_entry_id': entry_id, 'include_attachments': True, 'ocr_attachments': True},
        'sync': {'enabled': True, 'interval_seconds': 86400},
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body['kind'] == 'website' and body['scope_type'] == 'url'
    assert body['scope_value'] == 'https://docs.example.com/handbuch/index.html'
    assert sent == [('import_confluence', [body['id'], 0])]
    sent.clear()
    with _db() as db:
        run = db.get(ImportRun, body['id'])
        assert run.state['frontier'] == [['https://docs.example.com/handbuch/index.html', 0]]
        assert run.options['crawl_scope'] == 'prefix' and run.options['legal_source'] is True
        assert run.options['catalog_entry_id'] == entry_id and run.options['collection_id'] == collection_id
        sync = db.get(ImportSync, run.sync_id)
        assert sync.scope_type == 'url' and sync.enabled and sync.interval_seconds == 86400
        assert sync.options['crawl_scope'] == 'prefix' and sync.options['catalog_entry_id'] == entry_id

    syncs = client.get('/api/v1/import/syncs').json()['items']
    assert len(syncs) == 1 and syncs[0]['source_kind'] == 'website' and syncs[0]['options']['crawl_scope'] == 'prefix'
    # A deleted catalog entry must not block editing the sync that came from it.
    with _db() as db:
        db.delete(db.get(SourceCatalogEntry, entry_id))
        db.commit()
    resp = client.patch(f"/api/v1/import/syncs/{syncs[0]['id']}", json={'options': syncs[0]['options'], 'interval_seconds': 3600})
    assert resp.status_code == 200, resp.text
    assert resp.json()['options']['catalog_entry_id'] is None and resp.json()['interval_seconds'] == 3600

    # A Confluence connection refuses the url scope.
    resp = client.post('/api/v1/import/sources', json={'name': 'wiki', 'base_url': 'https://wiki.example.com', 'auth_type': 'pat_bearer', 'credential': 'pat'})
    wiki = resp.json()
    with _db() as db:
        db.get(ImportSource, wiki['id']).server_kind = 'datacenter'
        db.commit()
    resp = client.post('/api/v1/import/runs', json={'source_id': wiki['id'], 'scope': {'type': 'url', 'value': 'https://wiki.example.com/x'}})
    assert resp.status_code == 422


def test_website_sync_rerun_and_space_control(monkeypatch):
    owner = _user('web-sync')
    outsider = _user('web-sync-out')
    client = login_as(owner.username)
    source = _website_source(client)
    collection_id = _collection(owner.id)
    sent: list = []
    monkeypatch.setattr(import_routes.celery_app, 'send_task', lambda name, args=None, **kw: sent.append((name, args)))
    monkeypatch.setattr('app.workers.refresh_tasks.celery_app.send_task', lambda name, args=None, **kw: sent.append((name, args)))
    created = client.post('/api/v1/import/runs', json={
        'source_id': source['id'], 'scope': {'type': 'url', 'value': 'https://docs.example.com/a/'},
        'options': {'collection_id': collection_id},
    }).json()
    with _db() as db:
        run = db.get(ImportRun, created['id'])
        run.status = 'finished'
        db.commit()
        sync_id = run.sync_id
    # Syncs are owner-private: an outsider sees neither the sync nor can run it.
    assert login_as(outsider.username).post(f'/api/v1/import/syncs/{sync_id}/run').status_code == 404
    assert login_as(outsider.username).get('/api/v1/import/syncs').json()['items'] == []
    resp = client.post(f'/api/v1/import/syncs/{sync_id}/run')
    assert resp.status_code == 202, resp.text
    assert resp.json()['kind'] == 'website' and resp.json()['scope_type'] == 'url'
    with _db() as db:
        rerun = db.get(ImportRun, resp.json()['id'])
        assert rerun.options['is_refresh'] is True
        assert rerun.state['frontier'] == [['https://docs.example.com/a/', 0]]
    # "Sync now" from the finished run works for website runs too (the
    # Confluence wizard edit stays Confluence-only).
    listed = client.get('/api/v1/import/runs').json()['items']
    first = next(item for item in listed if item['id'] == created['id'])
    assert first['can_sync'] is True and first['can_edit'] is False


# --- catalog -----------------------------------------------------------------------

def test_catalog_listing_open_management_admin_only():
    user = _user('cat-user')
    admin = _user('cat-admin', role=UserRole.ADMIN)
    entry_id = _catalog_entry()
    client = login_as(user.username)
    resp = client.get('/api/v1/source-catalog')
    assert resp.status_code == 200
    body = resp.json()
    assert body['can_manage'] is False
    entry = next(item for item in body['items'] if item['id'] == entry_id)
    assert entry['host'] == 'www.gesetze-im-internet.de' and entry['category'] == 'legal' and entry['legal_source'] is True
    assert entry_id in [item['id'] for item in client.get('/api/v1/source-catalog', params={'q': 'verwaltungs'}).json()['items']]
    assert entry_id not in [item['id'] for item in client.get('/api/v1/source-catalog', params={'q': 'zzz-nomatch'}).json()['items']]
    assert entry_id not in [item['id'] for item in client.get('/api/v1/source-catalog', params={'category': 'internal'}).json()['items']]
    assert client.get(f'/api/v1/source-catalog/{entry_id}').status_code == 200

    payload = {'title': 'Satzungen', 'category': 'internal', 'start_url': 'https://stadt.example.de/satzungen/', 'crawl_scope': 'prefix'}
    assert client.post('/api/v1/source-catalog', json=payload).status_code == 403
    assert client.patch(f'/api/v1/source-catalog/{entry_id}', json={'title': 'x'}).status_code == 403
    assert client.delete(f'/api/v1/source-catalog/{entry_id}').status_code == 403

    admin_client = login_as(admin.username)
    assert admin_client.get('/api/v1/source-catalog').json()['can_manage'] is True
    resp = admin_client.post('/api/v1/source-catalog', json={**payload, 'start_url': 'ftp://x'})
    assert resp.status_code == 422
    resp = admin_client.post('/api/v1/source-catalog', json=payload)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created['host'] == 'stadt.example.de' and created['kind'] == 'website'
    resp = admin_client.patch(f"/api/v1/source-catalog/{created['id']}", json={'title': 'Satzungen der Stadt', 'legal_source': True, 'max_pages': 40})
    assert resp.status_code == 200 and resp.json()['title'] == 'Satzungen der Stadt' and resp.json()['legal_source'] is True
    assert admin_client.patch(f"/api/v1/source-catalog/{created['id']}", json={'title': '  '}).status_code == 422
    assert admin_client.delete(f"/api/v1/source-catalog/{created['id']}").status_code == 200
    assert admin_client.get(f"/api/v1/source-catalog/{created['id']}").status_code == 404
    with _db() as db:
        assert db.get(SourceCatalogEntry, created['id']) is None


def test_catalog_entry_grants_no_import_into_foreign_space(monkeypatch):
    user = _user('cat-noright')
    other = _user('cat-other')
    entry_id = _catalog_entry()
    client = login_as(user.username)
    source = _website_source(client, base_url='https://www.gesetze-im-internet.de')
    foreign_collection = _collection(other.id)
    monkeypatch.setattr(import_routes.celery_app, 'send_task', lambda *a, **kw: None)
    resp = client.post('/api/v1/import/runs', json={
        'source_id': source['id'], 'scope': {'type': 'url', 'value': 'https://www.gesetze-im-internet.de/vwvfg/'},
        'options': {'collection_id': foreign_collection, 'catalog_entry_id': entry_id},
    })
    assert resp.status_code in (403, 404)
    with _db() as db:
        assert db.scalar(select(ImportRun).where(ImportRun.owner_id == user.id)) is None


def test_update_website_source_auth_switches_need_a_credential_on_file():
    user = _user('web-patch')
    client = login_as(user.username)
    source = _website_source(client, tested=False)
    # none -> bearer without a credential: refused; with one: accepted.
    assert client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'bearer'}).status_code == 422
    resp = client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'bearer', 'credential': 'tok'})
    assert resp.status_code == 200 and resp.json()['auth_type'] == 'bearer' and resp.json()['has_credential'] is True
    # bearer -> http_basic keeps the stored secret but needs a username.
    assert client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'http_basic'}).status_code == 422
    resp = client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'http_basic', 'auth_username': 'bob'})
    assert resp.status_code == 200 and resp.json()['auth_username'] == 'bob'
    # back to none: credential cleared, username dropped; Confluence schemes stay refused.
    resp = client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'none'})
    assert resp.status_code == 200 and resp.json()['has_credential'] is False and resp.json()['auth_username'] == ''
    assert client.patch(f"/api/v1/import/sources/{source['id']}", json={'auth_type': 'pat_bearer', 'credential': 'x'}).status_code == 422
