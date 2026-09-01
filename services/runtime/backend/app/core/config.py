from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Runtime'

    # --- Incoming service auth (see app/core/auth.py). Bearer token every
    # caller of THIS service (Weave-API's gateway today) must present in
    # `Authorization: Bearer <...>` -- ADR-0002's "Bearer-Token (wie in
    # PaddleDoc)" pattern for machine callers, same fail-closed default as
    # Weave-Retrieval's RETRIEVAL_API_TOKEN: left empty, every
    # service-authenticated route answers 503 (misconfiguration) rather than
    # silently accepting an empty bearer token as a match (see auth.py's
    # docstring) -- a real deployment MUST set this.
    runtime_api_token: str = ''

    # --- Outgoing call to Weave-Retrieval (see app/services -- filled in a
    # later stage, when the chat pipeline actually performs retrieval).
    # Weave-Runtime is the CALLER here: retrieval_api_token is the token
    # THIS service presents to Weave-Retrieval's own require_service_token,
    # a completely different credential from runtime_api_token above (that
    # one authenticates callers OF this service; this one authenticates this
    # service AS a caller of another). Default host:port matches
    # Weave-Retrieval's own conventional local-dev port.
    retrieval_base_url: str = 'http://localhost:8002'
    retrieval_api_token: str = ''
    retrieval_timeout_seconds: float = 10.0

    # --- LLM provider abstraction (see app/services -- filled in a later
    # stage). 'fake' is a deterministic, dependency-free provider so local
    # dev and the pytest suite never need a real LLM API key or outbound
    # network access, exactly like every other Weave backend's 'fake'
    # embedding/rerank provider default (see e.g. Weave-Retrieval's
    # EMBEDDING_PROVIDER/RERANK_PROVIDER).
    llm_provider: str = 'fake'
    llm_base_url: str = ''
    llm_api_key: str = ''
    llm_default_model: str = ''
    # LLM generation is the slowest hop in the chat pipeline (an actual
    # model call, possibly with tool use), so it gets a much longer budget
    # than the retrieval call above.
    llm_timeout_seconds: float = 60.0

    # --- Intent router (see app/services -- filled in a later stage).
    # 'rules': a deterministic, dependency-free keyword/heuristic classifier.
    # 'llm': delegates the classification itself to llm_provider. Plain str
    # rather than a Literal/enum -- same looseness as the provider settings
    # above; an unrecognised value is that later stage's own concern to
    # reject, not something this settings object needs to gate.
    router_mode: str = 'rules'

    # --- Delegation tokens (see app/services/delegation.py,
    # contracts/n8n-flow.md). How Weave-Runtime lends an external agent
    # (n8n today, any MCP client tomorrow) the EXACT read-scope of the human
    # who is currently asking a question, for a short, signed window -- see
    # that module's own docstring for the full token contract. Must be
    # IDENTICAL to Weave-Tools' own WEAVE_DELEGATION_SECRET (the verifier
    # side of this same shared secret) -- left empty, `mint_delegation_token`
    # refuses to issue a token at all (a hard failure, never a silently
    # unsigned one -- see that function's own docstring); there is no
    # request-time code path in this service that can turn an unconfigured
    # secret into a 503 the way `require_service_token` does for
    # RUNTIME_API_TOKEN, since minting only ever happens deep inside one
    # specific bot's n8n turn, not on every request.
    weave_delegation_secret: str = ''
    delegation_token_ttl_seconds: int = 300

    # --- n8n bot provider (see app/services/n8n_client.py,
    # bots/n8n-agent.yaml.example, contracts/n8n-flow.md). A bot's
    # `n8n.webhook_url` (app/schemas/bot.py's N8nConfig) comes from a
    # hand-authored YAML file -- configuration, not attacker-controlled
    # end-user input -- but this service still must not become a means to
    # call an arbitrary network address just because some operator's bot
    # file says so. Every n8n-provider bot's `webhook_url` must start with
    # one of these base URLs or the bot fails to LOAD entirely
    # (app/services/botconfig.py raises BotConfigError, naming the file) --
    # checked once, at load time, deliberately never deferred to the first
    # actual call. Empty (the default) disables n8n bots outright: no base
    # URL can ever start-with-match against nothing, so every n8n-provider
    # bot fails to load until an operator sets this.
    #
    # JSON list env value (e.g. '["https://n8n.internal:5678/webhook/"]') --
    # same parsing convention as Weave-Ingest's own *_PRIVATE_HOST_ALLOWLIST
    # settings. Each entry's scheme/host/port is compared to a bot's own
    # `n8n.webhook_url` EXACTLY (via urllib.parse, never a raw
    # `str.startswith()` -- see app/services/botconfig.py's
    # `_base_url_matches`), with the path checked as a boundary-respecting
    # prefix only once scheme/host/port already agree. A trailing path
    # separator on an entry is no longer required for safety (it was, under
    # the old plain string-prefix check, where a bare "https://
    # n8n.internal:5678" would also match "https://
    # n8n.internal:56789.evil.example/" -- a completely different host that
    # merely shared a string prefix with the intended one); still harmless
    # to include one if an entry is itself a path prefix multiple distinct
    # webhook_urls should share.
    n8n_allowed_base_urls: list[str] = []

    # Base URL Weave-Tools is reachable at, for n8n's OWN outbound calls
    # (MCP or REST, once it holds a delegation token) -- forwarded verbatim
    # as `tools_base_url` in every n8n webhook request body
    # (app/services/n8n_client.py) so an n8n flow needs no separate
    # configuration of its own for where Weave-Tools lives.
    tools_base_url: str = ''

    # --- Bot configuration directory (see app/services/botconfig.py,
    # bots/*.yaml). Every route re-reads the YAML files under this directory
    # FRESH per call -- there is no in-process cache to invalidate when an
    # operator edits/adds a bot file (see that module's own docstring).
    # Relative to the process's current working directory: both local dev
    # (README's "Entwicklung" -- started from the repo root) and the
    # Dockerfile (WORKDIR /app, with `bots/` copied to /app/bots alongside
    # /app/app) run with `bots/` as a direct child of the CWD, so this
    # default resolves correctly unmodified in both cases; only a genuinely
    # custom layout needs to override it.
    bots_dir: str = './bots'


settings = Settings()
