"""Weave-Knowledge maintenance CLI.

Run from `backend/` as:

    python -m app.cli reindex [--document-id UUID] [--only-model MODEL_ID]
    python -m app.cli reconcile-collections [--dry-run]
    python -m app.cli s3vectors init
    python -m app.cli s3vectors sync [--prune] [--batch N]
    python -m app.cli s3vectors status [--count-vectors]

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

`s3vectors` manages the optional Amazon S3 Vectors mirror of the chunk
embeddings (VECTOR_BACKEND=s3vectors, see app/services/vector_index.py and
contracts/chunk-store.md): `init` creates the index or verifies an existing
one, `sync` (re)writes every eligible chunk and with `--prune` removes
vectors of chunks that no longer exist or are no longer eligible, `status`
reports the index and the counts. `init`/`sync` refuse to run (exit 2)
while VECTOR_BACKEND is not 's3vectors'; `status` always runs.

All exit non-zero on failure (1 = the run failed, 2 = refused/usage error),
so they are safe to wire into a deploy step or cron job that should alert on
failure.
"""

import argparse
import logging
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.models import Chunk, Document, DocumentStatus
from app.services.collection_sync import CollectionSyncError, fetch_job_collections
from app.services import vector_index
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
                # Same chunk ids, new embeddings: overwrite the optional S3
                # Vectors copies (no-op with pgvector; best effort).
                vector_index.mirror_document(db, document)
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
    to_mirror: list[Document] = []
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
                to_mirror.append(document)
                logger.info('reconcile-collections: document %s (job %s) -> %s', document.id, job_id, slug)
            if dry_run:
                db.rollback()
            else:
                db.commit()
                # The `collection` metadata of their S3 Vectors copies
                # changed (no-op with pgvector; best effort).
                for document in to_mirror:
                    vector_index.mirror_document(db, document)
            to_mirror.clear()
    finally:
        db.close()

    logger.info(
        'reconcile-collections: %s %d document(s), %d remain without a space%s',
        'would assign' if dry_run else 'assigned', reconciled, len(job_ids) - reconciled,
        ' (dry run, nothing written)' if dry_run else '',
    )
    return 0


def _require_s3vectors(command: str) -> bool:
    if vector_index.enabled():
        return True
    logger.error(
        's3vectors %s: VECTOR_BACKEND is %r, not \'s3vectors\'; refusing to touch the index',
        command, settings.vector_backend,
    )
    return False


def s3vectors_init() -> int:
    """Create the S3 Vectors index if missing, else verify its data type,
    dimension (EMBEDDING_DIMENSION) and distance metric. Returns 0 on
    success, 1 on a mismatch or an S3 error, 2 if the backend is not
    s3vectors."""
    if not _require_s3vectors('init'):
        return 2
    target = f'{settings.s3vectors_bucket}/{settings.s3vectors_index}'
    try:
        outcome = vector_index.ensure_index()
    except vector_index.VectorIndexError as exc:
        logger.error(
            's3vectors init: index %s does not match this deployment: %s -- use a new S3VECTORS_INDEX, '
            'then init + sync', target, exc,
        )
        return 1
    except Exception:
        logger.exception('s3vectors init: could not create/verify index %s', target)
        return 1
    logger.info('s3vectors init: index %s %s (dimension %d, %s)', target, outcome,
                settings.embedding_dimension, vector_index.DISTANCE_METRIC)
    return 0


def s3vectors_sync(*, prune: bool = False, batch_size: int = vector_index.MAX_BATCH) -> int:
    """Upsert every eligible chunk (status 'indexed', embedding of the
    current EMBEDDING_MODEL -- see vector_index.eligible_rows) into the
    index in PutVectors batches of `batch_size`. With `prune`, list the
    index afterwards and delete every key that is not such a chunk
    (re-checked against Postgres right before deleting, so a document
    indexed during the run keeps its vectors).

    One failed batch does not abort the run; it is logged and counted.
    Returns 0 on a clean run, 1 if the index is missing/mismatched or any
    batch/prune call failed, 2 if the backend is not s3vectors. Idempotent:
    PutVectors overwrites, so a re-run after a failure is always safe.
    """
    if not _require_s3vectors('sync'):
        return 2
    try:
        index = vector_index.get_index()
    except Exception:
        logger.exception('s3vectors sync: could not read the index')
        return 1
    if index is None:
        logger.error('s3vectors sync: index does not exist; run `python -m app.cli s3vectors init` first')
        return 1
    problems = vector_index.check_index(index)
    if problems:
        logger.error('s3vectors sync: index does not match this deployment: %s', '; '.join(problems))
        return 1

    db = SessionLocal()
    written = 0
    failed = 0
    batches = 0
    eligible: set[str] = set()
    pending: list[dict] = []

    def flush() -> None:
        nonlocal written, failed, batches
        if not pending:
            return
        try:
            written += vector_index.put_records(pending, batch_size=batch_size)
        except Exception:
            failed += len(pending)
            logger.exception('s3vectors sync: PutVectors failed for %d vector(s)', len(pending))
        pending.clear()
        batches += 1
        if batches % _PROGRESS_LOG_EVERY == 0:
            logger.info('s3vectors sync: %d vector(s) written, %d failed so far', written, failed)

    pruned = 0
    prune_failed = False
    try:
        for row in vector_index.eligible_rows(db, yield_per=batch_size):
            record = vector_index.build_record(row)
            if record is None:
                continue
            eligible.add(record['key'])
            pending.append(record)
            if len(pending) >= batch_size:
                flush()
        flush()

        if prune:
            try:
                candidates = [key for key in vector_index.iter_index_keys() if key not in eligible]
                stale: list[str] = []
                for start in range(0, len(candidates), batch_size):
                    batch = candidates[start:start + batch_size]
                    still_eligible = vector_index.eligible_chunk_keys(db, batch)
                    stale.extend(key for key in batch if key not in still_eligible)
                pruned = vector_index.delete_keys(stale, batch_size=batch_size)
            except Exception:
                prune_failed = True
                logger.exception('s3vectors sync: pruning failed')
    finally:
        db.close()

    logger.info(
        's3vectors sync: done, %d vector(s) written, %d failed%s',
        written, failed, f', {pruned} pruned' if prune else '',
    )
    return 1 if failed or prune_failed else 0


def s3vectors_status(*, count_vectors: bool = False) -> int:
    """Print the backend, the index (or that it is missing) and the number
    of eligible chunks in Postgres; with `count_vectors` also the number of
    vectors in the index (lists the whole index -- one ListVectors call per
    1000 vectors). Returns 0 if the mirror is disabled or the index exists
    and matches, 1 otherwise (missing, mismatched, S3 error)."""
    cfg = settings
    print(f'vector_backend: {cfg.vector_backend}')
    if not cfg.s3vectors_bucket:
        print('s3vectors: not configured (S3VECTORS_BUCKET empty)')
        return 0 if not vector_index.enabled() else 1
    print(f'index: {cfg.s3vectors_bucket}/{cfg.s3vectors_index}')

    db = SessionLocal()
    try:
        eligible = vector_index.count_eligible(db)
    finally:
        db.close()
    print(f'eligible chunks (postgres, model {cfg.embedding_model}): {eligible}')

    try:
        index = vector_index.get_index()
        if index is None:
            print('index status: missing (run `python -m app.cli s3vectors init`)')
            return 1 if vector_index.enabled() else 0
        problems = vector_index.check_index(index)
        print(
            f'index status: dataType={index.get("dataType")} dimension={index.get("dimension")} '
            f'distanceMetric={index.get("distanceMetric")} created={index.get("creationTime")}'
        )
        for problem in problems:
            print(f'mismatch: {problem}')
        if count_vectors:
            print(f'vectors in index: {sum(1 for _ in vector_index.iter_index_keys())}')
    except Exception as exc:
        print(f'index status: error ({exc})')
        logger.debug('s3vectors status failed', exc_info=True)
        return 1
    return 1 if problems else 0


def _parse_batch_size(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'{raw!r} is not an integer') from exc
    if not 1 <= value <= vector_index.MAX_BATCH:
        raise argparse.ArgumentTypeError(f'must be between 1 and {vector_index.MAX_BATCH}')
    return value


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

    s3v_parser = subparsers.add_parser(
        's3vectors',
        help='Manage the optional Amazon S3 Vectors mirror of the chunk embeddings (VECTOR_BACKEND=s3vectors)',
    )
    s3v_commands = s3v_parser.add_subparsers(dest='s3vectors_command', required=True)
    s3v_commands.add_parser('init', help='Create the index if missing, else verify dimension and metric')
    sync_parser = s3v_commands.add_parser(
        'sync', help='Write every indexed chunk of the current embedding model into the index',
    )
    sync_parser.add_argument(
        '--prune', action='store_true',
        help='Afterwards delete every vector that is not such a chunk (withdrawn, superseded, old model)',
    )
    sync_parser.add_argument(
        '--batch', type=_parse_batch_size, default=vector_index.MAX_BATCH,
        help=(
            f'Vectors per PutVectors/DeleteVectors call (1-{vector_index.MAX_BATCH}, default %(default)s); '
            'PutVectors is further capped by EMBEDDING_DIMENSION to stay under the 20 MiB request limit'
        ),
    )
    status_parser = s3v_commands.add_parser('status', help='Show the index and counts')
    status_parser.add_argument(
        '--count-vectors', action='store_true',
        help='Also count the vectors in the index (lists the whole index)',
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
    if args.command == 's3vectors':
        if args.s3vectors_command == 'init':
            return s3vectors_init()
        if args.s3vectors_command == 'sync':
            return s3vectors_sync(prune=args.prune, batch_size=args.batch)
        return s3vectors_status(count_vectors=args.count_vectors)

    parser.error(f'unknown command {args.command!r}')  # argparse exits itself here
    return 2  # pragma: no cover -- unreachable, parser.error() calls sys.exit


if __name__ == '__main__':
    sys.exit(main())
