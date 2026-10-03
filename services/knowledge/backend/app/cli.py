"""Weave-Knowledge maintenance CLI.

Run from `backend/` as:

    python -m app.cli reindex [--document-id UUID] [--only-model MODEL_ID]
    python -m app.cli reconcile-collections [--dry-run]

`reindex` re-embeds the chunks of
already-indexed documents with the currently configured embedding provider
(settings.embedding_provider), without re-chunking -- see
app/services/embeddings.py's embed_chunks(). This is what a provider/model
change (e.g. switching EMBEDDING_MODEL, or moving from the 'fake' dev
provider to a real 'openai'-compatible one) is expected to be followed by:
existing chunk text/structure is left untouched, only `chunk.embedding` /
`chunk.embedding_model` / `document.embedding_model` are refreshed.

`reconcile-collections` (audit finding F41, ADR 0008 addendum) gives legacy
documents without `collection_slug` the space their Weave-Ingest job has
since been put into, so that space's grants decide who reads them instead
of the uploader's team. See reconcile_collections() below.

Both exit non-zero on failure, so they are safe to wire into a deploy step
or cron job that should alert on failure.
"""

import argparse
import logging
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.models import Chunk, Document, DocumentStatus
from app.services.collection_sync import CollectionSyncError, fetch_job_collections
from app.services.embeddings import EmbeddingProvider, embed_chunks, get_provider

logger = logging.getLogger(__name__)

# How often reindex() logs a "processed N/total" progress line, in documents.
_PROGRESS_LOG_EVERY = 10
# Job ids per Weave-Ingest lookup (its GET /collections/registry/jobs limit).
_RECONCILE_BATCH_SIZE = 100


def _target_documents(db: Session, *, document_id: uuid.UUID | None, only_model: str | None) -> list[Document]:
    """Documents eligible for reindexing: status=indexed (a pending/failed/
    blocked/superseded document has no committed chunks worth re-embedding,
    or shouldn't be surfaced by search in the first place), optionally
    narrowed to one document id and/or one previously-recorded
    embedding_model -- the latter is how a partial rollout ("only migrate
    what's still on the old model") is expressed."""
    query = select(Document).where(Document.status == DocumentStatus.INDEXED)
    if document_id is not None:
        query = query.where(Document.id == document_id)
    if only_model is not None:
        query = query.where(Document.embedding_model == only_model)
    return list(db.execute(query.order_by(Document.created_at)).scalars().all())


def _reindex_one(db: Session, document: Document, provider: EmbeddingProvider) -> None:
    chunks = list(
        db.execute(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)
        ).scalars().all()
    )
    embed_chunks(db, document, chunks, provider)


def reindex(
    *,
    document_id: uuid.UUID | None = None,
    only_model: str | None = None,
    progress_every: int = _PROGRESS_LOG_EVERY,
) -> int:
    """Re-embed every matching document's existing chunks with the
    currently configured provider. Returns the number of documents that
    failed to re-embed (0 = a clean run) -- one document's failure does not
    abort the run; it's logged and counted, and the next document is still
    attempted, same as app/workers/import_tasks.py treats a single page
    failure as non-fatal to the rest of a crawl.
    """
    provider = get_provider()
    db = SessionLocal()
    processed = 0
    failed = 0
    try:
        documents = _target_documents(db, document_id=document_id, only_model=only_model)
        total = len(documents)
        logger.info(
            'reindex: %d document(s) matched (provider=%s, model=%s)',
            total, type(provider).__name__, provider.model_name,
        )
        for document in documents:
            try:
                _reindex_one(db, document, provider)
                db.commit()
            except Exception:
                db.rollback()
                failed += 1
                logger.exception('reindex: failed to re-embed document %s', document.id)
            processed += 1
            if processed % progress_every == 0 or processed == total:
                logger.info('reindex: processed %d/%d document(s), %d failed so far', processed, total, failed)
    finally:
        db.close()

    if failed:
        logger.error('reindex: %d/%d document(s) failed', failed, processed)
    else:
        logger.info('reindex: done, %d document(s) re-embedded', processed)
    return failed


def reconcile_collections(*, dry_run: bool = False, batch_size: int = _RECONCILE_BATCH_SIZE) -> int:
    """Patch `collection_slug` on legacy documents (any status) whose job
    Weave-Ingest now places in a space. Only `collection_slug`, the
    frontmatter's `collection`/`collection_name` and each chunk's
    `meta['collection']` change -- no re-chunking, no re-embedding, no new
    release: the indexed content stays exactly what was approved.

    Idempotent: only rows still without a slug are touched (re-checked
    under a row lock, so a concurrent `document.released` wins), and a
    second run finds nothing left to do. Rows whose job has no space stay
    as they are; their count is logged for the `include_uncollected`
    decision (docs/betrieb.md). Returns 1 if Weave-Ingest could not be
    asked (the batches committed before stay applied), else 0.
    """
    db = SessionLocal()
    reconciled = 0
    try:
        job_ids = list(db.execute(
            select(Document.source_job_id).where(Document.collection_slug.is_(None)).order_by(Document.created_at)
        ).scalars())
        logger.info('reconcile-collections: %d legacy document(s) without a space', len(job_ids))
        for start in range(0, len(job_ids), batch_size):
            try:
                assignments = fetch_job_collections(job_ids[start:start + batch_size])
            except CollectionSyncError:
                logger.exception('reconcile-collections: Weave-Ingest lookup failed, %d reconciled so far', reconciled)
                return 1
            for job_id, (slug, name) in assignments.items():
                document = db.execute(
                    select(Document)
                    .where(Document.source_job_id == job_id, Document.collection_slug.is_(None))
                    .with_for_update()
                ).scalar_one_or_none()
                if document is None:
                    continue
                document.collection_slug = slug
                document.frontmatter = {**(document.frontmatter or {}), 'collection': slug, 'collection_name': name}
                for chunk in db.execute(select(Chunk).where(Chunk.document_id == document.id)).scalars():
                    chunk.meta = {**(chunk.meta or {}), 'collection': slug}
                reconciled += 1
                logger.info('reconcile-collections: document %s (job %s) -> %s', document.id, job_id, slug)
            if dry_run:
                db.rollback()
            else:
                db.commit()
    finally:
        db.close()

    logger.info(
        'reconcile-collections: %s %d document(s), %d remain without a space%s',
        'would assign' if dry_run else 'assigned', reconciled, len(job_ids) - reconciled,
        ' (dry run, nothing written)' if dry_run else '',
    )
    return 0


def _parse_document_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'{raw!r} is not a valid UUID') from exc


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='app.cli', description='Weave-Knowledge maintenance CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    reindex_parser = subparsers.add_parser(
        'reindex',
        help='Re-embed existing chunks of indexed documents with the currently configured embedding provider',
    )
    reindex_parser.add_argument(
        '--document-id', type=_parse_document_id, default=None,
        help='Only reindex this one document (UUID); default is every indexed document',
    )
    reindex_parser.add_argument(
        '--only-model', default=None,
        help='Only reindex documents whose stored embedding_model equals this value exactly',
    )

    reconcile_parser = subparsers.add_parser(
        'reconcile-collections',
        help='Assign legacy documents without a space to the space their Weave-Ingest job belongs to',
    )
    reconcile_parser.add_argument(
        '--dry-run', action='store_true',
        help='Only log what would change; write nothing',
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == 'reindex':
        failed = reindex(document_id=args.document_id, only_model=args.only_model)
        return 1 if failed else 0
    if args.command == 'reconcile-collections':
        return reconcile_collections(dry_run=args.dry_run)

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
