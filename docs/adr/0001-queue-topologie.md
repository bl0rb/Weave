# ADR 0001: Queue-Topologie

**Status:** angenommen

**Datum:** 2026-08-31

## Kontext

PaddleDoc nutzt heute eine einzige Default-Celery-Queue für alle asynchronen Task-Verarbeitungen. Beim Split in mehrere Microservices (Weave-Ingest, Weave-Knowledge, Weave-Retrieval, Weave-Runtime, Weave-Tools, Weave-API) würden Worker über die Default-Queue gegenseitig Tasks konsumieren. Dies führt zu:

- Unvorhersehbarem Task-Routing zwischen Services
- Schwierigkeiten bei unabhängiger Skalierung der Worker pro Service
- Ressourcen-Konflikten (z.B. Memory-intensive Ingest-Tasks blockieren Knowledge-Worker)

## Entscheidung

Gemeinsame Redis-Instanz für alle Weave-Services, aber:

1. **Pro Service eine EIGENE benannte Celery-Queue:**
   - `weave.ingest.process` für Weave-Ingest
   - `weave.knowledge.index` für Weave-Knowledge
   - `weave.retrieval.search` für Weave-Retrieval
   - `weave.runtime.execute` für Weave-Runtime
   - `weave.tools.validate` für Weave-Tools
   - `weave.api.notify` für Weave-API

2. **Getrennte Redis-Logical-DBs für Broker, Result-Backend, Cache:**
   - DB 0: Broker (Task-Queues)
   - DB 1: Result-Backend (Task-Ergebnisse)
   - DB 2: Cache (temporäre Daten)

3. **Explizites Task-Routing via `task_routes`:**
   ```python
   CELERY_TASK_ROUTES = {
       'weave.ingest.*': {'queue': 'weave.ingest.process'},
       'weave.knowledge.*': {'queue': 'weave.knowledge.index'},
   }
   ```

## Konsequenzen

**Positiv:**
- Services können unabhängig skaliert werden
- Task-Routing ist explizit und deklarativ
- Keine gegenseitige Task-Konkurrenz
- Klare Ressourcen-Isolierung pro Service

**Negativ:**
- Höherer Verwaltungsaufwand (pro Service ein Worker-Pool)
- Migration auf getrennte Redis-Instanzen später möglich, aber noch nicht nötig

## Alternativen

1. **Separate Redis-Instanzen pro Service (später):**
   - Volle Isolation, höhere Infrastruktur-Komplexität
   - Wird erwogen bei Skalierungsproblemen

2. **Shared Queue mit Routing-Hints:**
   - Tasks müssen mit Hint versehen sein; fehleranfällig
   - Keine echte Isolierung

3. **Message-Bus (RabbitMQ/Kafka):**
   - Overkill für heutige Anforderungen
   - Deutlich höhere Komplexität
