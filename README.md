# Weave

## Overview

This repository is the monorepo for the Weave platform: a document-based
knowledge and chat system made up of six independently deployable application
services, two optional CPU model services, and two web interfaces. Every
service runs as its own process and container image, with its own dependencies
and test suite. The repository is shared; the runtimes remain isolated.

The services originated in six separate repositories (Weave-Ingest,
Weave-Knowledge, Weave-Retrieval, Weave-Runtime, Weave-Tools, and Weave-API).
Those repositories are frozen. The maintained source now lives under
`services/`.

The main benefit of the consolidation is **one configuration instead of six**.
The root-level `weave.yaml` declares each value once and maps shared values to
every service that consumes them. `scripts/weave_config.py render` creates the
gitignored `deploy/.env` file. Secrets are never stored in the repository;
`weave.yaml` only contains the name of the environment variable from which a
secret is read. The companion `check` command detects mismatches that would
otherwise surface at a service boundary as an unexplained 401 or 503 response.

## Repository layout

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

Each service also has its own README for service-specific details. Its
`.env.example` file applies when that service is run independently. The Compose
stack receives its configuration from `weave.yaml`.

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

Pull-request CI runs only the service suites affected by a path change and
fails a frontend build when `npm audit` reports a known high-severity issue.

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
