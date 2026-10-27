# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note:** History up to 08/2026 originates from the source project PaddleDoc. Since the
> Weave transformation, the product is called Weave Ingest. Entries below predating that
> change are left as written and refer to the project under its former name.

## [Unreleased]

## [0.7.17] - 2026-10-27

### Added
- **Websites and intranet pages as sources** ([docs/wissensportal.md](../../docs/wissensportal.md)):
  a website connection (`kind: website`, auth none / HTTP Basic / bearer; form logins, SSO and
  Kerberos/NTLM are marked unsupported) plus an import with scope `url`, a crawl scope (this
  page / its section / the whole host), linked PDF/Office documents as attachments, an optional
  legal-source flag and the usual refresh schedule. Pages are fetched through `safe_fetch`,
  keyed by URL, converted with `fetched_at` / `legal_status` frontmatter and crawled by the
  unchanged chunked import task; a refresh skips unchanged pages (content hash) and chains
  changed ones. Private-network targets stay blocked until an admin releases them per
  connection (`allow_private_target`, bound to that host); `robots.txt` is honoured.
  Migration `0056_source_catalog_website`.
- **Source catalog** (Wissensbereiche › Quellenkatalog, `/api/v1/source-catalog`): curated,
  reusable website entries with categories, search and a recommended scope, seeded with four
  laws from gesetze-im-internet.de. Everyone may browse and import from an entry (into a space
  they control, with a confirmed scope); only admins manage entries. An entry is a template and
  never imports a whole domain.
- **Portal:** "Quelle hinzufügen" gains "Website / Intranet" and "Aus Quellenkatalog" (a missing
  connection for the entry's host is created and tested on the fly); Verbindungen gets a
  Websites tab; a knowledge space lists origin, scope, schedule, last successful fetch and a
  readable error per source.
- **Provenance in the chat:** `source_kind: website`, `fetched_at` and `legal_status` flow from
  Ingest releases through Knowledge, Retrieval and Runtime to the chat's source cards, which
  show the fetch date and a stated legal status next to the link
  ([contracts/internal-chat.md](../../contracts/internal-chat.md), frontmatter schema).

### Fixed
- Running configuration and S3 backups, review round (#372): the password of `REDIS_URL` is
  masked in the overview and left out of the `.env` export like `DATABASE_URL`; the S3 test
  drops `S3_KMS_KEY_ID` unless `S3_SSE` is `aws:kms`; an S3 restore refuses an active run or
  a populated target before fetching the archive; a malformed Redis URL is a failed check,
  not a 500.
- `POST /api/v1/auth/admin/bots/import` applies an import only on an explicit `dry_run=false`:
  since fastapi 0.147 a missing `bool` form field parses as `False`, which turned an omitted
  `dry_run` into a real import (#377).

### Security
- pypdf 6.21.2 (CVE-2026-115572, CVE-2026-115580, CVE-2026-115585).
- dnspython 2.9.0 (CVE-2026-114605; transitive via email-validator).

## [0.7.16] - 2026-10-23

### Added
- **Running configuration in the admin UI** (Betrieb & Sicherheit → Konfiguration): every
  environment setting with secrets masked and a customized/default flag, the settings saved
  in the UI, connection checks for database, Redis, S3 and the backup directory, and an
  export as `.env` (secrets left out) or JSON (`GET /api/v1/admin/config`,
  `POST /api/v1/admin/config/checks/{name}`, `GET /api/v1/admin/config/export`).
- **S3 connection test** with form values that are not saved: head bucket, write/read/delete
  a probe object with SSE/KMS if set; a passing test shows a copyable env block.
- **Backups to S3:** "In S3 speichern" writes the archive to `<S3_KEY_PREFIX>backups/`;
  archives in the bucket can be listed, restored and deleted from the admin UI
  (`/api/v1/admin/backup/s3*`).

## [0.7.15] - 2026-10-23

### Added
- **Intake question per bot:** an optional `intake` (question, options, free text, label) is
  asked once before the first knowledge answer of a conversation. The answer is added to the
  system prompt as "<label>: <answer>" on every later turn, and the question asked first is
  answered right after. The chat shows the options as chips (migration 0055).
- **Blocked topics per bot:** `guard.blocked_topics` ({name, description, keywords}) with
  `guard.blocked_topic_reply` are checked in the router before any retrieval, n8n or answering
  LLM call. `ROUTER_MODE=llm` classifies the topic in the same router call; keywords apply in
  both modes. Both features can be set in the portal's bot editor and in bot documents.
- **Review weaknesses:** the review view highlights findings in the text, lists them, and asks
  the chat provider for a recommendation on demand (new
  `POST /api/v1/portal/documents/{id}/analysis`, cached per markdown version).

### Changed
- **Unified review flow:** finished documents in a knowledge space open in the review view
  from the job details; technical information moved into a "Technische Details" dialog.
- Access and "Neuer Wissensbereich" are guided steps with the same layout as the bot editor.
- Bot tiles are always a grid, with export and delete in a "…" menu and readable metadata.
- Jobs, imports, connections and settings use the same page layout; back links behave like
  the browser's back button.
- **Desktop app (desktop-v0.1.7):** the quick bar can be dragged and reopens where it was left
  (double-click on the grip resets it); on macOS it appears over full-screen apps instead of
  switching Spaces; a labelled "Fragen | Suchen" switch explains both modes; a hidden tray
  window no longer costs CPU while idle.

### Security
- opentelemetry-api 1.45.1 (CVE-2026-114603) in api, embeddings, ingest, knowledge,
  reranker, retrieval and tools.

## [0.7.14] - 2026-10-21

### Added
- **Portal redesign:** "Dokumente" now merges the former Aufgaben and Dokumente views, with
  view presets (Zu prüfen — default, Alle, Geparkt, Fehler, Von mir freigegeben) and one
  filter bar for all of them (search, Wissensbereich, Stand, Bewertung, Quelle). The filters
  live in the URL, and the status and grade filters have tooltips explaining each value
  (incl. "Ohne Bewertung"). Rows link to the original Confluence page or folder path.
  "Verbindungen" is its own navigation entry.
- **Übersicht** with a central search (filenames, Confluence titles, paths) and metrics:
  per-space counts, Confluence sync status, review wait time and releases per week
  (new `GET /api/v1/portal/stats`; `GET /api/v1/portal/documents` gains `q`, `source`,
  `released_by_me` and `review_state=failed`).
- **Wissensbereich detail** is management only: sources with "Jetzt synchronisieren" (starts
  the space's own Confluence imports instead of only reloading), access, maintenance
  (reindex with explanation, ZIP export) and a link to its filtered documents.
- **Chat options per bot:** `show_sources` hides the sources panel, `disclaimer` shows a
  note next to the composer (e.g. "Testsystem – keine verbindlichen Aussagen"). Both can be
  set in the bot editor of the portal and in bot documents (migration 0054).
- **Chat sources:** a "Quellen anzeigen" button under each answer with sources; the panel
  starts collapsed and remembers its state per browser; only sources the answer cites as
  `[N]` are listed (all of them if it cites none).

### Changed
- **Self-service API tokens are off by default:** `SELF_SERVICE_API_TOKENS_ENABLED` (Weave
  API, default `false`) hides "API-Zugang" in the chat and makes `/v1/me/tokens` answer
  `403`. Tokens issued earlier stay valid. Set it to `true` to keep the previous behavior.
- Daily Confluence sync is pre-selected when adding a Confluence source.

### Removed
- The `/aufgaben` page (no redirect); its content lives in "Dokumente".

### Fixed
- Saving a bot in the portal's bot editor no longer wipes its `clarify_*` settings.

### Security
- pypdf 6.21.1 (CVE-2026-113786).

## [0.7.13] - 2026-10-21

### Security
- **certifi 2026.10.19** in every Python service (api, embeddings, ingest, knowledge,
  reranker, retrieval, runtime, tools), fixing GHSA-6q48-6c7w-9gw2 that `pip-audit`
  flagged on 2026.7.22.

### Changed
- Ingest worker: Pillow 12.3.0 → 13.0.0 (pulled in by paddlex; OCR smoke-tested on PNG and
  rendered PDF pages). Ingest backend: aiohttp 3.14.4, yarl 1.25.1. Ingest frontend: vite 8.3.4.
- Docs: Claude Code and Entra steps in the MCP SSO guide, plus a platform overview diagram.

## [0.7.12] - 2026-10-20

### Added
- **Portable bot documents** ([contracts/bot-document.md](../../contracts/bot-document.md)):
  Runtime's `BotConfig` is the canonical, versioned bot format (`schema_version: 1`, JSON
  Schema in `contracts/bot-document.schema.json`, checked by both services' tests). The bot
  editor exports any bot as YAML/JSON (`GET /api/v1/auth/admin/bots/{id}/export`) and imports
  one with a dry-run report, conflict strategy and warnings (`POST /api/v1/auth/admin/bots/import`);
  secrets are never exported (`${WEAVE_SECRET}` placeholder), grants stay with the instance.
  Guard fields without a column live in the new `config_extras` JSON column (migration 0053).
- **The Weave mark moves in four ways** (weave, turn, flip, shuttle), picked at random per
  start and never twice in a row; the sidebar's mark weaves while jobs or import runs are in
  progress, a click on the mark switches the motion. Canonical mark and keyframes in
  `packages/weave-mark`, kept in sync with Weave-Chat and the desktop app by
  `scripts/check_weave_mark.py`.
- **Meeting transcripts (`.vtt`) as knowledge**: WebVTT exports from Teams, Zoom, Webex and
  Google Meet can be uploaded like `.md`/`.xml` (no OCR, engine `text-vtt`). The converter
  (`app/services/vtt_transcript.py`) drops NOTE/STYLE/REGION blocks, platform boilerplate
  ("Recording has started", "X joined the meeting", caption notices) and non-speech
  annotations (`[Musik]`, `(unverständlich)`), merges consecutive cues of one speaker into a
  turn (de-duplicating roll-up captions) and renders participants, duration and one
  `**Speaker** [hh:mm:ss]: …` paragraph per turn. Speakers come from `<v Name>` voice tags or,
  for Zoom, a recurring `Name:` prefix.
  - Optional free-text **clean-up instructions** per upload (`transcript_instructions` form field
    on `POST /collections/{id}/upload`, textarea in the portal upload form when a `.vtt` is
    selected): "Entferne den Smalltalk am Anfang", "kürze Max Mustermann zu Max", … are applied
    by the central chat provider (`default` endpoint under Administration › AI endpoints) to the
    rendered transcript in turn-aligned chunks (`app/services/transcript_refine.py`). The job
    fails with a clear message when no provider is enabled or the provider returns an empty
    transcript; the instructions are kept in `processing_info.settings` and survive restarts.
- **S3 server-side encryption headers** ([betrieb.md §6.1](../../docs/betrieb.md)): optional
  `S3_SSE` (`AES256` or `aws:kms`) and `S3_KMS_KEY_ID` set `x-amz-server-side-encryption` and
  `x-amz-server-side-encryption-aws-kms-key-id` on every S3 write (originals, markdown, backup
  import, `storage migrate`; with multipart uploads on `CreateMultipartUpload`), for bucket
  policies and SCPs that check the header in the request. Unset, behaviour is unchanged.

### Changed
- **Confluence import runs restart automatically** ([betrieb.md](../../docs/betrieb.md)): a run
  whose worker died or whose continuation message was lost is re-sent by a stale-run reaper on
  every publication tick (not only at worker start) and resumed from its persisted frontier,
  the active-run cap no longer fails such a run, a failed continuation enqueue or a lost
  DB/broker connection leaves the run running for the reaper, and transient Confluence errors
  (HTTP 429/5xx, network) are retried with exponential backoff at chunk start and per page (up
  to three times per page). New `IMPORT_MAX_RECOVERIES` (default 5) bounds consecutive
  lost-worker resumptions and backoff retries per run.
- **Weave-Runtime runs every chat turn through one compiled LangGraph graph**
  (`app/services/bot_graph.py`): direct RAG, n8n and agent mode (single and multi-subagent)
  share one topology and one streaming bridge; the internal chat contract, trace contents and
  error texts are unchanged.

## [0.7.11] - 2026-10-19

### Added
- **S3 as alternative object storage** ([betrieb.md §6.1](../../docs/betrieb.md)): with
  `OBJECT_STORAGE_BACKEND=s3` (default `db`), new originals, imported images/attachments and
  generated markdown (job result, editor versions, release snapshots) go to an S3-compatible
  bucket (AWS S3, MinIO, Ceph RGW). The bucket is configured with `S3_BUCKET`, `S3_ENDPOINT_URL`,
  `S3_REGION`, `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` (empty = IAM role/IRSA), `S3_KEY_PREFIX`
  and `S3_ADDRESSING_STYLE`. Each object keeps its own backend, so mixed data stays readable.
  - Migration in both directions while the services run, interruptible and repeatable:
    `python -m app.cli storage migrate --to s3|db`, plus `storage status` and `storage sweep`
    (deletes S3 keys without a database row; the prefix must belong to this instance alone).
  - Backups include S3 objects and restore them into whichever backend the target is configured
    for, under fresh keys. While an export or import runs, nothing deletes S3 keys (a PostgreSQL
    advisory lock); garbage collection catches up afterwards.
  - Migration `0052_markdown_objects`: object references for the three markdown columns, CHECK
    constraints instead of NOT NULL, an index on `stored_objects.storage_key`. The downgrade refuses
    while any markdown is still in S3.
  - New dependency `boto3`.
- **Desktop app:** the agent list reloads every time the window or overlay opens; "Verlauf leeren"
  replaces "Neue Unterhaltung", and after 30 minutes away the app starts a fresh conversation; "Im
  Web öffnen" opens the conversation in the web chat (Weave-API returns `chat_url` on `GET /v1/me`
  from the new `CHAT_PUBLIC_URL`; the web chat opens `?conversation=<id>`). A new question scrolls to
  the top and the answer streams in below it without pulling the view down.
- **Bot administration:** an active managed bot that Weave-Runtime did not load (typically an n8n
  webhook outside `N8N_ALLOWED_BASE_URLS`) is marked "Nicht im Chat", with the likely cause.
- User-flow diagram `docs/diagrams/user-flow.{html,svg}` (VS Code/MCP, desktop app, browser chat).

## [0.7.10] - 2026-10-19

### Added
- **Knowledge memory as search support** ([ADR-0009 §10](../../docs/adr/0009-wissensgedaechtnis.md)),
  only with `KNOWLEDGE_MEMORY_MODE` ≠ `off` and for bots without `retrieval.filters`; with the memory
  off nothing changes.
  - Weave-Runtime: a knowledge turn first asks `POST /api/v1/memory` which documents matter and
    focuses the chunk search on them (new `SearchFilters.document_ids` in Weave-Retrieval, retry
    without focus when nothing is found, `RetrievalTrace.focus_documents`, `timings_ms.memory_ms`).
    Sources stay chunks only; the summaries reach the LLM as a non-citable orientation block.
  - Agent mode: subagents get the tool `memory_lookup(query, collection?)` (overviews and document
    summaries within their own scope, at most two per research run, never citable) and can search a
    single document via `search_knowledge(document_id=...)`; `SubagentTrace.memory_lookups`.
- **Desktop app setup from Weave-Chat:** the chat rail's new "Desktop-App einrichten" dialog opens
  `weave://setup?server=<API address>` (the browser-reachable `WEAVE_API_PUBLIC_BASE_URL`, via
  `GET /api/desktop`) plus a download link. The desktop app registers the `weave://`
  scheme (`tauri-plugin-deep-link`), shows the proposed address in its settings window and saves it
  only after the user accepts; only the address travels in the link, never a credential, and an
  IT-managed `ServerUrl` stays unchanged.

## [0.7.9] - 2026-10-18

### Added
- **Knowledge memory** ([ADR-0009](../../docs/adr/0009-wissensgedaechtnis.md)), opt-in via
  `KNOWLEDGE_MEMORY_MODE` in `weave.yaml` (`off` by default, `extractive` without LLM budget, `llm`
  with the central chat provider and extractive fallback). After every change to a knowledge space
  (release, new version, re-index, withdrawal, rename), debounced per space, Weave-Knowledge builds a
  summary per document (title, type, summary, key points) and an overview per space. Bots answer
  "Was weißt du?", "Was ist im Wissensbereich X?", "Welche Dokumente gibt es zu Y?" and "Worum geht
  es in Dokument Z?" from it with sources (new intent `overview`, `Source.memory_kind`), falling back
  to the regular search when the memory has nothing.
  - Weave-Knowledge: migration `0007_knowledge_memory` (table `knowledge_memory`), task
    `weave.knowledge.build_memory`, scheduled by the existing collection-sync tick.
  - Weave-Retrieval: `POST /api/v1/memory`, read with the same permission predicate as the search;
    it refuses calls without a concrete `allowed_collections`.
  - Production: `GRANT SELECT ON knowledge_memory TO weave_retrieval_ro` unless default privileges
    already cover new tables.
  - Administration › KI › **Wissensgedächtnis**: mode, LLM endpoint, wait after changes, documents
    per run and input length are editable (migration `0051_knowledge_memory_config`, projected to
    Knowledge and, via the chat-provider snapshot, to Runtime). Metrics per knowledge space
    (coverage, LLM vs. extractive, pending/building, last run, duration, errors, LLM calls and
    failures) and a confirmed "rebuild everything" action.
  - Weave-Chat and the desktop app mark memory sources as "Zusammenfassung" / "Bereichs-Übersicht".

### Changed
- **License:** from this release on, Weave is licensed under the MIT License with the Commons
  Clause license condition. Using, modifying and self-hosting stay free, including inside an
  organization; selling the software, or a product or service (hosting, consulting, support) whose
  value derives entirely or substantially from it, is not permitted. Releases up to 0.7.8 remain
  available under the plain MIT License.

## [0.7.8] - 2026-10-18

### Added
- **Weave for the desktop** (`clients/desktop`): a tray / menu-bar app for macOS and Windows. Click
  the icon or press `CmdOrCtrl+Shift+Space`, pick an agent and ask (streamed answer with sources),
  or search every knowledge space you may read. Sign-in through the organisation's SSO; the
  session token lives in the OS credential store. Server URL and hotkey can be enforced via
  MDM/Intune. Installers are published separately on `desktop-v*` tags.
- **Weave-API: native-app login (RFC 8252).** With `api.NATIVE_LOGIN_ENABLED: true` (default
  `false`), both login paths also accept `return_to=http://127.0.0.1:<port>/callback` (or
  `[::1]`), but only together with a PKCE `code_challenge`. The one-time code then redeems at
  `POST /v1/auth/session/exchange` only with the matching `code_verifier`. Migration
  `0008_exchange_code_challenge` adds the column. Required for the desktop app.
- **Weave-API: `POST /v1/search`.** Hybrid search over exactly the collections the caller may
  read; a `collection` outside that set returns an empty list.

## [0.7.7] - 2026-10-18

### Added
- **Several synced Confluence imports per connection.** An import is now a configuration
  (`ImportSync`: scope, target knowledge space, options, schedule) and runs are executions of it
  (`import_runs.sync_id`). Before, a connection had one auto-sync that re-ran its newest finished
  run, whatever its scope, so importing a second space silently changed what was synced.
  - Migration `0050_import_syncs` creates one configuration per distinct (connection, scope, space)
    of the existing runs; the one the old scheduler would have re-run inherits the connection's
    schedule, all others start disabled. Restoring an older backup runs the same backfill.
  - The scheduler starts every due configuration, longest-waiting first, still one crawl per
    connection at a time.
  - API: `GET/PATCH/DELETE /api/v1/import/syncs/{id}`, `GET /api/v1/import/syncs`,
    `POST /api/v1/import/syncs/{id}/run`. `POST /api/v1/import/runs` attaches the run to the
    configuration of its scope (creating it) and accepts `sync: {enabled, interval_seconds}`.
    Scope and space of a configuration are fixed; options and schedule can be edited.
  - Verbindungen › Confluence lists each connection's imports with target space, auto-sync switch
    and interval, last success, next sync, last run and errors, plus "Sync now", "Edit", "Remove"
    and "Add import".
  - Import wizard: required target knowledge space, auto-sync section, edit mode
    (`/imports/new?sync=<id>`, also reached from any run of the configuration) with
    "Save" / "Save & sync now"; folder/subfolder fields removed. "Quelle hinzufügen" can import a
    whole space and saves the daily auto-sync with the import instead of switching the connection.

### Changed
- Navigation reorganised. Aufgaben has tabs Übersicht · Prüfen & freigeben (`/reviews`) ·
  Verarbeitung (`/processing`), Dokumente has Übersicht · Erweitert (`/jobs`); the sidebar
  keeps the parent entry highlighted. URLs are unchanged.
- Uploading is one flow: the legacy four-step wizard `/processing/new` now redirects to
  `/sources/new` (Quelle hinzufügen, with profile choice) and has been removed.
- Admin paths match their labels: `/admin/menschen` → `/admin/konten`, `/admin/wissen` →
  `/admin/ki`; the old paths redirect and keep `?bereich=`.
- Admin › Verarbeitung and Admin › Betrieb no longer list links to user pages or to redirect
  stubs (Mail-Import, Suche); Betrieb › Werkzeuge keeps index maintenance.
- Profile menu: "Farbschema" is a full-width row aligned with the other entries.

### Removed
- `refresh_enabled` / `refresh_interval_seconds` on `PATCH /api/v1/import/sources/{id}` and the
  `refresh_*` fields of source responses (now per import configuration); sources report
  `sync_count`. The columns stay in the database for downgrade.
- VL benchmark (`/benchmark`, `/api/v1/benchmarks*`) and its links under Admin → Verarbeitung and
  Admin → Betrieb. VL connections stay available as processing profiles; the read-only
  `GET /api/v1/vl-connections` list is unchanged. Existing benchmark runs and their variant jobs
  stay in the database and remain hidden from job lists, search and stats.

### Fixed
- Stray Chinese/Japanese characters in extracted Markdown. PP-OCRv6 recognises Chinese and
  Latin script with one shared model, and on noisy regions it occasionally emits an ideograph
  or full-width punctuation. For predominantly Latin documents (CJK share ≤ 5 %) these
  characters are now removed and full-width punctuation is mapped to ASCII; real Chinese or
  Japanese documents and the frontmatter are left untouched. The count is recorded as
  `stray_cjk_removed` in the processing details.

## [0.7.3] - 2026-10-10

### Added
- Portal index status rides out short Knowledge outages: one retry with a 5 s timeout, then for
  up to 5 minutes the last status Knowledge confirmed for the same release, shown with its time
  (`stale`, `checked_at`; kept in Redis for all replicas), then a neutral "Updating status…"
  (`retrying`), and only after 10 minutes the warning. Revoked access, a new release or snapshot,
  or an invalid answer clears it at once.

### Changed
- Portal and administration call teams "Gruppen" / "groups"; API fields, database and contracts
  keep `team`.
- Portal and administration dialogs are one size wider (576 / 768 / 1152 px).

### Fixed
- Documents page, home and knowledge-space overview showed "Index status unavailable" on every
  row once more than 50 released documents were listed, because the status lookup exceeded its
  50-ID limit; the portal now queries in batches of 50.
- The member search in the knowledge-space access dialog no longer renders as a field inside a
  field.

## [0.7.9] - 2026-10-18

### Removed
- The direct OpenWebUI integration has been retired: connection CRUD, push API, worker task,
  frontend panels and client-specific configuration are gone. The generic OpenAI-compatible
  chat gateway remains available independently in Weave API.
- `collection.updated` can no longer be subscribed to by user-managed export webhooks. It is
  now a dedicated signed Ingest-to-Knowledge freshness notification containing only the
  collection slug and timestamps, never the system-wide team ACL matrix.
- The retired mail API schema has been removed. Individual `.eml` files remain supported by
  the normal source upload, review and release workflow.

### Added
- Review portal: reviewers can park a document (`POST /api/v1/portal/documents/{job_id}/park`,
  bulk action `park`) next to "don't release"; parked documents have their own filter. The review
  page pages through the queue, one task inbox collects open work and errors, and a bulk release
  skips documents a reviewer skipped or parked. Knowledge-space names are unique among the spaces
  a user can see.
- Named LLM endpoints (Administration › Chat & LLM) and per-bot endpoint selection (migration 0045).
- Admin dry-run/apply job that withdraws Knowledge documents whose job or space no longer exists
  (`POST /api/v1/admin/knowledge/orphans`).
- Horizontal scaling: originals, results and images live in PostgreSQL, OCR and default worker
  pools with claim tokens and heartbeats, database-backed rate limits and migration locks.
- SSO for MCP clients through OAuth access tokens (authorization code with PKCE).
- Owners, members and readers per knowledge space, sharing with individual people, and owners and
  users per bot, checked by Runtime; self-service personal API tokens.
- German/English portal and administration, language stored per user account; system status page,
  source provenance in citations and a portal Markdown editor.

### Fixed
- Space grants now scope documents, spaces, bots, Confluence imports, page states and run control;
  legacy Knowledge rows without a space no longer bypass grants.
- Migration 0041 keeps the case-insensitive email index.

- "Neu indizieren" as a regular action: per document on the review page (highlighted when
  the indexing failed) and per knowledge space ("Wissensbereich neu indizieren"); both
  re-deliver the release with `reindex: true`, which makes Knowledge re-index even an
  already indexed release (`contracts/events/document.released.md`).
- Suchkonfiguration admin tab, new "Index-Wartung" section: two explicit, confirmation-gated
  admin actions independent of a config save. "Vektoren neu berechnen" triggers Knowledge's
  existing full-reindex webhook on demand (same call `update_retrieval_provider` already makes
  on an embedding change) and polls Knowledge's new `GET /api/v1/internal/reindex/status` for
  document counts by status while it runs. "Index aus Freigaben neu aufbauen" re-queues every
  non-withdrawn `DocumentRelease` through the existing publication outbox — the same engine the
  disaster-recovery import uses automatically — and polls a small status projection
  (open/ausgeliefert/fehlgeschlagen/zurückgezogen) while active; it refuses with 409 while a
  backup import is running. Both require an admin session and are rate-limited like the rest of
  the admin API.
- Disaster-recovery mode: administrators export all existing Ingest data
  (users, knowledge spaces, jobs, connections including credentials, files)
  into a single passphrase-encrypted `.weave-backup.tar.gz` archive from the
  new admin tab "Sicherung & Wiederherstellung" (or the `app/cli.py backup
  export|import` fallback), and restore it onto a fresh installation as the
  first new administrator — who need not be the same person who created the
  export. Every `*_encrypted` column is decrypted with the source key and
  re-encrypted under the export passphrase, then re-encrypted again under
  the target's own key on import; password hashes are carried over as-is.
  The knowledge index itself is not exported: after a successful import,
  every affected document release is re-queued through the existing
  publication outbox so Knowledge re-indexes automatically once an Ingest
  worker is running. Chat conversations (Weave-API) and local runtime bot
  YAMLs are out of scope. See `docs/betrieb.md` §12 for the runbook.
- Agent mode, rollout steps 4-5. Administration: the bot editor configures agent mode,
  subagents (mission, collections, filters, optional model, limits) and budgets; the
  control plane validates the shape (422 with a readable message); the central chat
  provider declares `supports_tools`, which a managed bot needs before agent mode runs.
  Streaming: an additive `status` event reports progress ("<Bereich> wird durchsucht",
  "Ergebnisse werden zusammengeführt", "Antwort wird formuliert") without prompts or
  tokens; Weave-Chat shows it as a transient progress line with per-subagent chips and
  renders `trace.agent`; the graph's final answer streams token by token.
- Technical identities (`contracts/technical-identities.md`): administrators create
  standalone integrations with explicitly granted collections (none by default), a
  `wti_…` bearer token shown exactly once, expiry, rotation, revocation and an audit
  log. Weave-Tools verifies such tokens through Weave-Ingest's introspection endpoint
  (`TOOLS_INTROSPECTION_TOKEN`, short cache) and applies the same scope rules to REST
  and MCP: requested collections only narrow the grants, out-of-scope requests return
  empty results without touching Weave-Retrieval, every request is audit-logged without
  secrets. The MCP transport of Weave-Tools is enabled in Compose and Helm by default.
- Agent mode for LLM bots (Weave-Runtime's `BotConfig.agent`, rollout plan "Schritt 2 --
  Tool-Calls und ein Subagent"): a direct-LLM bot can now configure one or more subagents,
  each with its own mission, explicitly allowed collections/filters, allowed tools (only
  `search_knowledge` in this round), optional model override and search/runtime limits.
  Enabling agent mode requires the configured model to declare tool-call support
  (`ModelConfig.supports_tools`; the `fake` provider always qualifies) and is rejected for
  `n8n`-provider bots. Weave-Runtime's LLM provider abstraction (`app/services/llm.py`)
  gained a `chat_with_tools()` call (structured tool calls/results, argument validation)
  implemented for both `FakeLLM` (deterministic, test-scriptable via `tool_responses`) and
  the OpenAI-compatible provider. A new `app/services/agents.py` runs one subagent's bounded
  `search_knowledge` research loop per turn; the chat pipeline defers an agent-mode turn's
  research and final answer into the streaming phase (reusing the existing n8n
  thread/queue/keepalive pattern) or runs it synchronously for the blocking endpoint. The
  chat trace gained an optional `agent` field (`trace.agent`: subagent id/status/searches
  used/hit count, turn budget) — see `contracts/internal-chat.md`'s "Agentenmodus" section.
  Managed (admin-configured) LLM bots can now persist an opaque `agent` JSON block
  (`ManagedBot.agent_config`, migration `0028_managed_bot_agent_config`), projected
  unvalidated through the admin and internal (Runtime-facing) APIs — there is no dedicated
  editor UI for this block yet. A new example bot, `services/runtime/bots/research-agent.yaml.example`,
  documents the YAML shape. Bots without `agent.enabled: true` are unaffected — the existing
  direct RAG pipeline is unchanged.
- Multi-subagent orchestration for Weave-Runtime's agent mode (rollout plan "Schritt 3 --
  LangGraph mit mehreren Subagenten", new `app/services/agent_graph.py`): a bot configured
  with more than one subagent now runs a LangGraph orchestrator-worker pipeline instead of
  the single-subagent path above (which stays unchanged, byte-for-byte, for a bot with
  exactly one subagent). The main bot's own LLM plans subquestions via one `research_area`
  tool offered only for subagents with a non-empty effective scope; a `Send`-based fan-out
  runs up to `agent.limits.max_parallel` research workers concurrently (each still Schritt
  2's own `run_subagent`, budgeted by both its own `SubagentLimits` and a turn-wide shared
  `budget_searches`); results are deduplicated by `(document_id, chunk_id)`, contradicting
  facts are lexically flagged, and up to `agent.limits.max_followups` follow-up rounds run
  for any subquestion that came back incomplete while budget remains. A failed/incomplete
  subagent never reads as "no information": with other usable evidence, the final answer
  names the gap in a fixed German sentence; with no evidence at all, the existing
  `require_sources` guard applies exactly as before. `langgraph`/`langchain-core` are now
  direct dependencies of Weave-Runtime's backend (`requirements.in`/`requirements.txt`).
  `ChatTrace.agent` (`contracts/internal-chat.md`) gained a new `mode: "graph"` value plus
  optional `plan`/`followups`/`budget_used` fields, additive and `null`/`0` for the
  unchanged `"single"` mode. `services/runtime/bots/research-agent.yaml.example` now
  configures two subagents (`it-support`, `hr-support`) to exercise this path.
- Weave-Tools search hits (REST `POST /api/v1/tools/search` and the MCP `search` tool) now
  carry each hit's own actual `collection`, `document_id` and `chunk_id`, taken straight
  from Weave-Retrieval's per-chunk response instead of the request-level collection — a
  caller merging or deduplicating results from several searches (e.g. an orchestrating
  agent) can now do so by stable identity. Scope enforcement is unchanged: a requested
  collection outside the caller's scope still yields an empty result. Also: a new pure
  `effective_scope()` helper in Weave-Runtime (`app/services/scope.py`) computes the
  4-way Collections intersection (user rights ∩ main-bot collections ∩ subagent
  collections ∩ per-request filter) that a future agent-mode subagent's own knowledge
  search will use; the existing single-bot retrieval path is unchanged.
- n8n bots stream (contract v2, `contracts/n8n-flow.md`): with "n8n-Streaming" enabled,
  Weave-Runtime reads the flow's response incrementally — n8n's native "Streaming
  response" (JSON lines `begin`/`item`/`end`/`error`) as well as the documented SSE
  contract (`delta`/`sources`/`done`/`error`); a plain JSON reply still works. The
  webhook call now runs inside the open stream with `: keepalive` comment lines every
  5 s (`STREAM_KEEPALIVE_SECONDS`), relayed by Weave-API and tolerated by Weave-Chat,
  so a single agent turn may run for hours; the bot timeout is an idle timeout between
  chunks. Bots that require sources are buffered until their evidence is known — the
  response guard cannot be bypassed by streaming. The chat shows "Der Assistent arbeitet
  noch …" after 30 s without output. The admin toggle is no longer disabled.
- Weave-Runtime skips a centrally managed bot whose data is invalid (typically a webhook
  URL outside `N8N_ALLOWED_BASE_URLS`) with a warning instead of failing
  `GET /internal/bots` — and every chat — with HTTP 500.
- Portal review: documents can be ticked in the review inbox and in a knowledge space and
  released, skipped or deleted together (`POST /api/v1/portal/documents/bulk`). A document
  can be marked "Nicht freigeben / überspringen" and brought back for review; skipped
  documents leave the inbox and get their own filter.
- Quality grades are explained: the review page shows why a document is A, B or C (OCR
  confidence, structure and text quality with the thresholds behind them), what the grades
  mean, and why a grade can be missing. Every document list can be filtered by grade,
  including "Ohne Bewertung".
- Documents show where they come from (upload folder, Confluence page or mail) and, once
  released, who released them (`source`, `released_by` in the portal API).
- Images from Confluence pages reach the chat: release snapshots carry absolute artifact
  URLs (`GET /api/v1/portal/releases/{id}/artifacts/{filename}`), Weave-Runtime lists them
  per source, Weave-API proxies them for Weave-Chat (`INGEST_SERVICE_TOKEN`, the same value
  as `WEAVE_KNOWLEDGE_INGEST_API_TOKEN`), and the chat renders them under the answer.
- Form fields keep their labels. Where the pipeline does not recognise a form section as a
  table, its fields decay into one-word paragraphs and label and value come apart —
  `Nachname:` / `Vorname:` / `Peter` / `Anschrift:`, in which nothing can tell you whose
  name Peter is. Six of nine sample documents were affected. Blocks now carry their
  geometry through the renderer, and a label (a text block ending in `:`) is paired with
  the nearest value to its right whose vertical band overlaps it. The guards matter more
  than the rule: a candidate that ends in `:` is never consumed as a value, so two labels
  side by side cannot eat each other; a gap wider than a quarter of the page blocks the
  pairing, so a label cannot reach into the next column; and a value is never taken across
  another label that sits between them, so an empty field cannot steal the next field's
  value. Blocks without geometry keep the old path untouched, and
  `_PAIR_LABEL_VALUE_GEOMETRY` switches the whole thing off
- `checkbox_detect` — a checkbox reader that is deliberately **not** wired into the
  pipeline. Of eleven verifiable ticked boxes the pipeline currently recognises one, and
  the layout model has no checkbox class at all, so the boxes would have to come from the
  page image. Measured on three pages, the obvious approach does not hold: fill ratio does
  not separate ticked from empty (0.265–0.335 against 0.31–0.51, almost fully overlapping)
  because the ink of a cross merges with the box outline into a single contour, and letter
  shapes, letterhead logos and stamp areas produce more square candidates per page (15–135)
  than there are real boxes (7–14). The module keeps a pure, testable core and imports
  cv2/numpy/pypdfium2 lazily; a heuristic that ticks the wrong box in production would be
  worse than today's silence. A workable version needs template matching against the
  printed outline or a small trained model
- Filled-in forms survive the trip to Markdown. PaddleOCR-VL classifies a form field's
  preprinted rule lines as a formula and emits LaTeX where a value belongs, so
  `Buchungsdatum:` arrived as `$$ \begin{array}{c}\underline{02}\quad\underline{082}…`.
  A new `form_latex` service flattens that back to plain text behind three gates — a hard
  veto on real mathematics (`^`, `\sum`, `\partial`, any `\begin{}` but `array`), a macro
  whitelist, and a positive form-field signal — plus a safety net that keeps the original
  whenever the result would still carry a backslash. Measured on 52 real LaTeX lines from
  nine scanned insurance forms: 51 flattened, the one holdout deliberate (a `\frac` chain
  with no form vocabulary). Genuine formulas pass through untouched. Checkbox glyphs are
  unified in the same pass: five codepoints for two states (`□ ☐ ☒ ☑ ⊠`, and `$\checkmark$`)
  become `[x]` / `[ ]`, so retrieval on "ticked" is possible at all — `○` is deliberately
  excluded, it shows up as a digit artefact, not a box
- Field validation that reports rather than repairs: a new `field_validation` service checks
  IBANs (mod-97, country code, length), ICD-10 shape, and date plausibility, and counts
  orphan labels. It never rewrites a value — a type rule that "fixes" a field tears apart
  correctly paired ones. Findings reach the quality gate as `signals.field_validation`
  plus a top-level `issues` list, so a document that reads cleanly but carries an invalid
  IBAN says so instead of passing silently. In the nine-document sample this flags seven
  of eight IBANs, a month `14`, a month `082`, and a cancellation dated before its booking
- The job JSON export carries `processing.structure` and `processing.converter`. The block
  labels and counts were already collected and then dropped at the API boundary, which made
  every diagnosis on a finished job guesswork. `structure` now also reports `bbox_coverage`
  — how many blocks carry usable geometry and under which key names — which is what tells
  you whether a geometric label/value pairing is buildable at all. Measured against
  PaddleOCR-VL 1.6: all 115 blocks of a six-page form carry both `block_bbox` and
  `block_polygon_points`
- The RAG frontmatter carries `original_filename` alongside `source`. `source` is the UUID
  the file has on disk, which is useless as a citation target
- Outbound webhooks for automation (n8n and friends): every user can register webhook
  connections under Connections › Webhooks — URL, optional signing secret (stored encrypted,
  never displayed; deliveries then carry an `X-PaddleDoc-Signature` HMAC-SHA256 header), and
  the events the connection accepts (job finished, job failed, import run finished).
  Delivery is strictly per-task opt-in: the File Task wizard's profile step and the
  Confluence import wizard's options both grew an optional "Send result to webhook"
  selection, and only a task configured that way ever sends — a connection that merely
  subscribes to an event receives nothing. The worker delivers automatically with retries
  and a visible delivery history, finished jobs can also be sent manually from the jobs
  table or detail page, and payloads carry the job metadata plus the full markdown so a
  flow can process it without calling back. Deliveries run through the SSRF guard;
  private-network targets are allowed via `WEBHOOK_PRIVATE_HOST_ALLOWLIST` (wired through
  compose and the Helm chart, both backend and worker)
- Confluence imports are diagnosable: every failed API request is logged (path, HTTP status,
  server kind, and Confluence's own error message) with plain-language readings of the
  common cases — including Data Center's habit of answering 404 for pages you lack
  permission to see — and import runs log start/finish summaries, per-page and
  children-listing failures with context, and cap events, all visible in the admin Logs tab
  and the run's error list. Personal spaces (`~` keys) get an explicit hint

### Changed
- The managed n8n bot timeout can now be set up to 14400 seconds (4 hours) to
  accommodate long-running streaming agent flows; the field is documented as an idle
  timeout between response chunks, not a total run cap.
- Bundled Redis in Compose (and in Helm when `redis.bundled.persistence.enabled`) runs with
  AOF (`appendonly yes`, `appendfsync everysec`). **Existing instances must enable AOF online
  first** (`CONFIG SET appendonly yes` + `BGREWRITEAOF`) before restarting with the new
  command, otherwise Redis starts from an empty AOF and drops the RDB dataset — see
  deploy/README.md (AV-02).
- "Mit anderem Profil neu verarbeiten" is now "Erneut prüfen".
- Saving a manually edited markdown recomputes the quality gate against the edited text
  instead of keeping the grade of the original extraction.
- The OpenWebUI push honours the quality gate. A job graded `block` was previously uploaded
  into the knowledge collection anyway, so the grade had no consequence whatsoever — every
  document in the nine-document sample landed in the vector index despite grading C. A job
  without a gate result (older jobs, fallback paths) still passes: absence is not failure
- `PaddleOCRVL.predict` is called with `format_block_content=True`, so title blocks arrive
  with the heading level the pipeline itself assigns (`#` document title, `##`/`###`
  sections) instead of bare text that got flattened to a uniform `##` — untuned output had
  105 `##` and not a single `#`, leaving hierarchical chunking exactly one level to work
  with. `_render_heading` keeps an engine-supplied level and only stamps its own when the
  content has none, so the PP-StructureV3 profiles are unaffected. The call site documents
  the eight parameters that are deliberately *not* set and why, measured across nine
  parameter sets on one input: `temperature`/`top_p` change nothing (this pipeline already
  decodes greedily), `markdown_ignore_labels` only shapes PaddleOCR's own markdown rendering
  which this module never consumes, and none of them reduce the number of LaTeX-carrying
  blocks
- Repeated headers, footers and page numbers appear once instead of on every page. Measured
  across the nine documents, boilerplate made up 18.4 % of all non-empty lines. The
  digit-normalisation that makes "Seite 2 von 6" and "Seite 3 von 6" compare equal is
  applied to header/footer/number labels only — on ordinary text it would collapse real
  numeric values. `_DEDUPLICATE_REPEATED_BOILERPLATE` switches it off
- `figure`/`image` blocks keep their recognised content instead of collapsing to a bare
  `*[Figure on page N]*` placeholder. In a filled-in form those regions hold signatures and
  handwriting
- **Breaking (compose file names).** One standard deployment for every platform:
  `docker-compose.nas.yml` became `docker-compose.yml`, so Windows, macOS, Linux and NAS
  hosts all start with a plain `docker compose up -d`. The old name described the file's
  origin, not its audience — it was always the deployment everyone should use. The
  local-build file that held the `docker-compose.yml` name is now `docker-compose.dev.yml`
  and runs under its own compose project name, so a contributor's trust-auth Postgres can
  no longer end up sharing a volume with a real installation.
- **Breaking (data location).** The standard file keeps Postgres, Redis, document storage
  and the PaddleOCR model cache in Docker-managed volumes instead of bind-mounting
  `./nas-data/`. That directory only ever existed on Windows and macOS because a compose
  file demanded it, and it forced a `chown -R 1000:1000` on Linux hosts. Named volumes
  need neither. To keep data on a specific share or disk, the new
  `docker-compose.nas.example.yml` overlay redirects all four volumes to host paths and
  changes nothing else. Migration steps for existing installations are in the README under
  *Upgrading an existing installation*.

### Fixed
- Indexing under load (incident 2026-09-22): a burst of releases drove 14 parallel
  embedding requests into the embeddings pod, which was OOM-killed; Knowledge retried
  only three times within seconds and marked the documents "Indizierung fehlgeschlagen"
  for good. Now the embeddings service processes at most `EMBEDDINGS_MAX_CONCURRENT_REQUESTS`
  (default 1) encodes at a time and answers `503 Retry-After` instead of growing without
  bound; Knowledge treats timeouts, disconnects, 5xx and busy answers as transient and
  schedules retries at 30 s / 2 min / 10 min / 30 min / 60 min (up to
  `EMBEDDING_MAX_ATTEMPTS`, default 6) that survive pod restarts (re-driven by the periodic
  sync tick and once at worker start); the knowledge worker runs with concurrency 2 by
  default instead of one process per CPU.
- Self-healing (review 2026-09-18): a periodic tick (publications, Confluence refresh,
  session cleanup) whose Redis lock expired or vanished re-elects itself instead of ending
  its chain, and the next-tick enqueue is retried before giving up (SH-01). Worker startup
  no longer resets OCR jobs that are still genuinely running (staleness gated on the hard
  time limit, SH-02) and re-queues attachment jobs stranded as PENDING under an already
  finished import; the finalize backstop survives a broker error per child (SH-04).
- Indexing status compared the pre-rewrite digest with what Knowledge downloaded, so every
  released document with an image showed as "abweichend" although it was indexed (ST-01).
- Ingest exposes `GET /api/v1/ready` (database + broker) separately from the no-I/O
  `/api/v1/health`; Helm readiness and the Compose healthcheck use it (AV-03). The Compose
  Redis healthcheck now authenticates and requires `PONG` (AV-04).
- `PUT /jobs/{id}/save` mutated the job's `processing_info` in place before reassigning it,
  so SQLAlchemy saw no change and silently dropped the editor/version metadata.
- Tables no longer lose their first row to the header. `_html_table_to_markdown` promoted
  `rows[0]` unconditionally, so in a form the first label/value pair became the column
  heading and its value left the data — six of twenty-five tables in the sample corpus were
  affected, `Nachname: | Cicekli` among them. A first row is now treated as a header only
  when it uses `<th>` or the rows below it mark the same columns as options; otherwise an
  empty header line is emitted and every row stays data. Alongside it: the two literal
  characters `\n`, which PaddleOCR-VL sometimes puts inside a cell and which break a GFM
  row, become `<br>` (39 occurrences in the corpus); `|` inside cell text is escaped
  instead of splitting the column; `colspan` is expanded into blank cells so a spanning
  cell no longer shifts every column after it out of alignment
- A table the renderer could not parse leaked its raw `<table>` markup into the generated
  Markdown. `_html_table_to_markdown` returned `''` for zero parseable rows and the caller
  then fell through to a branch that emitted the untouched HTML
- The quality gate's `ocr_confidence` was a measurement artefact. `_collect_numeric_values`
  harvested every numeric leaf whose *ancestor* key contained a hint as a substring —
  `conf` matches `preprocessor_config` — and silently divided anything in (1,100] by 100,
  turning a count of `8` into a "confidence" of 0.08. The collector now matches exact key
  names without inheriting context, and the score is `None` rather than `0.0` when the
  engine supplies no confidences at all, so the weight is renormalised instead of dragging
  the grade down. This was deterministic, not occasional: the `openai_vision` path, the
  pypdf fallback and the `.eml` path could never score above 0.5 and therefore always
  graded C, whatever the actual output looked like
- `structure_quality` accounts for the share of formula blocks, which previously left it at
  0.888–0.939 on documents whose form fields had been destroyed
- The noise metric no longer mistakes German for gibberish: `[A-Za-z0-9']+` split "für"
  into `f` + `r`, and any digit run of six or more counted as noise — birth dates and policy
  numbers included
- The generated Markdown no longer opens with a doubled `---` after the frontmatter
- The frontend no longer blocks its own API calls. `PADDLEDOC_PUBLIC_API_URL` is unset by
  default in compose, and the CSP then left `connect-src` at `'self'` while the client still
  called `<page hostname>:8000` — the browser refused every request before sending it, so a
  fresh install could not even reach `/setup`. The CSP now derives the same fallback origin
  from the request host, making the variable optional; Helm is unaffected (its chart always
  sets `frontend.apiUrl`)
- `scripts/init-env.sh` no longer keeps the placeholder secrets from `.env.example`. It only
  checked whether a key was present, so a `.env` copied from the example kept the published
  `SECRET_KEY` and `REDIS_PASSWORD` and the script still reported success. A value identical
  to the one in `.env.example` now counts as missing and is regenerated
- `.env.*` is gitignored (`.env.example` excepted): `.env.bak` and friends carry real secrets
- Postgres and Redis no longer restart-loop on startup. Both official images enter their
  entrypoint as root, `chown`/`chmod` their data directory and only then drop to uid 999
  via `gosu`/`setpriv`; the blanket `cap_drop: ALL` added in the security audit removed
  `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID` and `SETUID`, so all of that failed with
  `EPERM` (`chown: changing ownership of '.': Operation not permitted`), the entrypoint
  aborted and `restart: always` looped it — taking the backend (unhealthy) and worker
  (never started) down with it. Both services now add back exactly those five capabilities,
  keeping the other nine of Docker's defaults dropped. The Helm chart is unaffected: it
  pairs `capabilities.drop: [ALL]` with `runAsUser: 999`, so its containers never enter
  the entrypoint's root branch at all
- Pasted Confluence Server/Data-Center page links now work as import scope:
  `/display/SPACE/Page+Title` URLs (umlauts, personal spaces included) are resolved to the
  page id at creation time via the source's API, with a clear message when resolution fails —
  previously only bare page ids and Cloud-style URLs were accepted. A page link pasted into
  the *space* scope field is rejected instead of silently importing the whole space

## [1.3.3] - 2026-08-22

### Added
- Job lists and job details name their owner: every job row carries `owner` ({id, username},
  batch-loaded in one query; legacy pre-auth jobs show none) — the foundation for the
  personal home dashboard and the "by <username>" attribution on the Processing work center
- VL connections power normal processing, not just benchmarks: every enabled connection
  now appears as a selectable profile (`VL: <name>`) in the File Task wizard, the
  re-run-with-profile dialog, mail-API ingestion, and Confluence attachment OCR. Selecting
  one stores the same settings shape benchmark variants use, so the worker path — including
  key decryption strictly inside the worker and the clean failure when a connection was
  removed — is shared and unchanged. Previously the "OpenAI-compatible Vision API" profile
  read credentials from environment variables only, so a VL connection that worked in a
  benchmark silently had no effect on normal uploads. The jobs table shows such jobs by
  their connection name; the deployment default profile deliberately stays restricted to
  the static OCR profiles
- Confluence imports carry their logical location: every imported page's frontmatter now
  records `space`, `confluence_path` (the ancestor titles down to its parent), `parent_title`,
  and `depth`, and a `> Confluence: A › B › C` breadcrumb line sits at the top of the body so
  RAG chunking keeps the context beyond the first chunk. The path comes from the crawl tree
  itself (plus one `ancestors` API call for the import root), not from parsing rendered
  table-of-contents macros — deterministic on every re-import, and auto-refresh resolves it
  per changed page the same way, best effort. Pages that are essentially link lists (children/
  TOC macros) are flagged `is_navigation: true`, parents get `children_titles` stamped in the
  end-of-run pass that already rewrites cross-page links, and an opt-in **Use hierarchy as
  tags** toggle on the import wizard turns breadcrumb parts into job tags so the jobs list
  can filter by section
- Sign-in events show up in the admin Logs tab: OIDC logins (with a claim diagnostic —
  which of email/preferred_username/upn/unique_name the ID token carried and whether the
  userinfo endpoint had to be queried), account provisioning, username/email syncs and
  skipped collisions, plus local sign-ins, failures, and lockouts, all as `backend` rows.
  Messages never contain tokens, passwords, or full subject identifiers
- Jobs can be re-run with a different OCR profile: `POST /api/v1/jobs/{id}/restart` now
  accepts an optional `{"profile_id": ...}` body (validated against the profile registry,
  persisted as previous/requested/effective profile like the existing lower-profile retry),
  and both the jobs table and the job detail page grew a "Re-run with profile…" dialog for
  finished and failed jobs. Confluence-imported pages stay excluded, as with Restart
- Confluence import runs are re-runnable with edits: the run detail API now exposes
  `source_id` and the stored options snapshot, and an "Edit & run again" action on the
  imports list and run detail opens the import wizard prefilled with that run's connection,
  scope, and options (`/imports/new?from=<run>`) — starting it creates a new run, so history
  and page-version chaining stay intact
- The markdown editor is reachable from the jobs table: an Edit action per finished job
  opens the job detail page straight in edit mode (`?edit=1`), and the detail page's
  toolbar gained an "Edit markdown" button that jumps to the editor
- Identity providers have a "Use email as username" switch (for IdPs like Microsoft Entra
  whose `preferred_username` is a UPN): when enabled, the claimed email becomes the
  account's username at provisioning, and existing accounts are renamed on their next
  login — skipped safely when another account already holds that name or the IdP sent no
  real email claim (migration 0012_provider_email_username; its revision id is deliberately
  ≤ 32 chars — see Fixed below)
- The Processing page is now an overview: per-user stat tiles (running/finished/failed,
  pages processed), a breakdown by job type (single, multiple files, Confluence import,
  mail), and the most recent jobs — the upload wizard moved to its own **File Task** entry
  under Processing in the sidebar (`/processing/new`)
- PaddleDoc has an app icon: two crossed paddles behind a document page on the emerald
  tile — served as SVG favicon and apple-touch-icon, used as the brand mark in the
  sidebar and on the login/setup pages (shared `PaddleDocLogo` component), and shown
  in the README header
- **Connections** is a new top-level page (`/connections`) where every user configures the
  external systems their account talks to, in three tabs: Confluence (create, test, rename,
  delete import sources and their auto-refresh interval — moved out of the collapsible
  section on the imports page), OpenWebUI (the full connection CRUD and recent-push history
  that used to live at `/openwebui`, which now redirects here), and VL connections. VL stays
  an admin-managed, deployment-wide resource: administrators get the same CRUD panel as in
  the admin area, everyone else a read-only list of the enabled models so they can see what a
  benchmark can run against

### Changed
- Creating work is one centered gesture everywhere: Home, Processing, and Imports show the
  pair **New File Task** + **New import** together, centered under the page header in a soft
  emerald style — no more lone primary button tucked into the top corner, and the empty
  states offer the same pair
- Home and Processing now have distinct jobs: Home is a personal overview — "Welcome back",
  quick actions, a **Needs attention** section (failed jobs, your own first, with a counter),
  **Your recent jobs**, and a slim one-line stats summary instead of the four big tiles —
  while Processing is the team's work center with the full stat tiles, a type distribution
  bar plus four clickable type cards that jump to the jobs list pre-filtered
  (`/jobs?type=…`, shown there as a clearable filter chip), and recent jobs attributed to
  their owner
- The File Task wizard's Continue is context-aware: disabled with a visible reason until the
  step is valid (step 3 needs a chosen file or at least one uploaded collection file), the
  active step carries a ring and emphasized label on top of the existing checkmarks, and
  step-bound errors appear right under the element they concern instead of only in a global
  banner
- Mail reads like an inbox now: one prominent search bar (Enter applies), sender, subject
  and parts summary on the left with date and status on the right, the whole row linking to
  the message, and the remaining filters tucked behind a Filters toggle
- A UI polish pass across the whole app: the home dashboard leads with "what do I do now"
  (New File Task, stats with errors surfaced, five most recent jobs) and demotes the system
  readout to a compact strip with expandable details; the File Task wizard became a real
  four-step flow (Metadata → Profile → Upload → Review & Start) with a persistent, keyboard-
  operable stepper — a single file now uploads when you hit Start, not when you pick it; the
  jobs list gained All/Running/Completed/Failed filter chips with counts; the sidebar groups
  entries under Workspace and Analyze & connect, moves Mail API out of Processing as its own
  input channel, renames Confluence Import to Imports, marks Admin as privileged, and
  strengthens the active state; Connections separates External services from AI models (the
  VL tab is now "VL Models"); benchmark results compare side by side with Best result and
  Fastest badges; and throughout: one primary action per screen, a consistent type scale,
  lighter cards, empty states that say what to do next, aria-current/aria-controls/aria-live
  wiring, and arrow-key tab navigation
- The File Task wizard sheds its email, tags, and password fields; folder creation
  (folder + subfolder) moved into an "Add folder" dialog. The department field remains for
  multi-file batches
- The jobs table breathes again: real column gaps and no-wrap cells (profile, pages, and
  quality no longer run together into one string) and a wider page container. The New Task
  button left the Jobs page — starting tasks lives in the sidebar now
- The sidebar is now two levels deep: Processing carries Jobs, API Mail Extraction (the
  former Mail entry) and Confluence Import (which previously had no navigation entry at all)
  as a collapsible submenu, and Benchmark is named **VL Benchmark** to say what it compares —
  the same document run through several vision-language models
- Paddle runtime settings (default OCR profile, timeout) moved from the top of the Processing
  page into a **Paddle** tab in the admin area, where they belong: `PUT /api/v1/paddle/settings`
  has always required an admin, so non-admin users were shown a form that failed with 403 on
  save. The Processing page still reads the deployment default to preselect a profile
- Confluence importing has exactly one entry point: the wizard's "Import from Confluence"
  tile is gone, and everything import-related lives under Processing > Confluence Import.
  The File Task wizard keeps two equally sized tiles, Single file and Multiple files
- The jobs table got denser and calmer: every row action is an icon button with a hover
  tooltip (download, restart, retry lower, re-run with profile, edit, push, delete), the
  Created column is gone (the timestamp lives in the filename's hover tooltip; sorting
  still defaults to newest first), and Used Profile shows compact codes like `ocr6m+v3`
  with the full profile name on hover. The freed width goes to the document column
- One naming scheme throughout: the processed items are **Jobs** everywhere (the jobs page
  was still headlined "Tasks"), and **File Task** is deliberately the only "task" — the flow
  that creates jobs from uploads. Alongside, the jobs table dropped its never-rendered
  title/description/compact/hideHeader props and dead password state, and the orphaned
  documents-center component was deleted — the frontend lints with zero warnings again
- The home dashboard dropped the "Document Magic" marketing hero and its Tasks shortcut —
  navigation runs through the sidebar. The live service readout it contained (pipeline state,
  Paddle service, queue depth, containers, worker nodes, GPU/CPU runtime) survives as a
  standalone **System status** card, including its honest degradation to last-known values
  when the backend health check fails

### Fixed
- Migration 0012 crash-looped every PostgreSQL deployment: its original revision id
  (`0012_provider_use_email_as_username`, 35 chars) exceeded Alembic's
  `alembic_version.version_num` VARCHAR(32), so the final version UPDATE aborted the whole
  transactional-DDL upgrade — the backend died before serving a request. SQLite, which the
  test suite runs on, silently ignores varchar limits, so 466 tests stayed green. The
  revision is renamed to `0012_provider_email_username` (28 chars) and a guard test now
  fails the suite if any revision id in the chain exceeds 32 chars
- Entra sign-ins no longer provision garbage accounts (username = raw `sub`, email =
  `sub@entra.oidc.invalid`): the email is now resolved from `email`, `upn`, `unique_name`,
  or `preferred_username` (values must look like an address; `DOMAIN\user` forms are
  rejected), and when the ID token carries none of them the userinfo endpoint is queried
  with the access token — best effort, a failure never blocks the login. Existing accounts
  self-heal on their next sign-in: a genuinely resolved email replaces a stored synthetic
  one (guarded against collisions; account matching stays strictly `(provider, sub)`), and
  the "Use email as username" sync then picks it up
- Uploading files that already exist as identical versions during a multi-file batch
  looked like nothing happened: the "N unchanged file(s) skipped" notice (and the upload
  progress panel) rendered only on the wizard's last step, while collection uploads happen
  on step 2. The feedback now shows on every step, in a visible notice box
- Every page of every Confluence import failed on PostgreSQL with
  `ForeignKeyViolation: import_page_states_job_id_fkey`. `ImportPageState` carries the raw
  `jobs.id` foreign key but no `relationship()` to `Job`, so SQLAlchemy's unit of work saw no
  ordering between the two mappers and wrote `import_page_states` *before* the `jobs` INSERT
  of the very job the row points at. The importer now flushes the job row before the
  page-state upsert. It stayed invisible because SQLite ignores foreign keys unless
  `PRAGMA foreign_keys=ON` is set per connection, so the test suite stored the dangling
  reference without complaint -- the suite now pins that pragma, matching PostgreSQL, and
  `import_page_states.job_id` was the only FK column in the schema lacking a relationship
- The API token form under Settings put its two inputs on different baselines: the
  "Expires in (days)" field carried a `hint`, which `Field` renders as an extra block below
  the input and which the row's `items-end` alignment then pushed the input up against. The
  optional-ness now lives in the placeholder
- `VL_PRIVATE_HOST_ALLOWLIST` was documented in `.env.example` but never passed to the
  containers: neither compose file forwarded it and the Helm chart had no value for it, so a
  self-hosted vLLM/Ollama/LiteLLM on a private address could not be reached at all. Both
  compose files now forward it to backend and worker, and the chart exposes
  `vl.privateHostAllowlist` on both deployments (the test probe runs in the backend, the OCR
  call in the worker)
- The SSRF fetcher's "resolves to a blocked address" rejection now says what to do about it.
  A private-address target that is perfectly reachable from inside the pod is still refused
  by design, and the old message gave no hint that a per-host allowlist exists — it read like
  a bug rather than a policy. Firewall docs gained a section on the same point

## [1.3.1] - 2026-08-15

### Changed
- Processing and Mail are now cleanly separated by intent: the Processing page handles
  every manual upload — including `.eml`, which joins the normal file picker and produces
  one regular job whose markdown contains the rendered mail body plus each attachment as
  its own section (converted with the selected OCR profile), with the standard frontmatter,
  folder/tags/password settings, and content-hash versioning like any other document. The
  Mail page now exclusively shows messages ingested programmatically via
  `POST /api/v1/mail/messages` (list locked to `source=api`); its manual upload zone moved
  to Processing. API ingestion keeps its own `profile_id` parameter (falls back to the
  configured default profile), and mail messages default to `source=api`

## [1.3.0] - 2026-08-15

### Added
- Universal mail ingestion: `POST /api/v1/mail/messages` accepts a raw RFC-822 email
  (`Content-Type: message/rfc822`, or `multipart/form-data` with a single `file` part for
  `curl -F` / n8n form mode) from any client — SMTP gateway, n8n workflow, a script — reading
  the body with a streaming, size-capped reader (413 once `MAX_MAIL_MESSAGE_BYTES`, default
  100 MiB, is exceeded) so nothing buffers unboundedly. The message is parsed server-side with
  Python's stdlib `email` package via a deterministic depth-first MIME-tree walk (not
  `iter_attachments()`, which loses content inside `multipart/signed`): S/MIME-signed mail,
  nested containers, inline `Content-ID` images, and forwarded `message/rfc822` attachments are
  all classified correctly, and an unsupported or oversized part is recorded as skipped without
  failing the whole request. The body renders straight to Markdown (no OCR) via the existing
  Confluence markdownify pipeline; every supported attachment becomes an ordinary job and runs
  through the standard OCR pipeline like any upload. Identity is `sha256(raw bytes)`: replaying
  already-seen bytes returns the existing message instead of reprocessing it (200,
  `replayed: true`) and re-dispatches any of its attachment jobs still stuck PENDING from a
  crashed request — the mechanism that makes sender-side retry loops (gateway outbox, n8n
  retry-on-fail) safe. Full retrieval API for downstream consumers (n8n, Bedrock AgentCore, RAG
  ingestors): list/detail with filters (`q`, `message_id`, `sha256`, `source`, date range), body
  as plain text, the original `.eml` and any individual part as a download, and
  `GET .../export.json` (schema `paddledoc.mail-export/1`) bundling the envelope, body markdown,
  and every attachment's OCR markdown in one call for poll-until-`complete` consumers. `DELETE`
  removes a message, optionally cascading to its attachment jobs. See
  [docs/integrations/mail-ingestion.md](../../docs/integrations/mail-ingestion.md)
- Mail UI: a first-class **Mail** section (`/mail`, sidebar entry) with a filterable, paginated
  message list showing an aggregate status derived from each message's attachment jobs, and a
  message detail page (`/mail/{id}`) with the envelope, rendered body, a parts table linking
  every processed attachment to its job, and downloads for the raw `.eml` and individual parts.
  The detail page polls every 2.5s while any attachment job is still PENDING/RUNNING. Attachment
  jobs carry a "from mail" badge linking back to their source message on the job list and job
  detail pages
- Manual `.eml` upload on `/mail`: a drag-and-drop zone plus file picker uploads one or more
  `.eml` files sequentially against the ingest endpoint, with per-file progress and a result
  list (ingested / already ingested / failed, per file — one bad file never aborts the rest of
  the batch); uploading a single file that ingests successfully navigates straight to its
  detail page. Also reachable from the Processing page's step-1 source picker

### Changed
- Uploaded-file version chaining (matching filename -> next `document_version`) now excludes
  mail-attachment jobs — an attachment named e.g. `invoice.pdf` arriving by mail from an
  unrelated sender no longer gets chained into an unrelated document's version history. Mail
  attachment jobs still appear normally everywhere else (stats, folders, `/markdown-files`)

### Fixed
- `_attach_tags` could create a duplicate `Tag` row (surfacing only as a misleading commit-time
  `IntegrityError`) when called more than once for the same brand-new tag name inside a single
  uncommitted transaction — exactly what mail ingestion now does once per attachment job. A
  missing `db.flush()` after inserting a new tag meant a second call's lookup couldn't see the
  first call's still-pending insert

## [1.2.1] - 2026-08-13

### Added
- VL benchmark: admins manage multiple vision-language connections (OpenAI-compatible
  endpoint, model, write-only encrypted API key, per-connection system prompt, test button)
  in a new admin tab; users benchmark a document against up to 6 VL connections plus
  optionally one OCR profile (2-7 variants) on the new Benchmark page and get a comparison
  report — duration, pages, output size, quality grade, fallback/error per variant — with
  per-variant markdown preview and JSON export. Benchmark variants are processed as regular
  jobs but stay out of the jobs list, stats, folders, and document versioning (#60)
- Document versioning via content hash: every upload stores a SHA-256 of the file; uploading
  a same-named file visible to the same team creates version N+1 linked to its predecessor,
  byte-identical re-uploads are rejected with a pointer to the existing version instead of
  being processed twice. Job detail shows the version history (who uploaded which version
  when), the job list marks re-versioned documents with a version badge (#60)
- JSON result export: `GET /api/v1/jobs/{id}/export.json` (same per-job password gate as the
  markdown download) bundles document metadata (filename, hash, version, tags), uploader and
  team, processing details (profile, engine, pages, quality gate) and the full markdown;
  "Download JSON" button on the job detail page (#60)
- Personal API tokens: users create bearer tokens (shown exactly once, stored hashed,
  optional expiry) on the new Settings page and use them via `Authorization: Bearer` for
  programmatic access to the full API — no session cookie required (#60)

- Worker logs in the admin console: Celery workers mirror their log output into a new
  `worker_log_entries` Postgres table (portable — no docker.sock; identical under Compose
  and Helm/EKS), exposed via the admin-only endpoint `GET /api/v1/auth/admin/worker-logs`
  with level/worker/text/time filters and pagination, and a terminal-style "Logs" tab at
  Admin → Logs with auto-refresh, expandable tracebacks, and "load older" paging (#57)
- New worker tunables `WORKER_LOG_CAPTURE_LEVEL` (default `INFO`, genuinely independent of
  the worker `--loglevel` — console verbosity is unaffected) and
  `WORKER_LOG_RETENTION_MAX_ROWS` (default 20000), in compose env and Helm values (#57)

### Changed
- Result markdown frontmatter is now proper YAML (`yaml.safe_dump`) and carries
  `job_id`, `document_version`, `content_sha256`, `previous_job_id`, `uploaded_by`, `team`,
  `tags`, `processed_at` and `engine` in addition to the previous keys; plain-text fallback
  results now get a frontmatter header too (previously none) (#60)
- The sidebar is now a permanently visible rail on desktop viewports (≥1024px); small
  screens keep the burger-toggled drawer (#57)
- Snappier navigation: a dependency-free stale-while-revalidate cache renders Home, Tasks,
  and Processing instantly from cached data and refreshes in the background; polling is
  adaptive (fast only while jobs are pending/running) and pauses while the tab is hidden;
  the job detail page lazy-loads the markdown renderer and shows loading skeletons instead
  of blank states (#57)
- The dashboard degrades honestly when the backend becomes unreachable: service status,
  stats, and job state are marked as "last known" instead of continuing to display the
  pre-outage values as current (#57)

### Fixed
- The delete-confirmation modal could render beneath the (now permanent) sidebar on desktop (#57)
- Filter "Reset" buttons (document list, admin logs) fetched with the pre-reset filter
  values due to a stale closure, leaving the list filtered while the inputs showed cleared (#57)

## [1.2.0] - 2026-08-05

### Added
- Confluence → Markdown import: managed sources, chunked crawler runs with live progress,
  import wizard, rendered markdown view, and optional attachment OCR (#53, #54)
- OCR fallback visibility: when a job silently degrades to plain-text extraction (no OCR
  ran — e.g. the worker cannot download model weights), the job detail page now shows a
  warning banner with the real failure reason and the document list marks the job instead
  of displaying a profile that never ran (#55)
- Helm: PVC-backed worker model cache (`worker.modelCache.persistence.*`) so model weights
  survive pod restarts, `worker.extraEnv` for proxy/model-source configuration behind
  restricted egress, and `worker.updateStrategy` (#55)
- Firewall connection matrix per component in `docs/firewall-requirements.md` (#55)

### Changed
- Dashboard links consolidated into a single "Tasks" entry; the tasks page gains a
  "New Task" button that opens the upload flow (#55)
- Worker Deployment defaults to `strategy: Recreate` when the model-cache PVC has no
  ReadWriteMany access mode, so image upgrades cannot deadlock on the volume attachment (#55)

### Fixed
- `ppocrv6_small` profiles referenced model names that exist in no PaddleOCR release and
  always failed into the plain-text fallback; corrected to `PP-OCRv6_small_det`/`_rec` (#55)
- Docker Compose: the worker now waits for the backend healthcheck (i.e. completed Alembic
  migrations) before starting — fixes the `column jobs.owner_id does not exist` race on
  stack boot after the v1.1.0 upgrade (#55)

## [1.1.0] - 2026-08-04

### Added
- User accounts with authentication: every page and API endpoint now requires a login
- First-run `/setup` flow that bootstraps the initial admin account
- Local username/password login (rate-limited, no account enumeration)
- Runtime-configurable OIDC SSO (Keycloak, Microsoft Entra ID): providers are managed,
  tested, and enabled in the admin UI without redeploying; client secrets stored encrypted
- Admin console at `/admin`: users, teams, and identity providers
- Per-user data visibility; teams share access to all team jobs; pre-existing jobs stay
  admin-only until assigned via "Assign ownerless jobs"
- DB-backed httpOnly sessions (7-day sliding expiry, 30-day cap)

### Changed
- `SECRET_KEY` is now required (compose env var; Helm `auth.secretKey.*` — the chart fails
  to render without it). Set it once and never rotate it casually: rotation invalidates all
  sessions and makes stored OIDC client secrets unreadable
- Redis now requires a password (`REDIS_PASSWORD` / Helm `redis.auth.*`)
- `CORS_ORIGINS` must list concrete frontend origins (credentialed cookies; no wildcard)
- `PUBLIC_API_URL` must point at the externally reachable backend URL for OIDC redirects

### Security
- SSRF-safe OIDC discovery/token fetching, CSRF origin guard, trusted-proxy handling for
  `X-Forwarded-*`, Celery task time limits

## [0.1.0] - 2026-06-17

### Added
- Initial release of PaddleDoc
- Document processing with PaddleOCR support
- Web UI for uploading and managing documents
- FastAPI backend with Celery workers for async processing
- Support for PDFs, Office files, and images
- Structured Markdown output generation
- Folder organization and tagging system
- Password protection for sensitive documents
- Job tracking and status monitoring
- Docker and Docker Compose support
- Helm charts for Kubernetes deployment
- Database migrations with Alembic
- Comprehensive API endpoints
- Frontend dashboard with Next.js
