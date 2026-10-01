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

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.ratelimit import enforce_rate_limit
from app.models.models import User
from app.services.ingest_identity import ingest_subject
from app.services.retrieval_client import RetrievalClientError, list_collections, release_collection

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
    release_id = _require_read_access(release_id, user)

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
            # Revalidate on every view: each one runs the read check again, so
            # a revoked grant also stops images the browser already holds.
            'Cache-Control': 'private, no-cache',
        },
    )
