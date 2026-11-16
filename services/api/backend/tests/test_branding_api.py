"""GET /v1/branding and GET /v1/branding/assets/{kind} -- the public,
read-only branding gateway, against a mocked Weave-Ingest. Mocked at
`app.api.branding._client` via `httpx.MockTransport` (the same seam as
tests/test_me.py); the module's clock and caches are swapped per test.

What must hold: no credential in either direction, a cosmetic failure
never breaks the chat (200 with the last good value or the all-null
default), nothing unvalidated reaches a browser, and the asset proxy
answers only with passive image types under a sandbox CSP.
"""

import threading

import httpx
import pytest

from app.api import branding
from app.core.config import settings
from tests.conftest import client

_LOGO_SHA = 'a' * 64
_BUSY_SHA = 'b' * 64
_ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
_PALETTE = {
    'light': {
        'accent': '#1d6a53',
        'accent_hover': '#195a47',
        'accent_ink': '#1d6a53',
        'accent_soft': '#e4eeeb',
        'on_accent': '#ffffff',
    },
    'dark': {
        'accent': '#5fa08b',
        'accent_hover': '#77ae9c',
        'accent_ink': '#8fc0b0',
        'accent_soft': '#1f3a32',
        'on_accent': '#0c1511',
    },
}
_DEFAULT = {
    'primary_color': None,
    'accent_color': None,
    'palette': None,
    'logo': None,
    'busy': None,
    'updated_at': None,
}


def _upstream_branding(**overrides) -> dict:
    payload = {
        'primary_color': '#1d6a53',
        'accent_color': None,
        'palette': _PALETTE,
        'logo': {
            'content_type': 'image/svg+xml',
            'sha256': _LOGO_SHA,
            'size_bytes': 120,
            'url': f'/api/v1/branding/assets/logo?v={_LOGO_SHA[:16]}',
        },
        'busy': {
            'content_type': 'image/gif',
            'sha256': _BUSY_SHA,
            'size_bytes': 2048,
            'url': f'/api/v1/branding/assets/busy?v={_BUSY_SHA[:16]}',
        },
        'updated_at': '2026-11-16T10:00:00+00:00',
    }
    payload.update(overrides)
    return payload


class _Ingest:
    """Records every request; `handler` decides the answer."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = lambda request: httpx.Response(500)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def clock(monkeypatch):
    state = {'now': 1000.0}
    monkeypatch.setattr(branding, '_now', lambda: state['now'])
    return state


@pytest.fixture
def ingest(monkeypatch, clock):
    monkeypatch.setattr(settings, 'ingest_api_url', 'http://ingest.internal/')
    monkeypatch.setattr(branding, '_branding_cache', branding._CachedBranding())
    monkeypatch.setattr(branding, '_asset_cache', {})
    monkeypatch.setattr(branding, '_asset_misses', {})
    fake = _Ingest()
    monkeypatch.setattr(branding, '_client', lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def _serve_branding(ingest: _Ingest, payload) -> None:
    ingest.handler = lambda request: httpx.Response(200, json=payload)


def _serve_asset(ingest: _Ingest, content: bytes = b'<svg/>', content_type: str = 'image/svg+xml', etag=None, **extra):
    headers = {'content-type': content_type, **extra}
    if etag is not None:
        headers['etag'] = etag

    def _handle(request):
        if_none_match = request.headers.get('if-none-match')
        if etag is not None and if_none_match == etag:
            return httpx.Response(304, headers={'etag': etag})
        return httpx.Response(200, content=content, headers=headers)

    ingest.handler = _handle


# --- GET /v1/branding --------------------------------------------------------


def test_branding_is_proxied_and_asset_urls_point_at_this_gateway(ingest):
    _serve_branding(ingest, _upstream_branding())
    response = client.get('/v1/branding')

    assert response.status_code == 200
    assert response.headers['cache-control'] == 'public, max-age=30'
    assert response.json() == {
        'primary_color': '#1d6a53',
        'accent_color': None,
        'palette': _PALETTE,
        'logo': {
            'content_type': 'image/svg+xml',
            'sha256': _LOGO_SHA,
            'size_bytes': 120,
            'url': f'/v1/branding/assets/logo?v={_LOGO_SHA[:16]}',
        },
        'busy': {
            'content_type': 'image/gif',
            'sha256': _BUSY_SHA,
            'size_bytes': 2048,
            'url': f'/v1/branding/assets/busy?v={_BUSY_SHA[:16]}',
        },
        'updated_at': '2026-11-16T10:00:00+00:00',
    }
    [request] = ingest.requests
    assert str(request.url) == 'http://ingest.internal/api/v1/branding'


def test_no_credential_is_needed_or_forwarded(ingest):
    _serve_branding(ingest, _upstream_branding())
    # An invalid bearer token is not even looked at: the route is public.
    response = client.get('/v1/branding', headers={'Authorization': 'Bearer not-a-token'})
    assert response.status_code == 200
    assert 'authorization' not in ingest.requests[0].headers
    assert 'cookie' not in ingest.requests[0].headers


def test_branding_is_cached_for_30_seconds(ingest, clock):
    _serve_branding(ingest, _upstream_branding())
    assert client.get('/v1/branding').json()['primary_color'] == '#1d6a53'

    _serve_branding(ingest, _upstream_branding(primary_color='#123456'))
    clock['now'] += 29
    assert client.get('/v1/branding').json()['primary_color'] == '#1d6a53'
    assert len(ingest.requests) == 1

    clock['now'] += 1
    assert client.get('/v1/branding').json()['primary_color'] == '#123456'
    assert len(ingest.requests) == 2


def _fail_with_500(request):
    return httpx.Response(500, json={'detail': 'boom'})


def _fail_with_connect_error(request):
    raise httpx.ConnectError('connect to ingest.internal:8000 refused')


def _fail_with_timeout(request):
    raise httpx.ReadTimeout('timed out')


def _fail_with_invalid_json(request):
    return httpx.Response(200, content=b'<html>', headers={'content-type': 'text/html'})


def _fail_with_non_object(request):
    return httpx.Response(200, json=['not', 'an', 'object'])


_FAILURES = [
    _fail_with_500,
    _fail_with_connect_error,
    _fail_with_timeout,
    _fail_with_invalid_json,
    _fail_with_non_object,
]


@pytest.mark.parametrize('failure', _FAILURES)
def test_upstream_error_serves_the_last_good_value(ingest, clock, failure):
    _serve_branding(ingest, _upstream_branding())
    first = client.get('/v1/branding').json()

    ingest.handler = failure
    clock['now'] += 31
    response = client.get('/v1/branding')
    assert response.status_code == 200
    assert response.json() == first
    assert len(ingest.requests) == 2


@pytest.mark.parametrize('failure', _FAILURES)
def test_upstream_error_without_last_good_serves_all_null_default(ingest, failure):
    ingest.handler = failure
    response = client.get('/v1/branding')
    assert response.status_code == 200
    assert response.json() == _DEFAULT
    assert 'Ingest' not in response.text


def test_unconfigured_ingest_serves_all_null_default(ingest, monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    response = client.get('/v1/branding')
    assert response.status_code == 200
    assert response.json() == _DEFAULT
    assert ingest.requests == []


def test_failed_fetch_is_retried_after_a_short_pause_not_on_every_request(ingest, clock):
    ingest.handler = _fail_with_connect_error
    client.get('/v1/branding')
    clock['now'] += 4
    client.get('/v1/branding')
    assert len(ingest.requests) == 1

    _serve_branding(ingest, _upstream_branding())
    clock['now'] += 1
    assert client.get('/v1/branding').json()['primary_color'] == '#1d6a53'
    assert len(ingest.requests) == 2


def test_retry_pause_counts_from_the_end_of_a_slow_failed_fetch(ingest, clock):
    def _time_out(request):
        clock['now'] += 3  # the 3 s timeout ran out
        raise httpx.ConnectTimeout('timed out')

    ingest.handler = _time_out
    client.get('/v1/branding')
    clock['now'] += 4
    client.get('/v1/branding')
    assert len(ingest.requests) == 1


def test_a_fetch_in_flight_is_not_joined_by_more_fetches(ingest, clock):
    _serve_branding(ingest, _upstream_branding())
    first = client.get('/v1/branding').json()
    clock['now'] += 31

    entered, release = threading.Event(), threading.Event()

    def _slow(request):
        entered.set()
        release.wait(5)
        return httpx.Response(200, json=_upstream_branding(primary_color='#123456'))

    ingest.handler = _slow
    worker = threading.Thread(target=client.get, args=('/v1/branding',))
    worker.start()
    try:
        assert entered.wait(5)
        # Served the last good value at once instead of a second call.
        assert client.get('/v1/branding').json() == first
        assert len(ingest.requests) == 2
    finally:
        release.set()
        worker.join(5)
    assert client.get('/v1/branding').json()['primary_color'] == '#123456'
    assert len(ingest.requests) == 2


def _palette_with(mode: str, key: str, value) -> dict:
    palette = {name: dict(tokens) for name, tokens in _PALETTE.items()}
    if value is None:
        del palette[mode][key]
    else:
        palette[mode][key] = value
    return palette


@pytest.mark.parametrize(
    'palette',
    [
        _palette_with('light', 'accent', '#1D6A53'),  # uppercase
        _palette_with('dark', 'on_accent', '#fff'),  # short form
        _palette_with('light', 'accent_soft', 'red'),
        _palette_with('dark', 'accent', '#1d6a53\n'),
        _palette_with('light', 'accent_ink', 'url(javascript:alert(1))'),
        _palette_with('dark', 'accent_hover', None),  # missing token
        _palette_with('light', 'on_accent', 0x1D6A53),
        {'light': _PALETTE['light']},  # no dark set
        'not-a-palette',
    ],
)
def test_invalid_palette_is_dropped_but_the_rest_is_kept(ingest, palette):
    _serve_branding(ingest, _upstream_branding(palette=palette))
    body = client.get('/v1/branding').json()
    assert body['palette'] is None
    assert body['primary_color'] == '#1d6a53'
    assert body['logo'] is not None


def test_palette_carries_only_the_known_tokens(ingest):
    palette = _palette_with('light', 'background', 'url(https://evil.example/x)')
    _serve_branding(ingest, _upstream_branding(palette=palette))
    assert client.get('/v1/branding').json()['palette'] == _PALETTE


@pytest.mark.parametrize(
    'logo',
    [
        {'content_type': 'text/html', 'sha256': _LOGO_SHA, 'size_bytes': 1},
        {'content_type': 'image/jpeg', 'sha256': _LOGO_SHA, 'size_bytes': 1},
        {'content_type': ['image/png'], 'sha256': _LOGO_SHA, 'size_bytes': 1},
        {'content_type': 'image/png', 'sha256': 'A' * 64, 'size_bytes': 1},
        {'content_type': 'image/png', 'sha256': 'a' * 63, 'size_bytes': 1},
        {'content_type': 'image/png', 'sha256': '../../x', 'size_bytes': 1},
        {'content_type': 'image/png', 'sha256': _LOGO_SHA, 'size_bytes': 'big'},
        'logo.svg',
    ],
)
def test_invalid_asset_is_dropped_but_the_rest_is_kept(ingest, logo):
    _serve_branding(ingest, _upstream_branding(logo=logo))
    body = client.get('/v1/branding').json()
    assert body['logo'] is None
    assert body['busy']['url'] == f'/v1/branding/assets/busy?v={_BUSY_SHA[:16]}'
    assert body['palette'] == _PALETTE


def test_invalid_colours_become_null(ingest):
    _serve_branding(ingest, _upstream_branding(primary_color='javascript:x', accent_color='#ABCDEF'))
    body = client.get('/v1/branding').json()
    assert body['primary_color'] is None
    assert body['accent_color'] is None


# --- GET /v1/branding/assets/{kind} ------------------------------------------


@pytest.mark.parametrize(
    ('kind', 'content_type', 'extension'),
    [('logo', 'image/svg+xml', 'svg'), ('busy', 'image/gif', 'gif'), ('logo', 'image/png', 'png')],
)
def test_asset_is_proxied_with_exactly_the_safe_headers(ingest, kind, content_type, extension):
    etag = f'"{_LOGO_SHA}"'
    _serve_asset(
        ingest,
        b'<svg onload="alert(1)"/>',
        content_type,
        etag,
        **{'set-cookie': 'session=x', 'x-internal': 'yes', 'content-disposition': 'attachment'},
    )
    response = client.get(f'/v1/branding/assets/{kind}?v={_LOGO_SHA[:16]}', headers={'Authorization': 'Bearer nope'})

    assert response.status_code == 200
    assert response.content == b'<svg onload="alert(1)"/>'
    assert response.headers['content-type'] == content_type
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['content-security-policy'] == _ASSET_CSP
    assert response.headers['content-disposition'] == f'inline; filename="branding-{kind}.{extension}"'
    assert response.headers['cache-control'] == 'public, max-age=300'
    assert response.headers['etag'] == etag
    assert set(response.headers) == {
        'content-type',
        'content-length',
        'x-content-type-options',
        'content-security-policy',
        'content-disposition',
        'cache-control',
        'etag',
    }
    [request] = ingest.requests
    assert str(request.url) == f'http://ingest.internal/api/v1/branding/assets/{kind}'
    assert 'authorization' not in request.headers


def test_if_none_match_is_forwarded_and_304_passed_through(ingest):
    etag = f'"{_LOGO_SHA}"'
    _serve_asset(ingest, etag=etag)
    response = client.get('/v1/branding/assets/logo', headers={'If-None-Match': etag})

    assert response.status_code == 304
    assert response.content == b''
    assert response.headers['etag'] == etag
    assert response.headers['cache-control'] == 'public, max-age=300'
    assert 'content-type' not in response.headers
    assert ingest.requests[0].headers['if-none-match'] == etag


def test_cached_asset_answers_a_matching_if_none_match_with_304(ingest):
    etag = f'"{_LOGO_SHA}"'
    _serve_asset(ingest, etag=etag)
    assert client.get('/v1/branding/assets/logo').status_code == 200

    response = client.get('/v1/branding/assets/logo', headers={'If-None-Match': f'"other", W/{etag}'})
    assert response.status_code == 304
    assert response.headers['etag'] == etag
    assert len(ingest.requests) == 1


def test_asset_is_cached_per_kind_for_300_seconds(ingest, clock):
    _serve_asset(ingest, b'one', etag='"1"')
    assert client.get('/v1/branding/assets/logo').content == b'one'

    _serve_asset(ingest, b'two', etag='"2"')
    clock['now'] += 299
    assert client.get('/v1/branding/assets/logo').content == b'one'
    assert len(ingest.requests) == 1
    # Another kind is cached on its own.
    assert client.get('/v1/branding/assets/busy').content == b'two'
    assert len(ingest.requests) == 2

    clock['now'] += 1
    assert client.get('/v1/branding/assets/logo').content == b'two'
    assert len(ingest.requests) == 3


def test_newer_branding_with_another_file_bypasses_the_asset_cache(ingest, clock):
    _serve_asset(ingest, b'old', etag=f'"{"c" * 64}"')
    assert client.get('/v1/branding/assets/logo').content == b'old'

    clock['now'] += 10
    _serve_branding(ingest, _upstream_branding())  # names logo sha 'aaaa...'
    client.get('/v1/branding')

    _serve_asset(ingest, b'new', etag=f'"{_LOGO_SHA}"')
    assert client.get('/v1/branding/assets/logo').content == b'new'
    assert client.get('/v1/branding/assets/logo').content == b'new'
    assert len(ingest.requests) == 3


def test_upstream_404_is_404(ingest):
    ingest.handler = lambda request: httpx.Response(404, json={'detail': 'Not Found'})
    response = client.get('/v1/branding/assets/busy')
    assert response.status_code == 404
    assert response.json() == {'detail': 'Branding asset not found'}


def test_upstream_404_is_remembered_for_30_seconds(ingest, clock):
    ingest.handler = lambda request: httpx.Response(404)
    for _ in range(5):
        assert client.get('/v1/branding/assets/logo').status_code == 404
    assert len(ingest.requests) == 1

    clock['now'] += 30
    assert client.get('/v1/branding/assets/logo').status_code == 404
    assert len(ingest.requests) == 2


def test_newer_branding_naming_the_asset_bypasses_a_remembered_404(ingest, clock):
    ingest.handler = lambda request: httpx.Response(404)
    assert client.get('/v1/branding/assets/logo').status_code == 404

    clock['now'] += 1
    _serve_branding(ingest, _upstream_branding())  # an admin uploaded a logo
    client.get('/v1/branding')

    _serve_asset(ingest, b'new', etag=f'"{_LOGO_SHA}"')
    assert client.get('/v1/branding/assets/logo').content == b'new'
    assert len(ingest.requests) == 3


def test_upstream_failure_is_remembered_for_5_seconds(ingest, clock):
    ingest.handler = _fail_with_connect_error
    for _ in range(5):
        assert client.get('/v1/branding/assets/logo').status_code == 502
    assert len(ingest.requests) == 1

    _serve_asset(ingest, b'<svg/>')
    clock['now'] += 5
    assert client.get('/v1/branding/assets/logo').status_code == 200
    assert len(ingest.requests) == 2


def test_revalidations_with_a_cold_cache_reach_ingest_once(ingest):
    etag = f'"{_LOGO_SHA}"'
    _serve_asset(ingest, b'<svg/>', etag=etag)
    for _ in range(5):
        assert client.get('/v1/branding/assets/logo', headers={'If-None-Match': etag}).status_code == 304
    assert len(ingest.requests) == 1

    # A request that needs the bytes still gets them.
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 200
    assert response.content == b'<svg/>'
    assert len(ingest.requests) == 2


def test_concurrent_requests_share_one_upstream_fetch(ingest):
    entered, release = threading.Event(), threading.Event()

    def _slow(request):
        entered.set()
        release.wait(5)
        return httpx.Response(200, content=b'<svg/>', headers={'content-type': 'image/svg+xml'})

    ingest.handler = _slow
    results = []
    workers = [
        threading.Thread(target=lambda: results.append(client.get('/v1/branding/assets/logo'))) for _ in range(3)
    ]
    workers[0].start()
    try:
        assert entered.wait(5)
        for worker in workers[1:]:
            worker.start()
        threading.Event().wait(0.2)  # let them queue behind the first fetch
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
    assert [response.status_code for response in results] == [200, 200, 200]
    assert len(ingest.requests) == 1


def test_non_ascii_if_none_match_is_not_forwarded(ingest):
    _serve_asset(ingest, b'<svg/>', etag=f'"{_LOGO_SHA}"')
    response = client.get('/v1/branding/assets/logo', headers={'If-None-Match': b'"\xff"'})
    assert response.status_code == 200
    assert 'if-none-match' not in ingest.requests[0].headers


@pytest.mark.parametrize('kind', ['favicon', 'LOGO', 'logo.svg', '%2E%2E'])
def test_unknown_kind_is_404_without_asking_ingest(ingest, kind):
    response = client.get(f'/v1/branding/assets/{kind}')
    assert response.status_code == 404
    assert ingest.requests == []


def test_unconfigured_ingest_asset_is_404(ingest, monkeypatch):
    monkeypatch.setattr(settings, 'ingest_api_url', '')
    assert client.get('/v1/branding/assets/logo').status_code == 404
    assert ingest.requests == []


@pytest.mark.parametrize('content_type', ['text/html', 'image/jpeg', 'application/xhtml+xml', ''])
def test_unexpected_upstream_content_type_is_502(ingest, content_type):
    _serve_asset(ingest, b'<html><script>alert(1)</script>', content_type)
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 502
    assert b'script' not in response.content


def test_upstream_content_type_parameters_are_ignored(ingest):
    _serve_asset(ingest, b'<svg/>', 'Image/SVG+XML; charset=utf-8')
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/svg+xml'


def test_body_of_exactly_1_mib_is_served(ingest):
    _serve_asset(ingest, b'x' * (1024 * 1024), 'image/png')
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 200
    assert len(response.content) == 1024 * 1024


def test_oversized_body_is_502(ingest):
    _serve_asset(ingest, b'x' * (1024 * 1024 + 1), 'image/png')
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 502
    assert response.json() == {'detail': 'Branding asset too large'}


def test_oversized_body_without_content_length_is_502(ingest):
    def _chunks():
        for _ in range(3):
            yield b'x' * (512 * 1024)

    ingest.handler = lambda request: httpx.Response(200, content=_chunks(), headers={'content-type': 'image/gif'})
    response = client.get('/v1/branding/assets/busy')
    assert response.status_code == 502


def test_upstream_server_error_is_502(ingest):
    ingest.handler = _fail_with_500
    assert client.get('/v1/branding/assets/logo').status_code == 502


def test_transport_error_text_is_not_returned(ingest):
    ingest.handler = _fail_with_connect_error
    response = client.get('/v1/branding/assets/logo')
    assert response.status_code == 502
    assert response.json() == {'detail': 'Ingest unreachable'}
