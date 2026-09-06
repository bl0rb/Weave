# Weave-Knowledge

## Einordnung

Weave-Knowledge ist die **Index-Pipeline** des Weave-Systems. Sie konsumiert freigegebene, strukturierte Dokumente aus Weave-Ingest und transformiert sie in durchsuchbare Embeddings, die in einer vektorisierten Postgres-Datenbank persistiert werden. Damit besitzt die Plattform einen kontrollierten internen Indexierungsweg statt direkter Pushes an externe Wissenssysteme.

## Zweck

- Strukturbewusstes Chunking von Dokumenten (ATX-Headings, `<!-- page:N -->`-Marker, GFM-Tabellen, Confluence-Breadcrumbs)
- Idempotente Verarbeitung des `document.released`-Events von Weave-Ingest (Deduplication über `release_id`); `document.processed` wird authentifiziert mit `awaiting_release` bestätigt und indexiert nichts.
- Metadata-Enrichment aus YAML-Frontmatter
- Abstraktion von Embedding-Providern
- Persistierung in PostgreSQL mit pgvector für Vektorsuche und tsvector-Indizes für Fulltext-Matching

## Verantwortlichkeiten

- Event-Listening und Fehlerbehandlung (mit Retry-Logik)
- Dokumenten-Parsing und intelligentes Chunking
- Embedding-Generation über Provider-Interface
- Datenbankschema-Verwaltung und Indexoptimierung
- Telemetrie und Monitoring der Pipeline

## Nicht-Ziele / Abgrenzung

- **Kein OCR**: Nur bereits extrahierte Textinhalte
- **Keine Suche**: Suche ist Aufgabe von Weave-Retrieval
- **Kein Chat**: Konversations-Management gehört zu Weave-Runtime und Weave-API
- Keine Authentifizierung: Nutzer-Auth erfolgt downstream

## Schnittstellen

**Input:**
- Signierter interner Event-Endpunkt für Weave-Ingest: `document.released` wird indexiert, `document.processed` nur mit `awaiting_release` quittiert; der dedizierte `collection.updated`-Hinweis löst einen vollständigen Registry-Abruf aus und enthält selbst keine ACL.

**Output:**
- PostgreSQL + pgvector: Persistierte Embeddings mit Metadaten
- Interner Zustand: Content-SHA256 für Idempotenz-Tracking

**Abhängigkeiten:**
- Embedding-Provider (OpenAI, Local, etc.)
- PostgreSQL 14+ mit pgvector Extension

## Entwicklung

Das Backend liegt unter `backend/` und folgt demselben Layout wie Weave-Ingest
(`app/{core,models,schemas,api,services,workers}`, `alembic/`, `tests/`).

### Setup

```bash
cd backend
python3.12 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # und Werte anpassen
```

Ohne weitere Konfiguration läuft der Service gegen eine lokale SQLite-Datei
(`DATABASE_URL`-Default) -- für pgvector-Vektorspalten und die
`tsvector`-Volltextspalte ist eine echte PostgreSQL-Instanz mit installierter
`vector`-Extension nötig (siehe `alembic/versions/0001_init.py`).

### Tests

```bash
cd /pfad/zu/Weave-Knowledge
.venv/bin/python -m pytest backend/tests -q
```

Die Testsuite läuft komplett gegen SQLite (kein Postgres/Redis nötig) --
inklusive eines eigenen `alembic upgrade head`/`downgrade base`-Durchlaufs
gegen eine frische SQLite-Datei (`backend/tests/test_migrations.py`), der die
Postgres-only-Teile der Migration (pgvector-Spalte, generierte
`tsvector`-Spalte + GIN-Index) automatisch überspringt.

### Alembic

```bash
cd backend
../.venv/bin/alembic upgrade head      # Migrationen anwenden
../.venv/bin/alembic downgrade base    # zurückrollen
../.venv/bin/alembic revision -m "..."  # neue Migration anlegen
```

`DATABASE_URL` (Env-Var oder `.env`) bestimmt das Ziel; ohne echte Postgres-
Verbindung läuft `alembic upgrade head` gegen die konfigurierte SQLite-Datei.

### Start (lokal, ohne Docker)

```bash
cd backend
../.venv/bin/uvicorn app.main:app --reload --port 8001
../.venv/bin/celery -A app.workers.tasks worker --loglevel=info -Q weave.knowledge.index
```

`GET /health` prüft dabei per `SELECT 1` aktiv die Datenbankverbindung.

### Docker

```bash
docker build -f backend/Dockerfile -t weave-knowledge backend
```

Der Compose-Service (`weave-knowledge` + `weave-knowledge-worker`) ist Teil
von Weave-Ingests `deploy/docker-compose.weave.yml`.

## Status

Der Index-Lauf verarbeitet signierte `document.released`-Events, lädt den
unveränderlichen Release-Snapshot und schreibt ihn idempotent in den
Chunk-Store. `document.processed` erzeugt keinen Index-Task.
