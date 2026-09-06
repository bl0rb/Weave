# ADR 0004: Datenhaltungs- und Schemastrategie

**Status:** angenommen

**Datum:** 2026-08-31

## Kontext

PaddleDoc nutzt heute ein monolithisches Datenbank-Schema. Bei der Aufteilung in Microservices entstehen Fragen:

1. Gemeinsame oder separate Datenbanken?
2. Dürfen Services Tabellen teilen?
3. Wie koordiniert man Schema-Migrationen?

Cross-Service-Joins und geteilte Tabellen machen Services schwer zu skalieren und zu testen. Die Lösung muss Datenintegrität bewahren, während Services unabhängig bleiben.

## Entscheidung

### Ein Postgres-Cluster, aber eigene Datenbank pro Service

- **Infrastruktur:** Gemeinsamer Postgres-Server / Cluster (Kosteneffizienz)
- **Logical Separation:** Jeder Service bekommt eigene Database
  - `weave_ingest` für Weave-Ingest
  - `weave_knowledge` für Weave-Knowledge
  - `weave_retrieval` für Weave-Retrieval
  - `weave_runtime` für Weave-Runtime
  - `weave_tools` für Weave-Tools
  - `weave_api` für Weave-API

### Keine geteilten Tabellen

- Services müssen sich nicht einigen auf Tabellenstruktur
- Keine Abhängigkeiten zwischen Service-Schemas
- Führt zu weniger Verzahnung

> **Benannte Ausnahme (siehe ADR-0005):** Weave-Knowledges `documents`/`chunks`/`collections` sind ein geteiltes Read-Model — Weave-Knowledge bleibt einziger Schreiber, Weave-Retrieval liest read-only direkt auf derselben Datenbank, weil pgvector-KNN und tsvector-Ranking als SQL in der Datenbank laufen müssen. Das ist die einzige Ausnahme von "keine geteilten Tabellen" in der gesamten Plattform; sie präjudiziert kein weiteres Tabellenpaar.

### Datenaustausch nur über APIs und Events

- **Synchron:** HTTP/gRPC zwischen Services
- **Asynchron:** Event-Stream (Celery-Events, Kafka, etc.)
- **Kein:** Direkte DB-Joins, Trigger über Service-Grenzen

### Konkrete Schemas pro Service

**Weave-Ingest** (behält PaddleDoc-Alembic):
- `documents` (id, source_id, metadata, created_at)
- `ingestions` (id, document_id, status, result)
- `credentials` (encrypted credentials für externe APIs)

**Weave-Knowledge**:
- `documents` (id, ingest_document_id, title, content)
- `chunks` (id, document_id, text, page_number)
- `embeddings` (id, chunk_id, vector) — mit pgvector Extension
- `tsvector_index` (chunk_id, search_vector) — für Volltextsuche

**Weave-Retrieval**:
- `search_indices` (id, name, config)
- `cache` (id, query_hash, result) — volatile, TTL-managed
- Kein eigenes Schema für `documents`/`chunks`/`collections` — read-only Zugriff auf Weave-Knowledges gleichnamige Tabellen, siehe ADR-0005s benannte Ausnahme.

**Weave-Runtime**:
- `executions` (id, workflow_id, status, logs)
- `state_snapshots` (id, execution_id, step_id, state_json)

**Weave-Tools**:
- `tool_definitions` (id, name, schema, config)
- `tool_usage_logs` (id, tool_id, user_id, input, output)

**Weave-API**:
- `users` (id, email, oidc_sub)
- `teams` (id, name, owner_id)
- `api_tokens` (id, user_id, token_hash, expires_at)

### Schema-Migrationen

- **Weave-Ingest:** Weiter Alembic nutzen (etabliert in PaddleDoc)
- **Andere Services:** Alembic oder Liquibase (Migration-Tool der Wahl)
- **Koordination:** Jeder Service läuft Migrationen eigenständig beim Startup
- **Rückwärts-Kompatibilität:** Services müssen mit 2-3 früheren Schema-Versionen kompatibel sein (z.B. falls Rollback nötig)

## Umsetzungsstand

Der ausgelieferte Stack legt **drei** der oben genannten sechs Datenbanken an:
`weave_ingest` (als `POSTGRES_DB` vom Basis-Image), `weave_knowledge` und
`weave_api` (`deploy/postgres-init/01-create-databases.sh`). Die drei übrigen
existieren nicht, weil ihre Dienste keine eigenen Daten halten:

- **Weave-Retrieval** liest die Datenbank von Weave-Knowledge über die reine
  `SELECT`-Rolle `weave_retrieval_ro` mit — die eine bewusste Ausnahme zu
  „keine geteilten Tabellen", entschieden in [ADR-0005](0005-geteiltes-read-model.md).
- **Weave-Runtime** ist zustandslos: Bots kommen aus dem Volume `runtime_bots`
  und aus Ingests Bot-Verwaltung, sonst hält der Dienst nichts.
- **Weave-Tools** hält ebenfalls nichts; sein Umfang entsteht pro Aufruf aus
  dem Delegations- oder Personal-Token.

Der Grundsatz bleibt damit unberührt — kein Dienst schreibt in die Datenbank
eines anderen. Ein Dienst, der später eigene Daten bekommt, bekommt auch eine
eigene Datenbank nach demselben Muster.

## Konsequenzen

**Positiv:**
- Services können unabhängig skaliert werden (z.B. Weave-Knowledge mit pgvector kann horizontaler skaliert werden)
- Migrationen sind pro Service, nicht koordiniert
- Klare Daten-Ownership pro Service
- Später leicht auf separate DB-Cluster migrierbar

**Negativ:**
- Keine Database-seitigen Transaktionen über Services hinweg
- Services müssen sich auf API-Verträge einigen
- Datenkonsistenz muss auf Applikationsebene gelöst werden (z.B. Saga-Pattern)

## Alternativen

1. **Ein gemeinsames Schema:**
   - Einfacher anfangs, verhindert später Skalierung
   - Single Point of Failure

2. **Separate Postgres-Cluster pro Service:**
   - Volle Isolation, aber höhere Infrastruktur-Komplexität
   - Später evaluieren, wenn Datenvolumen wächst

3. **Hybrid: Shared Core, Private Extensions:**
   - Eine `core_weave` DB für Nutzer, Teams, etc.
   - Services mit privaten Erweiterungen
   - Zu viel Coordination overhead
