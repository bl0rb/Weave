"""Fetch status for already-authorized releases, using a fixed service URL."""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import redis as redis_lib
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.schemas.indexing import PortalIndexingStatus
from app.services import security

logger = logging.getLogger(__name__)
_SIGNING_CONTEXT = b'weave.indexing-status.v1\n'

# Transient Knowledge outages (timeout, connection error, 5xx/429): the last
# status Knowledge confirmed for EXACTLY the same release reference is served
# for STALE_GRACE, marked stale; afterwards the item is 'unavailable' with
# `retrying` until RETRYING_FOR has passed since the first failure.
# Both live in Redis (shared by every Ingest replica), keyed by job, release
# and snapshot hash; the confirmed status expires with the grace period.
STALE_GRACE = timedelta(minutes=5)
RETRYING_FOR = timedelta(minutes=10)
_RETRY_DELAY_SECONDS = 0.5
_FAILING_SINCE_TTL_SECONDS = 24 * 3600
_CONFIRMED_KEY_PREFIX = 'indexing-status:confirmed:'
_FAILING_KEY_PREFIX = 'indexing-status:failing-since:'
_TRANSIENT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


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


class _TransientLookupError(Exception):
    pass


def _key(prefix: str, ref: ReleaseReference) -> str:
    return f'{prefix}{ref.job_id}:{ref.release_id}:{ref.markdown_sha256}'


def _remember(confirmed: dict[ReleaseReference, PortalIndexingStatus], forget: list[ReleaseReference], now: datetime) -> None:
    """Fails open: without Redis there is simply no grace later on."""
    try:
        pipe = security._rate_limit_redis().pipeline(transaction=False)
        for ref, status in confirmed.items():
            value = json.dumps({'checked_at': now.isoformat(), 'status': status.model_dump(mode='json')})
            pipe.set(_key(_CONFIRMED_KEY_PREFIX, ref), value, ex=int(STALE_GRACE.total_seconds()) or 1)
            pipe.delete(_key(_FAILING_KEY_PREFIX, ref))
        for ref in forget:
            pipe.delete(_key(_CONFIRMED_KEY_PREFIX, ref), _key(_FAILING_KEY_PREFIX, ref))
        pipe.execute()
    except redis_lib.RedisError:
        logger.warning('Indexing status memory unavailable (Redis)')


def _forget(references: list[ReleaseReference]) -> None:
    _remember({}, references, datetime.now(timezone.utc))


def _confirmed(raw: str | None, now: datetime) -> PortalIndexingStatus | None:
    try:
        stored = json.loads(raw) if raw else None
        checked_at = datetime.fromisoformat(stored['checked_at'])
        status = PortalIndexingStatus.model_validate(stored['status'])
    except (TypeError, KeyError, ValueError, ValidationError):
        return None
    if now - checked_at > STALE_GRACE or status.state == 'unavailable':
        return None
    return status.model_copy(update={'stale': True, 'checked_at': checked_at})


def _during_outage(references: list[ReleaseReference]) -> dict[str, PortalIndexingStatus]:
    now = datetime.now(timezone.utc)
    try:
        pipe = security._rate_limit_redis().pipeline(transaction=False)
        for ref in references:
            pipe.set(_key(_FAILING_KEY_PREFIX, ref), now.isoformat(), nx=True, ex=_FAILING_SINCE_TTL_SECONDS)
        pipe.mget([_key(_FAILING_KEY_PREFIX, ref) for ref in references])
        pipe.mget([_key(_CONFIRMED_KEY_PREFIX, ref) for ref in references])
        *_, failing_since, confirmed = pipe.execute()
    except redis_lib.RedisError:
        # Without Redis an outage cannot be told apart from a lasting one.
        logger.warning('Indexing status memory unavailable (Redis)')
        return {ref.job_id: PortalIndexingStatus(state='unavailable') for ref in references}
    result = {}
    for ref, since_raw, confirmed_raw in zip(references, failing_since, confirmed):
        status = _confirmed(confirmed_raw, now)
        if status is not None:
            result[ref.job_id] = status
            continue
        try:
            since = datetime.fromisoformat(since_raw)
        except (TypeError, ValueError):
            since = now
        result[ref.job_id] = PortalIndexingStatus(state='unavailable', retrying=now - since < RETRYING_FOR)
    return result


def _request_status(base_url: str, secret: str, body: bytes) -> '_StatusResponse':
    """One quick retry for transient failures; anything else raises as is."""
    for attempt in range(2):
        if attempt:
            time.sleep(_RETRY_DELAY_SECONDS)
        timestamp = str(int(time.time()))
        signed = _SIGNING_CONTEXT + timestamp.encode('ascii') + b'\n' + body
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
        try:
            # No redirects or proxy environment: neither the target nor the
            # credential can be steered by browser-supplied data.
            with httpx.Client(timeout=5.0, follow_redirects=False, trust_env=False) as client:
                response = client.post(f'{base_url}/api/v1/indexing/status', content=body, headers={
                    'Content-Type': 'application/json',
                    'X-Weave-Status-Timestamp': timestamp,
                    'X-Weave-Status-Signature': signature,
                })
        except _TRANSIENT_ERRORS:
            continue
        if response.status_code >= 500 or response.status_code == 429:
            continue
        response.raise_for_status()
        return _StatusResponse.model_validate(response.json())
    raise _TransientLookupError


def fetch_indexing_status(references: list[ReleaseReference]) -> dict[str, PortalIndexingStatus]:
    """One bounded lookup per page (max 50 releases), at most one retry.

    A transient failure serves the last confirmed status of the same
    release reference for STALE_GRACE (see above). Misconfiguration or a
    malformed/mismatching answer becomes plain 'unavailable', never
    'indexed' or 'failed', and clears that memory. The caller re-checks
    permissions on every poll before this runs, and the memory is keyed by
    job, release and snapshot hash, so a revoked scope or a new release
    never sees an old status.
    """
    unavailable = {ref.job_id: PortalIndexingStatus(state='unavailable') for ref in references}
    if not references:
        return {}
    base_url = settings.portal_knowledge_base_url.rstrip('/')
    secret = settings.portal_knowledge_webhook_secret
    if not base_url or not secret or len(references) > 50:
        _forget(references)
        return unavailable

    body = json.dumps({'items': [vars(ref) for ref in references]}, separators=(',', ':')).encode('utf-8')
    try:
        parsed = _request_status(base_url, secret, body)
        expected = {ref.job_id: ref for ref in references}
        seen = set()
        result = dict(unavailable)
        confirmed = {}
        for item in parsed.items:
            job_id = str(item.job_id)
            ref = expected.get(job_id)
            if ref is None or job_id in seen:
                raise ValueError('unexpected indexing reference')
            seen.add(job_id)
            if str(item.release_id) != ref.release_id or item.markdown_sha256 != ref.markdown_sha256:
                continue
            result[job_id] = confirmed[ref] = PortalIndexingStatus(
                state=item.state,
                indexed_at=item.indexed_at if item.state == 'indexed' else None,
                chunk_count=item.chunk_count if item.state == 'indexed' else 0,
            )
        _remember(confirmed, [ref for ref in references if ref not in confirmed], datetime.now(timezone.utc))
        return result
    except _TransientLookupError:
        # Never log response bodies, signatures, URLs with credentials or
        # worker errors. Operators can inspect Knowledge's own logs.
        logger.warning('Knowledge indexing status lookup temporarily unavailable')
        return _during_outage(references)
    except (httpx.HTTPError, httpx.InvalidURL, ValidationError, ValueError):
        logger.warning('Knowledge indexing status lookup unavailable')
        _forget(references)
        return unavailable


def fetch_indexing_diagnostics(reference: ReleaseReference) -> dict:
    """Admin-only caller, separately signed from the normal user status lookup."""
    base_url = settings.portal_knowledge_base_url.rstrip('/')
    secret = settings.portal_knowledge_webhook_secret
    if not base_url or not secret:
        return {'state': 'unavailable', 'reason': 'knowledge_not_configured'}
    body = json.dumps(vars(reference), separators=(',', ':')).encode('utf-8')
    timestamp = str(int(time.time()))
    signed = b'weave.indexing-diagnostics.v1\n' + timestamp.encode('ascii') + b'\n' + body
    signature = 'sha256=' + hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(f'{base_url}/api/v1/indexing/diagnostics', content=body, headers={
                'Content-Type': 'application/json',
                'X-Weave-Status-Timestamp': timestamp,
                'X-Weave-Status-Signature': signature,
            })
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or 'state' not in result:
                raise ValueError('invalid diagnostics response')
            return result
    except httpx.HTTPStatusError as exc:
        return {'state': 'unavailable', 'reason': 'knowledge_http_error', 'http_status': exc.response.status_code}
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        return {'state': 'unavailable', 'reason': 'knowledge_connection_or_response_error'}
