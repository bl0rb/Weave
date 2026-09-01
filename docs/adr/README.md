# Architecture Decision Records (ADR)

Dieses Verzeichnis enthält die Architektur-Entscheidungen der Weave-Plattform. Jeder ADR dokumentiert ein Problem, die gewählte Lösung und deren Konsequenzen.

## Index

| ADR | Titel | Status | Datum |
|-----|-------|--------|-------|
| [0001](0001-queue-topologie.md) | Queue-Topologie | angenommen | 2026-08-31 |
| [0002](0002-auth-strategie.md) | Authentifizierungs- und Autorisierungsstrategie | angenommen | 2026-08-31 |
| [0003](0003-secrets-strategie.md) | Secrets- und Credentials-Verwaltungsstrategie | angenommen | 2026-08-31 |
| [0004](0004-datenhaltung.md) | Datenhaltungs- und Schemastrategie | angenommen | 2026-08-31 |
| [0005](0005-geteiltes-read-model.md) | Geteiltes Read-Model fuer den Chunk-Store | angenommen | 2026-08-31 |
| [0006](0006-foederierte-anmeldung.md) | Weave-Ingest als Identitaetsanbieter der Plattform | angenommen | 2026-09-01 |

## Format

Jeder ADR folgt dem Standard-Format:

- **Status:** angenommen / vorgeschlagen / abgelehnt / veraltet
- **Datum:** Annahmendatum
- **Kontext:** Hintergrund und Problemstellung
- **Entscheidung:** Die gewählte Lösung
- **Konsequenzen:** Positive und negative Auswirkungen
- **Alternativen:** Andere erwogene Optionen

## Übersicht der Entscheidungen

### 0001: Queue-Topologie
Zentrale Redis-Instanz mit pro-Service benannten Celery-Queues und separaten Logical-DBs. Verhindert gegenseitiges Task-Konsumieren zwischen Services.

### 0002: Auth-Strategie
Menschen nutzen OIDC am Gateway; Maschinen Personal-API-Tokens; Service-zu-Service Kommunikation mit kurzzeitigen signierten Tokens. Zentrale Authentifizierung, dezentralisierte Autorisierung.

### 0003: Secrets-Strategie
Pro Service eigener SECRET_KEY. Fernet + HKDF-SHA256 Verschlüsselung. Neu: Versionierte Key-Liste für transparente Key-Rotation ohne Datenverlust.

### 0004: Datenhaltung
Ein Postgres-Cluster, aber separate Datenbank pro Service. Kein Sharing von Tabellen. Datenaustausch nur über APIs und Events. Ermöglicht unabhängige Migrationen und Skalierung. Eine benannte Ausnahme dazu: siehe ADR-0005.

### 0005: Geteiltes Read-Model für den Chunk-Store
Bewusste, einmalige Ausnahme zu ADR-0004: Weave-Retrieval liest `documents`/`chunks`/`collections` read-only direkt aus Weave-Knowledges Datenbank, statt über dessen API. Grund: pgvector-KNN und tsvector-Ranking müssen als SQL in der Datenbank laufen. Eigene DB-Rolle mit reinen SELECT-Rechten; das Schema wird zum Vertrag zwischen beiden Services (`contracts/chunk-store.md`).

### 0006: Weave-Ingest als Identitätsanbieter
Weave-API führt keine eigene Kontenwelt mehr, sondern föderiert an Weave-Ingest: dort liegen lokale Benutzer, Teams und eine ganze Tabelle von OIDC-Verbindungen samt Oberfläche. Der Chat schickt zum Anmelden dorthin und bekommt die Identität über einen einmaligen, server-zu-server eingelösten Code zurück. Ein Administrator pflegt Konten an einer Stelle; jede Anmeldeart, die Weave-Ingest kennt, gilt damit auch für den Chat.
