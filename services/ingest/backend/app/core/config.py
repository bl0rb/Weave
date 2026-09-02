from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Ingest API'
    database_url: str = ''
    postgres_host: str = ''
    postgres_port: int = 5432
    postgres_db: str = ''
    postgres_user: str = ''
    postgres_password: str = ''
    redis_url: str = 'redis://redis:6379/0'
    cors_origins: list[str] = ['http://localhost:3000']
    max_upload_bytes: int = 100 * 1024 * 1024
    rate_limit_per_minute: int = 60
    # Reverse-proxy trust for X-Forwarded-For/X-Real-IP (used to key the
    # rate limiter -- see app/services/security.py:_client_id_from_request).
    # Those headers are attacker-controlled unless the direct TCP peer is
    # itself one of our own proxies, so they're only honored when the peer
    # matches an entry here. Individual IPs or CIDR ranges (e.g. the
    # in-cluster pod CIDR an ingress-controller/LB connects from). Empty
    # (the default) means never trust the headers -- the limiter falls back
    # to keying on the direct TCP peer, which is safe but coarse (shared
    # bucket per proxy) until this is configured for the deployment.
    trusted_proxy_ips: list[str] = []
    # How many trusted-proxy hops are expected to have appended to
    # X-Forwarded-For (e.g. 1 for a single ingress-controller/ALB in front
    # of the pod). The limiter reads the hop this many positions in from
    # the right of the comma-separated chain, not the leftmost (client-
    # supplied, spoofable) entry.
    trusted_proxy_hops: int = 1
    uploads_dir: Path = Path('backend/storage/uploads')
    results_dir: Path = Path('backend/storage/results')
    paddle_default_profile: str = 'ppocrv6_tiny'
    paddle_timeout_seconds: int = 300
    worker_concurrency: int = 1

    # Celery task hard/soft time limits. Long OCR jobs on CPU can legitimately
    # run for many minutes, but a hung/stuck task (e.g. a wedged onnxruntime
    # call) should eventually be killed rather than block a worker slot
    # forever. Soft raises SoftTimeLimitExceeded inside the task (catchable
    # for cleanup); hard SIGKILLs the worker child. Defaults: 25min/30min.
    celery_task_soft_time_limit_seconds: int = 1500
    celery_task_time_limit_seconds: int = 1800
    # Redis broker visibility_timeout: how long a task can be "invisible"
    # (claimed by a worker) before Redis assumes the worker died and
    # redelivers it to another worker. Must be >= celery_task_time_limit_seconds
    # -- otherwise a still-running long OCR task gets redelivered and
    # processed a second time before the first attempt's hard limit even
    # fires. celery_app.py also clamps this defensively at startup.
    celery_broker_visibility_timeout_seconds: int = 1800
    openai_api_base_url: str = ''
    openai_api_bearer_token: str = ''
    # Hostnames ('host' or 'host:port') of private-network VL endpoints
    # (self-hosted vLLM/Ollama/LiteLLM) that outbound benchmark and test
    # requests may reach. Same mechanism and parsing as
    # import_private_host_allowlist: passed into safe_fetch as
    # allowed_private_hosts and re-checked on every redirect hop, while
    # cloud-metadata addresses stay blocked regardless. Empty (the default)
    # means public endpoints only.
    vl_private_host_allowlist: list[str] = []

    # Central OpenAI-compatible provider configured in the Admin UI. Private
    # endpoints must be named explicitly; safe_fetch still blocks metadata
    # targets and revalidates every redirect hop.
    chat_llm_private_host_allowlist: list[str] = []
    # Shared bearer credential presented by Weave-Runtime when reading the
    # decrypted effective configuration. Empty fails closed with 503.
    chat_config_service_token: str = ''

    # Celery worker log capture into worker_log_entries (see
    # app/workers/log_capture.py) -- lets the admin UI tail worker container
    # logs without docker.sock/kubectl access, which the EKS/k8s deployment
    # has no portable equivalent for. Only the worker process wires the
    # handler (celery_app.py); these two settings are inert in the backend.
    # Minimum stdlib logging level mirrored to the DB -- independent of
    # --loglevel (stdout, still CELERY_LOG_LEVEL in worker.Dockerfile): if
    # this is set MORE verbose than --loglevel, log_capture.py lowers the
    # worker's root logger floor to match so records actually get
    # constructed, while pinning --loglevel's own console/stream handler(s)
    # so console verbosity doesn't change.
    worker_log_capture_level: str = 'INFO'
    # Row-count retention cap, enforced opportunistically by the handler
    # itself on write (there is no beat/cron worker in this deployment to
    # run a scheduled prune job).
    worker_log_retention_max_rows: int = 20000

    # Session-cookie signing, OIDC state HMAC, and the key material Fernet
    # client-secret encryption is derived from (see app/services/security.py).
    # Required in any real (postgres) deployment -- see
    # _resolve_secret_key below, which fails fast rather than silently
    # running with a guessable key.
    secret_key: str = ''
    # Base URL the API is publicly reachable at; used to build the OIDC
    # redirect_uri (`{public_api_url}/api/v1/auth/oidc/{slug}/callback`).
    public_api_url: str = 'http://localhost:8000'
    # Fixed Weave-Knowledge publication target. Both values must be present
    # before the portal release API mutates the durable outbox. The same
    # address/secret sign the bounded indexing-status lookup (separate HMAC
    # context), so the portal does not need Knowledge corpus-read access.
    portal_knowledge_base_url: str = ''
    portal_knowledge_webhook_secret: str = ''
    publication_tick_seconds: int = 30
    publication_lease_seconds: int = 120
    publication_max_attempts: int = 5

    # --- Cross-service login handoff (app/api/auth.py's /auth/handoff/*) ---
    #
    # This service is the platform's identity provider: Weave-API -- and
    # through it the chat UI -- has no user database of its own and asks
    # here instead, so users, teams and OIDC connections are administered
    # in exactly one place.
    #
    # HANDOFF_CALLBACK_URL is the ONE address a handoff code may ever be
    # delivered to (Weave-API's `/v1/auth/ingest/callback`). It is a fixed
    # configuration value on purpose, never anything a request can steer:
    # that leaves this service with no open-redirect surface at all, and
    # keeps the URL-allowlist parsing -- the part that is genuinely easy to
    # get wrong -- on Weave-API's side alone, where the variable target
    # (which chat instance to return to) actually belongs. Empty disables
    # the whole handoff surface with a 404, exactly like an unconfigured
    # OIDC provider.
    handoff_callback_url: str = ''
    # Shared with Weave-API (WEAVE_HANDOFF_SECRET there too). Presented by
    # Weave-API when it exchanges a code for the identity behind it; must
    # be non-empty whenever the handoff is enabled, or the exchange fails
    # closed with a 503 rather than comparing every caller's empty header
    # equal and turning into an open identity oracle.
    handoff_secret: str = ''
    # How long a freshly minted handoff code stays valid. Seconds: the code
    # is redeemed by a redirect that happens immediately, so this only has
    # to cover the browser hop, not any human hesitation.
    handoff_code_ttl_seconds: int = 60

    # --- Confluence import (app/services/confluence*.py, safe_fetch,
    # app/workers/import_tasks.py). The caps are hard server-side clamps:
    # client-supplied values can only lower them, never raise them.
    # Kill-switch: when False the /import API surface returns 404s.
    import_enabled: bool = True
    # Hostnames ('host' or 'host:port') of private-network Confluence servers
    # outbound import fetches may reach. Passed into safe_fetch as
    # allowed_private_hosts and enforced per redirect hop; cloud-metadata IPs
    # remain blocked unconditionally, allowlist or not. JSON list env value
    # (IMPORT_PRIVATE_HOST_ALLOWLIST), same parsing as cors_origins.
    import_private_host_allowlist: list[str] = []
    import_max_pages: int = 200
    import_max_depth: int = 10
    # Pages processed per chunked task execution before re-enqueueing, so
    # queued OCR jobs can interleave with a long crawl.
    import_chunk_pages: int = 25
    import_fetch_timeout_seconds: int = 30
    # safe_fetch max_bytes for JSON/HTML responses.
    import_fetch_max_bytes: int = 5 * 1024 * 1024
    # Per-attachment download cap; larger attachments are skipped, not fatal.
    import_attachment_max_bytes: int = 20 * 1024 * 1024
    # Cap on artifact_bytes + content_bytes accumulated by a single run
    # (stored page HTML counts too); reaching it ends discovery gracefully.
    import_run_max_total_bytes: int = 500 * 1024 * 1024
    import_max_active_runs_per_user: int = 1
    # A 'running' run whose updated_at is older than this is stale (worker
    # lost) -- drives the lease reclaim, requeue-on-worker-ready, force-cancel
    # and active-run-cap reaping paths.
    import_stale_run_seconds: int = 600
    # DB-backed cooldown between /import/sources/{id}/test probes (429 inside
    # the window); holds even when the Redis rate limiter fails open.
    import_test_cooldown_seconds: int = 10
    # Per-process asyncio semaphore capping concurrent outbound test probes.
    import_probe_concurrency: int = 4

    # --- Mail ingestion (POST /api/v1/mail/messages, app/services/mail_ingest.py).
    # Hard cap on the raw .eml body size, enforced by streaming
    # request.stream() chunk-wise and aborting with 413 once exceeded --
    # there is no body-size middleware anywhere in this stack (uvicorn is
    # started bare), so nothing else bounds it. Defaults to max_upload_bytes
    # (100 MiB) but is its own setting since a raw email (with attachments
    # inline as MIME parts) can legitimately want a different cap than a
    # single-file upload. Same ops caveat as uploads: the Helm chart sets no
    # proxy-body-size ingress annotation by default.
    max_mail_message_bytes: int = 100 * 1024 * 1024
    # Inline Content-ID parts (signature images, logos) never get an OCR Job
    # by default -- flip this on to burn worker time on them anyway.
    ocr_inline_images: bool = False

    # --- OpenWebUI push (app/services/openwebui.py, app/api/openwebui_routes.py,
    # app/workers/openwebui_tasks.py). Kill-switch: when False the
    # /openwebui API surface returns 404s, mirroring IMPORT_ENABLED.
    openwebui_enabled: bool = True
    # Overall wall-clock budget for one push: upload + processing poll +
    # knowledge-attach + best-effort replace. Also doubles as the worst-case
    # legitimate runtime the claim's stale-lease reclaim allows for before
    # treating a 'running' push as worker-lost (see _claim_push).
    openwebui_push_timeout_seconds: int = 300
    # Redis-backed cooldown between POST /openwebui/connections/{id}/test
    # probes (429 + Retry-After inside the window) -- Redis rather than a
    # DB column like ImportSource.last_test_at, since OpenWebUIConnection
    # carries no last-tested timestamp field.
    openwebui_test_cooldown_seconds: int = 10
    # Cap on pending+running OpenWebUIPush rows per user, enforced by
    # POST /openwebui/pushes -- a wedged/very active OpenWebUI instance must
    # not let one user queue unbounded outbound work.
    openwebui_push_max_pending_per_user: int = 50
    # Hostnames ('host' or 'host:port') of private-network OpenWebUI
    # instances outbound pushes may reach -- same shape and enforcement as
    # import_private_host_allowlist (passed into safe_fetch as
    # allowed_private_hosts, re-checked per redirect hop; cloud-metadata IPs
    # stay blocked unconditionally). Not part of the original OpenWebUI push
    # spec's config list, but required for the SSRF protection it does
    # mandate to be usable at all: OpenWebUI is typically self-hosted on a
    # private network, exactly like the Confluence Server/DC case this
    # mirrors. JSON list env value (OPENWEBUI_PRIVATE_HOST_ALLOWLIST), same
    # parsing as cors_origins/import_private_host_allowlist.
    openwebui_private_host_allowlist: list[str] = []

    # --- Outbound webhooks (app/services/webhooks.py, app/api/webhook_routes.py,
    # app/workers/webhook_tasks.py). Kill-switch: when False the /webhooks API
    # surface returns 404s, mirroring IMPORT_ENABLED/OPENWEBUI_ENABLED.
    webhooks_enabled: bool = True
    # DB-backed cooldown, per connection_id, between POST
    # /webhooks/connections/{id}/test probes (429 + Retry-After inside the
    # window) -- Redis-backed like openwebui_test_cooldown_seconds (see
    # app/api/webhook_routes._check_test_cooldown), since WebhookConnection
    # likewise carries no last-tested timestamp column.
    webhook_test_cooldown_seconds: int = 10
    # Cap on pending WebhookDelivery rows per user, enforced by POST
    # /webhooks/send -- same reasoning as openwebui_push_max_pending_per_user:
    # a wedged/unreachable receiving endpoint must not let one user queue
    # unbounded outbound work.
    webhook_max_pending_deliveries_per_user: int = 50
    # Hostnames ('host' or 'host:port') of private-network webhook receivers
    # outbound deliveries may reach -- same shape and enforcement as
    # openwebui_private_host_allowlist (passed into safe_fetch as
    # allowed_private_hosts, re-checked per redirect hop; cloud-metadata IPs
    # stay blocked unconditionally). Webhook receivers (e.g. n8n) are
    # typically self-hosted on a private LAN, exactly like the OpenWebUI
    # case this mirrors. JSON list env value (WEBHOOK_PRIVATE_HOST_ALLOWLIST),
    # same parsing as cors_origins/import_private_host_allowlist.
    webhook_private_host_allowlist: list[str] = []

    # --- Confluence refresh (periodic re-crawl of an ImportSource to pick up
    # upstream edits; the tick/dispatch worker itself is built separately --
    # these are just the shared cadence knobs it and PATCH /import/sources/{id}
    # both read).
    # How often the refresh scheduler checks for sources due a re-crawl.
    confluence_refresh_tick_seconds: int = 300
    # Server-side floor for ImportSource.refresh_interval_seconds: a
    # client-supplied value can only raise it, never lower it below this,
    # same clamp-never-raise-the-cap discipline as import_max_pages.
    confluence_refresh_min_interval_seconds: int = 900

    # --- Session cleanup (app/workers/session_cleanup_tasks.py). Sessions are
    # already lazily deleted one-at-a-time when a request presents an expired
    # cookie (app/api/deps.get_current_user), but a session nobody ever
    # presents again (an abandoned browser tab, a revoked machine) is never
    # touched by that path and would otherwise accumulate in the `sessions`
    # table forever. How often the self-re-enqueuing sweep tick runs --
    # default once/day, same "cadence knob read by the tick" role as
    # confluence_refresh_tick_seconds above.
    session_cleanup_tick_seconds: int = 86400

    # --- DB connection pool (SQLAlchemy QueuePool; see
    # app/database/session.py). Only applied for a real server backend
    # (postgres) -- sqlite's default pool implementation doesn't accept these
    # kwargs at all, so they're skipped entirely for any sqlite database_url
    # (local dev, the pytest suite). Defaults sized for a single backend
    # replica talking to a small/medium postgres instance; bump alongside
    # replica count in a multi-pod deployment.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600
    db_pool_timeout_seconds: int = 10


def _build_database_url(settings: Settings) -> str:
    if settings.database_url:
        return settings.database_url

    if settings.postgres_host and settings.postgres_db and settings.postgres_user:
        user = quote_plus(settings.postgres_user)
        password = quote_plus(settings.postgres_password)
        db = quote_plus(settings.postgres_db)
        if settings.postgres_password:
            auth = f'{user}:{password}'
        else:
            auth = user
        return f'postgresql+psycopg://{auth}@{settings.postgres_host}:{settings.postgres_port}/{db}'

    return 'sqlite:///./weave_ingest.db'


# NOT a real secret -- deterministic placeholder so sqlite-backed local dev
# and the pytest suite work with zero setup. Never used when database_url
# resolves to postgres (see _resolve_secret_key).
_DEV_ONLY_SQLITE_SECRET_KEY = 'dev-only-insecure-secret-key-do-not-use-in-production'


def _resolve_secret_key(settings: Settings) -> str:
    if settings.secret_key:
        return settings.secret_key
    if settings.database_url.startswith('sqlite'):
        return _DEV_ONLY_SQLITE_SECRET_KEY
    raise RuntimeError(
        'SECRET_KEY is required when database_url is not sqlite (i.e. any '
        'real, multi-user deployment). It signs session cookies and the '
        'OIDC state cookie, and client secrets are encrypted with a key '
        'derived from it -- set SECRET_KEY via env/secret before startup.'
    )


settings = Settings()
settings.database_url = _build_database_url(settings)
settings.secret_key = _resolve_secret_key(settings)
