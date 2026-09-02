"""Signed, bounded status lookup for the portal's immutable releases.

Uses the existing Ingest webhook secret with a distinct signing context.
It cannot be replayed as a publication event or used to read the corpus.
Ingest is responsible for authorizing the user before signing references.
"""

import hashlib
import hmac
import time
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.models import Chunk, Document, DocumentStatus
from app.schemas.indexing import IndexingStatusItem, IndexingStatusRequest, IndexingStatusResponse

_SIGNING_CONTEXT = b'weave.indexing-status.v1\n'


async def require_status_signature(request: Request) -> None:
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
    signed = _SIGNING_CONTEXT + timestamp.encode('ascii') + b'\n' + body
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode('utf-8'), expected.encode('ascii')):
        raise HTTPException(401, 'invalid signature')


router = APIRouter(prefix='/api/v1/indexing', dependencies=[Depends(require_status_signature)])


@router.post('/status', response_model=IndexingStatusResponse)
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
