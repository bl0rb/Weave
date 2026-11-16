"""GET /v1/branding and GET /v1/branding/assets/{kind} -- the instance's
colours, logo and busy animation, read-only and public, for the chat (and
later the desktop app), which only ever talk to Weave-API.

Weave-Ingest owns the branding (Administration › Betrieb ›
Erscheinungsbild) and serves it publicly at /api/v1/branding; this router
re-serves it without any credential, in either direction. No auth on
purpose: the login page is branded too. Deliberately not under /v1/auth,
whose routes 404 without a configured browser login (app/api/auth.py).

Branding is cosmetic and must never break the chat: GET /v1/branding always
answers 200 -- with the last good value when Weave-Ingest fails, or with the
all-null default (= the built-in look) if there is none -- and everything
Weave-Ingest sends is re-validated before it reaches a browser. Both routes
cache in process, so the public surface cannot multiply calls to Ingest.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.config import settings
from app.schemas.branding import BrandingResponse

router = APIRouter(prefix='/v1/branding', tags=['branding'])
logger = logging.getLogger(__name__)

_UPSTREAM_TIMEOUT_SECONDS = 3.0
_BRANDING_TTL_SECONDS = 30.0
# After a failed fetch the fallback is served this long before Ingest is
# asked again, so a down Ingest costs one timeout per interval, not one per
# request.
_BRANDING_RETRY_SECONDS = 5.0
_ASSET_TTL_SECONDS = 300.0
_MAX_ASSET_BYTES = 1024 * 1024

_KINDS = ('logo', 'busy')
_ASSET_EXTENSIONS = {'image/svg+xml': 'svg', 'image/gif': 'gif', 'image/png': 'png'}
# An SVG may carry script; it is only ever embedded via <img>, and opened
# directly it gets no subresources beyond inline styles and data: images,
# no script and an opaque origin.
_ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
_PALETTE_KEYS = ('accent', 'accent_hover', 'accent_ink', 'accent_soft', 'on_accent')
_COLOR = re.compile(r'#[0-9a-f]{6}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_VERSION = re.compile(r'[0-9a-f]{16}')
_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e]*"')
_DEFAULT: dict = {
    'primary_color': None,
    'accent_color': None,
    'palette': None,
    'logo': None,
    'busy': None,
    'updated_at': None,
}


@dataclass
class _CachedBranding:
    value: dict | None = None  # last good answer, already validated
    fetched_at: float = 0.0  # when `value` was fetched
    expires_at: float = 0.0  # Ingest is not asked again before this


@dataclass
class _CachedAsset:
    etag: str | None
    content_type: str
    # None: Ingest only confirmed `etag` as current (304); the bytes are
    # fetched once a request needs them.
    body: bytes | None
    fetched_at: float


@dataclass
class _AssetMiss:
    """A 404 or 502 for one kind, repeated without asking Ingest again
    until `expires_at`."""

    status_code: int
    detail: str
    fetched_at: float
    expires_at: float


_lock = threading.Lock()
_branding_cache = _CachedBranding()
_asset_cache: dict[str, _CachedAsset] = {}
_asset_misses: dict[str, _AssetMiss] = {}
# At most one request to Ingest per resource at a time. Every call to
# Ingest comes from this service's address, and Ingest rate-limits per
# address across all its routes -- the login handoff included -- so public
# traffic here must never turn into an unbounded number of calls there.
_branding_fetch_lock = threading.Lock()
_asset_fetch_locks = {kind: threading.Lock() for kind in _KINDS}
# Factored out so tests can advance time without sleeping.
_now = time.monotonic


def _client() -> httpx.Client:
    """Factored out so tests can monkeypatch it (mirrors
    app/services/ingest_identity.py's own `_client()`). No credential:
    the Ingest routes are public."""
    return httpx.Client(timeout=_UPSTREAM_TIMEOUT_SECONDS)


def _upstream_url(path: str) -> str:
    return settings.ingest_api_url.rstrip('/') + path


def _color(value: object) -> str | None:
    return value if isinstance(value, str) and _COLOR.fullmatch(value) else None


def _palette(value: object) -> dict | None:
    """Both modes with every token a lowercase #rrggbb, else None: a
    half-valid palette is dropped as a whole."""
    if not isinstance(value, dict):
        return None
    palette = {}
    for mode in ('light', 'dark'):
        tokens = value.get(mode)
        if not isinstance(tokens, dict):
            return None
        cleaned = {key: _color(tokens.get(key)) for key in _PALETTE_KEYS}
        if None in cleaned.values():
            return None
        palette[mode] = cleaned
    return palette


def _asset(kind: str, value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    content_type, sha256, size = value.get('content_type'), value.get('sha256'), value.get('size_bytes')
    if not isinstance(content_type, str) or content_type not in _ASSET_EXTENSIONS:
        return None
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    return {
        'content_type': content_type,
        'sha256': sha256,
        'size_bytes': size,
        'url': f'/v1/branding/assets/{kind}?v={sha256[:16]}',
    }


def _validated(payload: dict) -> dict:
    updated_at = payload.get('updated_at')
    return {
        'primary_color': _color(payload.get('primary_color')),
        'accent_color': _color(payload.get('accent_color')),
        'palette': _palette(payload.get('palette')),
        'logo': _asset('logo', payload.get('logo')),
        'busy': _asset('busy', payload.get('busy')),
        'updated_at': updated_at if isinstance(updated_at, str) else None,
    }


def _fetch_branding() -> dict | None:
    """Ingest's current branding, validated -- None on any failure."""
    if not settings.ingest_api_url:
        return None
    try:
        with _client() as http_client:
            upstream = http_client.get(_upstream_url('/api/v1/branding'), headers={'Accept': 'application/json'})
        if upstream.status_code != status.HTTP_200_OK:
            logger.warning('Ingest branding request answered %s', upstream.status_code)
            return None
        payload = upstream.json()
    except (httpx.HTTPError, ValueError) as exc:
        # The transport error names internal hosts/ports -- logs only.
        logger.warning('Ingest branding request failed: %s', exc)
        return None
    if not isinstance(payload, dict):
        logger.warning('Ingest branding response is not an object')
        return None
    return _validated(payload)


def _current_branding() -> dict:
    with _lock:
        if _now() < _branding_cache.expires_at:
            return _branding_cache.value or _DEFAULT
        # The last good value, or the default after a failed fetch.
        answered_before = _branding_cache.value is not None or _branding_cache.expires_at > 0
    # After a first answer, a request that finds a fetch in flight serves
    # that answer instead of waiting on (or adding to) a slow Ingest.
    if not _branding_fetch_lock.acquire(blocking=not answered_before):
        with _lock:
            return _branding_cache.value or _DEFAULT
    try:
        with _lock:
            if _now() < _branding_cache.expires_at:  # fetched while this request waited
                return _branding_cache.value or _DEFAULT
        fresh = _fetch_branding()
        with _lock:
            # Counted from the end of the fetch, so a timeout does not eat
            # into the pause before the next attempt.
            now = _now()
            if fresh is not None:
                _branding_cache.value = fresh
                _branding_cache.fetched_at = now
                _branding_cache.expires_at = now + _BRANDING_TTL_SECONDS
            else:
                _branding_cache.expires_at = now + _BRANDING_RETRY_SECONDS
            return _branding_cache.value or _DEFAULT
    finally:
        _branding_fetch_lock.release()


@router.get('', response_model=BrandingResponse)
def get_branding(response: Response) -> BrandingResponse:
    response.headers['Cache-Control'] = 'public, max-age=30'
    return BrandingResponse(**_current_branding())


def _etag_matches(if_none_match: str | None, etag: str | None) -> bool:
    """RFC 9110 weak comparison, as If-None-Match uses it."""
    if not if_none_match or not etag:
        return False
    if if_none_match.strip() == '*':
        return True
    opaque = etag.removeprefix('W/')
    return any(candidate.strip().removeprefix('W/') == opaque for candidate in if_none_match.split(','))


def _version(etag: str | None) -> str | None:
    """The ?v= of asset URLs naming the file with this ETag: the first 16
    hex of its sha256, which Weave-Ingest sends (quoted) as the ETag."""
    return etag.removeprefix('W/').strip('"')[:16] if etag else None


def _cache_control(version: str | None, etag: str | None) -> str:
    # Bytes that are not the file the URL's ?v= names must not stick to that
    # URL in browsers.
    return 'public, max-age=300' if version is None or version == _version(etag) else 'no-cache'


def _not_modified(etag: str | None, version: str | None) -> Response:
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': _ASSET_CSP,
        'Cache-Control': _cache_control(version, etag),
    }
    if etag:
        headers['ETag'] = etag
    return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)


def _replaced_since(kind: str, etag: str | None, since: float) -> bool:
    """A branding snapshot newer than `since` names another file for `kind`
    (or none): an admin replaced, removed or added it, so whatever was
    cached for it then is outdated."""
    branding = _branding_cache.value
    if branding is None or _branding_cache.fetched_at <= since:
        return False
    current = branding.get(kind)
    return etag != (f'"{current["sha256"]}"' if current else None)


def _asks_for_another(version: str | None, etag: str | None, since: float, now: float) -> bool:
    """The request's ?v= names another file than the one cached under `etag`
    (or than none, for a miss) -- the branding snapshot it came from, maybe
    another replica's, can be newer than this cache -- and Ingest was last
    asked at `since`, long enough ago. The pause bounds what made-up
    versions can cost Ingest."""
    return version is not None and version != _version(etag) and now - since >= _BRANDING_RETRY_SECONDS


def _cached_asset(kind: str, if_none_match: str | None, version: str | None) -> _CachedAsset | None:
    """What the cache can answer this request with, or None when Ingest has
    to be asked. A recent 404/502 for the kind is raised again."""
    now = _now()
    with _lock:
        entry = _asset_cache.get(kind)
        if (
            entry is not None
            and now - entry.fetched_at < _ASSET_TTL_SECONDS
            and not _replaced_since(kind, entry.etag, entry.fetched_at)
            and not _asks_for_another(version, entry.etag, entry.fetched_at, now)
            # Without the bytes only a matching conditional request is answerable.
            and (entry.body is not None or _etag_matches(if_none_match, entry.etag))
        ):
            return entry
        miss = _asset_misses.get(kind)
        if (
            miss is not None
            and now < miss.expires_at
            and not _replaced_since(kind, None, miss.fetched_at)
            and not _asks_for_another(version, None, miss.fetched_at, now)
        ):
            raise HTTPException(status_code=miss.status_code, detail=miss.detail)
    return None


def _read_capped(upstream: httpx.Response) -> bytes:
    too_large = HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Branding asset too large')
    declared = upstream.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > _MAX_ASSET_BYTES:
        raise too_large
    body = bytearray()
    for chunk in upstream.iter_bytes():
        body += chunk
        if len(body) > _MAX_ASSET_BYTES:
            raise too_large
    return bytes(body)


def _fetch_asset(kind: str, if_none_match: str | None, version: str | None) -> _CachedAsset | Response:
    """The asset from Ingest (and cached), or a 304 passed through. A 404 is
    remembered for 30 s, any other failure for 5 s."""
    try:
        return _request_asset(kind, if_none_match, version)
    except HTTPException as exc:
        now = _now()
        ttl = _BRANDING_TTL_SECONDS if exc.status_code == status.HTTP_404_NOT_FOUND else _BRANDING_RETRY_SECONDS
        with _lock:
            _asset_misses[kind] = _AssetMiss(exc.status_code, str(exc.detail), now, now + ttl)
        raise


def _request_asset(kind: str, if_none_match: str | None, version: str | None) -> _CachedAsset | Response:
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Branding asset not found')
    if not settings.ingest_api_url:
        raise not_found
    # Only a header httpx can encode is forwarded; no ETag could match another.
    forward = if_none_match and if_none_match.isascii() and if_none_match.isprintable()
    headers = {'If-None-Match': if_none_match} if forward else {}
    try:
        with (
            _client() as http_client,
            http_client.stream('GET', _upstream_url(f'/api/v1/branding/assets/{kind}'), headers=headers) as upstream,
        ):
            etag = upstream.headers.get('etag')
            etag = etag if etag and _ETAG.fullmatch(etag) else None
            if upstream.status_code == status.HTTP_304_NOT_MODIFIED:
                if etag is not None:  # Ingest confirmed this ETag as current
                    with _lock:
                        _asset_misses.pop(kind, None)
                        entry = _asset_cache.get(kind)
                        if entry is not None and entry.etag == etag:
                            entry.fetched_at = _now()
                        else:
                            # Remembered without bytes, so further revalidations
                            # (every browser after a restart) stay here.
                            _asset_cache[kind] = _CachedAsset(etag, '', None, _now())
                return _not_modified(etag, version)
            if upstream.status_code == status.HTTP_404_NOT_FOUND:
                with _lock:
                    _asset_cache.pop(kind, None)
                raise not_found
            if upstream.status_code != status.HTTP_200_OK:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail='Ingest rejected the branding asset request'
                )
            content_type = (upstream.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
            if content_type not in _ASSET_EXTENSIONS:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Unexpected branding asset type')
            body = _read_capped(upstream)
    except httpx.HTTPError as exc:
        # The transport error names internal hosts/ports -- logs only.
        logger.warning('Ingest branding asset request failed: %s', exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Ingest unreachable') from exc
    entry = _CachedAsset(etag=etag, content_type=content_type, body=body, fetched_at=_now())
    with _lock:
        _asset_cache[kind] = entry
        _asset_misses.pop(kind, None)
    return entry


@router.get('/assets/{kind}')
def get_branding_asset(kind: str, request: Request) -> Response:
    if kind not in _KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Branding asset not found')
    if_none_match = request.headers.get('if-none-match')
    # The cache-buster of the asset URLs in /v1/branding; anything else is ignored.
    version = request.query_params.get('v')
    version = version if version and _VERSION.fullmatch(version) else None
    entry = _cached_asset(kind, if_none_match, version)
    if entry is None:
        with _asset_fetch_locks[kind]:
            # Whoever waited here reuses the answer the request before it got.
            entry = _cached_asset(kind, if_none_match, version)
            if entry is None:
                fetched = _fetch_asset(kind, if_none_match, version)
                if isinstance(fetched, Response):
                    return fetched
                entry = fetched
    if _etag_matches(if_none_match, entry.etag):
        return _not_modified(entry.etag, version)
    headers = {
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': _ASSET_CSP,
        'Content-Disposition': f'inline; filename="branding-{kind}.{_ASSET_EXTENSIONS[entry.content_type]}"',
        'Cache-Control': _cache_control(version, entry.etag),
    }
    if entry.etag:
        headers['ETag'] = entry.etag
    return Response(content=entry.body, media_type=entry.content_type, headers=headers)
