from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VECTOR_BACKENDS = ('pgvector', 's3vectors')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Retrieval'

    # --- Database (see app/core/db.py + app/models/models.py). This service
    # owns NO schema of its own -- `documents`/`chunks` are a shared
    # read-model whose single writer is Weave-Knowledge (see
    # contracts/chunk-store.md and ADR-0004's
    # one-database-per-service split). A real deployment therefore points
    # DATABASE_URL at the SAME `weave_knowledge` postgres database Weave-
    # Knowledge writes, but through a dedicated, read-only DB role (SELECT
    # grants only -- see the contract's "DB-Rolle" section for the exact
    # GRANT statements) rather than Weave-Knowledge's own writer
    # credentials. sqlite is the zero-setup default for local dev and the
    # pytest suite, exactly like every other Weave backend.
    database_url: str = 'sqlite:///./weave_retrieval.db'

    # --- Service-to-service auth (see app/core/auth.py). A single shared
    # Bearer token, ADR-0002's "Bearer-Token (wie in PaddleDoc)" pattern for
    # machine callers -- Weave-Retrieval sits behind Weave-API's gateway and
    # is only ever called by other Weave services (Weave-Runtime today), so
    # one static token is enough here, not a per-caller PAT registry.
    # Deliberately defaults to empty rather than a dev placeholder: an empty
    # token means require_service_token() refuses every request with 503
    # instead of silently accepting an empty bearer token as a match (see
    # that dependency's docstring) -- a real deployment MUST set this.
    retrieval_api_token: str = ''
    chat_config_base_url: str = ''
    chat_config_service_token: str = ''

    # --- Embedding provider. MUST match the model/dimension Weave-Knowledge
    # indexed chunks.embedding with -- a query embedded under a different
    # model or dimension than the stored vectors is not just less accurate,
    # it is meaningless (pgvector cosine distance between incompatible
    # embedding spaces). Same defaults as Weave-Knowledge's
    # app/core/config.py for exactly that reason; 'fake' is a deterministic,
    # dependency-free provider so local dev and the pytest suite never need
    # a real embedding API key.
    embedding_provider: str = 'fake'
    embedding_base_url: str = ''
    embedding_api_key: str = ''
    embedding_model: str = 'fake-embed'
    # Must match the pgvector column width of the `chunks.embedding` column
    # Weave-Knowledge actually created (see that service's
    # alembic/versions/0001_init.py) -- this service never migrates that
    # column itself, but VectorType (app/models/models.py) still needs the
    # right width to map it correctly.
    embedding_dimension: int = 1536
    embedding_batch_size: int = 64
    # In-process LRU+TTL cache of query embeddings (app/services/embeddings.py
    # embed_query): a repeated query skips the provider call. Keyed by the
    # query text plus provider/base_url/model/dimension read per call, so a
    # central provider change never serves a stale vector. 0 disables it.
    embedding_query_cache_size: int = Field(default=512, ge=0)
    embedding_query_cache_ttl_seconds: float = Field(default=600, ge=0)

    # --- Reranker (see app/services -- filled in a later stage). 'none'
    # skips reranking entirely: RRF-fused results are returned as-is,
    # trimmed to final_k. A real deployment can point this at a hosted
    # reranker (Cohere, jina.ai, ...) or a self-hosted one.
    rerank_provider: str = 'none'
    rerank_base_url: str = ''
    rerank_api_key: str = ''
    rerank_model: str = ''

    # --- Hybrid-search pipeline tuning (see README's "Reciprocal Rank
    # Fusion" -- filled in by app/services in a later stage).
    # search_top_k: candidates pulled from EACH of the vector/fulltext legs
    # before fusion. search_final_k: results returned after RRF fusion (and
    # optional reranking) trims the candidate set down. rrf_k: RRF's own
    # smoothing constant (`1 / (rrf_k + rank)`) -- 60 is the value used by
    # the original RRF paper and most hybrid-search implementations that
    # cite it.
    search_top_k: int = 20
    search_final_k: int = 5
    rrf_k: int = 60
    semantic_weight: float = 0.5
    lexical_weight: float = 0.5
    # Run the fulltext leg on a second DB session in a small worker pool while
    # the request thread embeds the query and runs the vector leg (see
    # app/services/search.py search()). Same results either way; False runs
    # both legs sequentially on the request session.
    search_parallel_legs: bool = True
    # Knowledge cards (POST /api/v1/memory include_cards): cards whose cosine
    # similarity to the query is below this are not returned.
    memory_card_min_similarity: float = 0.3

    # --- DB connection pool (SQLAlchemy QueuePool; see app/core/db.py).
    # Only applied for a real server backend (postgres) -- sqlite's default
    # pool implementation doesn't accept these kwargs at all, so they're
    # skipped entirely for any sqlite database_url (local dev, the pytest
    # suite). Same defaults and reasoning as Weave-Knowledge's own
    # app/core/config.py.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600
    db_pool_timeout_seconds: int = 10

    # --- Vector index backend (see app/services/vector_index.py and
    # contracts/chunk-store.md's optional S3-Vectors mirror). 'pgvector' (the
    # default) runs the vector leg exactly as before: KNN over
    # `chunks.embedding` in Postgres (or the SQLite fallback in dev/tests).
    # 's3vectors' asks an Amazon S3 Vectors index -- written ONLY by
    # Weave-Knowledge -- for candidate chunk ids instead, then ALWAYS
    # re-checks every returned id against Postgres through the very same
    # apply_filters() statement the pgvector path uses: S3 metadata is never
    # trusted for permissions, status or model match on its own, Postgres
    # stays the single source of truth. Any other value fails at startup.
    vector_backend: str = 'pgvector'
    # Vector bucket + index name. An empty bucket with
    # vector_backend='s3vectors' is a startup error (see below).
    s3vectors_bucket: str = ''
    s3vectors_index: str = 'weave-chunks'
    # Empty region/endpoint = boto3's own region resolution (AWS_DEFAULT_REGION
    # or the profile's region -- NOT AWS_REGION and not instance metadata; EKS
    # IRSA sets AWS_DEFAULT_REGION) and the real AWS endpoint; the endpoint
    # override exists for tests and emulators only.
    s3vectors_region: str = ''
    s3vectors_endpoint_url: str = ''
    # Both empty = the standard AWS credential chain (IAM role / IRSA / env /
    # instance profile) -- the recommended setup. Static keys are optional.
    # Retrieval needs s3vectors:QueryVectors and s3vectors:GetVectors (a
    # filtered query requires the latter, too).
    s3vectors_access_key_id: str = ''
    s3vectors_secret_access_key: str = ''
    # Retrieval-only tuning. A selective metadata filter can make S3 Vectors
    # return fewer than topK hits, and the mandatory Postgres recheck can drop
    # more (stale vectors of superseded/withdrawn documents, non-mirrored
    # SearchFilters fields) -- so the query over-fetches `top_k *
    # s3vectors_overfetch` candidates, capped at s3vectors_max_top_k (the
    # service's own topK limit has changed over time, hence configurable).
    s3vectors_overfetch: int = Field(default=4, ge=1, le=20)
    s3vectors_max_top_k: int = Field(default=100, ge=1)
    # On an S3 Vectors error (botocore/boto3 exception, timeout): True runs
    # the regular pgvector leg instead (trace.vector_backend =
    # 'pgvector-fallback'); False leaves the vector leg empty for this request
    # (fulltext still answers). A warning is logged either way.
    s3vectors_fallback_pgvector: bool = True

    @field_validator('vector_backend', mode='before')
    @classmethod
    def _check_vector_backend(cls, value: str) -> str:
        # Case-insensitive, and blank means the default 'pgvector' -- same
        # rule as Weave-Knowledge's settings: deploy files pass optional
        # settings as `${VAR:-}` and the Helm chart passes "" through.
        normalized = str(value).strip().lower() or 'pgvector'
        if normalized not in VECTOR_BACKENDS:
            raise ValueError(f'VECTOR_BACKEND must be one of {", ".join(VECTOR_BACKENDS)}, got {value!r}')
        return normalized

    @model_validator(mode='after')
    def _check_s3vectors_bucket(self) -> 'Settings':
        if self.vector_backend == 's3vectors' and not self.s3vectors_bucket.strip():
            raise ValueError('VECTOR_BACKEND=s3vectors requires S3VECTORS_BUCKET to be set')
        return self


settings = Settings()
