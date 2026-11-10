"""GET /v1/portal/releases/{release_id}/artifacts/{filename} -- proxies a
released document's image/attachment bytes out of Weave-Ingest for the chat
UI, since Weave-Chat only ever talks to Weave-API and never directly to
Weave-Ingest (see deploy/charts/weave/templates/chat-deployment.yaml).

Behind the same auth+ratelimit dependency as every other authenticated
route (app/core/ratelimit.py's enforce_rate_limit). Weave-Ingest itself is
called with a shared service token (INGEST_SERVICE_TOKEN, mirroring the
WEAVE_INGEST_API_TOKEN Weave-Knowledge already presents to the same
get_knowledge_reader dependency there), which Weave-Ingest does not scope
to any person -- so this route checks read access itself first (ADR 0008):
the release's space, looked up in Weave-Retrieval, must be one the caller
may read right now (the same read authority as GET /v1/collections and the
chat's sources). A revoked grant therefore stops the images at once, and a
release UUID alone is no longer enough. Unknown, superseded or space-less
releases fail closed with the same 404 as a missing artifact.
"""

import logging
import uuid
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.services.ingest_identity import ingest_subject
from app.services.retrieval_client import RetrievalClientError, list_collections, release_collection

router = APIRouter(prefix='/v1/portal', tags=['portal-artifacts'])
logger = logging.getLogger(__name__)

# The only types a browser may render inline from THIS origin -- passive
# raster images, which is all the chat shows (Source.images). Mirrors
# Weave-Ingest's _ARTIFACT_INLINE_CONTENT_TYPES minus application/pdf: a
# PDF is not shown in the chat, and the sandbox CSP below would break the
# browser's viewer anyway. Everything else -- image/svg+xml above all,
# which carries script, and text/html -- is an attachment: this gateway's
# origin holds the session cookie, so rendering an uploaded document's
# active content here would be stored XSS.
_INLINE_CONTENT_TYPES = frozenset({'image/png', 'image/jpeg', 'image/gif', 'image/webp'})
# Defence in depth for whatever still renders (a direct navigation to an
# attachment the browser ignores, a type sniffed despite nosniff): no
# subresources, no script, an opaque origin. An image document needs none
# of it -- <img> in the chat is unaffected by the image's own CSP.
_ARTIFACT_CSP = "default-src 'none'; sandbox"


def _valid_filename(filename: str) -> bool:
    """One plain path segment: never '.'/'..', a separator, or anything
    that would end or re-shape the upstream path (query, fragment, control
    characters) once spliced into Weave-Ingest's URL."""
    if filename in ('', '.', '..'):
        return False
    return not any(ch in '/\\?#' or ord(ch) < 32 or ord(ch) == 127 for ch in filename)


def _content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266/5987 Content-Disposition, the same shape as Weave-Ingest's
    app/api/http_utils.py: ASCII-safe quoted fallback plus a UTF-8
    filename* parameter for everything else."""
    fallback = ''.join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else '_' for ch in filename) or 'download'
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


def _client() -> httpx.Client:
    """Factored out so tests can monkeypatch it with a fake client/response
    pair (mirrors app/services/runtime_client.py's own `_client()`)."""
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_seconds,
        read=settings.http_read_timeout_seconds,
        write=settings.http_read_timeout_seconds,
        pool=settings.http_read_timeout_seconds,
    )
    headers = {'Authorization': f'Bearer {settings.ingest_service_token}'}
    return httpx.Client(base_url=settings.ingest_api_url.rstrip('/'), headers=headers, timeout=timeout)


def _require_read_access(release_id: str, user: User) -> str:
    """The canonical release id, once the caller may read its space --
    else the same 404 as a missing artifact (no hint the release exists)."""
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Artifact not found')
    try:
        release_id = str(uuid.UUID(release_id))
    except ValueError:
        raise not_found from None
    try:
        slug = release_collection(release_id)
        if slug is None:
            raise not_found
        readable = list_collections(team=user.effective_teams, user=ingest_subject(user.oidc_subject))
    except RetrievalClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if slug not in {collection.get('slug') for collection in readable if isinstance(collection, dict)}:
        raise not_found
    return release_id


@router.get('/releases/{release_id}/artifacts/{filename}')
def get_release_artifact(release_id: str, filename: str, user: User = Depends(enforce_rate_limit)) -> Response:
    if not settings.ingest_api_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Ingest is not configured')
    if not _valid_filename(filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Artifact not found')
    release_id = _require_read_access(release_id, user)

    path = f'/api/v1/portal/releases/{release_id}/artifacts/{quote(filename, safe="")}'
    try:
        with _client() as http_client:
            upstream = http_client.get(path)
    except httpx.RequestError as exc:
        # The transport error names internal hosts/ports -- logs only.
        logger.warning('Ingest artifact request failed: %s', exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Ingest unreachable') from exc

    if upstream.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Artifact not found')
    if upstream.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Ingest rejected the artifact request')

    media_type = upstream.headers.get('content-type') or 'application/octet-stream'
    # Inline only for an allowlisted type AND only if Weave-Ingest itself
    # offered it inline; its 'attachment' is always kept.
    inline = media_type.split(';', 1)[0].strip().lower() in _INLINE_CONTENT_TYPES and not (
        upstream.headers.get('content-disposition') or ''
    ).strip().lower().startswith('attachment')
    return Response(
        content=upstream.content,
        media_type=media_type,
        headers={
            'Content-Disposition': _content_disposition('inline' if inline else 'attachment', filename),
            'Content-Security-Policy': _ARTIFACT_CSP,
            'X-Content-Type-Options': 'nosniff',
            # Revalidate on every view: each one runs the read check again, so
            # a revoked grant also stops images the browser already holds.
            'Cache-Control': 'private, no-cache',
        },
    )
