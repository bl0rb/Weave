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
- Widerspruchs-Kandidaten (`app/services/contradictions.py`, `CONTRADICTION_CHECK_ENABLED`, Standard `true`): nach jedem Index-Lauf werden Chunks des neuen Dokuments mit Chunks anderer indexierter Dokumente desselben Wissensbereichs verglichen (eigene Versionskette ausgenommen). Kandidat ist ein Paar mit Kosinus-Ähnlichkeit ≥ `CONTRADICTION_MIN_SIMILARITY` (0.86), dessen Fakten-Tokens (Daten, Beträge, Prozente, Fristen wie „30 Tage“) sich bei gemeinsamem Kontextwort unterscheiden; höchstens `CONTRADICTION_MAX_REPORTS_PER_DOCUMENT` (10) pro Dokument. Reine Heuristik: sie meldet nur *mögliche* Widersprüche best effort an Weave-Ingest (`POST /api/v1/quality/contradictions`, Prüfaufgabe für die Wissensverantwortlichen) und lässt den Index-Lauf nie scheitern.
- Wissensgedächtnis ([ADR-0009](../../docs/adr/0009-wissensgedaechtnis.md), `KNOWLEDGE_MEMORY_MODE`, Standard `off`): nach jeder Änderung an einem Wissensbereich, entprellt über den Collection-Sync-Tick, eine Zusammenfassung pro Dokument und eine Übersicht pro Bereich in `knowledge_memory` (`app/services/memory.py`, Task `weave.knowledge.build_memory`). Im Modus `llm` mit dem zentralen Chat-Provider, sonst oder bei dessen Ausfall extraktiv ohne LLM.

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
- `knowledge_memory`: Dokument-Zusammenfassungen und Bereichs-Übersichten (Lesezugriff durch Weave-Retrieval, `contracts/chunk-store.md`)
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

Incident 2026-09-22: ohne explizites `--concurrency` startet Celery mit der
Prozessanzahl des Hosts (in Kubernetes also der Node-CPU-Zahl) -- bei vielen
gleichzeitig ausgelieferten Releases feuerte das dutzende parallele
Embedding-Requests gegen den ressourcenlimitierten Embeddings-Pod und hat ihn
OOMKilled. `CELERY_WORKER_CONCURRENCY` (Default 2, siehe
`docker-compose.weave.yml`/Helm-Chart) begrenzt das analog zu Weave-Ingests
`worker.Dockerfile`-Muster (`--concurrency=${CELERY_WORKER_CONCURRENCY:-1}`);
lokal ohne Docker also z. B.
`--concurrency=${CELERY_WORKER_CONCURRENCY:-2}` an den obigen Celery-Aufruf
anhängen. `EMBEDDING_REQUEST_TIMEOUT_SECONDS` (Default 180) und
`EMBEDDING_MAX_ATTEMPTS` (Default 6, Backoff 30s/2min/10min/30min/60min)
steuern, wie geduldig der Worker einen vorübergehend überlasteten/
neustartenden Embeddings-Pod übersteht, statt das Dokument sofort dauerhaft
auf `failed` zu setzen.

### Optionaler Vektorindex: Amazon S3 Vectors

Standard ist `VECTOR_BACKEND=pgvector` -- dann bleibt alles wie oben, boto3
wird nicht einmal importiert. Mit `VECTOR_BACKEND=s3vectors` spiegelt der
Dienst zusätzlich jeden eingebetteten Chunk in einen S3-Vectors-Index
(`app/services/vector_index.py`), den Weave-Retrieval für die Vektorsuche
abfragt. Postgres bleibt die maßgebliche Quelle; Datensatzform und Regeln:
`contracts/chunk-store.md`, Abschnitt "Optionaler Vektorindex".

| Variable | Default | Bedeutung |
|---|---|---|
| `VECTOR_BACKEND` | `pgvector` | `pgvector` oder `s3vectors` (leer = `pgvector`); alles andere bricht den Start ab |
| `S3VECTORS_BUCKET` | leer | Vector Bucket; Pflicht bei `s3vectors` |
| `S3VECTORS_INDEX` | `weave-chunks` | Indexname; bei Modell-/Dimensionswechsel neuen Namen wählen |
| `S3VECTORS_REGION` | leer | leer = boto3-Standard (`AWS_DEFAULT_REGION` oder Profil-Region; nicht `AWS_REGION`, keine Instanz-Metadaten) |
| `S3VECTORS_ENDPOINT_URL` | leer | nur für Tests/Emulatoren |
| `S3VECTORS_ACCESS_KEY_ID` / `S3VECTORS_SECRET_ACCESS_KEY` | leer | beide leer = Standard-Credential-Chain (IAM-Rolle/IRSA/Env/Instance Profile); nur einer gesetzt bricht den Start ab |

Schreibzeitpunkte: nach jedem erfolgreichen Index-/Reindex-Commit (auch
`python -m app.cli reindex` und `reconcile-collections`) werden die Chunks
des Dokuments geschrieben; Vektoren gelöschter Chunks (Neu-Indizierung,
Supersede, `document.withdrawn`) werden erst nach dem Commit gelöscht -- beim
Rückzug nicht im Webhook-Request, sondern im Celery-Task
`weave.knowledge.delete_vectors`. Alles best effort: ein S3-Fehler erscheint
als Warnung im Log (mit Dokument-ID) und lässt nie einen Index-Lauf oder
Rückzug scheitern. `PutVectors`-Batches richten sich nach
`EMBEDDING_DIMENSION` (max. 500 Vektoren und unter dem 20-MiB-Limit pro
Request, z. B. 466 bei 1536, 180 bei 4096 Dimensionen); `--batch` ist nur
eine Obergrenze.

```bash
cd backend
../.venv/bin/python -m app.cli s3vectors init                 # Index anlegen bzw. Dimension/Metrik prüfen
../.venv/bin/python -m app.cli s3vectors sync [--prune] [--batch 500]  # alle indexierten Chunks schreiben, --prune entfernt Überzähliges
../.venv/bin/python -m app.cli s3vectors status [--count-vectors]      # Index und Zählerstände
```

**Reihenfolge beim Einschalten (verbindlich):** Ein leerer oder halb
gefüllter Index ist für Weave-Retrieval kein Fehler -- `QueryVectors`
liefert dann einfach weniger Treffer, der pgvector-Fallback greift nicht,
und der Vektoranteil der Suche fällt still weg. Deshalb:

1. `VECTOR_BACKEND=s3vectors` (plus `S3VECTORS_*`) zuerst **nur** für
   Weave-Knowledge (API und Worker) setzen; Weave-Retrieval bleibt auf
   `pgvector`.
2. `init`, dann `sync` ausführen, bis `sync` mit Exit-Code 0 endet;
   `status --count-vectors` zeigt Vektoren im Index neben den
   zulässigen Chunks in Postgres.
3. Erst danach Weave-Retrieval auf `s3vectors` umstellen.

Zurück auf pgvector in umgekehrter Reihenfolge: erst Retrieval, dann
Knowledge. `sync --prune` regelmäßig (z. B. als Cronjob) repariert verpasste Schreib-
oder Löschvorgänge. `init`/`sync` verweigern mit Exit-Code 2, solange
`VECTOR_BACKEND` nicht `s3vectors` ist; 1 = Lauf fehlgeschlagen.
Benötigte IAM-Aktionen: `s3vectors:PutVectors`, `DeleteVectors`,
`ListVectors`, `GetIndex`, `GetVectors` und für `init` `CreateIndex`.

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

**Wartung:** `GET /api/v1/internal/documents` (Inventar für den Abgleich mit Ingest) und der Admin-Job in Ingest (`POST /api/v1/admin/knowledge/orphans`, Dry-Run, dann Apply) ziehen Dokumente zurück, deren Auftrag oder Wissensbereich nicht mehr existiert ([docs/betrieb.md](../../docs/betrieb.md) §3.3). Altbestand ohne Wissensbereich wird mit `python -m app.cli reconcile-collections` einmalig zugeordnet (§3.2). Knowledge läuft mit mehreren Replikas ohne gemeinsamen Speicher.
