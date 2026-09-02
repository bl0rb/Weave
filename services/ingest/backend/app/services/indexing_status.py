"""Fetch status for already-authorized releases, using a fixed service URL."""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.schemas.indexing import PortalIndexingStatus

logger = logging.getLogger(__name__)
_SIGNING_CONTEXT = b'weave.indexing-status.v1\n'


@dataclass(frozen=True)
class ReleaseReference:
    job_id: str
    release_id: str
    markdown_sha256: str


class _StatusItem(PortalIndexingStatus):
    job_id: UUID
    release_id: UUID
    markdown_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def require_completion_evidence(self):
        if self.state == 'indexed' and (self.indexed_at is None or self.chunk_count < 1):
            raise ValueError('incomplete indexing confirmation')
        return self


class _StatusResponse(BaseModel):
    items: list[_StatusItem] = Field(max_length=50)


def fetch_indexing_status(references: list[ReleaseReference]) -> dict[str, PortalIndexingStatus]:
    """No retries on the request path; visible pages poll again shortly.

    A timeout/misconfiguration/malformed answer becomes 'unavailable', never
    'indexed' or 'failed'. No cross-user/process cache can retain a revoked
    scope. One request covers the whole page (max 50 releases).
    """
    unavailable = {ref.job_id: PortalIndexingStatus(state='unavailable') for ref in references}
    if not references:
        return {}
    base_url = settings.portal_knowledge_base_url.rstrip('/')
    secret = settings.portal_knowledge_webhook_secret
    if not base_url or not secret or len(references) > 50:
        return unavailable
    body = json.dumps({'items': [vars(ref) for ref in references]}, separators=(',', ':')).encode('utf-8')
    timestamp = str(int(time.time()))
    signed = _SIGNING_CONTEXT + timestamp.encode('ascii') + b'\n' + body
    signature = 'sha256=' + hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
    try:
        # No redirects or proxy environment: neither the target nor the
        # credential can be steered by browser-supplied data.
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(f'{base_url}/api/v1/indexing/status', content=body, headers={
                'Content-Type': 'application/json',
                'X-Weave-Status-Timestamp': timestamp,
                'X-Weave-Status-Signature': signature,
            })
            response.raise_for_status()
            parsed = _StatusResponse.model_validate(response.json())
        expected = {ref.job_id: ref for ref in references}
        seen = set()
        result = dict(unavailable)
        for item in parsed.items:
            job_id = str(item.job_id)
            ref = expected.get(job_id)
            if ref is None or job_id in seen:
                raise ValueError('unexpected indexing reference')
            seen.add(job_id)
            if str(item.release_id) != ref.release_id or item.markdown_sha256 != ref.markdown_sha256:
                continue
            result[job_id] = PortalIndexingStatus(
                state=item.state,
                indexed_at=item.indexed_at if item.state == 'indexed' else None,
                chunk_count=item.chunk_count if item.state == 'indexed' else 0,
            )
        return result
    except (httpx.HTTPError, httpx.InvalidURL, ValidationError, ValueError):
        # Never log response bodies, signatures, URLs with credentials or
        # worker errors. Operators can inspect Knowledge's own logs.
        logger.warning('Knowledge indexing status lookup unavailable')
        return unavailable
