"""Optional mirror of the chunk embeddings into an Amazon S3 Vectors index.

Only active with VECTOR_BACKEND=s3vectors (see app/core/config.py); with the
default 'pgvector' every public function here is a no-op and boto3 is never
imported -- the import happens lazily inside client(), so a pgvector-only
deployment does not even need the package to load.

Postgres stays the single source of truth (contracts/chunk-store.md,
"Optionaler Vektorindex: Amazon S3 Vectors"): chunks, embeddings, fulltext,
metadata and permissions never leave it. The index is a derived copy that
Weave-Retrieval queries for its vector leg and then re-checks hit by hit
against Postgres -- so a stale vector (a chunk deleted here but not yet in
S3) is dropped by that recheck, and a missing vector only costs recall until
`python -m app.cli s3vectors sync` repairs it. That is why every write from
the index pipeline / webhook path goes through the best-effort wrappers
mirror_document() and forget_keys(): an S3 error is logged as a warning and
never fails, delays or rolls back indexing or a withdrawal. The raising
primitives (upsert_document, delete_keys, ensure_index, ...) are for the
CLI, which must report failures through its exit code.

Vector record (the shared contract with Weave-Retrieval):

* key = str(chunks.id)
* data.float32 = chunks.embedding (chunks without an embedding are skipped)
* metadata (filterable unless noted): `collection` = documents.collection_slug
  or NONE_COLLECTION for a document without a space; `team` =
  documents.team or ''; `document_id` = str(documents.id);
  `embedding_model` = chunks.embedding_model; `chunk_index` (non-filterable)

Only chunks of documents with status 'indexed' whose embedding_model equals
the current EMBEDDING_MODEL are mirrored (eligible_rows()) -- the same
population Retrieval's Postgres recheck accepts, so the index never holds
vectors that recheck would drop by construction.

Parameter names follow botocore's bundled `s3vectors` service model
(PutVectors/DeleteVectors max 500 entries per call, ListVectors max 1000 per
page). A PutVectors request body is additionally capped at 20 MiB, which a
500-vector batch exceeds above roughly 1900 dimensions -- put_batch_size()
derives the effective PutVectors batch from EMBEDDING_DIMENSION.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus

logger = logging.getLogger(__name__)

# `collection` metadata value for documents without a knowledge space --
# S3 Vectors metadata filters cannot match a missing key with $in, so the
# "no space" case gets an explicit sentinel Retrieval's filter builder
# matches with $eq (see contracts/chunk-store.md).
NONE_COLLECTION = '__none__'

# PutVectors / DeleteVectors accept at most this many entries per call.
MAX_BATCH = 500

# PutVectors request bodies are capped at 20 MiB. botocore serializes the
# body with json.dumps' default separators; a float32 value widened to a
# Python float is at most 23 characters (e.g. '-1.2345678337905474e-05'),
# plus ', ' -- 25 bytes per dimension worst case. The per-record allowance
# covers key, metadata and JSON punctuation (S3 Vectors caps filterable
# metadata at 2 KB per vector; ours is a few short strings). The budget
# leaves ~2 MiB of the limit as headroom.
PUT_REQUEST_BUDGET_BYTES = 18 * 1024 * 1024
_BYTES_PER_DIMENSION = 25
_BYTES_PER_RECORD_OVERHEAD = 2048

# Index shape this service writes (CreateIndex / the `init` verification).
DATA_TYPE = 'float32'
DISTANCE_METRIC = 'cosine'
NON_FILTERABLE_METADATA_KEYS = ['chunk_index']

_client: Any = None
_client_key: tuple | None = None
# Guards the check-and-create in client(): the withdrawal webhook path runs
# in FastAPI's threadpool, and concurrent first calls must not race on
# client construction.
_client_lock = threading.Lock()


class VectorIndexError(Exception):
    """The S3 Vectors index exists but cannot be used as configured (e.g. a
    dimension or distance-metric mismatch found by ensure_index())."""


def enabled() -> bool:
    return settings.vector_backend == 's3vectors'


def client():
    """The boto3 `s3vectors` client built from settings, cached per
    configuration (a settings change -- tests, a reloaded config -- builds a
    new one). Region, endpoint and static keys are only passed when set, so
    empty values leave boto3's default chain in charge (IAM role / IRSA /
    environment / instance profile)."""
    global _client, _client_key
    key = (
        settings.s3vectors_region,
        settings.s3vectors_endpoint_url,
        settings.s3vectors_access_key_id,
        settings.s3vectors_secret_access_key,
    )
    with _client_lock:
        if _client is not None and _client_key == key:
            return _client

        import boto3  # lazy: never imported in pgvector mode
        from botocore.config import Config

        kwargs: dict[str, Any] = {
            # Bounded so a slow/unreachable endpoint cannot hold an index
            # run or a CLI call for botocore's 60s defaults. (The withdrawal
            # webhook does not call S3 at all -- it hands the keys to the
            # delete_vectors Celery task, see app/workers/tasks.py.)
            'config': Config(connect_timeout=5, read_timeout=30, retries={'max_attempts': 3, 'mode': 'standard'}),
        }
        if settings.s3vectors_region:
            kwargs['region_name'] = settings.s3vectors_region
        if settings.s3vectors_endpoint_url:
            kwargs['endpoint_url'] = settings.s3vectors_endpoint_url
        if settings.s3vectors_access_key_id and settings.s3vectors_secret_access_key:
            kwargs['aws_access_key_id'] = settings.s3vectors_access_key_id
            kwargs['aws_secret_access_key'] = settings.s3vectors_secret_access_key
        # A dedicated Session: boto3's shared default session is not
        # thread-safe.
        _client = boto3.session.Session().client('s3vectors', **kwargs)
        _client_key = key
        return _client


def _index_params() -> dict[str, str]:
    return {'vectorBucketName': settings.s3vectors_bucket, 'indexName': settings.s3vectors_index}


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        return (response.get('Error') or {}).get('Code')
    return None


# --- Records ---------------------------------------------------------------


def eligible_rows(db: Session, *, document_id: uuid.UUID | None = None, yield_per: int = 500):
    """(chunk id, chunk_index, embedding, embedding_model, document id,
    collection_slug, team) of every chunk that belongs in the index -- see
    the module docstring. Only the columns a record needs, never the chunk
    text. `embedding IS NULL` is still re-checked per row in build_record():
    on SQLite the JSON fallback column can hold a JSON null."""
    query = (
        select(
            Chunk.id, Chunk.chunk_index, Chunk.embedding, Chunk.embedding_model,
            Document.id, Document.collection_slug, Document.team,
        )
        .join(Document, Chunk.document_id == Document.id)
        .where(*_eligible_conditions())
        .order_by(Chunk.id)
    )
    if document_id is not None:
        query = query.where(Chunk.document_id == document_id)
    return db.execute(query.execution_options(yield_per=yield_per))


def _eligible_conditions() -> tuple:
    return (
        Document.status == DocumentStatus.INDEXED,
        Chunk.embedding.is_not(None),
        Chunk.embedding_model == settings.embedding_model,
    )


def count_eligible(db: Session) -> int:
    """Number of eligible chunks (for `s3vectors status`). Counted in SQL,
    so on SQLite a JSON-null embedding is included -- an upper bound there."""
    query = select(func.count(Chunk.id)).join(Document, Chunk.document_id == Document.id).where(*_eligible_conditions())
    return int(db.execute(query).scalar_one())


def eligible_chunk_keys(db: Session, keys: Iterable[str]) -> set[str]:
    """The subset of `keys` that are eligible chunks right now -- the
    `sync --prune` re-check that keeps a chunk indexed after the prune's
    snapshot from being pruned. Keys that are not chunk ids are never
    eligible."""
    ids = set()
    for key in keys:
        try:
            ids.add(int(key))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    query = (
        select(Chunk.id)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(sorted(ids)), *_eligible_conditions())
    )
    return {str(chunk_id) for chunk_id in db.execute(query).scalars()}


def build_record(row) -> dict | None:
    """One PutVectors entry for an eligible_rows() row, or None if the row
    has no usable embedding (NULL, or a width other than
    EMBEDDING_DIMENSION, which S3 would reject for the whole batch)."""
    chunk_id, chunk_index, embedding, embedding_model, document_id, collection_slug, team = row
    if embedding is None:
        return None
    vector = [float(value) for value in embedding]  # pgvector loads a numpy array
    if len(vector) != settings.embedding_dimension:
        logger.warning(
            's3vectors: chunk %s has %d dimensions, index expects %d; skipped',
            chunk_id, len(vector), settings.embedding_dimension,
        )
        return None
    return {
        'key': str(chunk_id),
        'data': {'float32': vector},
        'metadata': {
            'collection': collection_slug or NONE_COLLECTION,
            'team': team or '',
            'document_id': str(document_id),
            'embedding_model': embedding_model or '',
            'chunk_index': chunk_index,
        },
    }


def put_batch_size(requested: int = MAX_BATCH) -> int:
    """The PutVectors batch size actually used for `requested`: at most
    MAX_BATCH vectors and at most what fits PUT_REQUEST_BUDGET_BYTES at
    EMBEDDING_DIMENSION (500 up to ~1400 dimensions, 466 at 1536, 180 at 4096)."""
    per_record = settings.embedding_dimension * _BYTES_PER_DIMENSION + _BYTES_PER_RECORD_OVERHEAD
    return max(1, min(requested, MAX_BATCH, PUT_REQUEST_BUDGET_BYTES // per_record))


def put_records(records: list[dict], *, batch_size: int = MAX_BATCH) -> int:
    """PutVectors in batches of at most `batch_size`, capped by
    put_batch_size() (count and request-size limit). Raises on the first
    failed call."""
    batch_size = put_batch_size(batch_size)
    for start in range(0, len(records), batch_size):
        client().put_vectors(**_index_params(), vectors=records[start:start + batch_size])
    return len(records)


def upsert_document(db: Session, document: Document, *, batch_size: int = MAX_BATCH) -> int:
    """Put every eligible chunk of `document` (see eligible_rows()); returns
    the number of vectors written. PutVectors overwrites an existing key, so
    this is idempotent. Raises on an S3 error."""
    records = [record for record in map(build_record, eligible_rows(db, document_id=document.id)) if record]
    return put_records(records, batch_size=batch_size)


def delete_keys(keys: Iterable[str], *, batch_size: int = MAX_BATCH) -> int:
    """DeleteVectors in batches of at most MAX_BATCH; deleting a key that is
    not in the index is not an error. Raises on an S3 error."""
    keys = list(dict.fromkeys(str(key) for key in keys))
    batch_size = max(1, min(batch_size, MAX_BATCH))
    for start in range(0, len(keys), batch_size):
        client().delete_vectors(**_index_params(), keys=keys[start:start + batch_size])
    return len(keys)


def chunk_keys(db: Session, document_id: uuid.UUID) -> list[str]:
    """Keys of every chunk `document_id` currently has -- collected by the
    call sites right BEFORE they delete those chunks, so the vectors can be
    deleted once that delete is committed. Empty (and no query at all) when
    the mirror is disabled."""
    if not enabled():
        return []
    return [str(chunk_id) for chunk_id in db.execute(select(Chunk.id).where(Chunk.document_id == document_id)).scalars()]


# --- Best-effort wrappers for the index pipeline / webhook path --------------


def mirror_document(db: Session, document: Document, *, stale_keys: Iterable[str] = ()) -> None:
    """After a committed index/reindex: delete `stale_keys` (the document's
    chunk keys before the rewrite, plus a superseded version's) that are not
    current chunk keys anymore, then put the document's current chunks.

    Deleting first matters on SQLite, where a rewritten chunk can get the id
    a deleted one had: the fresh vector must not be removed afterwards.
    Best effort, see the module docstring: never raises."""
    if not enabled():
        return
    document_id = document.id
    try:
        current = set(chunk_keys(db, document_id))
        stale = [key for key in stale_keys if key not in current]
        if stale:
            delete_keys(stale)
        written = upsert_document(db, document)
        logger.info('s3vectors: document %s mirrored (%d vector(s), %d stale deleted)', document_id, written, len(stale))
    except Exception:  # noqa: BLE001 - best effort, see module docstring
        logger.warning(
            's3vectors: mirroring document %s failed; run `python -m app.cli s3vectors sync` to repair',
            document_id, exc_info=True,
        )


def forget_keys(keys: Iterable[str], *, document_id: Any) -> None:
    """After a committed chunk delete (withdrawal, supersede): delete the
    vectors. Best effort, see the module docstring: never raises."""
    keys = list(keys)
    if not enabled() or not keys:
        return
    try:
        delete_keys(keys)
        logger.info('s3vectors: deleted %d vector(s) of document %s', len(keys), document_id)
    except Exception:  # noqa: BLE001 - best effort, see module docstring
        logger.warning(
            's3vectors: deleting %d vector(s) of document %s failed; '
            'run `python -m app.cli s3vectors sync --prune` to repair',
            len(keys), document_id, exc_info=True,
        )


# --- Index management (CLI) --------------------------------------------------


def get_index() -> dict | None:
    """GetIndex's `index` dict, or None if the index does not exist."""
    try:
        return client().get_index(**_index_params())['index']
    except Exception as exc:
        if _error_code(exc) == 'NotFoundException':
            return None
        raise


def check_index(index: dict) -> list[str]:
    """Human-readable mismatches between an existing index and what this
    service writes (empty list = usable)."""
    problems = []
    if index.get('dataType') != DATA_TYPE:
        problems.append(f'dataType is {index.get("dataType")!r}, expected {DATA_TYPE!r}')
    if index.get('dimension') != settings.embedding_dimension:
        problems.append(
            f'dimension is {index.get("dimension")!r}, expected {settings.embedding_dimension} (EMBEDDING_DIMENSION)'
        )
    if index.get('distanceMetric') != DISTANCE_METRIC:
        problems.append(f'distanceMetric is {index.get("distanceMetric")!r}, expected {DISTANCE_METRIC!r}')
    return problems


def ensure_index() -> str:
    """Create the index if missing ('created'), else verify it ('exists').
    Raises VectorIndexError on a mismatch -- an index of the wrong dimension
    or metric cannot be fixed in place; a model/dimension change means a new
    index name (S3VECTORS_INDEX) plus `sync`."""
    index = get_index()
    if index is None:
        client().create_index(
            **_index_params(),
            dataType=DATA_TYPE,
            dimension=settings.embedding_dimension,
            distanceMetric=DISTANCE_METRIC,
            metadataConfiguration={'nonFilterableMetadataKeys': list(NON_FILTERABLE_METADATA_KEYS)},
        )
        return 'created'
    problems = check_index(index)
    if problems:
        raise VectorIndexError('; '.join(problems))
    return 'exists'


def iter_index_keys(*, page_size: int = 1000) -> Iterator[str]:
    """Every key in the index (ListVectors, paginated, keys only)."""
    paginator = client().get_paginator('list_vectors')
    for page in paginator.paginate(
        **_index_params(), returnData=False, returnMetadata=False,
        PaginationConfig={'PageSize': max(1, min(page_size, 1000))},
    ):
        for vector in page.get('vectors') or []:
            yield vector['key']
