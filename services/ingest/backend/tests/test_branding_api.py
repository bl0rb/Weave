"""Administration › Betrieb › Erscheinungsbild (app/api/branding.py): the
public read side (no session), the admin writes (admin only, CSRF origin
check), the asset headers and the per-process cache."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import app
from app.models.models import BrandingAsset, BrandingConfig, UserRole
from app.services import branding as branding_service
from app.services import runtime_config
from app.services.security import rate_limiter
from conftest import BROWSER_HEADERS, TestingSessionLocal, create_test_user, login_as

PUBLIC = '/api/v1/branding'
ADMIN = '/api/v1/auth/admin/branding'
NS = 'xmlns="http://www.w3.org/2000/svg"'
SVG = f'<svg {NS} viewBox="0 0 8 8"><circle cx="4" cy="4" r="3" fill="#1d6a53"/></svg>'.encode()
GIF = b'GIF89a\x01\x00\x01\x00\x00\x00\x00;'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
EMPTY = {'primary_color': None, 'accent_color': None, 'palette': None, 'logo': None, 'busy': None, 'updated_at': None}


@pytest.fixture(autouse=True)
def _clean():
    rate_limiter.reset()
    with TestingSessionLocal() as db:
        db.execute(delete(BrandingAsset))
        db.execute(delete(BrandingConfig))
        db.commit()
    branding_service.invalidate_cache()
    yield
    branding_service.invalidate_cache()


def _login(prefix: str, role: UserRole = UserRole.ADMIN) -> TestClient:
    return login_as(create_test_user(username=prefix, email=f'{prefix}@example.com', role=role).username)


def _anonymous() -> TestClient:
    return TestClient(app)


def test_public_branding_needs_no_session_and_starts_empty():
    resp = _anonymous().get(PUBLIC)
    assert resp.status_code == 200
    assert resp.json() == EMPTY
    assert resp.headers['cache-control'] == 'no-cache'


def test_admin_starts_unmanaged_and_saves_colours_key_by_key():
    admin = _login('brand-admin')
    assert admin.get(ADMIN).json() == {**EMPTY, 'managed': False}

    saved = admin.put(ADMIN, json={'primary_color': '#ABC'})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body['primary_color'], body['accent_color'], body['managed']) == ('#aabbcc', None, True)
    assert body['palette']['light']['accent'] == '#aabbcc'
    assert body['updated_at'] is not None

    # An absent key keeps its value, a present null resets it.
    body = admin.put(ADMIN, json={'accent_color': '#FF8800'}).json()
    assert (body['primary_color'], body['accent_color']) == ('#aabbcc', '#ff8800')
    body = admin.put(ADMIN, json={'primary_color': None}).json()
    assert (body['primary_color'], body['accent_color']) == (None, '#ff8800')
    assert body['palette']['light']['accent'] == '#1d6a53'
    body = admin.put(ADMIN, json={'accent_color': None}).json()
    assert (body['palette'], body['managed']) == (None, True)

    with TestingSessionLocal() as db:
        row = db.get(BrandingConfig, 'default')
        assert row.updated_by_id is not None

    public = _anonymous().get(PUBLIC).json()
    assert public['palette'] is None and 'managed' not in public


def test_invalid_colours_and_unknown_keys_are_refused():
    admin = _login('brand-invalid')
    for value in ('1d6a53', '#12345', 'red', ''):
        resp = admin.put(ADMIN, json={'primary_color': value})
        assert resp.status_code == 422
        assert resp.json()['detail'] == 'Farbe als Hex-Code angeben, z. B. #1d6a53.'
    assert admin.put(ADMIN, json={'logo': 'x'}).status_code == 422
    assert admin.get(ADMIN).json()['managed'] is False


@pytest.mark.parametrize('role', [UserRole.USER, UserRole.BOT_ADMIN])
def test_only_admins_may_read_or_write(role):
    other = _login(f'brand-{role.value}', role)
    assert other.get(ADMIN).status_code == 403
    assert other.put(ADMIN, json={'primary_color': '#000000'}).status_code == 403
    assert other.put(f'{ADMIN}/assets/logo', files={'file': ('a.svg', SVG, 'image/svg+xml')}).status_code == 403
    assert other.delete(f'{ADMIN}/assets/logo').status_code == 403
    # ...while the public side is readable for them like for anyone.
    assert other.get(PUBLIC).status_code == 200


def test_anonymous_admin_access_is_401():
    anonymous = TestClient(app, headers=BROWSER_HEADERS)
    assert anonymous.get(ADMIN).status_code == 401
    assert anonymous.put(ADMIN, json={'primary_color': '#000000'}).status_code == 401
    assert anonymous.put(f'{ADMIN}/assets/logo', files={'file': ('a.svg', SVG, 'image/svg+xml')}).status_code == 401
    assert anonymous.delete(f'{ADMIN}/assets/logo').status_code == 401


def test_writes_from_a_foreign_origin_are_refused():
    admin = _login('brand-csrf')
    evil = {'Origin': 'https://evil.example'}
    assert admin.put(ADMIN, json={'primary_color': '#000000'}, headers=evil).status_code == 403
    assert admin.put(f'{ADMIN}/assets/logo', files={'file': ('a.svg', SVG, 'image/svg+xml')}, headers=evil).status_code == 403
    assert admin.delete(f'{ADMIN}/assets/logo', headers=evil).status_code == 403
    assert admin.get(ADMIN).json()['managed'] is False


def test_svg_logo_upload_and_asset_headers():
    admin = _login('brand-assets')
    # The type comes from the content, not the filename or declared type.
    resp = admin.put(f'{ADMIN}/assets/logo', files={'file': ('logo.png', SVG, 'image/png')})
    assert resp.status_code == 200, resp.text
    sha = hashlib.sha256(SVG).hexdigest()
    assert resp.json()['logo'] == {
        'content_type': 'image/svg+xml', 'sha256': sha, 'size_bytes': len(SVG),
        'url': f'/api/v1/branding/assets/logo?v={sha[:16]}',
    }
    assert resp.json()['managed'] is True
    assert resp.json()['busy'] is None
    with TestingSessionLocal() as db:
        row = db.get(BrandingAsset, 'logo')
        assert (row.filename, row.updated_by_id is not None) == ('logo.png', True)

    anonymous = _anonymous()
    assert anonymous.get(PUBLIC).json()['logo']['sha256'] == sha
    asset = anonymous.get(f'/api/v1/branding/assets/logo?v={sha[:16]}')
    assert asset.status_code == 200
    assert asset.content == SVG
    assert asset.headers['content-type'] == 'image/svg+xml'
    assert asset.headers['x-content-type-options'] == 'nosniff'
    assert asset.headers['content-security-policy'] == CSP
    assert asset.headers['content-disposition'] == 'inline; filename="branding-logo.svg"'
    assert asset.headers['cache-control'] == 'public, max-age=300'
    assert asset.headers['etag'] == f'"{sha}"'
    assert 'set-cookie' not in asset.headers

    for etag in (f'"{sha}"', f'W/"{sha}"', f'"other", "{sha}"', '*'):
        unchanged = anonymous.get('/api/v1/branding/assets/logo', headers={'If-None-Match': etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b''
        assert unchanged.headers['etag'] == f'"{sha}"'
    assert anonymous.get('/api/v1/branding/assets/logo', headers={'If-None-Match': '"other"'}).status_code == 200


def test_gif_and_png_assets_keep_their_type():
    admin = _login('brand-raster')
    assert admin.put(f'{ADMIN}/assets/busy', files={'file': ('busy.svg', GIF, 'image/svg+xml')}).json()['busy']['content_type'] == 'image/gif'
    assert admin.put(f'{ADMIN}/assets/logo', files={'file': ('logo', PNG, 'application/octet-stream')}).json()['logo']['content_type'] == 'image/png'
    busy = _anonymous().get('/api/v1/branding/assets/busy')
    assert (busy.headers['content-type'], busy.headers['content-disposition']) == ('image/gif', 'inline; filename="branding-busy.gif"')
    assert busy.content == GIF
    logo = _anonymous().get('/api/v1/branding/assets/logo')
    assert (logo.headers['content-type'], logo.headers['content-disposition']) == ('image/png', 'inline; filename="branding-logo.png"')


def test_refused_uploads_store_nothing():
    admin = _login('brand-refused')
    upload = f'{ADMIN}/assets/logo'
    unsafe = admin.put(upload, files={'file': ('x.svg', f'<svg {NS} onload="alert(1)"/>'.encode(), 'image/svg+xml')})
    assert unsafe.status_code == 422
    assert 'onload' in unsafe.json()['detail']
    too_big = admin.put(upload, files={'file': ('x.gif', GIF + b'\x00' * (1024 * 1024), 'image/gif')})
    assert (too_big.status_code, too_big.json()['detail']) == (413, 'Die Datei ist größer als 1 MiB.')
    assert admin.put(upload, files={'file': ('x.svg', b'', 'image/svg+xml')}).status_code == 422
    unknown = admin.put(upload, files={'file': ('x.jpg', b'\xff\xd8\xff\xe0JFIF', 'image/jpeg')})
    assert (unknown.status_code, unknown.json()['detail']) == (415, 'Nur SVG, GIF oder PNG.')
    assert admin.put(f'{ADMIN}/assets/banner', files={'file': ('x.svg', SVG, 'image/svg+xml')}).status_code == 404
    assert admin.get(ADMIN).json()['managed'] is False


def test_unknown_or_unset_assets_are_404_and_delete_is_idempotent():
    anonymous = _anonymous()
    assert anonymous.get('/api/v1/branding/assets/banner').status_code == 404
    assert anonymous.get('/api/v1/branding/assets/busy').status_code == 404
    admin = _login('brand-delete')
    admin.put(f'{ADMIN}/assets/busy', files={'file': ('busy.gif', GIF, 'image/gif')})
    assert anonymous.get('/api/v1/branding/assets/busy').status_code == 200
    removed = admin.delete(f'{ADMIN}/assets/busy')
    assert removed.status_code == 200
    assert (removed.json()['busy'], removed.json()['managed']) == (None, False)
    assert anonymous.get('/api/v1/branding/assets/busy').status_code == 404
    assert anonymous.get(PUBLIC).json()['busy'] is None
    assert admin.delete(f'{ADMIN}/assets/busy').status_code == 200
    assert admin.delete(f'{ADMIN}/assets/banner').status_code == 404


def test_public_projection_is_cached_and_admin_writes_invalidate_it(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(branding_service.time, 'monotonic', lambda: clock[0])
    anonymous = _anonymous()
    assert anonymous.get(PUBLIC).json()['primary_color'] is None

    # A change that bypasses this process's admin routes (another replica)
    # shows only once the 10 s are over.
    with TestingSessionLocal() as db:
        db.add(BrandingConfig(id='default', primary_color='#112233'))
        db.commit()
    clock[0] += 9
    assert anonymous.get(PUBLIC).json()['primary_color'] is None
    clock[0] += 2
    assert anonymous.get(PUBLIC).json()['primary_color'] == '#112233'

    # A write through the admin routes of this process shows at once.
    admin = _login('brand-cache')
    admin.put(ADMIN, json={'primary_color': '#445566'})
    assert anonymous.get(PUBLIC).json()['primary_color'] == '#445566'
    admin.put(f'{ADMIN}/assets/logo', files={'file': ('logo.svg', SVG, 'image/svg+xml')})
    assert anonymous.get(PUBLIC).json()['logo'] is not None
    admin.delete(f'{ADMIN}/assets/logo')
    assert anonymous.get(PUBLIC).json()['logo'] is None


def test_configuration_overview_lists_the_colours_but_no_asset_bytes():
    admin = _login('brand-config')
    admin.put(ADMIN, json={'primary_color': '#123456'})
    admin.put(f'{ADMIN}/assets/logo', files={'file': ('logo.svg', SVG, 'image/svg+xml')})
    with TestingSessionLocal() as db:
        sections = {section['section']: section['items'] for section in runtime_config._manual_settings(db)}
    [item] = sections['branding_config']
    assert item['id'] == 'default'
    assert item['values']['primary_color'] == '#123456'
    assert set(item['values']) == {'id', 'primary_color', 'accent_color', 'updated_at'}
    assert 'branding_assets' not in sections
