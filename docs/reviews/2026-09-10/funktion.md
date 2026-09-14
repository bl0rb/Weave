# Funktionsprüfung vom 10.09.2026

Stand: Commit `7333479cbcff5395797fc93de66dc38a6ccb8f60`.
Das [Testprotokoll](testprotokoll.md) trennt ausgeführte Prüfungen von
Schlussfolgerungen aus dem Quellcode. Die folgenden Änderungen sind Vorschläge.

## FUN-01 · P1 · Indexierungs-Worker übernimmt Modellwechsel nicht

**Status (13.09.2026):** Teilweise umgesetzt. Compose- und Helm-Worker
erhalten jetzt `CHAT_CONFIG_BASE_URL`/`CHAT_CONFIG_SERVICE_TOKEN`
([docker-compose.weave.yml](../../../deploy/docker-compose.weave.yml),
[knowledge-worker-deployment.yaml](../../../deploy/charts/weave/templates/knowledge-worker-deployment.yaml)).
Der isolierte Worker-Test und der Reindexierungs-Integrationstest aus der
Abnahme fehlen weiterhin.

**Beleg:** [Compose-Worker](../../../deploy/docker-compose.weave.yml),
Zeilen 474–484; [Helm-Worker](../../../deploy/charts/weave/templates/knowledge-worker-deployment.yaml),
Zeilen 118–136; [Provider-Aktualisierung](../../../services/knowledge/backend/app/services/embeddings.py),
Zeilen 39–55 und 336–348.

Knowledge-HTTP und Retrieval bekommen `CHAT_CONFIG_BASE_URL` und
`CHAT_CONFIG_SERVICE_TOKEN`. Dem Compose-Indexierungs-Worker fehlen beide;
dem gerenderten Helm-Worker fehlt die URL. Ohne vollständige Konfiguration
kehrt `refresh_control_plane()` sofort zurück. Gerade dieser Worker ruft
bei der Indexierung `get_provider()` auf.

**Auswirkung:** Nach einem Modellwechsel in der Administration kann Retrieval
bereits mit dem neuen Provider suchen, während der Worker neue Dokumente mit
den statischen, gegebenenfalls noch auf `fake` stehenden Einstellungen
indexiert. Unterschiedliche Vektorräume verschlechtern die Suche; wechselnde
Dimensionen können zusätzliche Fehler verursachen. Der gemeinsame Compose-
Embedding-Block hält nur die statischen Werte synchron.

**Verifikation:** Compose wurde mit synthetischen Secrets ausgewertet und
das Helm-Chart gerendert. Das Fehlen der Variablen wurde bestätigt; ein
Live-Modellwechsel wurde nicht ausgeführt.

**Vorschlag:** Dieselbe Provider-Konfiguration an HTTP-Dienst und Worker
weitergeben und die benötigten Variablen als gemeinsamen Vertrag testen.
Providerstand/Dimension pro Indexgeneration erfassen. Modellwechsel mit
vollständiger Reindexierung koordinieren; fehlende Konfiguration sichtbar
melden, statt still mit einem anderen Provider weiterzuarbeiten.

**Abnahme:** Beide Deployment-Varianten liefern URL und Token an den Worker.
Ein isolierter Worker-Test mit geänderter Provider-Antwort benutzt tatsächlich
die neue Konfiguration. Ein Integrationstest bestätigt konsistente Index-
und Query-Vektoren nach Reindexierung.

## FUN-02 · P2 · n8n-Bearer-Token wird konfiguriert, aber nicht gesendet

**Status (13.09.2026):** ✅ Umgesetzt. `run_flow()` sendet den konfigurierten
Token als `Authorization: Bearer …`
([n8n_client.py](../../../services/runtime/backend/app/services/n8n_client.py)),
mit/ohne-Token-Regressionstest in
[test_n8n_client.py](../../../services/runtime/backend/tests/test_n8n_client.py).

**Beleg:** [Schema](../../../services/runtime/backend/app/schemas/bot.py),
Zeilen 60–64; [Konfigurationsübernahme](../../../services/runtime/backend/app/services/botconfig.py),
Zeilen 303–308; [HTTP-Aufruf](../../../services/runtime/backend/app/services/n8n_client.py),
Zeilen 219–234.

`auth_token` wird als Secret übernommen, die Webhook-Anfrage enthält aber nur
Content-Type und HMAC-Signatur. Ein isolierter Mock-Aufruf mit gesetztem Token
bestätigte: kein `Authorization`-Header. Ein n8n-Endpunkt mit Bearer-Pflicht
weist deshalb auch korrekt konfigurierte Bots mit 401/403 ab.

**Vorschlag:** Bei nichtleerem Token `Authorization: Bearer …` senden;
SecretStr erst unmittelbar beim Header-Aufbau auslesen. URL-Allowlist, HMAC
und Scope-gebundenes Delegationstoken beibehalten.

**Abnahme:** Mock-Tests mit/ohne Token prüfen die Header; ein abgewiesener
Aufruf zeigt einen verständlichen Fehler und schreibt kein Secret in Logs.

## FUN-03 · P2 · n8n-Streaming-Schalter hat keine Wirkung

**Beleg:** [Streaming-Feld](../../../services/runtime/backend/app/schemas/bot.py),
Zeilen 65–68; [Übernahme](../../../services/runtime/backend/app/services/botconfig.py),
Zeile 306; [Client](../../../services/runtime/backend/app/services/n8n_client.py),
Zeilen 234–250.

`n8n.streaming` wird eingelesen, im ausführenden Code aber nicht ausgewertet.
Der Client benutzt `httpx.post()` und erwartet danach JSON. In einer
isolierten Reproduktion mit `streaming=True` führte eine SSE-Antwort zu
`N8nError: … returned a non-JSON response`.

**Vorschlag:** Den dokumentierten SSE-Vertrag implementieren, einschließlich
Abbruch, Timeouts und Quellenprüfung. Bis dahin den Schalter in der
Administration als nicht verfügbar kennzeichnen oder entsprechende
Konfigurationen kontrolliert ablehnen.

**Abnahme:** Delta-, Quellen-, Done- und Error-Ereignisse werden gemäß
Vertrag verarbeitet. Ein Bot mit Quellenpflicht darf auch beim Streaming
keine ungeprüfte Antwort ausgeben. JSON bleibt für Bots ohne Streaming gültig.

## FUN-04 · P1 · Helm-Erstinstallation mit --wait kann auf sich selbst warten

**Beleg:** [Bootstrap-Job](../../../deploy/charts/weave/templates/db-bootstrap-job.yaml),
Zeile 65; [Postgres-Initialdatenbank](../../../deploy/charts/weave/templates/postgres-bundled.yaml),
Zeilen 131–137; [Knowledge-Entrypoint](../../../services/knowledge/backend/entrypoint.sh),
Zeilen 46–54. API verwendet dieselbe Startreihenfolge.

Der Job legt zusätzliche Datenbanken/Rollen erst als
`post-install,post-upgrade`-Hook an. Auf einem frischen Postgres fehlen
`weave_knowledge` und `weave_api` bis dahin. Deren Pods führen vor dem
HTTP-Start Alembic aus und können nicht bereit werden. Mit `--wait` wartet
Helm auf bereite Ressourcen, bevor es den Post-Install-Hook startet.
Diese Reihenfolge ist in der [Helm-Dokumentation](https://helm.sh/de/docs/v3/topics/charts_hooks/)
festgelegt.

**Status:** Belastbare Ableitung aus gerendertem Chart, Entrypoints und
Helm-Lifecycle; kein frischer Kubernetes-Cluster wurde gestartet.
Vorab angelegte Datenbanken oder ein extern koordinierter Bootstrap können
das Problem vermeiden. Ein negatives Hook-Gewicht ordnet lediglich Hooks
untereinander und löst diese Abhängigkeit nicht.

**Vorschlag:** Bootstrap als normalen, früh gestarteten Job oder getrennten
Installationsschritt koordinieren; Migrationen/Dienste warten auf dessen
Erfolg. Nicht unbesehen auf `pre-install` wechseln: Ein im selben Chart
angelegter Postgres existiert zu diesem Zeitpunkt noch nicht.

**Abnahme:** Frische Installation mit leeren Volumes und `--wait` sowie
anschließendes Upgrade funktionieren in den unterstützten Postgres-Modi.
Vorhandene Daten und Rollen bleiben erhalten; Secret-Rotation wird geprüft.

## FUN-05 · P2 · Recovery verwechselt unbekannte/aktive Jobs mit verwaisten Jobs

**Beleg:** [Aktive Jobs und Recovery](../../../services/ingest/backend/app/api/routes.py),
Zeilen 115–134, 1544–1556 und 1618–1641.

Zwei Ursachen sind sichtbar:

1. Ein Fehler von `inspect.active()` wird zu einer leeren Menge. Damit ist
   „Worker nicht befragbar“ von „kein Job aktiv“ nicht unterscheidbar.
   Bei erreichbarem Broker, aber ausbleibender Worker-Antwort kann die Route
   anschließend einen tatsächlich laufenden Job neu einreihen.
2. `restart_pending_jobs()` reduziert die aktiven IDs auf eine Anzahl und
   verwendet `running_jobs[active_process_jobs:]`. Beispiel: Ein neuerer,
   verwaister Job steht vor einem älteren, tatsächlich aktiven Job. Bei einer
   aktiven Aufgabe wird der verwaiste übersprungen und der aktive neu
   eingereiht. Die sortierte Position beweist keine Worker-Zugehörigkeit.

**Auswirkung:** Doppelte Verarbeitung und konkurrierende Ergebnisschreibvorgänge
sind möglich. Der Einzeljob-Neustart hat eine DB-Sperre; sie ersetzt die
fehlende verlässliche Aussage über den laufenden Worker nicht.

**Zusätzliche Beobachtung:** Einige Tests kontaktieren trotz gemocktem
`process_job.delay` noch Celery-Control. Der vollständige Ingest-Lauf dauerte
383 Sekunden. `inspect(timeout=5)` ist kein belegtes Gesamtlimit für
Broker-Verbindungsaufbau und Wiederholungen.

**Vorschlag:** Jobs per ID abgleichen, unbekannten Workerstatus separat
behandeln und automatische Recovery bei Unsicherheit abbrechen. Für robuste
Recovery DB-Leases/Heartbeats mit atomarem Claim verwenden. Control-Plane-
Abfragen aus häufigen Request-Pfaden entfernen oder kurz cachen.

**Abnahme:** Tests mit aktiven/verwaisten IDs in unterschiedlicher zeitlicher
Reihenfolge; bei Inspect-Fehler oder Timeout kein Dispatch und keine Löschung
von Ergebnissen eines laufenden Jobs. Test-Fixtures mocken auch Control-Aufrufe.

## FUN-06 · P2 · Sonderzeichen in Infrastrukturpasswörtern brechen den Start

**Beleg:** [Datenbank-URL im Chart](../../../deploy/charts/weave/templates/_helpers.tpl),
Zeilen 625–635; [Compose-URL](../../../deploy/docker-compose.weave.yml),
beispielsweise Zeile 475; [Compose-Init-SQL](../../../deploy/postgres-init/01-create-databases.sh),
Zeilen 40–44.

Passwörter werden unkodiert in URLs eingefügt. Die isolierte Auswertung von
`postgresql+psycopg://weave:Audit@Secret/42@db:5432/weave_knowledge` mit
SQLAlchemy ergab falsche Passwort- und Hostwerte. Im Compose-Init-Skript
bricht zusätzlich ein Apostroph im Retrieval-Passwort das SQL-Literal.

**Einordnung:** Die Einschränkungen stehen bereits in Codekommentaren. Dies
ist ein Robustheitsproblem bei Einrichtung/Rotation, kein nachgewiesener
SQL-Injection-Zugang für normale Anwendungsnutzer. Das Helm-Bootstrap-SQL
verwendet bereits korrekt quotierte psql-Variablen.

**Vorschlag:** Verbindungen aus Einzelwerten mit geeignetem URL-Builder
erzeugen; SQL-Literale wie im Helm-Job per psql-Variablen und `format('%L', …)`
behandeln. Redis-URLs und dotenv-Serialisierung in dieselbe Prüfung aufnehmen.

**Abnahme:** Synthetische Passwörter mit `@`, `/`, `%`, `#` und `'` durchlaufen
Rendern, Start und Rotation. Keine Secretwerte werden in Fehlermeldungen
ausgegeben; bestehende Datenbanken bleiben nutzbar.

## FUN-07 · P2 · Chart-Prüfer meldet zwölf nicht eingeordnete Abweichungen

**Status (13.09.2026):** ✅ Umgesetzt. Die zehn Frontend-Ausschlüsse und die
beiden `CHAT_CONFIG_BASE_URL`-Chart-Abweichungen sind jetzt in
[check_chart_env.py](../../../scripts/check_chart_env.py) benannt;
`pytest scripts/tests` läuft grün (50 bestanden) und
`check_chart_env.py` meldet 0 offene Abweichungen über alle 16 Container.

**Beleg:** [Prüfertest](../../../scripts/tests/test_chart_env.py), Zeilen 30–34;
[Abweichungsdefinitionen](../../../scripts/check_chart_env.py), ab Zeile 128.

**Ausgeführt:** `pytest scripts/tests` ergibt **48 bestanden, 1 fehlgeschlagen**.
`test_chart_env_matches_weave_yaml` meldet:

- Zehn Embedding-/Reranking-Variablen fehlen angeblich im Ingest-Frontend:
  `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_BATCH_SIZE`,
  `EMBEDDING_DIMENSION`, `EMBEDDING_MODEL`, `EMBEDDING_PROVIDER`,
  `RERANK_API_KEY`, `RERANK_BASE_URL`, `RERANK_MODEL`, `RERANK_PROVIDER`.
- `CHAT_CONFIG_BASE_URL` wird bei Knowledge und Retrieval vom Chart erzeugt,
  ist im erwarteten Konfigurationsvertrag aber nicht eingeordnet.

Das sind **nicht zwölf nachgewiesene Laufzeitfehler**. Die Modell-Secrets
gehören nicht ins Frontend; hier ist insbesondere der Prüfer veraltet.
Gleichzeitig erkennt er die tatsächlich fehlende Worker-URL aus FUN-01 nicht.

**Vorschlag:** Erwartete Variablen pro Prozess genauer modellieren. Bewusst
ausgeschlossene Backend-Variablen mit Begründung ergänzen, Chart-Overrides
deklarieren und den Worker-Vertrag ausdrücklich prüfen. Den Test nicht durch
Weitergabe von Secrets ans Frontend „reparieren“.

**Abnahme:** Die Skript-Suite ist grün und ein absichtlich entfernter
Worker-Konfigurationseintrag lässt sie zuverlässig scheitern.

## FUN-08 · P2 · Zentrale Plattformänderungen laufen an PR-CI vorbei

**Status (13.09.2026):** ✅ Umgesetzt. Ein eigener `platform`-Pfadfilter und
-Job in [pr-ci.yml](../../../.github/workflows/pr-ci.yml) führt bei
Änderungen an `weave.yaml`, `scripts/`, `deploy/`, `contracts/` oder
Workflow-Dateien `pytest scripts/tests` sowie eine Compose-Validierung mit
Platzhalter-Secrets aus; im Betrieb bestätigt (Lauf nach anfänglichem
CI-Fix grün).

**Beleg:** [CI-Pfadfilter](../../../.github/workflows/pr-ci.yml), Zeilen 62–85.

Alle Filter betrachten nur `services/<dienst>/**`. Änderungen ausschließlich
an `weave.yaml`, `scripts/`, `deploy/`, `contracts/` oder der Workflow-Datei
aktivieren keine Dienstsuite. Außerdem wird `scripts/tests` in keinem Job
ausgeführt. Die vorhandenen Compose-/Helm-Prüfungen prüfen den älteren
Ingest-Deploymentpfad und ersetzen keine Prüfung des zentralen Plattformcharts.

**Vorhandener Schutz:** Der Release-Aufruf erzwingt Diensttests über
`run_all`; dadurch verschwinden die fehlenden zentralen Prüfungen nicht.

**Vorschlag:** Eigenen Plattform-CI-Job für Skripte, zentrales Compose und
Helm einführen; gemeinsame Verträge und relevante Workflowänderungen müssen
die abhängigen Diensttests auslösen. Dependency-Audits auf alle Python-Dienste
einschließlich Worker ausweiten.

**Abnahme:** Eine PR mit ausschließlich zentraler Konfigurationsänderung
führt den Plattform-Job aus. Ein kaputter Chart-Vertrag oder eine ungültige
Compose-Konfiguration blockiert den Merge über den verpflichtenden CI-Status.

## FUN-09 · P3 · Ungültiger Sessionablauf erzeugt langlebiges Chat-Cookie

**Status (13.09.2026):** ✅ Umgesetzt. Ein ungültiges oder abgelaufenes
`expires_at` bricht den Callback jetzt mit `error=invalid_code` ab, statt ein
Cookie zu setzen
([route.ts](../../../services/chat/src/app/api/auth/sso/callback/route.ts)),
mit Regressionstests in
[route.test.ts](../../../services/chat/src/app/api/auth/sso/callback/route.test.ts).

**Beleg:** [SSO-Callback](../../../services/chat/src/app/api/auth/sso/callback/route.ts),
Zeilen 86–90; [Cookie-Erzeugung](../../../services/chat/src/lib/session.ts),
Zeilen 97–116.

Ein ungültiges oder bereits abgelaufenes `expires_at` ergibt `undefined`.
Die Cookie-Funktion interpretiert das als fehlenden Wert und setzt 30 Tage.
Der Gateway prüft den tatsächlichen Sessionablauf weiterhin; Sessionrechte
werden dadurch nicht verlängert. Es entsteht jedoch ein falscher Loginzustand
mit unnötig langlebiger Tokenablage.

**Vorschlag und Abnahme:** Ungültige/abgelaufene Exchange-Antworten ablehnen,
ohne Cookie zu setzen. Tests für ungültiges Datum, Vergangenheit und positive
Restlaufzeit ergänzen; Personal-Token-Defaults nicht für Sessions verwenden.
