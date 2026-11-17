from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Accepted VECTOR_BACKEND values (see Settings.vector_backend below).
VECTOR_BACKENDS = ('pgvector', 's3vectors')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Knowledge'

    # --- Database (see app/core/db.py). sqlite is the zero-setup default for
    # local dev and the pytest suite; a real deployment sets DATABASE_URL to
    # a postgresql+psycopg:// URL with the pgvector extension available (see
    # alembic/versions/0001_init.py) -- see ADR-0004 for the one-database-
    # per-service split (this service owns `weave_knowledge`, never a table
    # in another service's database).
    database_url: str = 'sqlite:///./weave_knowledge.db'

    # --- Redis / Celery (see app/workers/celery_app.py). Logical DB 1, not
    # 0 -- ADR-0001 assigns Weave-Ingest DB 0 and gives every other Weave
    # service its own logical DB on the same shared Redis instance so
    # broker/result-backend traffic never collides across services.
    redis_url: str = 'redis://localhost:6379/1'

    # Signs/encrypts whatever this service persists at rest. Mirrors
    # Weave-Ingest's SECRET_KEY (see its app/core/config.py), but per ADR-0003
    # every service holds its own -- never shared across services.
    secret_key: str = 'dev-only-insecure-secret-key-do-not-use-in-production'

    # --- Service token for this service's own read API (app/core/auth.py,
    # app/api/routes.py). `GET /documents`, `/documents/{id}` and
    # `/collections` return the indexed corpus itself -- past the team and
    # `read_teams` scoping the rest of the platform enforces -- so they are
    # not a surface to leave open on a published port.
    #
    # Unset means those routes answer 503, never 200: an empty token
    # compared against a caller's empty bearer would succeed and turn a
    # forgotten variable into an open corpus. No Weave service calls these
    # routes at all (they exist for operators and debugging), so leaving it
    # unset is a perfectly reasonable deployment -- just an explicitly
    # closed one rather than an accidentally open one. `/health` and the
    # signed webhook ingress are unaffected.
    knowledge_api_token: str = ''

    # --- Weave-Ingest client (fetching an immutable markdown snapshot after
    # document.released -- see contracts/events/document.released.md). The
    # download route requires the same auth as any other Weave-Ingest API
    # call, so this service needs a real service-user token, not just a URL.
    weave_ingest_base_url: str = 'http://localhost:8000'
    weave_ingest_api_token: str = ''
    # HMAC-SHA256 shared secret used to verify the
    # X-Weave-Ingest-Signature header on an inbound document event or the
    # dedicated collection.updated notification (see contracts/events/).
    # REQUIRED: while it is unset, the event route
    # answers 503 rather than accepting unsigned events -- this route writes
    # into the index, and a forged event carries whatever markdown, team and
    # collection slug its sender picks. Must match Weave-Ingest's
    # PORTAL_KNOWLEDGE_WEBHOOK_SECRET.
    # Also authenticates the narrow indexing/status lookup with a separate
    # HMAC context. It never grants access to the corpus read API above.
    weave_ingest_webhook_secret: str = ''

    # --- Collection registry sync (app/services/collection_sync.py,
    # app/workers/collection_sync_tasks.py). Pulls Weave-Ingest's
    # authoritative `GET /api/v1/collections/registry` (same
    # weave_ingest_base_url/weave_ingest_api_token as the markdown fetch
    # above) into this service's own `collections` mirror table -- see
    # app/models/models.py's Collection docstring. How often the
    # self-re-enqueuing sync tick runs; there is no Celery Beat in this
    # deployment (see app/workers/collection_sync_tasks.py's module
    # docstring for the chain design), so this is purely a countdown
    # between one tick's self-re-enqueue and the next.
    #
    # This is a safety net, not the primary freshness mechanism: a
    # dedicated `collection.updated` notification (app/api/events.py) triggers an
    # immediate sync whenever Weave-Ingest's registry actually changes (an
    # access revocation in particular must not wait out a full tick -- see
    # that handler's docstring). 300s was too wide a residual window for a
    # permissions system even as a fallback, hence the lower default here;
    # see contracts/chunk-store.md's "Sync-Richtung" section for the
    # documented worst case.
    collection_sync_tick_seconds: int = 60

    # --- Embedding provider (see app/services -- filled in a later stage).
    # 'fake' is a deterministic, dependency-free provider so local dev and
    # the pytest suite never need a real embedding API key.
    embedding_provider: str = 'fake'
    embedding_base_url: str = ''
    embedding_api_key: str = ''
    embedding_model: str = 'fake-embed'
    # Must match the pgvector column width created by
    # alembic/versions/0001_init.py -- changing it requires a migration
    # (new vector column) plus a full reindex of existing chunks, not just an
    # env var flip (see that migration's comment on chunks.embedding).
    embedding_dimension: int = 1536
    embedding_batch_size: int = 64
    # --- Incident 2026-09-22: the embeddings service now bounds its own
    # concurrency (EMBEDDINGS_MAX_CONCURRENT_REQUESTS) and queues excess
    # requests up to EMBEDDINGS_QUEUE_TIMEOUT_SECONDS (default 90s) before
    # answering 503 -- this client's read timeout must comfortably exceed
    # that queue timeout so a queued-but-eventually-served request isn't cut
    # off client-side first. embedding_max_attempts governs the SEPARATE
    # Celery-level retry (app/workers/tasks.py's embedding-failure backoff
    # table), not this HTTP client's own small in-process retry loop (see
    # app/services/embeddings.py's _DEFAULT_MAX_ATTEMPTS).
    embedding_request_timeout_seconds: float = 180.0
    embedding_max_attempts: int = 6
    chat_config_base_url: str = ''
    chat_config_service_token: str = ''

    # --- Knowledge memory (ADR-0009, app/services/memory.py): condensed
    # summaries per document and collection. 'off' (default) builds nothing;
    # 'extractive' needs no LLM; 'llm' uses the central chat provider read
    # through chat_config_base_url/chat_config_service_token above and falls
    # back to extractive per entry whenever that model is missing or fails.
    knowledge_memory_mode: str = 'off'
    # Named central LLM endpoint (Weave-Ingest › Chat & LLM) for mode 'llm'.
    # These memory_* values are overridden by Weave-Ingest's admin settings
    # (GET /api/v1/internal/knowledge-memory) once an admin saved them there.
    memory_llm_endpoint: str = 'default'
    memory_debounce_seconds: int = 120
    memory_max_documents_per_run: int = 25
    memory_max_input_chars: int = 12000
    memory_llm_timeout_seconds: float = 120.0

    # --- Contradiction candidates (app/services/contradictions.py, run at
    # the end of app/workers/tasks.py's index pipeline). A HEURISTIC that
    # only flags *possible* contradictions between documents of the same
    # knowledge space for human review; results are reported best-effort to
    # Weave-Ingest's POST /api/v1/quality/contradictions with the same
    # weave_ingest_base_url/weave_ingest_api_token as the markdown fetch
    # (app/services/ingest_client.py's report_contradictions). Never fails
    # or delays an index run beyond the check itself. Similarity is cosine
    # similarity of two chunk embeddings (0..1); keep it high -- lower
    # values flood the review inbox with near-misses.
    contradiction_check_enabled: bool = True
    contradiction_min_similarity: float = 0.86
    contradiction_max_reports_per_document: int = 10

    # --- Optional vector index mirror: Amazon S3 Vectors (see
    # app/services/vector_index.py, contracts/chunk-store.md's "Optionaler
    # Vektorindex" section). 'pgvector' (default) changes nothing at all:
    # the chunks.embedding column in Postgres stays the only vector index,
    # and boto3 is never even imported. 's3vectors' additionally mirrors
    # every embedded chunk into an S3 Vectors index after each successful
    # index/reindex commit and deletes the vectors of chunks this service
    # deletes (re-index, supersede, withdrawal) -- Weave-Retrieval then runs
    # its vector leg against that index and re-checks every hit in Postgres.
    # Postgres stays the single source of truth either way: chunks,
    # embeddings, permissions and fulltext never leave it, the mirror is
    # best-effort (an S3 error is logged, never fails an index run) and
    # `python -m app.cli s3vectors sync` repairs any drift. The same setting
    # names exist in Weave-Retrieval's app/core/config.py.
    #
    # Must be one of VECTOR_BACKENDS above (case-insensitive; empty means
    # 'pgvector'); anything else fails at startup (see _check_vector_backend
    # below), as does 's3vectors' without a bucket or with only one of the
    # two static keys.
    vector_backend: str = 'pgvector'
    s3vectors_bucket: str = ''
    s3vectors_index: str = 'weave-chunks'
    # Empty = boto3's own region resolution: AWS_DEFAULT_REGION or the
    # profile's region -- NOT AWS_REGION and not instance metadata (EKS IRSA
    # sets AWS_DEFAULT_REGION; everywhere else set this explicitly).
    s3vectors_region: str = ''
    # Empty = the real AWS endpoint; only set for tests or an emulator.
    s3vectors_endpoint_url: str = ''
    # Both empty (the recommended setup) = the standard boto3 credential
    # chain: IAM role / IRSA / environment / instance profile. Static keys
    # are only an escape hatch for environments without any of those.
    s3vectors_access_key_id: str = ''
    s3vectors_secret_access_key: str = ''

    # --- Structure-aware chunking (see app/services -- filled in a later
    # stage). Chars, not tokens: cheap to compute against raw markdown
    # without pulling in a tokenizer at this layer.
    chunk_max_chars: int = 3000
    chunk_overlap_chars: int = 200

    # --- DB connection pool (SQLAlchemy QueuePool; see app/core/db.py).
    # Only applied for a real server backend (postgres) -- sqlite's default
    # pool implementation doesn't accept these kwargs at all, so they're
    # skipped entirely for any sqlite database_url (local dev, the pytest
    # suite). Same defaults and reasoning as Weave-Ingest's
    # app/core/config.py.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600
    db_pool_timeout_seconds: int = 10

    @field_validator('vector_backend', mode='before')
    @classmethod
    def _normalize_vector_backend(cls, value):
        """Case-insensitive, and an empty/blank value means the default
        'pgvector' -- deploy files pass optional settings as `${VAR:-}`,
        which yields '' when the variable is unset."""
        if isinstance(value, str):
            return value.strip().lower() or 'pgvector'
        return value

    @model_validator(mode='after')
    def _check_vector_backend(self) -> 'Settings':
        """Fail at startup (module import builds `settings` below) instead
        of at the first index run: a typo in VECTOR_BACKEND would otherwise
        silently keep pgvector-only behaviour, and 's3vectors' without a
        bucket would only surface as a warning per document."""
        if self.vector_backend not in VECTOR_BACKENDS:
            raise ValueError(
                f'VECTOR_BACKEND must be one of {", ".join(VECTOR_BACKENDS)}, got {self.vector_backend!r}'
            )
        if self.vector_backend == 's3vectors' and not self.s3vectors_bucket.strip():
            raise ValueError('VECTOR_BACKEND=s3vectors requires S3VECTORS_BUCKET')
        # Static keys are used only as a pair (vector_index.client()); one
        # without the other would silently fall back to the default
        # credential chain. Only checked when the mirror is on, so stray
        # S3VECTORS_* values never stop a pgvector deployment.
        if self.vector_backend == 's3vectors' and (
            bool(self.s3vectors_access_key_id.strip()) != bool(self.s3vectors_secret_access_key.strip())
        ):
            raise ValueError(
                'S3VECTORS_ACCESS_KEY_ID and S3VECTORS_SECRET_ACCESS_KEY must be set together (or both left empty '
                'for the default AWS credential chain)'
            )
        return self


settings = Settings()
