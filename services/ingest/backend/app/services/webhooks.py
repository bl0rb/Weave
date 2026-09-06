"""Outbound webhook delivery transport.

Kept FastAPI-free so both app/api/webhook_routes.py (the synchronous probe) and
app/workers/webhook_tasks.py (the `deliver_webhook` Celery task, with its own
retry/backoff loop) call the exact same function for the exact same wire
format -- there must be only one place that builds headers/signs the body.

Every outbound request goes through app.services.safe_fetch.safe_fetch:
private-IP blocking with an admin-managed allowlist, unconditional metadata
blocking, DNS pinning and redirect revalidation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import yaml
from sqlalchemy import select

from app.core.config import settings
from app.models.models import Tag, job_tags
from app.services.safe_fetch import SafeFetchError, safe_fetch

if TYPE_CHECKING:
    from app.models.models import ImportRun, Job

_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 64 * 1024
_ERROR_DETAIL_MAX_CHARS = 500


def _sign_body(body: bytes, secret: str) -> str:
    """HMAC-SHA256 over the raw request body, hex-encoded, in the
    'sha256=<hex>' shape sent as X-Weave-Ingest-Signature."""
    mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={mac}'


def send_webhook_request(
    url: str,
    payload: dict,
    secret: str | None,
    allowed_private_hosts: frozenset[str],
) -> tuple[int, str | None]:
    """POST `payload` as JSON to `url`.

    Always sets 'X-Weave-Ingest-Event: <payload['event']>'. When `secret` is
    set, also signs the raw request body and sends it as
    'X-Weave-Ingest-Signature: sha256=<hex-hmac-sha256>'.

    Returns (http_status, error_message): error_message is None for any 2xx
    response, otherwise a short human-readable detail (never headers or the
    secret). http_status is 0 for a transport-level failure (SSRF block, DNS
    failure, timeout, connection refused, ...) that never produced a real
    HTTP response, in which case error_message is always set.
    """
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Weave-Ingest-Event': str(payload.get('event', '')),
    }
    if secret:
        headers['X-Weave-Ingest-Signature'] = _sign_body(body, secret)

    try:
        response = safe_fetch(
            url,
            method='POST',
            headers=headers,
            body=body,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_bytes=_MAX_RESPONSE_BYTES,
            allowed_private_hosts=allowed_private_hosts,
        )
    except SafeFetchError as exc:
        return 0, str(exc)[:_ERROR_DETAIL_MAX_CHARS]

    if 200 <= response.status_code < 300:
        return response.status_code, None

    detail = response.body.decode('utf-8', errors='replace').strip()
    if not detail:
        detail = f'HTTP {response.status_code}'
    return response.status_code, detail[:_ERROR_DETAIL_MAX_CHARS]


def _job_download_url(job_id: str) -> str:
    return f"{settings.public_api_url.rstrip('/')}/api/v1/jobs/{job_id}/download"


def build_job_payload(db, job: 'Job', event: str, include_markdown: bool) -> dict:
    """Build the JSON payload for a 'job.finished'/'job.failed' event, or for
    a manual /webhooks/send (which always builds with event='job.finished'
    -- see app/api/webhook_routes.send_webhook and
    app/workers/webhook_tasks.deliver_webhook).

    `db` is accepted (rather than reading job.tags' lazy-loaded relationship
    implicitly) so the caller's own session is always what runs the tags
    query -- job may be a fresh db.get() result from a short-lived worker
    session, and this keeps the query explicit rather than relying on
    whichever session the ORM instance happens to still be attached to.

    `include_markdown` is the caller's call, not derived from `event` here:
    a manual send is always event='job.finished' with include_markdown=True
    (POST /webhooks/send only accepts a FINISHED job), the automatic
    job.finished hook likewise passes True, and the job.failed hook passes
    False -- see those call sites for the actual event/include_markdown
    pairing. `error_message` is only ever non-null for event='job.failed'.
    """
    tag_names = db.scalars(
        select(Tag.name).join(job_tags, job_tags.c.tag_id == Tag.id).where(job_tags.c.job_id == job.id)
    ).all()

    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    job_settings = info.get('settings') if isinstance(info.get('settings'), dict) else {}
    profile_id = job_settings.get('profile_id') if isinstance(job_settings.get('profile_id'), str) else None
    folder = job_settings.get('folder') if isinstance(job_settings.get('folder'), str) else ''
    subfolder = job_settings.get('subfolder') if isinstance(job_settings.get('subfolder'), str) else ''

    return {
        'event': event,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'job': {
            'id': job.id,
            'filename': job.original_filename,
            'status': job.status.value if hasattr(job.status, 'value') else str(job.status),
            'folder': folder,
            'subfolder': subfolder,
            'tags': sorted(tag_names),
            'profile_id': profile_id,
            'document_version': job.document_version,
            'content_sha256': job.content_sha256,
        },
        'markdown': job.result_markdown if include_markdown else None,
        'error_message': job.error_message if event == 'job.failed' else None,
        'download_url': _job_download_url(job.id),
    }


def _parse_frontmatter(markdown: str | None) -> dict:
    """Extract the YAML frontmatter block a generated result markdown starts
    with (see app/services/paddle_service.py's _build_rag_frontmatter /
    _prepend_frontmatter for how it is written: '---\\n<yaml>---\\n\\n<body>')
    as a plain dict.

    Tolerant like app/services/confluence_markdown.add_frontmatter_keys's own
    parsing -- no frontmatter, an unterminated block, invalid YAML, or a YAML
    document that isn't a mapping all return {} rather than raising. A
    document.processed payload must never fail to build just because a
    result happens to have no (or a malformed) frontmatter block.
    """
    if not markdown or not markdown.startswith('---\n'):
        return {}
    end = markdown.find('\n---\n', 4)
    if end == -1:
        return {}
    block = markdown[4:end + 1]
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_document_processed_payload(job: 'Job') -> dict:
    """Build the JSON payload for a 'document.processed' event (see
    contracts/events/document.processed.md + .schema.json).

    Dispatched alongside 'job.finished' -- never on its own, never for
    job.failed -- by app/workers/tasks.py's completion hook, via
    app/workers/webhook_tasks.dispatch_job_event(db, job, 'document.processed')
    (same per-task opt-in / same WebhookConnection.events filter / same
    WebhookDelivery audit row as every other event); this function is only
    ever called from app/workers/webhook_tasks.deliver_webhook once a
    delivery for that event already exists, so `job` is expected to be
    FINISHED with a non-null result_markdown.

    `frontmatter` is the actual YAML frontmatter parsed back out of
    job.result_markdown (not re-derived from job.processing_info), so it is
    byte-for-byte what a consumer would see if it fetched `markdown_url` --
    `engine`/`processed_at` are read from that same parsed frontmatter for
    the same reason (the contract requires them to match the frontmatter's
    own copies), falling back to job.processing_info['execution'] only when
    the frontmatter is missing/malformed.

    `quality` comes from job.processing_info['execution']['quality_gate']
    (app/services/quality_gate.evaluate_document_quality's return value) --
    `grade`/`recommendation` are exactly its own enum values, and `signals`
    is passed through as-is (its real keys are ocr_confidence,
    confidence_sample_size, structure_quality, noise_penalty, text_quality,
    field_validation -- see quality_gate.py; contracts/events/document.processed.md
    documents this real shape rather than the placeholder one an earlier
    draft of the contract had).
    """
    frontmatter = _parse_frontmatter(job.result_markdown)

    info = job.processing_info if isinstance(job.processing_info, dict) else {}
    execution = info.get('execution') if isinstance(info.get('execution'), dict) else {}
    quality_gate = execution.get('quality_gate') if isinstance(execution.get('quality_gate'), dict) else {}
    signals = quality_gate.get('signals') if isinstance(quality_gate.get('signals'), dict) else {}

    engine = frontmatter.get('engine') if isinstance(frontmatter.get('engine'), str) else execution.get('engine')
    processed_at = (
        frontmatter.get('processed_at')
        if isinstance(frontmatter.get('processed_at'), str)
        else execution.get('finished_at') or datetime.now(timezone.utc).isoformat()
    )

    return {
        'event': 'document.processed',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'job_id': job.id,
        'document_version': job.document_version,
        'previous_job_id': job.previous_job_id,
        'content_sha256': job.content_sha256,
        'original_filename': job.original_filename,
        'markdown_url': _job_download_url(job.id),
        'frontmatter': frontmatter,
        'quality': {
            'grade': quality_gate.get('grade'),
            'recommendation': quality_gate.get('recommendation'),
            'signals': signals,
        },
        'engine': engine,
        'processed_at': processed_at,
    }


def build_run_payload(run: 'ImportRun') -> dict:
    """Build the JSON payload for an 'import_run.finished' event."""
    return {
        'event': 'import_run.finished',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'run': {
            'id': run.id,
            'scope_type': run.scope_type,
            'scope_value': run.scope_value,
            'pages_imported': run.pages_imported,
            'pages_failed': run.pages_failed,
        },
    }
