"""GET /v1/portal/releases/{release_id}/artifacts/{filename} -- proxies a
released document's image/attachment bytes out of Weave-Ingest for the chat
UI, since Weave-Chat only ever talks to Weave-API and never directly to
Weave-Ingest (see deploy/charts/weave/templates/chat-deployment.yaml).

Behind the same auth+ratelimit dependency as every other authenticated
route (app/core/ratelimit.py's enforce_rate_limit) -- a caller needs a valid
Weave-API session, nothing more. Weave-Ingest itself is called with a
shared service token (INGEST_SERVICE_TOKEN, mirroring the
WEAVE_INGEST_API_TOKEN Weave-Knowledge already presents to the same
get_knowledge_reader dependency there), so this endpoint inherits that
dependency's existing trust boundary: any caller with a valid Weave-API
session and the release's (unguessable) UUID can fetch its images, exactly
like Weave-Ingest's own /releases/{id}/download today. There is no
per-collection ACL re-check on the image bytes -- an accepted, pre-existing
limitation of that trust boundary, not one introduced here.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.ratelimit import enforce_rate_limit
from app.models.models import User

router = APIRouter(prefix='/v1/portal', tags=['portal-artifacts'])


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


@router.get('/releases/{release_id}/artifacts/{filename}')
def get_release_artifact(release_id: str, filename: str, _user: User = Depends(enforce_rate_limit)) -> Response:
    if not settings.ingest_api_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Ingest is not configured')

    path = f'/api/v1/portal/releases/{release_id}/artifacts/{filename}'
    try:
        with _client() as http_client:
            upstream = http_client.get(path)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Ingest unreachable: {exc}') from exc

    if upstream.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Artifact not found')
    if upstream.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Ingest rejected the artifact request')

    return Response(
        content=upstream.content,
        media_type=upstream.headers.get('content-type', 'application/octet-stream'),
        headers={
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=3600',
        },
    )
