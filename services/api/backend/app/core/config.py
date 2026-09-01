from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave API'

    # --- Database (see app/core/db.py + app/models/models.py). Weave-API
    # owns its own `weave_api` database (ADR-0004's one-database-per-service
    # split: `users`/`api_tokens`/`conversations`/`messages`, never a table
    # shared with another service). sqlite is the zero-setup default for
    # local dev and the pytest suite, same as every other Weave backend.
    database_url: str = 'sqlite:///./weave_api.db'

    # Signs/encrypts whatever this service persists at rest. Per ADR-0003
    # every service holds its own SECRET_KEY -- never shared across
    # services, even though nothing in this skeleton stage actually
    # encrypts anything with it yet (reserved for a future OIDC/session
    # layer at this gateway, see ADR-0002).
    secret_key: str = 'dev-only-insecure-secret-key-do-not-use-in-production'

    # --- Weave-Runtime client (app/services/runtime_client.py). Weave-API
    # is a trusted internal caller of Weave-Runtime's `/internal/bots`
    # surface (GET /v1/bots, GET /v1/bots/{id} in app/api/bots.py proxy
    # straight through) -- a single shared service token, same
    # service-to-service pattern ADR-0002 describes for Weave-Retrieval.
    # Deliberately no empty-token 503 guard here (unlike Weave-Retrieval's
    # own RETRIEVAL_API_TOKEN): Weave-Runtime is the one validating this
    # token on ITS side, not this service -- an empty value here just means
    # every proxied call gets Weave-Runtime's own 401, surfaced to our
    # caller as the usual 502 (see runtime_client.RuntimeClientError).
    runtime_base_url: str = 'http://localhost:8003'
    runtime_api_token: str = ''

    # --- Weave-Retrieval client (app/services/retrieval_client.py, used by
    # GET /v1/collections in app/api/collections.py). Weave-API is a
    # trusted, service-token-authenticated caller of Weave-Retrieval's own
    # Collections read-authority surface (GET /api/v1/collections, that
    # service's app/api/collections.py) -- same service-to-service pattern
    # as `runtime_api_token` above, just towards a different backend.
    # Unlike `runtime_api_token`, an empty value here needs no explicit
    # guard on THIS side either: Weave-Retrieval's own `require_service_token`
    # already refuses an unauthenticated/mismatched caller with its own
    # 401/503, which `retrieval_client.list_collections` surfaces to our
    # caller as the usual 502 -- see that module's docstring.
    retrieval_base_url: str = 'http://localhost:8002'
    retrieval_api_token: str = ''

    # --- POST /internal/tokens/introspect (app/api/internal.py). A SEPARATE
    # secret from `runtime_api_token`/`retrieval_api_token` above -- those
    # authenticate Weave-API calling OUT to another service, this one
    # authenticates another service calling IN to ask "is this Personal-API-
    # Token still valid, and whose is it". Treat it like a master key (see
    # README): anyone holding it can resolve the identity behind ANY
    # Personal-API-Token in this database. Empty by default so a deployment
    # that never sets it gets a hard 503 on that endpoint rather than a
    # silently-open one (see app/core/auth.py's
    # require_introspection_service_token, mirroring Weave-Retrieval's own
    # require_service_token empty-token guard).
    introspection_service_token: str = ''

    # --- Rate limiting (app/core/ratelimit.py). Fixed-window requests per
    # authenticated user per rolling minute -- see that module's docstring
    # for why fixed-window/in-process is enough for a single-instance
    # skeleton and what a multi-replica deployment needs instead.
    rate_limit_per_minute: int = 30

    # --- Chat history (used once app/api/chat.py's POST /v1/chat is filled
    # in, see README's Status). Caps how many of a conversation's most
    # recent messages are replayed to Weave-Runtime as context on the next
    # turn -- unbounded history would make every request's payload (and
    # Weave-Runtime's own context-window usage) grow linearly with
    # conversation length.
    history_max_messages: int = 20

    # --- DB connection pool (SQLAlchemy QueuePool; see app/core/db.py).
    # Only applied for a real server backend (postgres) -- sqlite's default
    # pool implementation doesn't accept these kwargs at all, so they're
    # skipped entirely for any sqlite database_url (local dev, the pytest
    # suite). Same defaults and reasoning as every other Weave backend's
    # app/core/config.py.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600
    db_pool_timeout_seconds: int = 10

    # --- HTTP timeouts for outbound calls to Weave-Runtime (see
    # app/services/runtime_client.py). Split connect/read rather than one
    # blanket timeout: a slow-to-accept-connections Weave-Runtime and a
    # slow-to-respond one are different failure modes worth tuning
    # separately, and httpx.Timeout takes them as separate knobs anyway.
    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 10.0

    # --- POST /v1/chat request body (app/schemas/chat.py's ChatRequest).
    # Caps a single chat message's length -- both to keep one request's
    # outbound payload to Weave-Runtime bounded and to give a caller sending
    # a pasted-in novel a clear 422 instead of an oversized request that
    # only fails once it hits Weave-Runtime's own limits.
    chat_message_max_length: int = 8000

    # --- OIDC browser-session login (app/api/auth.py, app/services/oidc.py,
    # ADR-0002). Follows Weave-Ingest's own approach and libraries (authlib
    # for the state/PKCE/nonce authorize dance, joserfc for ID-token
    # verification against the provider's JWKS -- see that service's
    # app/services/oidc.py) but simplified to ONE statically-configured
    # provider via these env vars, not Weave-Ingest's own admin-managed,
    # DB-backed multi-provider table (that service's AuthProvider model) --
    # this gateway has exactly one identity provider to talk to per
    # deployment, not a tenant-configurable list.
    #
    # OIDC is considered ENABLED exactly when both `oidc_issuer` and
    # `oidc_client_id` are non-empty (app/api/auth.py's `oidc_enabled()`).
    # When disabled, every `/v1/auth/oidc/*` endpoint and `/v1/auth/logout`
    # behaves as if it were never registered (404) and a Personal-API-Token
    # (app/cli.py) remains the only way for anything -- human or machine --
    # to authenticate against this gateway.
    oidc_issuer: str = ''
    oidc_client_id: str = ''
    oidc_client_secret: str = ''
    # Must be registered with the provider verbatim (README's OIDC-setup
    # section) -- sent as `redirect_uri` on both the initial authorize
    # request (GET /v1/auth/oidc/login) and the subsequent token exchange
    # (GET /v1/auth/oidc/callback), which per the OAuth2 spec must match
    # exactly or the provider rejects the code exchange outright.
    oidc_redirect_url: str = ''
    oidc_scopes: str = 'openid email profile'
    # Optional claim name carrying this user's team (e.g. a Keycloak/Entra
    # 'groups' claim) -- see app/api/auth.py's `_resolve_team` for exactly
    # how a list-valued claim is handled (first element wins). Empty
    # (the default) means every OIDC-provisioned user's `team` stays `None`,
    # exactly like a CLI-created user with no `--team` given.
    oidc_team_claim: str = ''

    # --- Cross-origin post-login handoff (app/api/auth.py's `return_to`
    # handling on GET /v1/auth/oidc/login + GET /v1/auth/oidc/callback,
    # README's "OIDC-Anmeldung einrichten"). The chat UI this gateway serves
    # can live on a DIFFERENT origin than this gateway itself, so the
    # gateway's own session cookie -- scoped to ITS origin -- never reaches
    # the UI's browser storage on its own; a UI that wants its own cookie
    # asks to be redirected back here with `?return_to=<its own URL>` and
    # gets a one-time code (POST /v1/auth/session/exchange trades it for a
    # real session token) instead.
    #
    # `return_to` is attacker-adjacent input (an unauthenticated query
    # parameter on GET /v1/auth/oidc/login) -- an open redirect there would
    # let a phishing link start a REAL login against this gateway's own
    # configured provider and land the browser (carrying the one-time code)
    # on an attacker-controlled origin instead. Every candidate is therefore
    # checked against this allowlist (scheme/host/port compared EXACTLY,
    # path as a boundary-respecting prefix only afterwards -- same
    # discipline as Weave-Runtime's own N8N_ALLOWED_BASE_URLS/
    # `_base_url_matches`, see app/api/auth.py's `_return_to_matches_base`)
    # and, if it matches no entry, silently IGNORED -- falling back to the
    # fixed `_POST_LOGIN_REDIRECT` exactly as if `return_to` had never been
    # given at all, never an error response that would tell a caller
    # whether their candidate URL was well-formed or merely disallowed.
    #
    # JSON list env value, same convention as N8N_ALLOWED_BASE_URLS above.
    # Empty (the default) disables the handoff outright: no candidate can
    # ever match an empty allowlist, so `return_to` is always ignored and
    # this gateway's behaviour is byte-for-byte what it was before this
    # setting existed. Every entry here is a UI ORIGIN this gateway will
    # hand a live, freshly-issued session's one-time code to -- list only
    # UIs you trust as much as this gateway's own frontend, never a
    # third party's.
    oidc_post_login_allowed_urls: list[str] = []


settings = Settings()
