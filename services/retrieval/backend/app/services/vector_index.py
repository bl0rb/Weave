"""Read side of the optional Amazon S3 Vectors index (settings.vector_backend
== 's3vectors'; the default 'pgvector' never imports or touches anything
here beyond enabled()).

The index is an additive MIRROR of `chunks.embedding` documented in
contracts/chunk-store.md: Weave-Knowledge is its only writer, this service
only ever queries it. One vector per chunk with an embedding:

- key = `str(chunks.id)`
- data float32 = the chunk's embedding
- metadata (filterable): `collection` (documents.collection_slug, or the
  sentinel "__none__" for documents without a collection), `team`
  (documents.team or ""), `document_id` (str(documents.id)),
  `embedding_model` (chunks.embedding_model); non-filterable `chunk_index`.

Postgres stays authoritative. The S3 metadata filter built here only NARROWS
the candidate set so the over-fetched topK is spent on plausibly in-scope
chunks; app/services/search.py re-checks every returned id against Postgres
through the very same apply_filters() statement the pgvector path uses, so a
stale vector (superseded/withdrawn document, changed team or collection, a
model change) can cost recall for one query but can never leak a chunk.

boto3/botocore are imported lazily inside client(): the pgvector
default must not depend on them being importable at all.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from app.core.config import settings
from app.schemas.search import SearchFilters

logger = logging.getLogger(__name__)

# Same string as app.services.search.NO_COLLECTION_SENTINEL (and the value
# Weave-Knowledge stores in the `collection` metadata key for documents
# without a collection) -- defined here too rather than imported, so this
# module stays importable without the search module (no import cycle).
NO_COLLECTION_SENTINEL = '__none__'


class VectorIndexError(Exception):
    """Any failure talking to S3 Vectors (botocore/boto3 exception, timeout,
    unexpected response shape, boto3 not installed). search.py turns it into
    the pgvector fallback or an empty vector leg -- never a failed request.
    """


def enabled() -> bool:
    """True when the vector leg should query S3 Vectors. Read fresh from
    settings on every call so tests can monkeypatch the backend."""
    return settings.vector_backend == 's3vectors'


# Time budget of ONE QueryVectors call, kept well under the callers' own
# search timeout (Weave-Runtime's retrieval_timeout_seconds is 10s, and the
# query embedding runs before this): worst case ~1s connect + ~2s read, NO
# botocore retries (total_max_attempts=1; botocore's plain `max_attempts`
# would count RETRIES on top of the first call) -- so a slow or
# throttling S3 Vectors fails within ~3s and the pgvector fallback (or the
# fulltext-only answer) still reaches the caller in time.
_CONNECT_TIMEOUT_SECONDS = 1
_READ_TIMEOUT_SECONDS = 2
_TOTAL_MAX_ATTEMPTS = 1
# After a failed query, skip S3 Vectors entirely for this many seconds
# (query() raises VectorIndexError immediately), so a brownout costs the
# timeout budget above once per window instead of on every search.
_FAILURE_COOLDOWN_SECONDS = 30.0

_client_lock = threading.Lock()
_client_cache: dict[tuple[str, str, str, str], object] = {}
_cooldown_until = 0.0


def _build_client(region: str, endpoint_url: str, access_key_id: str, secret_access_key: str):
    import boto3
    from botocore.config import Config

    kwargs: dict = {
        # Search is synchronous and user-facing: fail fast and let the
        # caller fall back rather than hang on botocore's 60s defaults.
        'config': Config(
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_READ_TIMEOUT_SECONDS,
            retries={'total_max_attempts': _TOTAL_MAX_ATTEMPTS, 'mode': 'standard'},
        ),
    }
    if region:
        kwargs['region_name'] = region
    if endpoint_url:
        kwargs['endpoint_url'] = endpoint_url
    if access_key_id and secret_access_key:
        kwargs['aws_access_key_id'] = access_key_id
        kwargs['aws_secret_access_key'] = secret_access_key
    # A PRIVATE Session, never boto3's shared DEFAULT_SESSION: Sessions are
    # not thread-safe, and /search runs in FastAPI's threadpool.
    return boto3.session.Session().client('s3vectors', **kwargs)


def client():
    """The process-wide boto3 `s3vectors` client for the current settings.
    Empty region/endpoint/keys fall through to boto3's standard default
    chain (IAM role / IRSA / env / instance profile). Built once per
    settings tuple under a lock, so concurrent first requests from the
    threadpool never race inside botocore's session setup (the known
    KeyError('credential_provider') race); the finished client itself is
    thread-safe."""
    key = (
        settings.s3vectors_region,
        settings.s3vectors_endpoint_url,
        settings.s3vectors_access_key_id,
        settings.s3vectors_secret_access_key,
    )
    with _client_lock:
        cached = _client_cache.get(key)
        if cached is None:
            cached = _client_cache[key] = _build_client(*key)
        return cached


def reset_cooldown() -> None:
    """Forget a previous failure (tests; never needed in production)."""
    global _cooldown_until
    _cooldown_until = 0.0


def _in(key: str, values: list[str]) -> dict:
    return {key: {'$in': list(values)}}


def _eq(key: str, value: str) -> dict:
    return {key: {'$eq': value}}


def _and(clauses: list[dict]) -> dict:
    return clauses[0] if len(clauses) == 1 else {'$and': clauses}


def build_filter(
    allowed_teams: list[str] | None,
    allowed_collections: list[str] | None,
    filters: SearchFilters | None,
    embedding_model: str,
) -> dict | None:
    """The S3 Vectors metadata filter mirroring app.services.search.
    apply_filters()'s scope semantics, or `None` when the effective scope is
    provably empty -- the caller must then NOT query S3 at all (Postgres
    would return zero rows for the same request, too).

    - always `embedding_model $eq <embedding_model>`;
    - `allowed_collections is None`: no collection constraint; `allowed_teams`
      (if not None) applies to every row (`team $in`, `[]` = empty scope);
    - concrete `allowed_collections`: `collection $in <real slugs>` OR (only
      if NO_COLLECTION_SENTINEL is listed) `collection $eq "__none__"` AND
      (if `allowed_teams` is not None) `team $in allowed_teams` -- rows inside
      a collection are decided by `allowed_collections` alone (ADR 0008);
    - refinements: `filters.team` -> AND `team $eq`; `filters.collection`
      -> AND `collection $eq` (the sentinel
      never names a real collection_slug in Postgres, so it is an empty
      scope there and here); `filters.document_ids` -> AND `document_id $in`
      over the VALID UUIDs only (none valid = empty scope).

    Every other SearchFilters field (department, source, language,
    document_type, tags) and `documents.status` are applied ONLY by the
    mandatory Postgres recheck -- that is a deliberate recall-for-simplicity
    trade-off covered by the over-fetch factor, never a security gap.
    """
    clauses: list[dict] = [_eq('embedding_model', embedding_model)]

    if allowed_collections is None:
        if allowed_teams is not None:
            if not allowed_teams:
                return None
            clauses.append(_in('team', allowed_teams))
    else:
        real_slugs = [slug for slug in allowed_collections if slug != NO_COLLECTION_SENTINEL]
        scope: list[dict] = []
        if real_slugs:
            scope.append(_in('collection', real_slugs))
        if NO_COLLECTION_SENTINEL in allowed_collections and allowed_teams != []:
            legacy = _eq('collection', NO_COLLECTION_SENTINEL)
            if allowed_teams is not None:
                legacy = {'$and': [legacy, _in('team', allowed_teams)]}
            scope.append(legacy)
        if not scope:
            return None
        clauses.append(scope[0] if len(scope) == 1 else {'$or': scope})

    if filters is not None:
        if filters.team is not None:
            # Safe narrowing: Postgres compares Document.team == filters.team
            # and Knowledge mirrors `documents.team or ""` into `team`.
            clauses.append(_eq('team', filters.team))
        if filters.collection is not None:
            if filters.collection == NO_COLLECTION_SENTINEL:
                return None
            clauses.append(_eq('collection', filters.collection))
        if filters.document_ids is not None:
            document_ids = []
            for value in filters.document_ids:
                try:
                    document_ids.append(str(uuid.UUID(str(value))))
                except ValueError:
                    continue
            if not document_ids:
                return None
            clauses.append(_in('document_id', document_ids))

    return _and(clauses)


def query(vector: list[float], top_k: int, filter: dict | None) -> list[tuple[int, float]]:
    """Nearest neighbours of `vector` in the configured index, best first:
    `(chunk_id, cosine distance)` pairs as S3 Vectors returned them. Keys that
    are not chunk ids (foreign/garbage vectors) are skipped. Raises
    VectorIndexError for EVERY failure -- boto3/botocore missing, client
    construction, the call itself, an unexpected response -- so the search
    never fails because of S3 Vectors. After a failure, further calls raise
    immediately for _FAILURE_COOLDOWN_SECONDS (see above)."""
    global _cooldown_until
    if time.monotonic() < _cooldown_until:
        raise VectorIndexError('skipped: cooling down after a recent S3 Vectors failure')
    try:
        return _query(vector, top_k, filter)
    except Exception as exc:
        _cooldown_until = time.monotonic() + _FAILURE_COOLDOWN_SECONDS
        if isinstance(exc, VectorIndexError):
            raise
        raise VectorIndexError(f'{type(exc).__name__}: {exc}') from exc


def _query(vector: list[float], top_k: int, filter: dict | None) -> list[tuple[int, float]]:
    params: dict = {
        'vectorBucketName': settings.s3vectors_bucket,
        'indexName': settings.s3vectors_index,
        'queryVector': {'float32': [float(x) for x in vector]},
        'topK': top_k,
        'returnDistance': True,
        'returnMetadata': False,
    }
    if filter is not None:
        params['filter'] = filter

    response = client().query_vectors(**params)

    results: list[tuple[int, float]] = []
    try:
        for item in response.get('vectors') or []:
            try:
                chunk_id = int(item['key'])
            except (KeyError, TypeError, ValueError):
                logger.warning('s3vectors: ignoring vector with non-chunk key %r', item.get('key'))
                continue
            results.append((chunk_id, float(item.get('distance', 1.0))))
    except (AttributeError, TypeError) as exc:
        raise VectorIndexError(f'unexpected QueryVectors response: {exc}') from exc
    return results
