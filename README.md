# Weave

![Two colleagues beside a stack of binders. One asks "Wer kennt den aktuellen Stand?" — who knows the current state? The other, holding a laptop, answers "Der Kollege. Auf Bali." — our colleague. He is on Bali. The chair between them is empty.](docs/teaser.jpeg)

## What Weave is for

Every organisation keeps its real knowledge in two places: in documents nobody
can find, and in the heads of people who are not at their desk. The first place
is searchable in theory and unusable in practice; the second goes on holiday.

Weave turns the documents into something that answers. It reads PDFs, Office
files, scans, e-mails and Confluence pages, converts them into structured text
with their metadata intact, and makes them answerable in a chat — with the
source next to every answer, so a reader can check where a statement comes
from instead of trusting it.

Three decisions shape the product, and they are the ones worth understanding
before anything technical:

| Decision | What it means in practice |
|---|---|
| **Nothing is indexed by accident** | A processed document is not a published one. Someone reviews it and releases it explicitly; only then does it become findable. Uploading a folder does not expose it. |
| **Permissions are part of the search, not a filter afterwards** | What a person may read is decided inside the database query. Someone outside a knowledge space gets no results — not a hidden result, and not an error that reveals one exists. |
| **No sources, no answer** | When the search finds nothing, the bot says so. It does not produce a fluent, unsourced answer, which is the failure mode that makes such systems untrustworthy in the first place. |

The platform runs on your own infrastructure. The language model is
exchangeable and can be one you host yourself; out of the box the stack starts
with placeholder models and reaches no external service at all, so it can be
evaluated before any data or budget leaves the building.

**What it is not:** Weave does not replace the people who know things, and it
does not decide anything. It answers questions from documents that someone
deliberately published, and it shows its work.

The current release is **v0.2.8** — nine services, running and tested end to
end. The [wiki](https://github.com/bl0rb/Weave/wiki) is the place to
start reading; this README covers the repository itself.

## The services

Six application services, two optional CPU model services and two web
interfaces. Each runs as its own process and container image, with its own
dependencies and its own test suite; the runtimes stay isolated from one
another.

| Path | Responsibility |
|---|---|
| `services/ingest` | Document ingestion: OCR with PaddleOCR, Markdown and frontmatter, quality gate, and the knowledge portal with manual release. **Owns users, teams, OIDC connections, and knowledge spaces (collections)** and is the platform identity provider (ADR-0006). |
| `services/knowledge` | Indexing pipeline: consumes released `document.released` snapshots, chunks and embeds them, writes the pgvector chunk store, and mirrors the collection registry. |
| `services/retrieval` | Hybrid search using pgvector, tsvector, RRF fusion, and cross-encoder reranking. Reads the Knowledge chunk store through a read-only database role (ADR-0005). |
| `services/runtime` | LLM executor: intent routing, YAML-defined bots, Retrieval calls, and delegated n8n turns. The central chat-provider configuration comes from Ingest (ADR-0007). |
| `services/api` | Gateway for API tokens, sessions, and conversations. Identities originate in Ingest and are mirrored here. |
| `services/tools` | MCP server and REST mirror for permission-bound `list_collections` and `search` tools. |
| `services/chat` | Next.js chat interface. It talks only to `services/api` and signs users in through **Sign in with Weave**. |
| `services/embeddings` | Optional OpenAI-compatible CPU embedding service using `intfloat/multilingual-e5-small` and ONNX Runtime at `/v1/embeddings`. |
| `services/reranker` | Optional Cohere/Jina-compatible CPU reranking service using `BAAI/bge-reranker-v2-m3` at `/rerank`. |
| `contracts/` | Cross-service contracts: `frontmatter.schema.json`, `openapi.json`, `chunk-store.md`, `internal-chat.md`, `n8n-flow.md`, `indexing-status.md`, and event contracts. |
| `deploy/` | Docker Compose stack, local build override, and PostgreSQL initialization. `deploy/.env` is generated and ignored by Git. |
| `docs/` | Operations guide, knowledge-portal guide, ADRs, browser-readable architecture artifacts, and the screenshot-based user wiki. |
| `weave.yaml`, `scripts/` | The single configuration source and its `render` and `check` commands. |

The [knowledge portal guide](docs/wissensportal.md) explains the separation
between processing and publication. Under the normal workflow, only content
that a user explicitly releases is indexed.

### Administration and retrieval controls

The Ingest administration UI supports persistent overrides for bundled Runtime
bots and confirmed ZIP backups of local `uploads/` and `results/` storage. No
admin file browser or unrestricted individual-file read endpoint is exposed.
The **Suche & Modelle** screen configures embedding providers and optional
reranking, including an explicit **Aus** mode (`RERANK_PROVIDER=none`) and a
slider between lexical full-text and semantic vector search. Embedding model
or dimension changes require a complete reindex; reranking can be disabled
without reindexing.

The Helm chart exposes the deployment-level choice through `reranker.enabled`
and `config.retrieval.RERANK_PROVIDER`; use `reranker.enabled=false` and
`RERANK_PROVIDER=none` when reranking is not desired.

Each service also has its own README for service-specific details. Its
`.env.example` file applies when that service is run independently. The Compose
stack receives its configuration from `weave.yaml`.

### One configuration for all of them

`weave.yaml` declares each value once and maps the shared ones into every
service that consumes them. `scripts/weave_config.py render` writes the
gitignored `deploy/.env` from it; the file is never edited by hand. Secrets are
not stored in the repository — `weave.yaml` holds only the name of the
environment variable a secret is read from. The companion `check` command finds
the mismatches that would otherwise surface at a service boundary as an
unexplained 401 or 503.

## Technology stack

| Layer | Technology |
|---|---|
| APIs and workers | Python, FastAPI, Pydantic Settings, SQLAlchemy, Alembic, Celery |
| Web interfaces | Node.js 26, Next.js, React, TypeScript, Tailwind CSS |
| Persistent data | PostgreSQL with pgvector; separate `weave_ingest`, `weave_knowledge`, and `weave_api` databases |
| Queues and coordination | Redis with isolated logical databases and named Celery queues |
| Retrieval | HNSW vector search, PostgreSQL full-text search, Reciprocal Rank Fusion, cross-encoder reranking |
| Local CPU models | ONNX Runtime for multilingual E5 embeddings; BGE multilingual cross-encoder for reranking |
| Integrations | Confluence, n8n, OpenAI-compatible chat endpoints, MCP |
| Deployment target | Container images, local Docker Compose, and Kubernetes/Helm for enterprise operation |

## Quick start

```bash
# 1. Provide secrets as environment variables through direnv, a password
#    manager, or a CI secret store. `render` reports every missing value at once.
python scripts/weave_config.py render      # writes deploy/.env
python scripts/weave_config.py check       # checks cross-service consistency

# 2. Build and start the stack. The large OCR worker is opt-in.
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.weave.yml \
  -f deploy/docker-compose.local.yml \
  up -d --build

# 3. Start the OCR worker. Without it, uploaded jobs remain PENDING.
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.weave.yml \
  -f deploy/docker-compose.local.yml \
  --profile ocr up -d weave-ingest-worker
```

If port 3000 is already in use, export `FRONTEND_PORT=3002` and
`CORS_ORIGINS='["http://localhost:3002"]'` before rendering. `check` reports
when only one of these values is set. The knowledge portal then opens at
`http://localhost:3002`, and the chat interface at `http://localhost:3001`.
The first local administrator is created through the portal setup page.

[docs/betrieb.md](docs/betrieb.md) documents all required and optional values,
shared-secret relationships, silent misconfiguration risks, authentication,
real model services, and the end-to-end self-test.

## Network design

All fourteen containers share **one flat bridge network** (`weave_netbridge`).
There is no second network and no `internal: true` segment, so any container
can reach any other on its container port. What separates the services is
therefore **not the network but the bearer token each one demands** — every
service token is checked fail-closed, and an unset token yields `503` rather
than an open door.

![Weave network topology: browsers and n8n clients reach four services that are meant to be
published, five internal-only services are published on the host as well, and only PostgreSQL
and Redis have no host port.](docs/diagrams/network-topology.svg)

*Source: [docs/diagrams/network-topology.html](docs/diagrams/network-topology.html)*

Note what the diagram does *not* contain: Runtime never calls Tools. It only
hands the address on to an n8n flow, which then comes back into the platform
from the outside.

### What is published on the host

The Compose stack publishes ten host ports. Only five of them are meant for
anyone but the stack itself:

| Port | Service | Who needs it |
|---|---|---|
| 3000 | Ingest portal UI | Users |
| 8000 | Ingest API | **Users** — the portal UI does not proxy; the browser calls this directly, so both 3000 and 8000 must be reachable |
| 3001 | Chat UI | Users |
| 8004 | Weave-API gateway | Browsers during the login redirect, plus optional OpenAI-compatible clients |
| 8005 | Tools REST | Only an n8n flow or MCP client running outside the stack |
| 8001, 8002, 8003, 8006, 8007 | Knowledge, Retrieval, Runtime, Embeddings, Reranker | **Nobody.** These are internal-only services that the Compose file publishes anyway |

Two warnings follow from this, and both apply to the shipped configuration:

- **Every published port binds `0.0.0.0`.** No `ports:` entry carries a bind
  address, so on a reachable host all ten are exposed to the network — including
  the five that no external client should ever reach. Prefix them with
  `127.0.0.1:` or keep the host behind a firewall.
- **There is no TLS anywhere.** `deploy/` contains no reverse proxy, no ingress
  and no certificate handling; every published port and every internal hop is
  plain HTTP. Terminating TLS in front of the stack is left to the operator
  (see [deploy/README.md](deploy/README.md)).

Only PostgreSQL and Redis have no host port at all.

### Internal calls and how each one authenticates

| From | To | Purpose | Credential |
|---|---|---|---|
| Chat UI | Weave-API | Every chat request; the API base URL never reaches the browser | The user's session, forwarded server-side |
| Weave-API | Runtime | Run a chat turn, list bots | `RUNTIME_API_TOKEN` |
| Weave-API | Retrieval | Resolve readable collections (never search) | `RETRIEVAL_API_TOKEN` |
| Weave-API | Ingest | Redeem the single-use login handoff code (ADR-0006) | `HANDOFF_SECRET` |
| Runtime | Retrieval | Hybrid search and collection scope | `RETRIEVAL_API_TOKEN` |
| Runtime | Ingest | Fresh chat-provider snapshot and the managed n8n bots (ADR-0007) | `CHAT_CONFIG_SERVICE_TOKEN` |
| Runtime | n8n | Delegate a whole turn | `X-Weave-Signature` (HMAC over the exact body) and a target that must match `N8N_ALLOWED_BASE_URLS` |
| Tools | Retrieval | Search on the caller's behalf | `RETRIEVAL_API_TOKEN` |
| Tools | Weave-API | Resolve who owns a personal token | `INTROSPECTION_SERVICE_TOKEN` |
| Ingest | Knowledge | `document.released` (the only regular indexing trigger), `collection.updated`, indexing status | HMAC-SHA256 over the raw body, `X-Weave-Ingest-Signature` |
| Knowledge | Ingest | Fetch the released Markdown snapshot and the collection registry | `WEAVE_INGEST_API_TOKEN` — must belong to an Ingest **administrator** |
| n8n | Tools | Search as the person who asked | Two headers: `X-Tools-Service-Token` *and* a short-lived delegation token |

Three of these are effectively master keys and deserve the strictest handling:
`INTROSPECTION_SERVICE_TOKEN` resolves the identity behind any personal token,
`WEAVE_INGEST_API_TOKEN` pulls the complete collection access map, and
`CHAT_CONFIG_SERVICE_TOKEN` retrieves the decrypted LLM provider key.

### Data stores

Three databases share one PostgreSQL cluster (ADR-0004): `weave_ingest`,
`weave_knowledge`, and `weave_api`. Ingest, Knowledge, and Weave-API each
connect with the same superuser, so the separation between them is a database
boundary, not a privilege boundary. The single real privilege boundary is
`weave_retrieval_ro`: Retrieval reads Knowledge's database directly through a
`SELECT`-only role, because pgvector similarity and tsvector ranking have to run
as SQL *inside* the database (ADR-0005). The schema is therefore a contract —
[contracts/chunk-store.md](contracts/chunk-store.md).

Redis is split by logical database only: `0` for Ingest, `1` for Knowledge
(ADR-0001), both behind one shared password. Retrieval, Runtime, Tools, and
Weave-API never touch Redis.

### Outbound connections

Required for a default start:

| From | To | Why |
|---|---|---|
| Docker host | `ghcr.io`, Docker Hub | Image pulls |
| Embeddings, Reranker | `huggingface.co` and its CDN | Model weights, downloaded on first start; cached in a volume afterwards |
| Ingest worker | `*.bcebos.com` (or `huggingface.co` with `PADDLE_PDX_MODEL_SOURCE=HuggingFace`) | PaddleOCR weights. Without this egress, jobs silently fall back to plain-text extraction |

Optional, and each one off by default: the LLM endpoint (`LLM_PROVIDER=fake`
until configured), n8n webhooks (`N8N_ALLOWED_BASE_URLS` empty disables
n8n-backed bots entirely), OIDC providers, Confluence imports, an
OpenAI-compatible vision endpoint, hosted embedding or rerank providers, and
user-configured export webhooks. Every admin-supplied URL goes through an
SSRF-hardened fetcher that rejects private addresses unless the specific host
is allowlisted — opening the firewall alone is not enough.

[docs/firewall-requirements.md](docs/firewall-requirements.md) holds a
port-by-port connection matrix, but note its scope: it predates this repository
layout and covers only the Ingest service and its own Compose files. The section above is
the platform-wide picture.

## Authentication and authorization

Weave provides one account experience across the platform. Administrators
manage local users, teams, and OIDC providers in Ingest. Anyone who can sign in
there can use the chat with the same identity, regardless of whether the
account is local or federated. The API gateway does not maintain a second user
directory; it receives the identity through a short-lived, single-use handoff
code (ADR-0006).

Teams grant access to knowledge spaces. Runtime intersects the user's readable
collections with the bot configuration and any request filter before search.
n8n and MCP receive short-lived delegated scopes and cannot widen them. Shared
service credentials remain server-side and are checked fail-closed. Run
`python scripts/weave_config.py check` after every secret or endpoint change.

## Testing

Every Python service has its own virtual environment and pinned requirements.
The Python version and exact test command may differ by service; the CI workflow
in `.github/workflows/pr-ci.yml` is the executable reference.

```bash
# services/tools, services/embeddings, services/reranker
cd services/<service>
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q

# services/ingest, services/knowledge, services/retrieval, services/runtime,
# and services/api
cd services/<service>
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pytest -q backend/tests

# services/chat and services/ingest/frontend
npm install
npm test

# Configuration renderer and validator
services/tools/.venv/bin/python -m pytest scripts/tests -q
```

Pull-request CI runs only the service suites affected by a path change —
`.github/workflows/pr-ci.yml` has one job per service, gated on a path filter —
and fails a frontend build when `npm audit` reports a known high-severity
issue. The two model services skip their real-weight tests there
(`RUN_REAL_MODEL_TESTS`, `RERANKER_SKIP_MODEL_TESTS`) rather than download
several gigabytes per commit.

## Documentation

| Location | Contents |
|---|---|
| [docs/betrieb.md](docs/betrieb.md) | Authoritative operations guide |
| [docs/betriebshandbuch.html](docs/betriebshandbuch.html) | Browser-readable operations handbook |
| [docs/architektur.html](docs/architektur.html), [docs/architektur-detail.html](docs/architektur-detail.html) | System architecture and the detailed sequence of a knowledge query |
| [docs/bauplan.html](docs/bauplan.html) | Transformation plan and implementation status |
| [docs/glossar.html](docs/glossar.html) | Technical terms and their role in Weave |
| [docs/wissensportal.md](docs/wissensportal.md) | Knowledge spaces, release workflow, and indexing status |
| [docs/screenshots/user-wiki/](docs/screenshots/user-wiki/) | Sanitized screenshots covering the user and administrator interfaces |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [contracts/](contracts/) | Data and API contracts between services |
| [SECURITY.md](SECURITY.md) | How to report a vulnerability, and what is in scope |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) | Bundled and runtime-downloaded third-party components and their terms |
