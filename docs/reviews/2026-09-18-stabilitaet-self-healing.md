# Verfügbarkeit, Stabilität und Self-Healing · 18.09.2026

Stand: `e14b822`. Prüfung des aktuellen Quellcodes und der mitgelieferten
Deployment-Konfiguration; zwei günstige GPT-5.6-Luna-Agenten für getrennte
Bereiche, anschließend Gegenprüfung der Befunde. AGENTS.md wurde berücksichtigt.
Keine Anwendungskorrekturen oder Änderungen an laufenden Diensten vorgenommen.

## Bewertung

| Bereich | Ergebnis |
|---|---|
| Verfügbarkeit | Neustartmechanismen vorhanden; Standarddeployment bietet keine Hochverfügbarkeit. Abhängigkeiten und Workerfortschritt werden nur teilweise durch Probes abgedeckt. |
| Stabilität | Transaktionen, Sperren, begrenzte Wiederholungen und OCR-Laufzeitgrenzen vorhanden. Mehrere Fehlerpfade können dennoch Doppelverarbeitung oder dauerhaft liegen gebliebene Arbeit verursachen. |
| Self-Healing | Teilweise implementiert, aber nicht durchgängig: Die Wiederanlaufkette selbst kann enden; nicht jeder verlorene Auftrag wird aus dauerhaftem Zustand wiederhergestellt. |
| Beobachtbarkeit | Indexierungsdiagnostik verbessert. Ein neuer Prüfsummenfehler kann jedoch einen korrekt indexierten Stand als abweichend darstellen. |

**Fazit:** Ein erfolgreicher Normalbetrieb und bestandene Unit-Tests belegen
noch keinen verlässlichen Wiederanlauf nach Abhängigkeitsausfällen. Für einen
beaufsichtigten Einzelinstanzbetrieb sind wesentliche Grundlagen vorhanden;
ein unbeaufsichtigter, hochverfügbarer Betrieb ist mit diesem Check nicht
nachgewiesen. Keine gemessene Uptime, Ausfallrate, Wiederherstellungsdauer oder
Lastgrenze liegt vor. Die tatsächliche Produktionskonfiguration wurde nicht
ausgelesen; die folgenden Betriebsrisiken beziehen sich auf Repository-Defaults.

## Konkrete Befunde

P1: wichtig für korrekte Verarbeitung bzw. Wiederherstellung. P2: nachrangige
Betriebsverbesserung. „Isoliert reproduziert“ bezeichnet ausgeführte Teile der
Originalfunktionen mit synthetischen Abhängigkeiten, keinen Live-Systemtest.

### SH-01 · P1 · Abgelaufene Scheduler-Sperre beendet die Recovery-Kette

**Beleg:** [publication_tasks.py](../../services/ingest/backend/app/workers/publication_tasks.py),
Zeilen 166–179 und 242–255;
[Startimpuls](../../services/ingest/backend/app/workers/tasks.py), Zeilen 253–258.
Dasselbe Muster findet sich im
[Collection-Sync](../../services/knowledge/backend/app/workers/collection_sync_tasks.py).

Der periodische Auftrag trägt seinen Redis-Sperrtoken zur nächsten Ausführung
weiter. Ist die Sperre inzwischen abgelaufen oder verloren, liefert die
Erneuerung `None`. Der Auftrag kehrt zurück, ohne einen neuen Tick einzuplanen.
Ein Startimpuls wird beim Workerstart gesendet; ein unabhängiger periodischer
Startimpuls ist in diesem Ablauf nicht vorhanden.

**Auslöser:** Queue-Verzögerung länger als die Sperrfrist, verlorener Redis-Zustand
oder längere Unterbrechung. Auch ein endgültig fehlgeschlagenes `send_task`
kann die selbst fortgesetzte Kette abbrechen.

**Auswirkung:** Die automatische Nachlieferung gespeicherter Freigaben und
Entzüge kann ausbleiben, obwohl die Worker weiterlaufen. Beim entsprechenden
Sync-Muster kann der regelmäßige Abgleich der Wissensbereiche stehen bleiben.

**Prüfung:** Originalfunktionen für Sperrerneuerung und `publication_tick`
isoliert mit fehlender Sperre ausgeführt: weder Reconcile noch Folgeauftrag.

**Abhilfe / Abnahme:** Einen unabhängigen Wiederanlauf oder eine sichere
Neuwahl nach verwaister Sperre vorsehen. Nach Redis-Unterbrechung und einer
Queue-Verzögerung über die TTL hinaus müssen fällige Einträge ohne manuellen
Workerneustart abgearbeitet werden; parallele Instanzen dürfen keine
unkontrollierten Tick-Ketten erzeugen.

### SH-02 · P1 · Workerstart setzt noch aktive OCR-Aufträge zurück

**Beleg:** [tasks.py](../../services/ingest/backend/app/workers/tasks.py),
Zeilen 135–220, insbesondere 164–189; normale Auftragsübernahme ab Zeile 275.

Die Startup-Recovery liest sämtliche OCR-Aufträge mit Status `RUNNING` und
setzt sie auf `PENDING`. Anders als bei Importläufen fehlt hier die Prüfung
auf einen veralteten Stand bzw. einen ausgefallenen Besitzer. Startet eine
weitere Workerinstanz während einer laufenden Verarbeitung, wird diese
ebenfalls neu eingeplant.

**Auswirkung:** Ein zweiter Worker kann den zurückgesetzten Auftrag regulär
übernehmen, während der ursprüngliche Worker weiterarbeitet. Die atomare
`PENDING`-Übernahme verhindert diesen Fall nicht, weil die Recovery den Status
zuvor zurückgesetzt hat. Doppelte OCR-Arbeit und konkurrierende Ergebniswrites
sind möglich. Relevant bei Skalierung und rollenden Neustarts.

**Prüfung:** Original-Recovery-Schleife isoliert ausgeführt: ein aktiver
Auftrag wird zurückgesetzt und zur erneuten Einplanung vorgemerkt. Keine
Mehrprozess-Reproduktion mit echter OCR durchgeführt.

**Abhilfe / Abnahme:** Verwaiste Aufträge anhand belastbarer Besitz-/Lease-
Informationen erkennen; Ergebnisse gegen überholte Ausführungen absichern.
Während Worker A einen Auftrag verarbeitet Worker B starten: A darf nicht
dupliziert werden. Nach echtem Ausfall von A muss der Auftrag wieder anlaufen.

### ST-01 · P1 · Bildlinks verursachen falschen Indexierungsstatus

**Beleg:** [portal.py](../../services/ingest/backend/app/api/portal.py),
Zeilen 814–834;
[portal_indexing.py](../../services/ingest/backend/app/api/portal_indexing.py),
Zeilen 37 und 54–62;
[Knowledge-Status](../../services/knowledge/backend/app/api/indexing.py),
Zeilen 84–89.

Bei der Freigabe werden relative `artifacts/...`-Bildlinks in absolute
Freigabe-URLs umgeschrieben. Das Event enthält korrekt die Prüfsumme des
umgeschriebenen Inhalts. `DocumentRelease.markdown_sha256` behält hingegen
die Prüfsumme vor der Umschreibung für den bisherigen Vergleichsvertrag.
Status- und Diagnoseabfrage verwenden weiterhin diesen alten Wert.

**Auswirkung:** Sobald die Umschreibung den Inhalt verändert, findet Knowledge
einen anderen Hash. Die normale Statusabfrage meldet `mismatch`, die Diagnose
kann `not_received_or_mismatch` melden, selbst wenn die Indexierung erfolgreich
war. Dieser Befund belegt eine falsche Anzeige, nicht das Ausbleiben der
Indexierung selbst.

**Prüfung:** Originalfunktion zur Linkumschreibung und Original-Vergleichsschleife
der Statusabfrage isoliert ausgeführt: unterschiedliche Hashes und `mismatch`.

**Abhilfe / Abnahme:** Für die Indexierungsreferenz die Identität der tatsächlich
ausgelieferten Freigabe verwenden; den Vergleich vor Freigabe getrennt halten.
Ein Dokument mit relativem Bildlink freigeben und indexieren: UI und Diagnose
müssen denselben erfolgreichen Stand erkennen. Einzel- und Sammelfreigabe prüfen.

### SH-03 · P1 · Fehlgeschlagene Retry-Einplanung kann Indexierung beenden

**Beleg:** [Knowledge-Worker](../../services/knowledge/backend/app/workers/tasks.py),
Zeilen 193–212 und 285–287.

Nach einem temporären Snapshot-Downloadfehler wird der Versuchszähler gespeichert
und der nächste Auftrag direkt an den Broker gesendet. Scheitert dieses Senden
endgültig, fängt die äußere Fehlerbehandlung die Exception ab und setzt das
Dokument auf `FAILED`. Der untersuchte Worker enthält keine periodische
Nachholung solcher fehlgeschlagenen Indexierungsaufträge.

**Auswirkung:** Eine vorübergehende Kombination aus Download- und Brokerfehler
erholt sich nach Rückkehr der Dienste nicht automatisch. Ein erneuter manueller
Anstoß bleibt erforderlich. Die Freigabe-Outbox ersetzt diese Wiederherstellung
nicht, wenn die ursprüngliche Übergabe bereits erfolgreich war.

**Prüfung:** Codepfad durch Agent und Hauptprüfung nachvollzogen; kein echter
Broker-Ausfall provoziert.

**Abhilfe / Abnahme:** Fällige Indexierungsversuche dauerhaft speichern und
unabhängig vom unmittelbaren Broker-Senden nachholen. Retry-Einplanung gezielt
fehlschlagen lassen; nach Broker-Rückkehr muss das Dokument automatisch
indexiert werden, ohne doppelte Chunks.

### SH-04 · P1 · OCR-Anhänge können nach Importabschluss liegen bleiben

**Beleg:** [import_tasks.py](../../services/ingest/backend/app/workers/import_tasks.py),
Zeilen 465–515; [Startup-Recovery](../../services/ingest/backend/app/workers/tasks.py),
Zeilen 154–164.

Die Finalisierung speichert den Import als `FINISHED` und sendet anschließend
noch ausstehende OCR-Kindaufträge. Scheitert die Einplanung, ist der Import
bereits abgeschlossen. Die Startup-Recovery berücksichtigt ausstehende/laufende
Importläufe und laufende OCR-Jobs, aber nicht diese noch nie gestarteten
`PENDING`-Kinder eines abgeschlossenen Imports.

**Auswirkung:** Ein Import kann als abgeschlossen erscheinen, während einzelne
Anhänge ohne Queue-Auftrag unbearbeitet bleiben.

**Prüfung:** Codebefund; kein Live-Ausfalltest. Besonders relevant für Kinder,
deren ursprüngliche Einplanung bereits verloren ging und die auf die
Nachholung bei Abschluss angewiesen sind.

**Abhilfe / Abnahme:** Dauerhafte Einplanung oder periodische Nachholung
ausstehender Kinder vorsehen. Beim Abschluss die erste oder eine spätere
Broker-Sendung abbrechen; nach Wiederherstellung müssen alle betroffenen
Anhänge verarbeitet werden. Der Importstatus muss verbleibende Arbeit erklären.

## Verfügbarkeit und Betriebsgrenzen

Diese Punkte sind überwiegend dokumentierte Deployment-Entscheidungen, keine
neu behaupteten Programmfehler. Eine abweichende Produktionskonfiguration
kann sie bereits adressieren.

### AV-01 · Einzelinstanzen begrenzen die Ausfallsicherheit

[Compose](../../deploy/docker-compose.weave.yml) betreibt eine PostgreSQL-
und eine Redis-Instanz. Die [Helm-Defaults](../../deploy/charts/weave/values.yaml)
verwenden eine Replik pro regulärem Dienst, deaktiviertes Autoscaling sowie
`postgresql.mode: bundled` und `redis.mode: bundled`.

Ein Prozessneustart bietet Wiederanlauf, aber keinen unterbrechungsfreien
Failover bei Host-, Datenbank- oder Broker-Ausfall. Der Helm-Pfad wird im
Repository ausdrücklich als Evaluierungspfad beschrieben. Für PostgreSQL sind
CNPG mit drei Instanzen oder eine externe Datenbank konfigurierbar; für Redis
ist ein externer Dienst vorgesehen. Daraus folgt noch kein aktivierter HA-Betrieb.

**Empfehlung:** Verfügbarkeitsziel und tolerierten Datenverlust festlegen,
Produktionskonfiguration dagegen prüfen und Failover sowie Restore messen.
Vor mehr OCR-Replikaten SH-02 beheben. Replikazahl allein reicht nicht.

### AV-02 · Brokerverlust kann noch nicht gestartete Arbeit verlieren

Die [Helm-Redis-Defaults](../../deploy/charts/weave/values.yaml), Zeilen 997–1040,
deaktivieren Persistenz. Das
[Redis-Deployment](../../deploy/charts/weave/templates/redis-deployment.yaml)
ist eine Einzelinstanz. Nach Pod-Ersatz ist Brokerzustand ohne persistentes
Volume nicht verlässlich erhalten.

Compose verwendet dagegen ein Redis-Volume und `--save 60 1`; AOF wird im
mitgelieferten Command nicht aktiviert. Ein Volume allein garantiert nicht,
dass bereits alle angenommenen Queue-Nachrichten dauerhaft gespeichert sind.
Ein genauer Verlustzeitraum wurde nicht gemessen.

**Empfehlung:** Dauerhaftigkeit des Brokers und vollständige Rekonstruktion
aus der Datenbank zusammen betrachten. Test: Queue mit synthetischen Aufträgen
füllen, Brokerzustand kontrolliert verlieren, anschließend alle Aufträge
abgleichen. Die bestehenden Recovery-Lücken SH-01/03/04 sind dabei relevant.

### AV-03 · Healthchecks bilden die Nutzbarkeit nur teilweise ab

- Ingests [Health-Endpunkt](../../services/ingest/backend/app/main.py), Zeilen
  55–57, liefert ohne DB-/Brokerprüfung `healthy`. Helm nutzt ihn sowohl für
  Liveness als auch Readiness. Nach späterem Abhängigkeitsausfall kann der
  Dienst daher weiterhin als bereit gelten.
- Knowledge prüft im [Health-Endpunkt](../../services/knowledge/backend/app/main.py)
  die Datenbank mit `SELECT 1`. Helm verwendet denselben Endpunkt für Liveness
  und Readiness. Ein anhaltender DB-Ausfall kann deshalb auch Prozessneustarts
  auslösen, obwohl diese die ausgefallene DB nicht reparieren.
- Der optionale Embedding-Dienst verwendet laut
  [Helm-Defaults](../../deploy/charts/weave/values.yaml), Zeilen 682–701,
  `/health` für beide Probes, obwohl die dortige Beschreibung ausdrücklich
  sagt, dass dieser auch während des Modellladens 200 liefert. Modellbereitschaft
  ist damit nicht bewiesen. Der Dienst ist standardmäßig deaktiviert.
- Der [Knowledge-Worker](../../deploy/charts/weave/templates/knowledge-worker-deployment.yaml)
  hat bewusst keine HTTP-Probes. Dass ein Container läuft, beweist jedoch
  nicht, dass er Queue-Aufträge abarbeitet. Das Fehlen einer HTTP-Probe ist
  allein kein Defekt; ein geeigneter Fortschrittscheck fehlt in diesem Template.

**Empfehlung:** Prozesslebendigkeit und Dienstbereitschaft getrennt prüfen.
Queue-Alter, Zeit seit letztem Fortschritt und nicht abgearbeitete Freigaben
überwachen. DB-/Broker-/Modellausfall gezielt testen: Die Anzeige muss den
Ausfall erkennen und nach Rückkehr wieder bereit werden, ohne Neustartschleife.

### AV-04 · Redis-Healthcheck in Compose prüft die Anmeldung nicht korrekt

[Compose](../../deploy/docker-compose.weave.yml), Zeilen 217–229, startet Redis
mit `--requirepass`, führt aber `redis-cli --raw incr ping` ohne Authentifizierung
aus. Das ist keine erfolgreiche authentifizierte Funktionsprüfung. Wie die
konkret eingesetzte CLI-Version den Authentifizierungsfehler als Exitcode
abbildet, wurde nicht live geprüft; daher wird weder ein bestimmter Container-
Healthstatus noch ein konkreter Startausfall behauptet.

**Empfehlung:** Einen authentifizierten, zustandslosen PING verwenden und
explizit eine PONG-Antwort verlangen. Mit richtigem/falschem Passwort und
nicht erreichbarem Redis prüfen. `depends_on: service_healthy` ist nur so
aussagekräftig wie der zugrunde liegende Check.

## Bereits vorhandene Schutzmaßnahmen

- Ingest-Freigaben werden vor dem Queue-Trigger dauerhaft gespeichert.
  Leasing und Reconcile können verlorene Zustellungen auffangen, solange
  die Tick-Kette läuft.
- Beide Celery-Worker konfigurieren späte Bestätigung und Wiederzustellung
  bei Worker-Verlust. Das schützt nicht automatisch vor jedem abgefangenen
  Fehler oder vor verlorenem Brokerzustand.
- OCR hat Soft-/Hard-Limits und einen daran angepassten Redis-Visibility-
  Timeout; das Worker-Image begrenzt standardmäßig Aufgaben pro Kindprozess.
- Indexierung besitzt Transaktionen, Dokumentsperren und Schutz gegen
  wiederholte Zustellung nach erfolgreichem Abschluss.
- Snapshot-Downloads und Embedding-Requests besitzen begrenzte Wiederholungen
  und Backoff. Dauerhafte Fehler sollen weiterhin begrenzt bleiben; nicht
  jeder Fehler darf endlos neu gestartet werden.
- Indexierungsdiagnostik überträgt ausgewählte Fehlerkategorien statt voller
  Providerantworten und Secrets. ST-01 beeinträchtigt derzeit die Zuordnung.

## Ausgeführte Prüfungen und Grenzen

| Prüfung | Ergebnis / Aussagegrenze |
|---|---|
| Aktueller Code und relevante Änderungen seit dem früheren Review | Gezielte Prüfung von Recovery, Publikation, Indexierung und Deployment; keine vollständige Repository-Prüfung |
| Isolierte Original-Logik für ST-01 | Prüfsummenabweichung und Status `mismatch` reproduziert |
| Isolierte Original-Logik für SH-01 | Ausbleibende Fortsetzung bei verlorener Sperre reproduziert |
| Isolierte Original-Recovery-Schleife für SH-02 | Aktiver Auftrag wird auf `PENDING` gesetzt und erneut vorgemerkt |
| `test_index_task.py` und `test_indexing_status.py` | **44 bestanden**, Python 3.14.3 / pytest 9.1.0; eigene temporäre Arbeitsdirectory, synthetische Konfiguration und SQLite |
| `test_celery_app.py` und `test_import_tasks.py` | **40 bestanden laut Subagent**, drei Warnungen; kein separat nachvollzogener zweiter Lauf |
| Git-Status nach dem Codecheck | Keine Quellcodeänderungen |

**Nebenwirkung des Subagenten-Testlaufs:** Der Agent meldete, entgegen der
Anweisung zur Isolation im Ingest-Backend-Verzeichnis getestet zu haben.
Dessen Testsetup setzt die relative `test.db` zurück. Somit wurde laut Agent
`services/ingest/backend/test.db` zurückgesetzt; dies war kein isolierter
temporärer Testlauf. Der später von der Hauptprüfung ausgeführte Knowledge-
Testlauf verwendete eine temporäre Datenbank. Die standardmäßig aufgelöste
Python-Umgebung hatte kein pytest; eine weitere vorhandene Runtime wurde
für die 44 nachvollzogenen Tests explizit verwendet.

Nicht ausgeführt: Live-Ausfallinjektion, echter Brokerverlust, PostgreSQL-
Parallelitätstest, OCR-/Modelllasttest, Kubernetes-Failover, Backup-Restore,
Dauertest, Messung von Latenz/Queue-Durchsatz oder Prüfung eines produktiven
Monitorings. SQLite- und isolierte Tests ersetzen diese Nachweise nicht.

Ein zunächst vom Agenten vermuteter Race beim Start einer Collection wurde
nach Gegenprüfung verworfen: Die Claim-Operation des Workers wartet auf die
gesperrten Zeilen und liest die Einstellungen erst anschließend. Dieser
Verdacht wird ausdrücklich nicht als Befund gezählt.

## Empfohlene Reihenfolge

1. **ST-01 beheben**, damit Indexierungsstatus und Diagnose wieder verlässlich sind.
2. **SH-01 und SH-02 beheben**, bevor unbeaufsichtigter Betrieb oder zusätzliche
   OCR-Worker als Stabilitätsmaßnahme genutzt werden.
3. **SH-03 und SH-04 schließen:** Indexierungs- und Anhangsaufträge müssen auch
   nach fehlgeschlagener Queue-Einplanung aus dauerhaftem Zustand wieder anlaufen.
4. **AV-03/04 korrigieren und AV-01/02 gegen das Betriebsziel prüfen.**
5. Danach in einer isolierten Testumgebung Ausfälle von Worker, Redis,
   Datenbank und Embedding-Dienst auslösen. Erfolg heißt: nachvollziehbarer
   Status, kein unbemerkter Auftragsverlust, keine Doppelverarbeitung und
   Wiederherstellung innerhalb eines vorher vereinbarten Zeitfensters.

## Gegenprüfung und Umsetzung (18.09.2026)

Gegenprüfung mit 14 günstigen Sonnet-Agenten (Widerlegungsversuch je Befund, P1
doppelt; SH-01, SH-02, ST-01, SH-03 isoliert reproduziert, AV-04 live am
laufenden Compose-Stack): **alle neun Befunde bestätigt**, keine sachlichen
Korrekturen, nur Zeilendrift von wenigen Zeilen. Ergänzungen: SH-02 ist schon
bei einem normalen Rolling-Restart mit einer Replika erreichbar (Helm-Default
`RollingUpdate`); SH-01 tritt nur ein, wenn der Lock-Lookup erfolgreich einen
fremden/fehlenden Wert liefert (eine Redis-Exception wird bereits wiederholt);
SH-03 kann schon beim ersten von fünf Versuchen stranden.

| Befund | Status | Umsetzung |
|---|---|---|
| SH-01 | ✅ umgesetzt | Verlorener Lock → frische NX-Übernahme statt Kettenende; Folge-Tick-Enqueue mit 3 Versuchen (Ingest: Publikation, Refresh, Session-Cleanup; Knowledge: Collection-Sync). Rest: dauerhaft nicht erreichbarer Broker endet die Kette weiterhin mit ERROR-Log; Neustart über `worker_ready`. |
| SH-02 | ✅ umgesetzt | Startup-Recovery setzt nur RUNNING-Jobs zurück, deren `updated_at` älter als Hard-Time-Limit + 5 min ist (`process_job` hat keinen Heartbeat). Kein Lease-Feld; theoretischer Rest an der Limit-Grenze. |
| ST-01 | ✅ umgesetzt | Status/Diagnose vergleichen `payload.markdown_sha256` (ausgelieferte Bytes) statt der Concurrency-Prüfsumme. |
| SH-03 | ✅ umgesetzt | Fehlgeschlagenes Retry-Enqueue setzt nicht mehr FAILED; `collection_sync_tick` treibt fällige Retries aus DB-Zustand nach (CAS auf `updated_at`/`index_attempts`, max. 50 je Tick). Rest: ein noch zugestellter alter Retry kann parallel einen Versuch verbrauchen. |
| SH-04 | ✅ umgesetzt | Backstop-Sendung je Kind abgesichert; Startup-Recovery sendet PENDING-Kinder terminaler Importläufe erneut; `pending_attachment_jobs` im Importlauf-Detail. |
| AV-01 | 📝 dokumentiert | HA-Hinweis in deploy/README.md (Deployment-Entscheidung). |
| AV-02 | ✅ umgesetzt | AOF in Compose und (bei aktivierter Persistenz) Helm; Online-Migration bestehender Instanzen dokumentiert. |
| AV-03 | ✅ umgesetzt | `/api/v1/ready` (Ingest), `/ready` (Knowledge, Embeddings) getrennt von Liveness; Helm-Readiness-Pfade; exec-Liveness `celery inspect ping` für den Knowledge-Worker. Reranker nicht angepasst. |
| AV-04 | ✅ umgesetzt | Authentifizierter `PING` mit `PONG`-Pflicht. |

Nicht ausgeführt bleiben die unter „Ausgeführte Prüfungen und Grenzen“ genannten
Live-Ausfalltests. Beim Zusammenlauf mehrerer Ingest-Testdateien in einer
pytest-Sitzung treten schon auf `e14b822` Isolationsfehler auf (16 Fehler bei
`test_portal.py` + `test_api.py`); die Dateien einzeln sind grün.
