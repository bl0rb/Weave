from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
