# Security Policy

## Reporting a vulnerability

Use GitHub Security Advisories: open the repository's Security tab and choose
"Report a vulnerability". The report stays private between you and the
maintainer until an advisory is published.

Do not open a public issue or pull request for a suspected vulnerability, and
do not attach a proof of concept to one. Issues and pull requests here are
public from the moment they are created.

If private reporting is unavailable to you, write to info@werkworks.de and
keep the details out of that first message.

This is a single-maintainer project with no support contract behind it. Reports
are read and acknowledged; there is no guaranteed response or fix window. If a
report stays unanswered longer than you consider reasonable, a reminder to
info@werkworks.de is welcome. Please give a fix a chance to land before
writing about the issue publicly.

## What a useful report contains

| Item | Detail |
|---|---|
| Affected service | The directory under `services/` — one of `api`, `chat`, `embeddings`, `ingest`, `knowledge`, `reranker`, `retrieval`, `runtime`, `tools` — or `contracts/`, `deploy/`, `scripts/` |
| Version | The tag or commit SHA the finding was reproduced against |
| Reproduction | The requests, inputs, or steps in the order you ran them, including the response that shows the effect |
| Configuration | Which deviations from the service's `.env.example` or from `weave.yaml` the finding depends on, and in particular which secrets were set and which were left empty |
| Impact | What an attacker gains, and what access they need to begin |

A finding that only reproduces under one particular configuration is still
worth reporting. Name the configuration.

## Supported versions

The repository carries a single tag, `v0.1.0`, and a single branch, `main`.
There are no release branches and no backports. Fixes land on `main`; report
against `main` or against the commit you tested.

## Scope

In scope: the nine services under `services/`, the cross-service contracts in
`contracts/`, the Compose stack and database initialization in `deploy/`, and
the configuration renderer `scripts/weave_config.py`.

Out of scope:

- **The development defaults shipped in the `.env.example` files.** Some name
  themselves in the value —
  `SECRET_KEY=dev-only-insecure-secret-key-do-not-use-in-production`
  (`services/api/.env.example`, `services/knowledge/.env.example`) — and the
  rest are marked by the comment above them: the SQLite `DATABASE_URL`
  fallbacks and the `fake` embedding and LLM providers. A deployment that
  keeps any of them is misconfigured rather than vulnerable. Values written as
  an instruction rather than a default (`generate-me-with-openssl-rand-hex-32`)
  are placeholders that cannot start a service at all.
- **Anything that presupposes access to `deploy/.env`** or to the environment
  variables it is rendered from. That file is generated, is ignored by Git, and
  holds every service token in the stack; the README's "Internal calls and how
  each one authenticates" table names three of them as effectively master keys.
- **The two documented properties of the shipped Compose stack**: every
  published port binds `0.0.0.0`, and there is no TLS anywhere in `deploy/`.
  Both are stated in the README and left to the operator. A way past an
  authentication check on one of those ports is in scope; the port listening is
  not.
- **Dependency advisories already covered by CI.**
  `.github/workflows/pr-ci.yml` runs `npm audit --audit-level=high` against
  `services/ingest/frontend` and `pip-audit --strict` against
  `services/ingest/backend/requirements.txt`. Those two audits are the only
  ones in the workflow; the other services' dependencies are not audited
  there, and a finding in them is in scope. A demonstrated exploit path
  through a dependency remains in scope either way.

## Security model

Worth knowing before digging in:

- Identity lives in Ingest. The gateway receives it through a single-use
  handoff code and keeps no second user directory (ADR-0006).
- All containers share one flat bridge network. What separates the services is
  the bearer token each one demands, checked fail-closed — an unset token
  yields `503` rather than an open door. The credential on each internal hop is
  tabulated in the README.
- Three databases share one PostgreSQL cluster (ADR-0004): `weave_ingest`,
  `weave_knowledge`, and `weave_api`. Ingest, Knowledge, and Weave-API each
  connect with the same superuser, so what separates them is a database
  boundary, not a privilege boundary. The one privilege boundary inside
  PostgreSQL is `weave_retrieval_ro`, the `SELECT`-only role through which
  Retrieval reads the Knowledge chunk store (ADR-0005).
- Ingest routes the outbound URLs an administrator can set — OIDC discovery,
  Confluence import, export webhooks, the vision endpoint — through an
  SSRF-hardened fetcher (`services/ingest/backend/app/services/safe_fetch.py`)
  that rejects private addresses unless the specific host is allowlisted. The
  endpoints the other services call are requested over plain `httpx` without
  that check.

For the detail: [docs/betrieb.md](docs/betrieb.md) is the operations guide (in
German) — section 5 covers the values that several services must share, section
6 the misconfigurations that fail silently. [docs/adr/](docs/adr/) holds the
architecture decisions, among them ADR-0002 (authentication and authorization),
ADR-0003 (secrets), ADR-0005 (the shared read model), and ADR-0006 (federated
login).
