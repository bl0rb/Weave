from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
