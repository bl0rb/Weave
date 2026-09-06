# Weave Platform Deployment

## Overview

`docker-compose.weave.yml` is the Docker Compose configuration for the full Weave platform: the six core services (Weave-Ingest, Weave-Knowledge, Weave-Retrieval, Weave-Runtime, Weave-Tools, Weave-API), the chat UI, and the two optional CPU model services (Weave-Embeddings, Weave-Reranker). Every variable it reads is declared once in `weave.yaml` at the repo root; `deploy/.env` is generated from it (see "Quick Start") and never edited by hand.

## What's Here

Infrastructure:
- **postgres**: PostgreSQL 16 with pgvector extension. Hosts three databases (`weave_ingest`, `weave_knowledge`, `weave_api`) plus the read-only `weave_retrieval_ro` role — see "Database initialization" below.
- **redis**: Redis 7 for Celery broker/result backend and caching. Used only by Weave-Ingest (logical DB 0) and Weave-Knowledge (logical DB 1) — see `docs/adr/0001`. None of Weave-Retrieval, Weave-Runtime, Weave-Tools, or Weave-API use Redis.

Services, in the order they depend on each other (see "Startup order" below):
- **weave-ingest-backend** / **weave-ingest-worker** / **weave-ingest-frontend**: document ingestion, async extraction, upload UI.
- **weave-knowledge** / **weave-knowledge-worker**: consumes `document.released` events (a release is an immutable, manually approved snapshot -- see `docs/wissensportal.md`), chunks + embeds documents into its own `weave_knowledge` database (pgvector). `document.processed` is only acknowledged (`awaiting_release`) and indexes nothing.
- **weave-retrieval**: hybrid search (pgvector KNN + tsvector ranking, RRF fusion). Owns no schema — reads Weave-Knowledge's `documents`/`chunks`/`collections` tables directly, read-only. This is the one named exception to "no shared tables" (`docs/adr/0004`), documented in full in `docs/adr/0005`.
- **weave-runtime**: chat/agent execution — intent routing, LLM calls, retrieval-augmented answers, optional delegation of a turn to an n8n agent flow.
- **weave-tools-backend**: MCP + REST tool surface (`list_collections`, `search`) for delegated or personal-token-scoped callers. `weave-tools-frontend` is its chat UI, talking only to Weave-API.
- **weave-api**: the unified public gateway — owns its own `users`/`api_tokens`/`conversations` schema, proxies into Weave-Runtime and Weave-Retrieval.
- **weave-embeddings** / **weave-reranker**: optional, self-hosted CPU model services (`services/embeddings`, `services/reranker` in this monorepo) for `intfloat/multilingual-e5-small` embeddings and `BAAI/bge-reranker-v2-m3` reranking. Started like any other service here, but idle by default — nothing calls them until `EMBEDDING_BASE_URL`/`RERANK_BASE_URL` are pointed at them and `EMBEDDING_PROVIDER`/`RERANK_PROVIDER` are switched away from the `fake`/`none` default. See `docs/betrieb.md` section 7 for the full walkthrough.

## Quick Start

1. Generate `deploy/.env` from `weave.yaml`. You do not write that file by hand — `weave.yaml` in the repo root is the declarative source for every variable the compose file reads, and `render` turns it into `deploy/.env`:
   ```bash
   # Secrets are never stored in weave.yaml — it only names the environment
   # variable each one is read from. Export them however you like (direnv, a
   # password manager, a CI secret store) before rendering; `render` lists
   # every missing required value at once instead of writing a partial file.
   export WEAVE_POSTGRES_PASSWORD=... WEAVE_REDIS_PASSWORD=...   # ... and the rest

   python scripts/weave_config.py render        # writes deploy/.env
   python scripts/weave_config.py check         # re-validates an existing deploy/.env
   ```
   `docs/betrieb.md` section 11 ("Deklarative Konfiguration (`weave.yaml`)") has the full list of secrets to export and explains both subcommands. To change a value later, edit `weave.yaml` (or the exported secret) and re-render — do not edit `deploy/.env` itself.

2. `render` computes each shared value once and mirrors it into every variable that needs it, so those values cannot drift within a generated `.env`. Even so, **read the compose file's "SHARED SECRETS" header comment in `docker-compose.weave.yml`** before you generate tokens: it names which services issue and which verify each shared secret, and what the silent 401/503 at a service boundary looks like when one of them does drift. "Environment variables" below remains the per-variable reference for what each value means.

3. Start the stack:
   ```bash
   docker compose -f deploy/docker-compose.weave.yml up -d
   ```
   Compose resolves the `depends_on: ... condition: service_healthy` graph on its own — you do not need to start services one at a time — but see "Startup order" below for what happens in what sequence and how to watch it.

4. Verify services:
   ```bash
   docker compose -f deploy/docker-compose.weave.yml ps
   ```

5. Check logs:
   ```bash
   docker compose -f deploy/docker-compose.weave.yml logs -f weave-ingest-backend
   ```

## Startup order

`depends_on: condition: service_healthy` encodes this dependency chain, so `docker compose up -d` schedules containers correctly on its own — this section is for understanding what's waiting on what, and for diagnosing a stack that won't come up. Each tier below waits on every service named in the tier(s) above it:

1. `postgres`, `redis`
2. `weave-ingest-backend` (needs postgres + redis)
3. `weave-ingest-worker`, `weave-ingest-frontend`, `weave-knowledge` (each needs weave-ingest-backend; weave-knowledge also needs postgres + redis directly)
4. `weave-knowledge-worker` (needs weave-knowledge + redis), `weave-retrieval` (needs weave-knowledge **and** postgres directly — it reads `weave_knowledge`'s tables straight from Postgres, not through Weave-Knowledge's API)
5. `weave-runtime` (needs weave-retrieval)
6. `weave-api` (needs postgres + weave-runtime + weave-retrieval)
7. `weave-tools-backend` (needs weave-api + weave-retrieval)
8. `weave-tools-frontend` (needs weave-api — it talks only to the gateway, never to weave-tools-backend or weave-retrieval directly)

Notes:
- `postgres`'s own healthcheck (`pg_isready`) only proves the server accepts connections — the three databases and the `weave_retrieval_ro` role come from `postgres-init/01-create-databases.sh` (see "Database initialization"), which runs as part of postgres's *first-ever* startup, before `pg_isready` starts succeeding.
- `weave-knowledge`'s healthcheck round-trips its own `/health`, which does **not** touch the database (see that service's `app/main.py`) — a healthy `weave-knowledge` proves the process is up, not that Alembic already created `documents`/`chunks`. In practice this is fine because Alembic runs synchronously in `entrypoint.sh` *before* uvicorn starts serving, so "healthy" already implies "migrated". `weave-retrieval`'s own `/health` **does** run a real `SELECT 1` against `weave_knowledge`, so it will report unhealthy (not just slow) if the read-only role or the tables aren't there yet.
- `weave-runtime` has no database at all — its healthcheck only proves the process answers HTTP.

## Verifying the chain end-to-end

Once `docker compose ps` shows every service `healthy`:

1. **Each service's own health:**
   ```bash
   curl -sf http://localhost:8000/api/v1/health   # weave-ingest-backend
   curl -sf http://localhost:8001/health          # weave-knowledge
   curl -sf http://localhost:8002/health          # weave-retrieval (SELECT 1 against weave_knowledge)
   curl -sf http://localhost:8003/health          # weave-runtime
   curl -sf http://localhost:8004/health          # weave-api (SELECT 1 against weave_api)
   curl -sf http://localhost:8005/health          # weave-tools-backend
   ```
   Any non-2xx here means that service itself is broken, before you even get to cross-service auth.

2. **The shared-secret chain (the failure mode that looks confusing):** if every `/health` above is green but a real request through the stack still 401s/503s, it's almost always one of the four shared secrets drifting between services — see the compose file's "SHARED SECRETS" header comment for exactly which pair to compare. A quick way to check for drift without printing secrets to a terminal:
   ```bash
   # Same value everywhere? (prints only whether they match, not the value)
   for svc in weave-runtime weave-tools-backend; do
     docker compose -f deploy/docker-compose.weave.yml exec "$svc" \
       sh -c 'echo -n "$WEAVE_DELEGATION_SECRET" | sha256sum'
   done
   ```
   Matching hashes on both lines means `WEAVE_DELEGATION_SECRET` is consistent; repeat the same pattern for `INTROSPECTION_SERVICE_TOKEN` (weave-tools-backend vs. weave-api), `RETRIEVAL_API_TOKEN` (weave-retrieval vs. weave-runtime vs. weave-tools-backend vs. weave-api), and `RUNTIME_API_TOKEN` (weave-runtime vs. weave-api).

3. **The read-model itself is live** (the thing `docs/adr/0005` is actually about): ingest a document through Weave-Ingest, wait for Weave-Knowledge to index it (`docker compose logs -f weave-knowledge-worker`), then confirm Weave-Retrieval can see it without going through Weave-Knowledge at all:
   ```bash
   curl -sf -X POST http://localhost:8002/api/v1/search \
     -H "Authorization: Bearer $RETRIEVAL_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"query": "<something from the document you just ingested>"}'
   ```
   A result here proves the read-only role can actually see rows Weave-Knowledge's writer wrote — the whole point of `docs/adr/0005`.

## Environment Variables

### Shared secrets (must be IDENTICAL across the services listed)

See the compose file's own "SHARED SECRETS" header comment for the full explanation of what breaks on a mismatch. Summary:

| Variable | Set identically on | Wrong value looks like |
|---|---|---|
| `WEAVE_DELEGATION_SECRET` | `weave-runtime` (issuer), `weave-tools-backend` (verifier) | every n8n-delegated bot turn fails with one generic error |
| `INTROSPECTION_SERVICE_TOKEN` | `weave-tools-backend` (caller), `weave-api` (verifier) | Personal-Token-scoped Weave-Tools calls fail (503-ish, looks like Weave-Tools misconfiguration) |
| `RETRIEVAL_API_TOKEN` | `weave-retrieval` (verifier), `weave-runtime`, `weave-tools-backend`, `weave-api` (all callers) | 401/503 from whichever caller has the wrong value |
| `RUNTIME_API_TOKEN` | `weave-runtime` (verifier), `weave-api` (caller) | weave-api's `/v1/bots` proxy fails with 401/503 |

Generate each with `openssl rand -hex 32`. Never reuse one shared secret's value for another — they protect different trust boundaries.

### Required (block startup if unset)

- `POSTGRES_PASSWORD` – PostgreSQL password for the `weave` superuser
- `REDIS_PASSWORD` – Redis authentication password
- `SECRET_KEY` – Weave-Ingest's own session signing + credential encryption (`docs/adr/0003`: one `SECRET_KEY` per service, never shared)
- `RETRIEVAL_DB_PASSWORD` – password for the read-only `weave_retrieval_ro` Postgres role (`docs/adr/0005`); set on **both** `postgres` (consumed by the init script that creates the role) and `weave-retrieval` (its `DATABASE_URL`)
- `WEAVE_KNOWLEDGE_SECRET_KEY` – Weave-Knowledge's own `SECRET_KEY`
- `WEAVE_KNOWLEDGE_INGEST_API_TOKEN` – a real Weave-Ingest Personal-Token for Weave-Knowledge's service user (fetches the released snapshot after a `document.released` webhook, and the collection registry)
- `WEAVE_KNOWLEDGE_WEBHOOK_SECRET` – required: the internal event ingress fails closed (503) while it is unset, since that route writes into the index. `render` mirrors it into Ingest's `PORTAL_KNOWLEDGE_WEBHOOK_SECRET` for `document.released` and the ACL-free `collection.updated` registry hint. Both use the dedicated Ingest→Knowledge channel; no user-managed webhook or n8n configuration is involved
- `RETRIEVAL_API_TOKEN`, `RUNTIME_API_TOKEN`, `WEAVE_DELEGATION_SECRET`, `INTROSPECTION_SERVICE_TOKEN` – see "Shared secrets" above
- `WEAVE_API_SECRET_KEY` – Weave-API's own `SECRET_KEY`
- `TOOLS_API_TOKEN` – gates Weave-Tools' REST surface (not shared with any other service in this compose file — e.g. an n8n HTTP-node credential)

### Optional (have sensible defaults)

- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PORT` – default `weave_ingest` / `weave` / `5432`
- `BACKEND_PORT` (8000), `FRONTEND_PORT` (3000), `KNOWLEDGE_PORT` (8001), `RETRIEVAL_PORT` (8002), `RUNTIME_PORT` (8003), `API_PORT` (8004), `TOOLS_PORT` (8005), `TOOLS_FRONTEND_PORT` (3001), `EMBEDDINGS_PORT` (8006), `RERANKER_PORT` (8007)
- `RETRIEVAL_DB_USER` – read-only role name, default `weave_retrieval_ro`
- `WEAVE_INGEST_TAG`, `WEAVE_KNOWLEDGE_TAG`, `WEAVE_RETRIEVAL_TAG`, `WEAVE_RUNTIME_TAG`, `WEAVE_API_TAG`, `WEAVE_TOOLS_TAG`, `WEAVE_TOOLS_FRONTEND_TAG`, `WEAVE_EMBEDDINGS_TAG`, `WEAVE_RERANKER_TAG` – image tags, default `latest`
- `EMBEDDING_PROVIDER` / `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` / `EMBEDDING_BATCH_SIZE` – defined ONCE as the `x-embedding-env` anchor at the top of `docker-compose.weave.yml` and merged into `weave-knowledge` (+worker) and `weave-retrieval` with `<<: *embedding-env`, so the three services cannot technically drift from each other; default `fake` / `fake-embed` / `1536`. **Not** in the shared-secrets table above (it's not a credential), but just as cross-service: change it and every service that merges the anchor changes with it, or vector search silently becomes meaningless (see the anchor's own comment).
- `RERANK_PROVIDER` / `RERANK_BASE_URL` / `RERANK_API_KEY` / `RERANK_MODEL`, `SEARCH_TOP_K` (20), `SEARCH_FINAL_K` (5), `RRF_K` (60) – Weave-Retrieval's hybrid-search tuning; `RERANK_BASE_URL` can point at this stack's own `weave-reranker` service (`http://weave-reranker:8000`) or a hosted Cohere/jina.ai-compatible endpoint
- `EMBEDDINGS_MODEL` / `EMBEDDINGS_API_TOKEN` / `EMBEDDINGS_BATCH_SIZE` / `EMBEDDINGS_THREADS` / `EMBEDDINGS_MAX_INPUTS` / `EMBEDDINGS_NORMALIZE` – `weave-embeddings` only (the model this container itself loads, default `intfloat/multilingual-e5-small`); `EMBEDDINGS_API_TOKEN` empty by default like `RERANKER_API_TOKEN` below, not `:?required` — see the compose file's comment on that service for why
- `RERANKER_MODEL` / `RERANKER_API_TOKEN` / `RERANKER_BATCH_SIZE` / `RERANKER_THREADS` / `RERANKER_MAX_DOCUMENTS` – `weave-reranker` only, default model `BAAI/bge-reranker-v2-m3`
- `RETRIEVAL_TIMEOUT_SECONDS` (10) – HTTP timeout used by both Weave-Runtime's and Weave-Tools' calls into Weave-Retrieval
- `CHAT_CONFIG_SERVICE_TOKEN` – gemeinsames Secret von Ingest und Runtime für die zentrale **Administration → Chat & LLM**; `CHAT_CONFIG_BASE_URL` (intern standardmäßig Ingest), `CHAT_CONFIG_TIMEOUT_SECONDS` und `CHAT_LLM_PRIVATE_HOST_ALLOWLIST` steuern den Abruf und private Providerziele
- `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL` / `LLM_TIMEOUT_SECONDS` – Standalone-Fallback der Runtime, wenn keine zentrale Chat-Konfiguration verdrahtet ist; `ROUTER_MODE`, `N8N_ALLOWED_BASE_URLS`, `TOOLS_BASE_URL`, `DELEGATION_TOKEN_TTL_SECONDS` bleiben Runtime-Einstellungen
- `WEAVE_API_TIMEOUT_SECONDS` (10) – HTTP timeout for Weave-Tools' calls into Weave-API's introspection endpoint
- `RATE_LIMIT_PER_MINUTE` (30), `HISTORY_MAX_MESSAGES` (20) – Weave-API
- `WORKER_MEMORY_LIMIT` (3g), `WORKER_CPUS` (2.0), `CELERY_WORKER_CONCURRENCY` (1), `CELERY_PREFETCH_MULTIPLIER` (1), `CELERY_MAX_TASKS_PER_CHILD` (5) – Weave-Ingest worker
- `PUBLIC_API_URL`, `WEAVE_INGEST_PUBLIC_API_URL`, `CORS_ORIGINS` – Weave-Ingest frontend/OIDC URLs

See `docker-compose.weave.yml` for the complete list and inline documentation — every variable above is documented in place, next to the service that reads it.

## SSO einrichten

Weave-API kann zusätzlich zum Personal-Token-Login eine browserbasierte OIDC-Anmeldung anbieten (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URL`, `OIDC_SCOPES`, `OIDC_TEAM_CLAIM`, `OIDC_POST_LOGIN_ALLOWED_URLS` bei `weave-api`; `WEAVE_API_OIDC_ENABLED` und `WEAVE_API_PUBLIC_BASE_URL` bei `weave-tools-frontend`). Alle diese Variablen sind optional und leer per Default — ohne sie startet der Stack unverändert nur mit Personal-Token-Login. Beim Einrichten zwei Punkte beachten, die erfahrungsgemäß die naheliegendste Fehlkonfiguration sind:

1. **Redirect-URL**: Die beim OIDC-Provider (Keycloak, Entra, …) für diesen Client hinterlegte Redirect-URI muss byte-genau `OIDC_REDIRECT_URL` entsprechen — sonst lehnt der Provider den Code-Austausch beim Login rundweg ab.
2. **Callback-URL der Oberfläche**: Die öffentlich erreichbare Callback-URL von `weave-tools-frontend` (dessen eigene `APP_BASE_URL` plus `/api/auth/sso/callback`) muss als Eintrag in `OIDC_POST_LOGIN_ALLOWED_URLS` stehen. Fehlt der Eintrag, gibt es keine Fehlermeldung — `return_to` wird von Weave-API still ignoriert, und der Nutzer landet nach dem Login auf dem Gateway statt zurück in der Oberfläche.

Personal-Tokens für maschinelle Zugriffe (CLI, n8n, MCP, …) bleiben von alledem vollständig unberührt — SSO ist ein rein zusätzlicher Weg für menschliche Browser-Logins.

## Database Initialization

Three databases live on one Postgres cluster (`docs/adr/0004`):
- `weave_ingest` — created by the base Postgres image itself (it's `$POSTGRES_DB`).
- `weave_knowledge` — created by `postgres-init/01-create-databases.sh`, migrated by Weave-Knowledge's own Alembic at container startup.
- `weave_api` — created by the same init script, migrated by Weave-API's own Alembic at container startup.

Plus the read-only `weave_retrieval_ro` role `docs/adr/0005` specifies for Weave-Retrieval's shared access to `weave_knowledge` — also created by that init script, which grants it `SELECT` on the `weave_knowledge` database (via `ALTER DEFAULT PRIVILEGES`, so tables Alembic creates *after* this script runs are automatically covered, no manual per-table `GRANT` needed).

**This only runs once**, automatically, the first time the `postgres` container initializes an empty `pgdata` volume — it will not re-apply to an already-initialized cluster (that's how the official Postgres image's `docker-entrypoint-initdb.d` mechanism works, not something specific to this script). If you're adding Weave-Retrieval to an existing deployment that already has a populated `pgdata` volume, run the script's SQL by hand instead:
```bash
docker compose -f deploy/docker-compose.weave.yml exec postgres \
  sh /docker-entrypoint-initdb.d/01-create-databases.sh
```
(safe to re-run only up to the point where `CREATE DATABASE`/`CREATE ROLE` first succeeds — a second run will error on those two statements already existing; drop into `psql` directly if you need to pick up only the `weave_api` database or only the role).

See `postgres-init/01-create-databases.sh` for the exact SQL.

## Volumes

- **pgdata** – PostgreSQL data directory (all three databases)
- **redisdata** – Redis append-only file (RDB snapshots)
- **storage** – Shared document storage (Weave-Ingest backend + worker)
- **paddlex_models** – PaddleOCR model cache (Weave-Ingest worker)
- **runtime_bots** – Weave-Runtime's bot configuration directory (`BOTS_DIR`). Docker seeds a fresh volume from the image's own bundled example bots on first mount — edit/add YAML files here (`docker compose exec weave-runtime sh`, or bind-mount your own directory instead) for a real bot roster; no restart needed, it's re-read per request.

All are Docker-managed named volumes. For host-based storage (NAS, dedicated disk), create a `docker-compose.override.yml` overlay or migrate to Kubernetes PersistentVolumes.

## Production Notes

When deploying to production:
1. Use external secret management (AWS Secrets Manager, HashiCorp Vault, etc.) instead of `.env` files — especially for the four shared secrets above, where an operator manually copy-pasting the same value into multiple places is exactly the failure mode this file's "Startup order"/"Verifying the chain" sections exist to help debug.
2. Add a reverse proxy (Nginx, Traefik) for TLS termination
3. Configure resource limits per environment (dev/staging/prod)
4. Set up monitoring (Prometheus, ELK, etc.)
5. Consider Kubernetes deployment for scaling beyond single-host

## Architecture References

- **docs/adr/0001** – Queue topology (Celery queues, Redis logical DBs)
- **docs/adr/0002** – Auth strategy (OIDC, Personal-Tokens, service-to-service tokens)
- **docs/adr/0003** – Secrets strategy (per-service `SECRET_KEY`)
- **docs/adr/0004** – Data ownership (one database per service, no shared tables)
- **docs/adr/0005** – The one named exception to 0004: Weave-Retrieval's read-only access to Weave-Knowledge's chunk store
- **contracts/chunk-store.md** – the full schema contract behind `docs/adr/0005`
- **contracts/n8n-flow.md** – the delegation-token contract behind `WEAVE_DELEGATION_SECRET`

## Troubleshooting

- **Port conflicts**: change the relevant `*_PORT` variable in `.env` (see "Optional" above for the full list and defaults)
- **Out of memory**: increase `WORKER_MEMORY_LIMIT`
- **Redis auth fails**: ensure `REDIS_PASSWORD` matches between the `redis` service's `command` and every `REDIS_URL` that references it
- **Database won't start**: check `pgdata` volume permissions; `docker volume rm weave-ingest_pgdata` to reset (this also wipes `weave_knowledge`/`weave_api` and re-runs `postgres-init/` from scratch on next start)
- **`weave-retrieval` never becomes healthy**: its healthcheck runs `SELECT 1` against `weave_knowledge` — check `docker compose logs weave-retrieval` for a Postgres auth/role error first; the most common cause is `RETRIEVAL_DB_PASSWORD` differing between `postgres` (where the init script set the role's actual password) and `weave-retrieval` (where `DATABASE_URL` tries to connect with it). This only matters on a *fresh* volume, though — the password is set once at role creation, so changing `RETRIEVAL_DB_PASSWORD` later requires an `ALTER ROLE ... PASSWORD` by hand, not just an env var edit.
- **401/503 that isn't a broken health check**: see "Verifying the chain end-to-end" above — almost always one of the four shared secrets drifting between services.
- **n8n-delegated bot turns fail with a generic error**: `WEAVE_DELEGATION_SECRET` differs between `weave-runtime` and `weave-tools-backend` (see "Shared secrets" above) — deliberately the same generic error whether the cause is a mismatch, an expired token, or a wrong format, so check the secret first.

## Network

All services communicate over the `weave_netbridge` bridge network. Services reach each other by container name and their **internal** port (always `8000`/`3000` — the `*_PORT` variables above only remap the **host**-side port, e.g. `http://weave-retrieval:8000`, not `http://weave-retrieval:8002`).

---

Die vollständige Betriebsdoku steht in [../docs/betrieb.md](../docs/betrieb.md):
Pflicht- und geteilte Variablen, Erstinbetriebnahme, stille Fehlkonfigurationen
und ein Selbsttest der ganzen Kette.
