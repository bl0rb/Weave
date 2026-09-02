# Weave

## Ueberblick

Dieses Repository ist das Monorepo der Weave-Plattform: einer dokumentbasierten
Suche/Chat-Loesung, aufgeteilt in sechs eigenstaendig lauffaehige Dienste plus
zwei optionale, self-hosted CPU-Modelldienste und eine eigene Chat-Oberflaeche.
Jeder Dienst laeuft als eigener Prozess/eigenes Image, hat eigene Tests und
eigene Abhaengigkeiten — sie teilen sich in diesem Repo nur die Quelle, nicht
die Laufzeit.

Die Dienste stammen aus sechs vormals getrennten Repos (Weave-Ingest,
Weave-Knowledge, Weave-Retrieval, Weave-Runtime, Weave-Tools, Weave-API), die
hierher migriert wurden und seither eingefroren sind — Verweise zeigen ab
jetzt ausschliesslich auf die `services/`-Verzeichnisse hier, nicht mehr auf
die alten Repos.

## Verzeichnisuebersicht

| Verzeichnis | Aufgabe |
|---|---|
| `services/ingest` | Dokumenten-Ingestion: OCR (PaddleOCR), Konvertierung nach Markdown + Frontmatter, Quality-Gate; besitzt die Collections. |
| `services/knowledge` | Index-Pipeline: konsumiert freigegebene `document.released`-Events, chunked und embedded Dokumente, schreibt den Chunk-Store (pgvector). |

Das [Wissensportal](docs/wissensportal.md) indexiert regulär ausschließlich unveränderliche, manuell freigegebene `document.released`-Snapshots.
| `services/retrieval` | Hybride Suche (pgvector + tsvector, RRF-Fusion) — liest den Chunk-Store von `services/knowledge` read-only mit, ohne eigene Migrationen. |
| `services/runtime` | LLM-Executor und Agentic Loop: Intent-Routing, Bots als YAML, ruft `services/retrieval` fuer Suche, kann Chat-Turns an n8n delegieren. |
| `services/api` | Oeffentliches Gateway und Identitaets-Autoritaet: Nutzer, API-Tokens, Sitzungen, Gespraeche, optionales OIDC-Login. |
| `services/tools` | MCP-Server + REST-Spiegel fuer rechte-gebundene Suche (`list_collections`, `search`) — die Action-Layer fuer delegierte/Personal-Token-Aufrufer. |
| `services/chat` | Next.js-Chat-Oberflaeche fuer Menschen, spricht ausschliesslich mit `services/api`. |
| `services/embeddings` | Optionaler, self-hosted CPU-Embedding-Server (`intfloat/multilingual-e5-small`), OpenAI-kompatibel unter `/v1/embeddings`. |
| `services/reranker` | Optionaler, self-hosted CPU-Rerank-Server (`BAAI/bge-reranker-v2-m3`), Cohere/Jina-kompatibel unter `/rerank`. |
| `contracts/` | Zentrale, dienstuebergreifende Vertraege: `frontmatter.schema.json`, `openapi.json`, `chunk-store.md`, `internal-chat.md`, `n8n-flow.md`, `events/`. |
| `deploy/` | Docker-Compose-Stack (`docker-compose.weave.yml` + lokales Override `docker-compose.local.yml`), Postgres-Init-Skripte. |
| `docs/` | Architekturentscheidungen (`docs/adr/`) und das Betriebshandbuch (`docs/betrieb.md`). |

Jeder Dienst hat zudem sein eigenes README mit den fuer ihn spezifischen
Details (Layout, Konfiguration, bekannte Einschraenkungen).

## Starten

Der komplette Stack laeuft ueber Docker Compose — siehe `deploy/` und vor
allem `docs/betrieb.md` fuer Pflichtwerte, geteilte Secrets und alle
Stolperfallen. Kurzfassung fuer den lokalen Start (baut alle Dienste aus
diesem Monorepo statt Registry-Images zu ziehen, die nie veroeffentlicht
wurden):

```bash
cd deploy
touch .env    # Werte fuellen -- siehe docs/betrieb.md Abschnitt 4 und deploy/README.md

docker compose -f docker-compose.weave.yml -f docker-compose.local.yml up -d --build
```

Der OCR-Worker (PaddleOCR, mehrere GB) haengt hinter einem eigenen Profil,
damit der Rest des Stacks in wenigen Minuten statt deutlich laenger
hochkommt:

```bash
docker compose -f docker-compose.weave.yml -f docker-compose.local.yml \
  --profile ocr up -d weave-ingest-worker
```

`docs/betrieb.md` deckt daruber hinaus Erstinbetriebnahme, SSO-Einrichtung,
den Umstieg von den Attrappen-Providern auf die echten Embedding-/
Rerank-Dienste und einen Selbsttest fuer den laufenden Stack ab.

## Tests

Jeder Python-Dienst hat sein eigenes `.venv` und seine eigene
`requirements.txt`; Python-Version und Testbefehl unterscheiden sich pro
Dienst (siehe `.github/workflows/pr-ci.yml`, das genau das pro Dienst
abbildet). Beispielhaft:

```bash
# services/tools, services/embeddings, services/reranker (requirements.txt
# im Dienst-Wurzelverzeichnis):
cd services/<dienst>
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q

# services/ingest, services/knowledge, services/retrieval, services/runtime,
# services/api (requirements.txt liegt hier unter backend/):
cd services/<dienst>
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/pytest -q backend/tests
```

```bash
# services/chat (Next.js):
cd services/chat
npm install
npm test
```

Die CI (`.github/workflows/pr-ci.yml`) fuehrt pro Pull-Request nur die
Dienste aus, deren Pfad sich tatsaechlich geaendert hat.
