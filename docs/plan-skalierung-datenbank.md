# Umsetzungsplan: Skalierung über PostgreSQL

Stand: 2. Oktober 2026 · Status: Entwurf zur Abstimmung · Geltungsbereich: neue Deployments

Grundlage ist die Skalierbarkeitsanalyse vom selben Tag (API-Prozesse, Celery-Worker, Kubernetes-Replikas, MCP). Mit **(A)** markierte Aussagen sind Annahmen, die vor der Umsetzung zu prüfen sind.

## 1. Ziel und Rahmen

- **PostgreSQL ist die einzige dauerhafte Ablage** für Originale, Markdown, Bilder/Anhänge, Editor-Versionen, Freigabe-Snapshots und Laufzeitkonfiguration.
- **Kein gemeinsames Dateivolume.** Jeder API- und Worker-Pod hat nur ein eigenes `emptyDir` für temporäre Dateien.
- **Redis bleibt flüchtig:** Broker, kurzlebige Sperren, Ratelimit. Ein Verlust von Redis kostet keine Fachdaten.
- **Jede Replika kann jede Anfrage und jeden Job übernehmen.** Korrektheit kommt aus Datenbank-Claims mit Token, nicht aus Prozesszustand oder `celery inspect`.
- **Nur neue Deployments:**
  - keine Datenmigration und kein Dual-Read
  - keine Rückwärtskompatibilität zur Dateiablage
  - bestehende Installationen bleiben auf der bisherigen Chart-Version.
- **S3 kommt später**; auf der bestehenden EKS-Kundenkonfiguration ist es derzeit nicht möglich. Alle Binärdaten laufen deshalb schon jetzt über eine Objektablage mit austauschbarem Backend.
- **Datenbank-Optimierung kommt später** (Abschnitt 6.2). Jetzt werden nur Startwerte gesetzt, die eine Skalierung überhaupt erlauben.

Nicht-Ziele: Mountpoint-for-S3, Multi-Region, Multi-Master-Datenbanken.

Entscheidungen vom 2. Oktober 2026:

- **Uploadgröße:** 100 MiB pro Datei. Jede Anfrage trägt genau eine Datei; es gibt keinen Endpunkt, der mehrere annimmt.
- **In-App-Backup bleibt:** für konsistente Gesamtstände und für Migrationen zwischen Installationen. Disaster Recovery läuft über die nativen DB-Backups.
- **`max_connections` = 200.** Heute vermutlich 100, der Postgres-Standard. Daraus ergeben sich das Verbindungsbudget und die Replika-Maxima in E6.
- **KEDA** ist im Kundencluster nicht installiert. Es wird als Helm-Schalter eingeplant, Standard ist aus (E7). Ohne KEDA gilt die in Kubernetes eingebaute HPA nach CPU.

## 2. Ausgangslage in Kürze

Bereits datenbankbasiert: `jobs.upload_content`, `jobs.result_markdown`, `job_markdown_versions`, `job_artifacts`, `mail_messages.raw_content`, `document_releases.markdown_snapshot`, Confluence-Import und Release-Outbox mit Lease.

Noch offen:

| Bereich | Problem | Fundstelle |
|---|---|---|
| Job-Ausführung | Kein Claim-Token, kein Heartbeat; ein laufender Job wird nach 2 min erneut übernommen, das Ergebnis ungeprüft geschrieben. | `services/ingest/backend/app/workers/tasks.py:49`, `:336-352`, `:534-547` |
| Neustarts | `celery inspect` als Wahrheit; bei Fehlern leere Menge, in `restart_pending_jobs` nur zählbasiert. | `services/ingest/backend/app/api/routes.py:140-159`, `:1863-1870`, `:1932` |
| Transaktion | Offene Transaktion während der gesamten OCR (bis 30 min); blockiert u. a. Migrationen. | `tasks.py:480-501` |
| Versionierung | Upload-Versionen ohne Sperre. | `routes.py:996-1019` |
| Dateisystem | Upload-Kopie in der API, Upload- und Ergebniskopie im Worker, Fallbacks, Backup-Archiv, Ordner. | `services/storage.py:90-125`, `tasks.py:66-84`, `:526-531`, `routes.py:660-677`, `services/backup.py:221-228` |
| Speicher | Blobs werden mit `db.get(Job)` vollständig in den RAM geladen. | `routes.py:2188`, `tasks.py:356`, `api/portal.py:1354` |
| Deployment | RWX-Volume Pflicht, eine Queue für alles, `Recreate`, Migration beim Start jeder Replika, MCP zustandsbehaftet. | `deploy/charts/weave/values.yaml:803-813`, `workers/celery_app.py:14-31`, `services/tools/app/mcp_server.py:159` |

## 3. Architekturentscheidungen

### E1 Objektablage in PostgreSQL mit Chunks

```sql
CREATE TABLE stored_objects (
    id            varchar(36) PRIMARY KEY,
    sha256        char(64)    NOT NULL,
    size_bytes    bigint      NOT NULL,
    content_type  varchar(128),
    backend       varchar(8)  NOT NULL DEFAULT 'db',  -- 'db' | später 's3'
    storage_key   varchar(1024),                      -- nur für 's3'
    chunk_bytes   integer     NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE stored_object_chunks (
    object_id  varchar(36) NOT NULL REFERENCES stored_objects (id) ON DELETE CASCADE,
    seq        integer     NOT NULL,
    data       bytea       NOT NULL,
    PRIMARY KEY (object_id, seq)
);
-- PDFs und Bilder sind bereits komprimiert: keinen Kompressionsversuch.
ALTER TABLE stored_object_chunks ALTER COLUMN data SET STORAGE EXTERNAL;

-- Referenzen statt Blob-Spalten (FK RESTRICT, jeweils indiziert)
ALTER TABLE jobs          ADD COLUMN upload_object_id varchar(36) REFERENCES stored_objects (id);
ALTER TABLE job_artifacts ADD COLUMN object_id        varchar(36) NOT NULL REFERENCES stored_objects (id);

-- Da es keine Altdaten gibt, entfallen die Blob- und Pfadspalten in derselben Migration:
ALTER TABLE jobs          DROP COLUMN upload_content, DROP COLUMN result_path;
ALTER TABLE job_artifacts DROP COLUMN content;
-- mail_messages bleibt unverändert: die Mail-API ist stillgelegt, die Tabelle wird nicht mehr beschrieben.
```

| Variante | RAM pro Upload im Pod | Späterer S3-Umstieg | Bewertung |
|---|---|---|---|
| `bytea` in der Fachspalte (heute) | 2–3 × Dateigröße | Spalten in drei Tabellen umbauen | nein |
| Large Objects (`pg_largeobject`) | gestreamt | wie unten | nein: eigene Löschung (`lo_unlink`), Sonderfälle bei Rechten und Backup |
| **Objekttabelle, Chunks à 4 MiB** | ≈ 1 Chunk | Backend-Schalter | **empfohlen** |

Regeln:

- Objekte sind unveränderlich. Interne Kopien referenzieren dasselbe Objekt statt Bytes zu kopieren. Das betrifft die neue Job-Version beim Bearbeiten einer Freigabe (`api/portal.py:362-368`) und die Benchmark-Varianten.
- Gelöscht wird nur durch eine Garbage Collection (GC) nicht mehr referenzierter Objekte. `FK RESTRICT` verhindert, dass ein noch referenziertes Objekt verschwindet.
- Zugriff ausschließlich über ein neues Modul `app/services/object_store.py`:
  - `put_file(db, fileobj, content_type) -> StoredObject`
  - `iter_chunks(object_id)`, mit eigener Session und einer Abfrage pro Chunk über den Primärschlüssel
  - `copy_to_path(db, object_id, path)`
  - später `presigned_url(object_id)` für S3.
- Für `StreamingResponse` öffnet der Generator eine eigene Session, weil FastAPI die Session aus `get_db` vor dem Senden der Antwort schließt.
- Markdown bleibt Text in den Fachtabellen und wandert auch später nicht nach S3.
- `jobs.upload_path` bleibt als reiner Anzeigename (Dateiendung, Ordner) ohne Bedeutung im Dateisystem.

### E2 Job-Ausführung mit Claim-Token, Heartbeat und Reaper (umgesetzt in Schritt 1)

Neue Spalten in `jobs` (Migration `0042_job_claims`):

- `claim_token varchar(36)`: Übernahme-Token der aktuellen Ausführung.
- `heartbeat_at timestamptz`: letztes Lebenszeichen des Workers.
- `recovery_count integer NOT NULL DEFAULT 0`: aufeinanderfolgende Wiederaufnahmen nach Worker-Verlust; jedes Endergebnis (FINISHED/FAILED) setzt es auf 0 zurück.

Zeitvergleiche nutzen die UTC-Zeit der Pods, damit dieselbe Logik auch in den SQLite-Tests läuft. Das Fenster von 120 s fängt übliche Uhrenabweichungen ab.

```sql
-- Claim (services/ingest/backend/app/workers/tasks.py, process_job)
UPDATE jobs
   SET status = 'RUNNING', claim_token = :token, heartbeat_at = :now, updated_at = :now,
       recovery_count = CASE WHEN status = 'RUNNING' THEN recovery_count + 1 ELSE recovery_count END
 WHERE id = :job_id
   AND (status = 'PENDING'
        OR (status = 'RUNNING' AND coalesce(heartbeat_at, updated_at) < :now - :stale));

-- Heartbeat alle 30 s aus einem Thread im Task, eigene kurze Session
UPDATE jobs SET heartbeat_at = :now, updated_at = updated_at
 WHERE id = :job_id AND claim_token = :token;

-- Vor jedem Endergebnis: Zeile sperren, Token prüfen; fremdes Token = Ergebnis verwerfen, kein Webhook
SELECT claim_token FROM jobs WHERE id = :job_id FOR UPDATE;
```

- `stale` = 120 s (`JOB_STALE_SECONDS`), Heartbeat 30 s (`JOB_HEARTBEAT_SECONDS`). Das ersetzt die festen 2 min und die Startwiederherstellung mit Hard-Limit plus 5 min.
- **Reaper** `reap_stale_running_jobs`: läuft im bestehenden `publication_tick` (alle 30 s, vorhandene NX-Sperre) und beim Worker-Start.
  - Setzt RUNNING-Jobs mit veraltetem Heartbeat auf PENDING, löscht deren Token und sendet sie neu (`FOR UPDATE SKIP LOCKED`, maximal 100 pro Lauf).
  - Nach mehr als `JOB_MAX_RECOVERIES` (3) Verlusten in Folge wird der Job FAILED.
  - PENDING-Jobs sendet der Reaper **nicht** erneut: Manche warten bewusst auf einen manuellen Start (z. B. E-Mail-Uploads in Collections).
- Die Regel „Worker-Verlust → FAILED mit Profilvorschlag“ greift erst ab `recovery_count >= 2` statt beim ersten `redelivered`. Ein normales Herunterskalieren erzeugt damit keine Fehler mehr.
- Die Neustart-Endpunkte, die Jobliste und `/paddle/status` entscheiden über `heartbeat_at` statt über `celery inspect`.
- `restart-pending` arbeitet nach Job-ID statt nach Anzahl.
- Jede Rücksetzung auf PENDING löscht das Token und sperrt damit eine vermeintlich verlorene Ausführung aus.
- OCR läuft außerhalb jeder Transaktion: Owner, Team und Tags laden, `commit()`, OCR ausführen, dann Token prüfen und schreiben.

### E3 Temporäre Dateien

- Worker: pro Task ein `TemporaryDirectory(dir=WORKER_TMP_DIR)` auf einem `emptyDir`. Original per `copy_to_path` hinein; OCR, PDF-Chunks und Mail-Anhänge laufen darin (`TMPDIR=WORKER_TMP_DIR`). Kein Ergebnis-File mehr. Beim Start der Kindprozesse wird `WORKER_TMP_DIR` geleert; das entfernt Reste nach SIGKILL oder OOM.
- API: Starlette puffert Uploads über 1 MiB unter `/tmp`. Dieses Verzeichnis wird ein `emptyDir` mit `sizeLimit`.
- Worker: `sizeLimit` ≥ parallele Tasks pro Pod × 100 MiB × 3 (Original, PDF-Chunks, Anhänge) + 1 GiB Reserve. Bei Concurrency 1: 2 GiB.
- API: `sizeLimit` ≥ gleichzeitige Uploads pro Pod × 100 MiB + 1 GiB Reserve, z. B. 8 × 100 MiB → 2 GiB. Backup-Importe laufen nicht über diesen Pfad (siehe 4.5).

### E4 Queues nach ADR 0001

- `weave.ingest.ocr` für `process_job`.
- `weave.ingest.default` für Confluence-Import, Webhooks, Release-Zustellung, Ticks und Rücknahmen.
- Zwei Worker-Deployments: OCR (schwer, Concurrency 1, skaliert nach Queue-Länge) und I/O (leicht, Concurrency 4–8).
- Damit blockiert eine 30-min-OCR keine Webhooks und keine Freigaben mehr (`docs/adr/0001-queue-topologie.md`).

### E5 Migrationen

- `alembic/env.py` holt per `pg_advisory_lock` eine Sperre; gleichzeitig startende Repliken migrieren nacheinander, die späteren finden das Schema aktuell vor. `runAlembicOnStartup` bleibt deshalb an.
- Ein eigener Helm-Hook-Job entfällt: Hooks laufen beim Install nach den Deployments (wie der vorhandene `db-bootstrap`-Job), die Pods würden bis zum Ende der Migration in einer Crash-Schleife hängen.

### E6 Verbindungsbudget: `max_connections` = 200

Ingest, Knowledge und API liegen auf einem Datenbankserver; das Limit gilt für alle zusammen. Ohne Pooler muss gelten:

```text
Σ Replikas × DB-Prozesse × (pool_size + max_overflow)  +  Reserve  ≤  max_connections
```

**Festlegung: 200.**
- Mit 100 passen nur 2 API- und 4 OCR-Replikas; das ist für das Ziel „maximale Skalierung“ zu knapp.
- 200 verdoppelt den Spielraum, ohne dass ein Pooler nötig wird.
- Über 300 ist ohne Pooler nicht sinnvoll: Jede Verbindung ist ein eigener Serverprozess und kostet auch im Leerlauf Speicher und CPU.

Versteckter Verbraucher: Die Log-Spiegelung der Ingest-Worker hat eine eigene Engine mit 2 + 2 Verbindungen pro Prozess, auch im Celery-Hauptprozess (`services/ingest/backend/app/workers/log_capture.py:52`). Sie wird auf 1 + 0 gesetzt.

| Dienst | DB-Nutzung je Pod | je Pod | Replikas max | Verbindungen max |
|---|---|---|---|---|
| ingest-backend | 1 Uvicorn-Prozess, 4 + 3 | 7 | 4 | 28 |
| api | 1 Uvicorn-Prozess, 3 + 2 | 5 | 3 | 15 |
| knowledge | 1 Uvicorn-Prozess, 3 + 2 | 5 | 2 | 10 |
| retrieval | 1 Uvicorn-Prozess, 3 + 3 | 6 | 4 | 24 |
| ingest-worker-ocr | Hauptprozess: Log 1 · 1 Kindprozess: 2 + 0, Log 1 | 4 | 10 | 40 |
| ingest-worker-io | Hauptprozess: Log 1 · 4 Kindprozesse: je 2 + 0, Log 1 | 13 | 2 | 26 |
| knowledge-worker | Hauptprozess 1 **(A)** · 2 Kindprozesse: je 2 + 0 | 5 | 4 | 20 |
| Reserve | Superuser (3), Migrations-Job, KEDA, Monitoring, Admin | – | – | 15 |
| **Summe** | | | | **178** |

- Die `maxReplicas` der HPA (heute 5–10, `deploy/charts/weave/values.yaml:1091-1149`) werden auf diese Werte gesetzt. Für die OCR-Worker bleiben die bisherigen 10.
- Die restlichen ≈ 20 Verbindungen sind Puffer für Rolling Updates: Zusätzliche Pods (`maxSurge`) halten kurz eigene Verbindungen.
- **Ressourcen des DB-Servers:**
  - grob 5–10 MB RAM pro Verbindung, also bis 2 GiB nur dafür; dazu `shared_buffers` und der Vektorindex.
  - Richtwert: mindestens 4 vCPU und 8 GiB RAM **(A)**.
  - `work_mem` bleibt beim Standard (4 MB), weil der Wert je Sortierung × aktive Verbindungen gilt.
- **Umsetzung je Betriebsart** (eine Änderung erfordert einen Neustart des DB-Servers):
  - `bundled`: Das Chart setzt `-c max_connections=200`; heute gibt es keinen Parameter, es gilt also der Postgres-Standard 100. Außerdem `resources` setzen, die heute leer sind (`values.yaml:944`).
  - `cnpg`: `spec.postgresql.parameters.max_connections`. Das Template rendert diesen Block heute nur zusammen mit `pg_hba` (`templates/postgres-cnpg-cluster.yaml:64-67`).
  - `external`/RDS: in der Parametergruppe des Kunden. Der RDS-Standard hängt vom Instanzspeicher ab (bei 4 GiB etwa 400); dort reicht der vorhandene Wert eventuell schon. Prüfen mit `SHOW max_connections;`.
- Für noch mehr Replikas einen Pooler (6.2) einsetzen, statt weiter zu erhöhen.
- Bei Suchlast wird Retrieval zuerst knapp; eine Lesereplika (6.2) nimmt seine Verbindungen vom Primärserver.

### E7 Optionaler KEDA-Schalter im Helm-Chart

KEDA (Kubernetes Event-driven Autoscaling) ist ein Operator, der Deployments nach externen Messwerten skaliert, statt nur nach CPU. Im Kundencluster ist er derzeit nicht installiert. Das Chart bekommt dafür einen Schalter; ohne KEDA ändert sich nichts.

```yaml
autoscaling:
  ingestWorker:
    enabled: false                        # Autoscaling überhaupt an
    engine: hpa                           # hpa | keda (umgesetzt unter autoscaling.ingestWorker)
    minReplicas: 1
    maxReplicas: 10                       # Verbindungsbudget E6
    targetCPUUtilizationPercentage: 75    # nur engine=hpa
    keda:
      jobsPerReplica: 1                   # = Concurrency des OCR-Workers
      pollingInterval: 30
      scaleDownStabilizationSeconds: 600
```

Template-Regeln (`templates/hpa.yaml`, neu `templates/keda-scaledobject.yaml`):

- **Wahl der Engine:**
  - `engine: hpa` erzeugt die HorizontalPodAutoscaler wie heute.
  - `engine: keda` erzeugt stattdessen `ScaledObject` und `TriggerAuthentication`. KEDA legt seine eigene HPA an; zwei Autoscaler auf einem Deployment würden sich gegenseitig überschreiben.
- **Fehlender Operator:** Fehlt im Cluster die API `keda.sh/v1alpha1` (`.Capabilities.APIVersions.Has`), bricht `helm install/upgrade` mit klarer Meldung ab, statt ein wirkungsloses Objekt anzulegen.
- **Startwert:** `replicas` des Deployments startet wie heute mit `minReplicas`.
- **Zwei Trigger, KEDA nimmt den größeren Wert:**
  - `redis`: wartende Nachrichten in der Liste `weave.ingest.ocr`.
  - `postgresql`: laufende Aufträge, `SELECT count(*) FROM jobs WHERE status = 'RUNNING'`.

  Die Redis-Liste allein enthält nur wartende Nachrichten; ohne den zweiten Trigger würde KEDA Worker abbauen, die gerade OCR machen. PENDING-Jobs zählen bewusst nicht: manche warten auf einen manuellen Start (Uploads in eine Collection) und haben noch keine Nachricht in der Queue. KEDA belegt dabei kurz 1 Verbindung pro Abfrage aus der Reserve in E6.
- **Zugangsdaten:** über `TriggerAuthentication` aus dem vorhandenen Chart-Secret (`POSTGRES_PASSWORD`), also kein Passwort im Manifest. Später eine eigene Lese-Rolle (6.2).
- **Scale-to-zero:** `minReplicas: 0` ist möglich, aber nicht Standard. Ohne persistenten Modell-Cache lädt jeder Kaltstart die Paddle-Modelle neu (`values.yaml:257-271`).
- **Knowledge:** Dasselbe Muster ist später auch für `knowledgeWorker` möglich (`documents` im Status `pending`).

## 4. Umsetzungsschritte

Aufwand: S ≤ 2 Tage, M ≤ 1 Woche (Schätzung). Die Schritte bauen aufeinander auf und können für Neuinstallationen gemeinsam als ein Release ausgeliefert werden; wegen des Wegfalls des Volumes ist das eine neue Chart-Major-Version.

### Schritt 1 – Job-Korrektheit mit mehreren Workern (M) – umgesetzt

| Nr | Arbeitspaket | Dateien |
|---|---|---|
| 1.1 | Migration `0042`: `claim_token`, `heartbeat_at`, `recovery_count` | `models/models.py`, `alembic/versions/` |
| 1.2 | Claim, Heartbeat-Thread und Schreiben mit Token-Prüfung (E2); Webhooks nur nach erfolgreichem Schreiben | `workers/tasks.py` |
| 1.3 | OCR außerhalb der Transaktion | `workers/tasks.py:480-501` |
| 1.4 | Reaper im `publication_tick`; `worker_ready` ruft nur noch einmalig den Reaper auf | `workers/publication_tasks.py`, `workers/tasks.py:143-299` |
| 1.5 | Neustart-Endpunkte und Statusanzeige über `heartbeat_at` statt `inspect` | `api/routes.py:140-159`, `:1816-1870`, `:1932`, `:2511` |
| 1.6 | Advisory-Lock für die Versionskette: `pg_advisory_xact_lock(hashtextextended('job-chain:' \|\| :collection \|\| ':' \|\| :filename, 0))` vor `_find_predecessor_job` | `api/routes.py:991-1019` |
| 1.7 | Advisory-Lock in `alembic/env.py` aller Dienste | `*/alembic/env.py` |

### Schritt 2 – Objektablage, Dateisystem entfernen (M) – umgesetzt

| Nr | Arbeitspaket | Dateien |
|---|---|---|
| 2.1 | Migration `0043` nach E1: Tabellen anlegen, Referenzen anlegen, Blob- und Pfadspalten entfernen | `models/models.py`, `alembic/versions/` |
| 2.2 | `object_store.py` mit Backend `db` | `services/object_store.py` (neu) |
| 2.3 | Schreibpfade; Ablauf beim Upload:<br>1. Datei aus dem Puffer lesen<br>2. Größe und SHA-256 im ersten Durchlauf prüfen<br>3. Advisory-Lock setzen, Duplikat prüfen<br>4. Chunks und Job in **einer** Transaktion schreiben, Commit, Task senden | `services/storage.py`, `api/routes.py:951-1031`, `workers/import_tasks.py:638-665`, `:962`, Mail-Ingest, `api/portal.py:362-368`, `api/benchmarks.py` |
| 2.4 | Lesepfade: Worker per `copy_to_path` ins Task-Verzeichnis (E3); Artefakt- und Mail-Downloads streamen | `workers/tasks.py:66-84`, `api/routes.py` (Artefakt-Endpunkte), `api/portal.py:1336-1370` |
| 2.5 | Temporäre Verzeichnisse im Worker nach E3; keine `.md`-Datei, kein Überschreiben von `upload_path` | `workers/tasks.py:470-531`, `services/paddle_service.py:1721` |
| 2.6 | Plattenzugriffe entfernen: Fallbacks, Löschen von Dateien, Ordner-`mkdir`/`rmtree`, ZIP-Download des Speichers, Orphan-Report, `ensure_storage_dirs`, Mountpoint-Workarounds | `api/routes.py:660-677`, `:783-834`, `:2212-2229`, `:2648-2694`, `:2737-2743`, `:2810`; `services/storage.py:90-229`; `services/publications.py:86-95`; `api/benchmarks.py:482`; `api/auth.py:1766`; `main.py:44`, `:59` |
| 2.7 | `uploads_dir`/`results_dir` durch `worker_tmp_dir` und `object_chunk_bytes` ersetzen | `core/config.py:38-39` |
| 2.8 | Objekt-GC im Reaper-Tick: Objekte älter als 1 h ohne Referenz, 100 pro Lauf | `workers/publication_tasks.py` |
| 2.9 | Backup-Engine: Export und Restore der Speicherdateien entfernen; Blobs kommen über `stored_object_chunks` mit (zusammengesetzter Primärschlüssel prüfen) | `services/backup.py:200-230`, `:582-626` |

Umgesetzt mit Migration `0043_stored_objects` und `app/services/object_store.py`. Abweichungen und Ergänzungen:

- `mail_messages.raw_content` bleibt: Die Mail-API ist stillgelegt, nichts schreibt die Tabelle.
- Entfallen sind außerdem der Speicher-ZIP-Download `GET /api/v1/admin/backup.zip` (samt Button in Admin → Betrieb) und der Report `GET /api/v1/auth/admin/storage/orphaned-files`; die Objekt-GC ersetzt ihn. `contracts/openapi.json` ist angepasst.
- `POST /api/v1/folders` prüft nur noch den Pfad: Ordner entstehen mit dem ersten Upload.
- Benchmark-Varianten teilen sich ein Objekt.
- Das Backup-Archiv enthält keinen Dateibaum mehr; `files_restored` bleibt im Report, ist aber immer 0. Archive liegen unter `BACKUP_DIR` (Umbau in 4.5).
- Scratch-Verzeichnisse heißen `job-<pid>-…`; beim Start eines Celery-Kindprozesses werden Verzeichnisse toter Prozesse entfernt.

### Schritt 3 – Deployment (M) – umgesetzt

| Nr | Arbeitspaket | Dateien |
|---|---|---|
| 3.1 | Helm:<br>• `ingest-storage-pvc.yaml`, den `persistence`-Block und die `shared-storage`-Mounts entfernen<br>• `emptyDir` mit `sizeLimit` für `/tmp` (API) und `WORKER_TMP_DIR` (Worker) | `deploy/charts/weave/templates/ingest-*`, `values.yaml:803-813` |
| 3.2 | Worker:<br>• zwei Deployments (E4)<br>• Strategie `RollingUpdate`, wenn der Modell-Cache kein RWO-PVC ist<br>• `terminationGracePeriodSeconds: 120`, Rest übernimmt der Reaper | `templates/ingest-worker-deployment.yaml`, `values.yaml` |
| 3.3 | Skalierung:<br>• HPA nach CPU als Standard, auch für `tools-mcp`<br>• KEDA-Schalter `autoscaling.ingestWorker.engine: keda` nach E7, Standard `hpa`<br>• Template-Prüfungen: KEDA-API vorhanden, `minReplicas` ≤ `maxReplicas` | `templates/hpa.yaml`, `templates/tools-deployment.yaml:159-162` |
| 3.4 | Migrations-Job als Helm-Hook (E5); `runAlembicOnStartup: false` | `templates/`, `values.yaml:185`, `:331`, `:528` |
| 3.5 | Verbindungsbudget nach E6:<br>• Pool-Werte je Dienst über die vorhandenen Env-Variablen (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`)<br>• Pool der Log-Engine konfigurierbar, Standard 1 + 0<br>• `maxReplicas` nach der Tabelle<br>• Neuer Wert `postgresql.maxConnections` (Standard 200): in den Modi `bundled` und `cnpg` als Server-Parameter gesetzt, im Modus `external` nur für die Prüfung<br>• Template-Prüfung, die abbricht, wenn die Summe dieses Budget überschreitet | `values.yaml`, `weave.yaml`, `templates/_helpers.tpl`, `templates/postgres-bundled.yaml`, `templates/postgres-cnpg-cluster.yaml`, `services/ingest/backend/app/workers/log_capture.py:52` |
| 3.6 | Compose: Volume `storage` entfernen; `container_name` bei den Worker-Diensten entfernen, damit `--scale` funktioniert | `deploy/docker-compose.weave.yml:350`, `:379`, `:403`, `:544`, `:1219` |
| 3.7 | `NOTES.txt` und Betriebsdoku: neue Chart-Major-Version, nur für Neuinstallationen | `templates/NOTES.txt`, `docs/betrieb.md` |

Umgesetzt im Chart (`deploy/charts/weave`), in `deploy/docker-compose.weave.yml` und im Worker-Image. Abweichungen und Ergänzungen:

- 3.4: kein Migrations-Job, siehe E5.
- `Recreate` galt schon vorher nur bei einem RWO-PVC als Modell-Cache; daran ändert sich nichts.
- Queues: `process_job` → `weave.ingest.ocr`, alles andere → `weave.ingest.default` (`app/workers/celery_app.py`). Das Image wählt sie über `CELERY_QUEUES`; ohne Angabe bedient ein Worker beide (Compose).
- Zweiter Pool `ingestWorkerIo` (Concurrency 4) im selben Template wie der OCR-Pool.
- Verbindungen: `<workload>.dbPool` je Dienst, Log-Spiegelung 1 + 0 (`WORKER_LOG_DB_POOL_SIZE`/`WORKER_LOG_DB_MAX_OVERFLOW`), der Celery-Hauptprozess gibt seine Verbindung nach der Startwiederherstellung zurück. `weave.requireDbBudget` bricht das Rendern bei Überschreitung mit einer Aufstellung ab.
- `postgresql.maxConnections` (200) und `postgresql.reservedConnections` (15).
- HPA für `tools-mcp` (nutzbar, seit der MCP-Server mit 4.1 zustandslos läuft).
- Admin-Backup-Archive liegen im Helm-Pfad auf dem flüchtigen `/scratch` des Backends (bis 4.5), in Compose auf dem Volume `backups`.
- Die eigenständigen Compose-Dateien unter `services/ingest/` (Entwicklung) behalten ihr `storage`-Volume; es schadet nicht, wird aber nur noch für Backups genutzt.
- `scripts/check_chart_env.py` kennt die neuen Chart-Variablen; Render-Tests in `scripts/tests/test_chart_render.py`.

### Schritt 4 – Nebenpfade (S–M) – umgesetzt

| Nr | Arbeitspaket | Dateien |
|---|---|---|
| 4.1 | MCP zustandslos: `streamable_http_app(stateless_http=True, json_response=True, …)` | `services/tools/app/mcp_server.py:159` |
| 4.2 | JWKS-Cache mit 10 min TTL und einmaligem Neuladen bei unbekannter `kid`; gemeinsamer `httpx.Client`; Größenlimit für den Cache technischer Identitäten | `services/ingest/backend/app/services/oidc.py:82-93`, `api/mcp_oauth.py:45`, `services/tools/app/services/scope.py:548-668` |
| 4.3 | Ratelimit der Weave-API über Redis, nach dem Muster von Ingest `RedisRateLimiter` | `services/api/backend/app/core/ratelimit.py`, `services/ingest/backend/app/services/security.py:22-60` |
| 4.4 | Paddle-Laufzeiteinstellungen aus Redis in eine Tabelle `runtime_settings`, mit In-Prozess-Cache von höchstens 10 s | `services/paddle_service.py:37`, `:1484-1513` |
| 4.5 | Backup für Konsistenz und Migrationen:<br>• Export liest alle Tabellen in **einer** Transaktion `REPEATABLE READ, READ ONLY`. Heute gilt `READ COMMITTED`, jede Tabelle hat also einen eigenen Stand (`services/backup.py:629-690`). Mit Objekt-Referenzen würde ein Export während laufender Uploads sonst beim Import an Fremdschlüsseln scheitern.<br>• Export als Streaming-Download direkt aus der DB, keine Datei im Pod<br>• Sperre per `pg_advisory_lock` statt `threading.Lock`, Fortschritt in `backup_runs`<br>• Import für Migrationen als Kubernetes-Job mit der vorhandenen CLI (`app/cli.py:69`) und eigenem Volume<br>• UI-Import puffert ins `emptyDir`; dessen `sizeLimit` begrenzt die Archivgröße, also `backup_max_upload_bytes` passend setzen (z. B. 2 GiB statt 10 GiB) | `api/backup.py`, `services/backup.py`, `app/cli.py`, `templates/` |

Abweichungen und Ergänzungen:

- 4.1: `stateless_http=True, json_response=True` in `services/tools/app/mcp_server.py`. Danach darf `autoscaling.toolsMcp` eingeschaltet werden.
- 4.2:
  - JWKS-Cache in `app/services/oidc.py` gilt für Login und MCP-OAuth. Ein Token mit unbekannter `kid` lädt die Schlüssel neu, höchstens einmal pro Minute.
  - Der Cache technischer Identitäten in Tools ist auf 10.000 Einträge begrenzt.
  - Einen gemeinsamen `httpx.Client` gibt es **nicht**: Die Tools-Tests patchen `httpx.post`/`httpx.get` an vielen Stellen, und der Gewinn wäre gering.
- 4.3: Das Ratelimit der Weave-API zählt in **ihrer eigenen Datenbank** (Tabelle `rate_limit_windows`, Migration `0007`) statt in Redis. Die Weave-API hat kein Redis; so kommen weder eine neue Abhängigkeit noch neue Konfiguration dazu.
- 4.4: Tabelle `runtime_settings` (Migration `0044`). Lesend 10 s In-Prozess-Cache, die Einstellungs-Endpunkte lesen frisch.
- 4.5:
  - `POST /api/v1/admin/backup/exports` streamt das Archiv direkt in die Antwort, aus einer Transaktion `REPEATABLE READ, READ ONLY` (auf PostgreSQL). Der Download-Endpunkt entfällt; die Laufliste ist nur noch Verlauf.
  - Ein Import läuft weiter als Thread auf dem Pod, der das Archiv angenommen hat. Status und Bericht stehen in `backup_runs`; der Live-Fortschritt bleibt im Speicher, sichtbar nur, wenn die Abfrage diesen Pod trifft.
  - „Nur ein Lauf gleichzeitig“ gilt über eine Advisory-Sperre für alle Replikas.
  - Läufe, deren Pod gestorben ist, gelten nach `BACKUP_RUN_STALE_SECONDS` (6 h) als abgebrochen.
  - UI-Uploads sind auf 2 GiB begrenzt; größere Archive per CLI im Pod, siehe `docs/betrieb.md` Abschnitt 12.
  - Frontend, Tests und `contracts/openapi.json` sind angepasst.

## 5. Testplan

### 5.1 Zwei Worker

- Integrationstests auf PostgreSQL (z. B. Testcontainers):
  - `process_job` zweimal parallel für denselben Job, `convert` gemockt mit 2 s Laufzeit: genau ein Convert, ein Webhook, Status FINISHED.
  - Veraltete Übernahme: Worker A hält den Claim, `heartbeat_at` wird künstlich gealtert, Worker B übernimmt, A beendet zuletzt. Das Ergebnis von A wird verworfen; heute schlägt dieser Test fehl.
  - Zwei parallele Uploads gleichen Namens ergeben Version 1 und 2, nicht zweimal 2.
- E2E Compose mit zwei Workern (`--scale weave-ingest-worker=2`), ohne Volume, 20 PDFs:
  - Alle Jobs FINISHED.
  - `recovery_count = 0` nach Abschluss; kein Job lief doppelt.
  - Pro Job ein `document.processed`.

### 5.2 Worker-Abbruch

- `kill -9` des Worker-Containers mitten in der OCR: Der Reaper stellt den Job innerhalb von ≤ 150 s neu ein, das Ergebnis ist FINISHED. Nach dem Neustart ist `WORKER_TMP_DIR` leer.
- Nur den Kindprozess beenden (`pkill -9 -f ForkPoolWorker`): Der Job bleibt nicht auf RUNNING hängen.
- SIGTERM beim Herunterskalieren: Der Job wird nicht FAILED, sondern übernommen (`recovery_count = 1` während des zweiten Laufs).
- Unit-Test: Nach Erfolg und nach Fehler ist das Task-Verzeichnis leer.
- Broker-Nachricht nach Ablauf der Visibility-Timeout erneut zugestellt: kein zweiter Lauf, weil der Claim fehlschlägt.

### 5.3 Wechselnde MCP-Replikas

- Zwei `tools-mcp` hinter nginx im Round-Robin ohne Affinität. Ablauf `initialize` → `tools/list` → `search` × 50.
  - Heute ist 404 „Session not found“ zu erwarten, im zustandslosen Modus 0 Fehler.
  - Das Ganze mit `MCP-Protocol-Version` 2025-06-18, 2025-11-25 und 2026-07-28.
- Grant entziehen zwischen zwei Aufrufen auf verschiedenen Replikas: Der Entzug greift sofort; bei technischen Identitäten spätestens nach Ablauf der Cache-TTL.
- JWKS-Abrufe beim IdP pro 100 Aufrufe: höchstens 1.

### 5.4 Objektablage

- Upload mit 100 MiB: RSS der API steigt um weniger als 32 MiB; Download streamt mit konstantem Speicher.
- SHA-256 und Größe des gespeicherten Objekts stimmen mit dem Original überein; eine Bearbeitung der Freigabe kopiert keine Bytes.
- GC: Ein referenziertes Objekt wird nie gelöscht; ein nicht referenziertes nach 1 h.
- Keine Datei unter `/tmp` oder `WORKER_TMP_DIR` überlebt einen Request oder Task.
- Backup-Konsistenz: Export während 20 laufender Uploads und OCR-Jobs, dann Import in eine leere DB. Kein Fremdschlüsselfehler, die Zeilenzahlen entsprechen dem Manifest, Objekt-Prüfsummen stimmen.
- Migration per CLI-Job (Compose-Installation → neue Kubernetes-Installation): Dokumente, Freigaben und Downloads sind danach vollständig.
- Verbindungen: Alle Dienste laufen unter Last auf `maxReplicas`; `SELECT count(*) FROM pg_stat_activity` bleibt ≤ 185.
- Helm-Template-Test: Kein Pod mountet ein PVC außer Postgres, Redis und den Modell-Caches.

### 5.5 Autoscaling-Schalter (Helm)

- Standardwerte: `helm template` erzeugt eine HPA und kein `ScaledObject`.
- `engine: keda` ohne KEDA-API: Rendering bricht mit verständlicher Meldung ab.
- `engine: keda` mit `--api-versions keda.sh/v1alpha1`: `ScaledObject` und `TriggerAuthentication` werden erzeugt, keine HPA; das Passwort kommt aus dem Secret und steht nicht im Manifest.
- `maxReplicas` über dem Verbindungsbudget: Rendering bricht ab.
- Optional, sobald ein Testcluster mit KEDA existiert (z. B. kind plus KEDA-Helm-Chart): 10 Jobs einstellen → hochskalieren auf `maxReplicas`. Während der Läufe kein Herunterskalieren; nach dem Abarbeiten zurück auf `minReplicas`.

## 6. Später

### 6.1 S3

Dank der Spalte `backend` ist **keine Migration nötig**: Bestehende Objekte bleiben mit `backend='db'` lesbar, neue Objekte gehen nach dem Umschalten nach S3. Ein Umzugsjob ist optional.

- Backend `s3` in `object_store.py` (boto3, Multipart-Upload; Zugriffsrechte über IRSA, Verschlüsselung SSE-KMS), Schalter `OBJECT_STORE_BACKEND=s3`.
- Downloads über Presigned URLs mit kurzer TTL oder weiter gestreamt durch die API (entscheiden).
- GC löscht auch S3-Objekte; Bucket-Versionierung und Lifecycle.
- Backup: DB-PITR und S3-Versionierung zum selben Zeitpunkt; Restore-Test mit beiden.

### 6.2 Datenbank-Optimierung (Merkposten)

- Pooler (PgBouncer, CNPG-`Pooler` oder RDS Proxy) im Transaktionsmodus; psycopg 3 mit `prepare_threshold=None`.
- Timeouts pro Rolle: `idle_in_transaction_session_timeout`, `statement_timeout`, `lock_timeout`.
- Indizes auf `jobs`:
  - Status (partiell auf `PENDING`/`RUNNING`)
  - `created_at`
  - Vorgängersuche
  - `collection_id` als echte Spalte statt JSON-Pfad
  - `processing_info` als `JSONB`.
- Tabellenpflege: `fillfactor` und Autovacuum für `jobs`; Aufbewahrung für Logs und Webhooks; Celery `task_ignore_result`.
- Suche: `hnsw.iterative_scan`/`ef_search`, pgvector-Tag pinnen, Lesereplika für Retrieval.
- Knowledge-Indexierung per Lease statt `FOR UPDATE` während der Embeddings; Celery-Limits für Knowledge.
- Ingest und Knowledge auf getrennten Instanzen; PITR, Restore-Tests, Monitoring.

## 7. Offene Entscheidungen

1. Bei externer DB (RDS) prüfen, ob die Parametergruppe des Kunden `max_connections` ≥ 200 erlaubt (`SHOW max_connections;`) und ob der DB-Server dafür genug RAM hat (Richtwert E6).
2. Deduplizierung über Uploads hinweg (gleicher SHA-256): zunächst nur bei internen Kopien.
