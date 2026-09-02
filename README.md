# Weave

## Überblick

Dieses Repository ist das Monorepo der Weave-Plattform: einer dokumentbasierten
Such- und Chat-Lösung aus sechs eigenständig lauffähigen Diensten, zwei
optionalen CPU-Modelldiensten und einer eigenen Chat-Oberfläche. Jeder Dienst
läuft als eigener Prozess und eigenes Image, hat eigene Tests und eigene
Abhängigkeiten — geteilt wird hier nur die Quelle, nicht die Laufzeit.

Die Dienste stammen aus sechs vormals getrennten Repos (Weave-Ingest,
Weave-Knowledge, Weave-Retrieval, Weave-Runtime, Weave-Tools, Weave-API). Die
sind eingefroren; alles lebt jetzt unter `services/`.

Was die Zusammenlegung vor allem gebracht hat: **eine Konfiguration statt
sechs.** `weave.yaml` im Wurzelverzeichnis deklariert jeden Wert genau einmal —
geteilte Werte mit allen Diensten, die sie brauchen —, und
`scripts/weave_config.py render` erzeugt daraus `deploy/.env`. Geheimnisse
stehen nie im Repo, nur der Name der Umgebungsvariable, aus der `render` sie
liest. `check` findet danach, was früher erst an einer Dienstgrenze als stilles
401 oder 503 auffiel.

## Verzeichnisübersicht

| Verzeichnis | Aufgabe |
|---|---|
| `services/ingest` | Dokumenten-Ingestion: OCR (PaddleOCR), Markdown + Frontmatter, Quality-Gate, Wissensportal mit Freigaben; **besitzt Nutzer, Teams, OIDC-Verbindungen und Collections** — der Identitätsdienst der Plattform (ADR-0006) |
| `services/knowledge` | Index-Pipeline: nimmt freigegebene `document.released`-Snapshots entgegen, chunkt und embeddet, schreibt den Chunk-Store (pgvector), spiegelt die Collection-Registry |
| `services/retrieval` | Hybride Suche (pgvector + tsvector, RRF-Fusion, Cross-Encoder-Rerank); liest den Chunk-Store von `services/knowledge` read-only mit (ADR-0005) |
| `services/runtime` | LLM-Executor: Intent-Routing, Bots als YAML, ruft `services/retrieval`, delegiert Turns an n8n; Chat-Provider kommt zentral aus Ingest (ADR-0007) |
| `services/api` | Gateway: Tokens, Sitzungen, Gespräche. Identitäten kommen aus Ingest, hier liegt nur ihr Spiegel |
| `services/tools` | MCP-Server + REST-Spiegel für rechte-gebundene Suche (`list_collections`, `search`) |
| `services/chat` | Next.js-Chat-Oberfläche, spricht ausschließlich mit `services/api`; Anmeldung „Mit Weave anmelden" über Ingest |
| `services/embeddings` | Optionaler CPU-Embedding-Dienst (`intfloat/multilingual-e5-small`, onnxruntime), OpenAI-kompatibel unter `/v1/embeddings` |
| `services/reranker` | Optionaler CPU-Rerank-Dienst (`BAAI/bge-reranker-v2-m3`), Cohere/Jina-kompatibel unter `/rerank` |
| `contracts/` | Dienstübergreifende Verträge: `frontmatter.schema.json`, `openapi.json`, `chunk-store.md`, `internal-chat.md`, `n8n-flow.md`, `indexing-status.md`, `events/` |
| `deploy/` | Docker-Compose-Stack (`docker-compose.weave.yml` + Build-Override `docker-compose.local.yml`), Postgres-Init; `deploy/.env` wird generiert |
| `docs/` | Betriebshandbuch (`betrieb.md`), Wissensportal, ADR 0001–0007 und die HTML-Artefakte zum Lesen im Browser |
| `weave.yaml`, `scripts/` | Die eine Konfigurationsquelle und ihr `render`/`check` |

Das [Wissensportal](docs/wissensportal.md) trennt Verarbeitung und
Veröffentlichung: indexiert wird regulär nur, was jemand ausdrücklich
freigegeben hat.

Jeder Dienst hat zudem ein eigenes README mit den für ihn spezifischen
Details. Die `.env.example`-Dateien dort gelten für den Einzelbetrieb eines
Dienstes außerhalb des Compose-Stacks — im Stack kommt alles aus `weave.yaml`.

## Starten

```bash
# 1. Geheimnisse als Umgebungsvariablen bereitstellen (direnv, Passwort-
#    manager, CI-Secret-Store). `render` nennt alle fehlenden auf einmal.
python scripts/weave_config.py render      # schreibt deploy/.env
python scripts/weave_config.py check       # prüft sie auf Widersprüche

# 2. Stack bauen und starten (ohne OCR-Worker, der ist mehrere GB groß)
docker compose -f deploy/docker-compose.weave.yml -f deploy/docker-compose.local.yml up -d --build

# 3. OCR-Worker nachziehen — ohne ihn bleiben Aufträge auf PENDING
docker compose -f deploy/docker-compose.weave.yml -f deploy/docker-compose.local.yml --profile ocr up -d weave-ingest-worker
```

Ist Port 3000 belegt, beim Rendern `FRONTEND_PORT=3002` **und**
`CORS_ORIGINS='["http://localhost:3002"]'` exportieren — `check` meldet, wenn
nur eines von beiden gesetzt ist. Danach: Ingest unter `http://localhost:3002`
(erster Admin über die Setup-Seite), Chat unter `http://localhost:3001`.

[docs/betrieb.md](docs/betrieb.md) deckt Pflichtwerte, geteilte Werte, stille
Fehlkonfigurationen, die Anmeldung, den Umstieg auf die echten Modelldienste
und einen Selbsttest ab.

## Anmeldung

Es gibt eine Anmeldung für alles. Ein Administrator legt in Ingest lokale
Benutzer, Teams und OIDC-Verbindungen an; wer sich dort anmelden kann, kann
sich am Chat anmelden — egal womit. Das Gateway führt keine eigene Kontenwelt
mehr, sondern holt sich die Identität über einen einmaligen Handoff-Code
(ADR-0006). Vier Werte über drei Dienste müssen dafür zusammenpassen; `check`
prüft die Kette.

## Tests

Jeder Python-Dienst hat sein eigenes `.venv` und seine eigene
`requirements.txt`; Python-Version und Testbefehl unterscheiden sich pro
Dienst (`.github/workflows/pr-ci.yml` bildet genau das ab). Beispielhaft:

```bash
# services/tools, services/embeddings, services/reranker
# (requirements.txt im Dienst-Wurzelverzeichnis):
cd services/<dienst>
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q

# services/ingest, services/knowledge, services/retrieval, services/runtime,
# services/api (requirements.txt liegt unter backend/):
cd services/<dienst>
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/pytest -q backend/tests

# services/chat und services/ingest/frontend (Next.js):
npm install && npm test

# die Konfiguration selbst:
services/tools/.venv/bin/python -m pytest scripts/tests -q
```

Die CI führt pro Pull-Request nur die Dienste aus, deren Pfad sich geändert
hat, und lässt ein Frontend mit bekannter Schwachstelle in `npm audit`
durchfallen.

## Dokumentation

| Wo | Was |
|---|---|
| [docs/betrieb.md](docs/betrieb.md) | Die maßgebliche Betriebsdoku |
| [docs/betriebshandbuch.html](docs/betriebshandbuch.html) | Dieselbe, lesbar aufbereitet |
| [docs/architektur.html](docs/architektur.html), [docs/architektur-detail.html](docs/architektur-detail.html) | Gesamtschaubild und Detail samt Ablauf einer Wissensfrage |
| [docs/bauplan.html](docs/bauplan.html) | Der Transformationsplan mit Umsetzungsstand |
| [docs/glossar.html](docs/glossar.html) | Die Fachbegriffe, je allgemein und in ihrer Rolle in Weave |
| [docs/wissensportal.md](docs/wissensportal.md) | Freigaben, Indexstatus, Wissensbereiche |
| [docs/adr/](docs/adr/) | Warum eigentlich so — sieben Entscheidungen |
| [contracts/](contracts/) | Die Datenverträge zwischen den Diensten |
