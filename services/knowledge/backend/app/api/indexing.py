"""Signed, bounded status lookup for the portal's immutable releases.

Uses the existing Ingest webhook secret with a distinct signing context.
It cannot be replayed as a publication event or used to read the corpus.
Ingest is responsible for authorizing the user before signing references.
"""

import hashlib
import hmac
import re
import time
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.models import Chunk, Document, DocumentStatus
from app.schemas.indexing import ReleaseReference, IndexingStatusItem, IndexingStatusRequest, IndexingStatusResponse

_SIGNING_CONTEXT = b'weave.indexing-status.v1\n'


async def _require_signature(request: Request, context: bytes) -> None:
    secret = settings.weave_ingest_webhook_secret
    if not secret:
        raise HTTPException(503, 'service misconfigured')
    timestamp = request.headers.get('X-Weave-Status-Timestamp', '')
    signature = request.headers.get('X-Weave-Status-Signature', '')
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        raise HTTPException(401, 'invalid signature')
    if abs(time.time() - int(timestamp)) > 60:
        raise HTTPException(401, 'invalid signature')
    body = await request.body()
    if len(body) > 32_768:
        raise HTTPException(413, 'request too large')
    signed = context + timestamp.encode('ascii') + b'\n' + body
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode('utf-8'), expected.encode('ascii')):
        raise HTTPException(401, 'invalid signature')


async def require_status_signature(request: Request) -> None:
    await _require_signature(request, _SIGNING_CONTEXT)


async def require_diagnostics_signature(request: Request) -> None:
    await _require_signature(request, b'weave.indexing-diagnostics.v1\n')


router = APIRouter(prefix='/api/v1/indexing')


@router.post('/status', response_model=IndexingStatusResponse, dependencies=[Depends(require_status_signature)])
def indexing_status(
    payload: IndexingStatusRequest, response: Response, db: Session = Depends(get_db),
) -> IndexingStatusResponse:
    response.headers['Cache-Control'] = 'no-store'
    # One bounded SQL statement and one MVCC snapshot. Never load markdown,
    # embeddings or raw worker errors. Verify committed, embedded chunk rows
    # as well as the worker's counter so a damaged/empty index is not green.
    chunk_count = select(func.count(Chunk.id)).where(Chunk.document_id == Document.id).correlate(Document).scalar_subquery()
    embedding_present = Chunk.embedding.is_not(None)
    if db.get_bind().dialect.name == 'sqlite':
        # SQLite's JSON fallback stores Python None as JSON null, which is
        # not SQL NULL. Only a nonempty vector array confirms an embedding.
        embedding_present = and_(func.json_type(Chunk.embedding) == 'array', func.json_array_length(Chunk.embedding) > 0)
    embedded_count = select(func.count(Chunk.id)).where(
        Chunk.document_id == Document.id, embedding_present,
    ).correlate(Document).scalar_subquery()
    rows = db.execute(select(
        Document.source_job_id,
        Document.frontmatter['_weave_release_id'].as_string().label('release_id'),
        Document.frontmatter['_weave_markdown_sha256'].as_string().label('markdown_sha256'),
        Document.status, Document.chunk_count, Document.indexed_at,
        chunk_count.label('stored_chunks'), embedded_count.label('embedded_chunks'),
    ).where(Document.source_job_id.in_([str(item.job_id) for item in payload.items]))).all()
    by_job = {row.source_job_id: row for row in rows}
    items = []
    for reference in payload.items:
        row = by_job.get(str(reference.job_id))
        result = IndexingStatusItem(**reference.model_dump(), state='not_received')
        if row is not None:
            if row.release_id != str(reference.release_id) or row.markdown_sha256 != reference.markdown_sha256:
                result.state = 'mismatch'
            elif row.status == DocumentStatus.INDEXED:
                if row.chunk_count == row.stored_chunks == 0:
                    result.state = 'empty'
                elif row.indexed_at is None or not (row.chunk_count == row.stored_chunks == row.embedded_chunks > 0):
                    result.state = 'incomplete'
                else:
                    result.state = 'indexed'
                    result.indexed_at = row.indexed_at if row.indexed_at.tzinfo else row.indexed_at.replace(tzinfo=timezone.utc)
                    result.chunk_count = row.chunk_count
            else:
                result.state = row.status.value
        items.append(result)
    return IndexingStatusResponse(items=items)


def _failure_diagnostic(error: str | None) -> dict | None:
    """Export allowlisted facts, never SQL parameters, provider bodies or credentials."""
    if not error:
        return None
    if match := re.search(r'expected (\d+) dimensions?, (?:not|got) (\d+)', error, re.I):
        return {'code': 'embedding_dimension_mismatch', 'expected': int(match[1]), 'actual': int(match[2])}
    if match := re.search(r'HTTP (\d{3})\b', error):
        stage = 'embedding' if error.startswith('embedding request') else 'snapshot_download' if error.startswith('fetching ') else 'upstream'
        return {'code': 'upstream_http_error', 'stage': stage, 'http_status': int(match[1])}
    if 'SHA-256' in error:
        return {'code': 'snapshot_hash_mismatch'}
    if 'not JSON serializable' in error:
        return {'code': 'metadata_not_json_serializable'}
    if error.startswith('embedding request'):
        return {'code': 'embedding_request_failed'}
    if error.startswith('fetching '):
        return {'code': 'snapshot_download_failed'}
    return {'code': 'unclassified_worker_error', 'hint': 'Inspect knowledge-worker logs using document_id and updated_at.'}


@router.post('/diagnostics', dependencies=[Depends(require_diagnostics_signature)])
def indexing_diagnostics(payload: ReleaseReference, response: Response, db: Session = Depends(get_db)) -> dict:
    response.headers['Cache-Control'] = 'no-store'
    # Select only operational fields; document bodies and embeddings never enter the export.
    row = db.execute(select(
        Document.id, Document.status, Document.index_attempts, Document.chunk_count,
        Document.indexed_at, Document.updated_at, Document.embedding_model, Document.error,
    ).where(
        Document.source_job_id == str(payload.job_id),
        Document.frontmatter['_weave_release_id'].as_string() == str(payload.release_id),
        Document.frontmatter['_weave_markdown_sha256'].as_string() == payload.markdown_sha256,
    )).one_or_none()
    if row is None:
        return {'state': 'not_received_or_mismatch'}
    return {
        'document_id': str(row.id), 'state': row.status.value,
        'fetch_attempts': row.index_attempts, 'chunk_count': row.chunk_count,
        'indexed_at': row.indexed_at, 'updated_at': row.updated_at,
        'embedding_model': row.embedding_model, 'failure': _failure_diagnostic(row.error),
    }
