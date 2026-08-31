from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Tools'

    # --- Incoming service auth for the REST mirror (app/api/tools.py) only
    # -- NOT the MCP server (app/mcp_server.py, which has no such gate at
    # all), and NOT the same credential as the Authorization bearer BOTH
    # surfaces read to resolve a caller's Scope (app/services/scope.py).
    # This is a coarse, deployment-level gate ("is this caller even allowed
    # to reach Weave-Tools' REST surface at all" -- e.g. n8n's own static
    # integration secret, configured once), carried on a dedicated header
    # (X-Tools-Service-Token, see app/api/deps.py) precisely so it can never
    # collide with the per-request Authorization header that carries WHOSE
    # rights a given call should run under. Same fail-closed default as
    # every other Weave service's own static service token (Weave-
    # Retrieval's RETRIEVAL_API_TOKEN, Weave-Runtime's RUNTIME_API_TOKEN):
    # left empty, the REST routes answer 503 rather than silently accepting
    # an empty header as a match -- a real deployment MUST set this.
    tools_api_token: str = ''

    # --- Personal-Token path of scope resolution (app/services/scope.py).
    # Weave-Tools is the CALLER here: introspection_service_token is the
    # credential THIS service presents to Weave-API's own
    # POST /internal/tokens/introspect, a completely different secret from
    # tools_api_token above (that one authenticates callers OF this
    # service; this one authenticates this service AS a caller of
    # Weave-API) -- the same split Weave-Runtime's own runtime_api_token vs.
    # retrieval_api_token draws in that service's config.py.
    weave_api_base_url: str = 'http://localhost:8004'
    introspection_service_token: str = ''
    weave_api_timeout_seconds: float = 10.0

    # --- Both scope-resolution paths' onward calls to Weave-Retrieval
    # (app/services/tools.py): listing a caller's readable collections
    # (GET .../api/v1/collections) and running the actual hybrid search
    # (POST .../api/v1/search). retrieval_api_token is the static Bearer
    # Weave-Retrieval's own require_service_token expects (see that
    # service's app/core/auth.py) -- yet another distinct secret from the
    # two settings above.
    retrieval_base_url: str = 'http://localhost:8002'
    retrieval_api_token: str = ''
    retrieval_timeout_seconds: float = 10.0

    # --- Delegations-Token (see app/services/scope.py's module docstring
    # for the full wire format). A symmetric secret: Weave-Runtime signs
    # with it when minting a short-lived token for an external agent (n8n,
    # an MCP client) acting on one specific human's behalf; Weave-Tools
    # verifies with the SAME value. Unlike every *_api_token above this is
    # never itself sent over the wire as a bearer credential -- only used
    # locally, on both ends, to compute/check an HMAC. Empty by default so
    # an unconfigured deployment fails every delegation-token verification
    # (see scope.py's _verify_delegation_token) rather than an empty secret
    # on this side ever matching an attacker-supplied empty-secret
    # signature.
    weave_delegation_secret: str = ''
    # How long a freshly minted delegation token stays valid, in seconds.
    # Deliberately short: the entire point of delegating instead of handing
    # the external agent a copy of the user's own long-lived Personal-Token
    # is that the loan expires almost immediately on its own -- it never
    # needs to outlive the single chat turn/tool call it was minted for.
    delegation_token_ttl_seconds: int = 300


settings = Settings()
