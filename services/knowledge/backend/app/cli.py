"""Weave-Knowledge maintenance CLI.

Run from `backend/` as:

    python -m app.cli reindex [--document-id UUID] [--only-model MODEL_ID]

Today this has exactly one subcommand, `reindex`: re-embed the chunks of
already-indexed documents with the currently configured embedding provider
(settings.embedding_provider), without re-chunking -- see
app/services/embeddings.py's embed_chunks(). This is what a provider/model
change (e.g. switching EMBEDDING_MODEL, or moving from the 'fake' dev
provider to a real 'openai'-compatible one) is expected to be followed by:
existing chunk text/structure is left untouched, only `chunk.embedding` /
`chunk.embedding_model` / `document.embedding_model` are refreshed.

Exits non-zero if any targeted document failed to re-embed, so this is safe
to wire into a deploy step or cron job that should alert on failure.
"""

import argparse
import logging
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.models import Chunk, Document, DocumentStatus
from app.services.embeddings import EmbeddingProvider, embed_chunks, get_provider

logger = logging.getLogger(__name__)

# How often reindex() logs a "processed N/total" progress line, in documents.
_PROGRESS_LOG_EVERY = 10


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

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == 'reindex':
        failed = reindex(document_id=args.document_id, only_model=args.only_model)
        return 1 if failed else 0

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
