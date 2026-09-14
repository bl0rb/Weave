# Testprotokoll · 10.09.2026

Basis: Commit `7333479cbcff5395797fc93de66dc38a6ccb8f60`.
Ergebnisse sind Beobachtungen dieses Audits und kein dauerhafter CI-Status.

## Python-Suiten

Je Dienst wurde ein eigener Python-Prozess mit eigener temporärer
Arbeitsdirectory gestartet. Das ist hier erforderlich: Mehrere `conftest.py`
verwenden `sqlite:///./test.db` und löschen bei der Collection Tabellen.
Ein Lauf im normalen Projektverzeichnis hätte dortige Testdaten verändern
können. Außerdem tragen mehrere Dienste denselben Importnamen `app`.

Verwendet wurden vorhandene dienstspezifische Umgebungen mit Python 3.14.6
und pytest 9.1.1. Die installierten Distributionen wurden gegen alle exakten
Pins der jeweiligen acht `requirements.txt` verglichen: **keine Abweichung**.
Die alte Wurzel-`.venv` mit Python 3.9.6 und ohne pytest wurde nicht verwendet.
Für Ingest ist Python 3.13 in CI vorgesehen; diese zweite Runtime wurde lokal
nicht zusätzlich ausgeführt.

Der ausgeführte Befehlsaufbau war je Dienst:

```sh
# WEAVE_REPO und WEAVE_TEST_PY auf Checkout und passende Dienstumgebung setzen.
# Beispiel für Knowledge; für jeden Dienst eine neue Arbeitsdirectory nutzen.
WEAVE_TEST_CWD="$(mktemp -d)"
cd "$WEAVE_TEST_CWD"
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$WEAVE_REPO/services/knowledge/backend" \
DATABASE_URL="sqlite:///$WEAVE_TEST_CWD/app.db" \
HF_HUB_OFFLINE=1 \
RERANKER_SKIP_MODEL_TESTS=1 \
"$WEAVE_TEST_PY" -m pytest -q \
  "$WEAVE_REPO/services/knowledge/backend/tests" \
  --tb=short -o "cache_dir=$WEAVE_TEST_CWD/pytest_cache" \
  --junitxml="$WEAVE_TEST_CWD/results.xml"
```

Die tatsächlichen Audit-Prozesse erhielten zusätzlich einen synthetischen
`SECRET_KEY` und eine reduzierte Umgebung. Weder bestehende `.env`-Dateien
noch Betriebsdatenbanken wurden für diesen Lauf geladen.

| Suite | Bestanden | Fehlgeschlagen | Übersprungen |
|---|---:|---:|---:|
| Ingest Backend | 929 | 0 | 2 |
| API | 163 | 0 | 0 |
| Knowledge | 240 | 0 | 0 |
| Retrieval | 126 | 0 | 0 |
| Runtime | 324 | 0 | 0 |
| Tools | 65 | 0 | 0 |
| Embeddings | 26 | 0 | 6 |
| Reranker | 21 | 0 | 2 |
| Zentrale Skripte | 48 | 1 | 0 |
| **Python gesamt** | **1.942** | **1** | **10** |

Die Ingest-Suite dauerte etwa 383 Sekunden einschließlich Prozessstart.
Einige Tests mocken den Task-Dispatch, lassen aber Celery-Control-Aufrufe
zum nicht vorhandenen Standard-Broker zu. Der Lauf wurde vollständig beendet;
er wurde nicht nach einem Teilergebnis als erfolgreich gewertet.

**Fehlgeschlagener Test:**
`scripts/tests/test_chart_env.py::test_chart_env_matches_weave_yaml`.
Er meldete zwölf Abweichungen. Die vollständige fachliche Einordnung steht
unter FUN-07 in [funktion.md](funktion.md). Der zweite Chart-Test, der
`values.generated.yaml` gegen die zentrale Quelle vergleicht, bestand.

**Übersprungene Tests:**

- Ingest: `test_detect_box_candidates_needs_image_libs` wegen fehlendem `cv2`;
  `test_spreadsheet_fallback_frontmatter_satisfies_contract_via_real_xlsx`
  wegen fehlendem pandas und XLSX-Writer.
- Embeddings: sechs Tests aus `test_real_model.py`, da
  `RUN_REAL_MODEL_TESTS` nicht gesetzt war. Keine Modellgewichte nachgeladen.
- Reranker: zwei Tests aus `test_rerank_content.py` durch
  `RERANKER_SKIP_MODEL_TESTS=1` bewusst ausgelassen.

Die Python-Ausgaben enthalten Deprecation-Warnungen, besonders zu
Starlette/FastAPI-Konstanten und TestClient. Sie verhinderten die Tests nicht.

## Frontends mit aktuellen Lockfiles

Die zuerst geprüften `node_modules` enthielten tatsächlich Next.js 16.2.11,
obwohl Manifest und Lockfile 16.3.4 vorgaben. Diese vorläufigen Ergebnisse
sind nicht die Grundlage der folgenden Tabelle.

Beide Quellen wurden ohne `node_modules` und `.next` in getrennte temporäre
Kopien übertragen. Dort erfolgte `npm ci` mit Node **26.3.1**. Anschließend
wurde die tatsächlich installierte Next.js-Version **16.3.4** verifiziert.
Die Builds liefen sequenziell. Die Repository-Lockfiles und Quelltexte
wurden dabei nicht geändert.

Ausgeführt je Kopie:

```sh
npm ci
npm test -- --run
npm run lint
npx tsc --noEmit
npm run build
```

| Prüfung | Chat | Ingest-Frontend |
|---|---|---|
| Installation aus Lockfile | Erfolgreich | Erfolgreich |
| Vitest | 109 Tests bestanden | 75 Tests bestanden |
| Lint | 0 Fehler, 0 Warnungen | 0 Fehler, 6 Warnungen |
| TypeScript | Erfolgreich | Erfolgreich |
| Produktionsbuild | Erfolgreich | Erfolgreich |

Die sechs Warnungen betreffen
`@next/next/no-location-assign-relative-destination`. Die erste
sandboxgebundene Build-Ausführung scheiterte an einer Prozess-/Port-Grenze;
die genehmigte Wiederholung außerhalb dieser Grenze war erfolgreich.

**Gesamtsumme einschließlich Frontends:** 2.126 bestanden, 1 fehlgeschlagen,
10 übersprungen. Wiederholungen der Frontend-Prüfung wurden nicht doppelt
gezählt.

## Deployment und Konfiguration

Lokal verfügbar: Helm **4.2.3**, Docker Compose **5.2.0**.
Es wurden Ressourcen gerendert/validiert, keine Container gestartet oder
Kubernetes-Ressourcen angelegt.

```sh
helm lint deploy/charts/weave \
  -f deploy/charts/weave/values.generated.yaml \
  --set secrets.existingSecret=audit-placeholder

helm template audit deploy/charts/weave \
  -f deploy/charts/weave/values.generated.yaml \
  --set secrets.existingSecret=audit-placeholder

docker compose --env-file /private/tmp/weave-audit-2026-09-10/compose.env \
  -f deploy/docker-compose.weave.yml \
  -f deploy/docker-compose.local.yml config --format json

sh -n deploy/postgres-init/01-create-databases.sh
```

Ergebnis: alle vier Prüfungen erfolgreich; Helm meldete lediglich die optionale
Icon-Empfehlung. Die Compose-Umgebung wurde zuvor per `weave_config.render()`
ausschließlich aus synthetischen Secretwerten erzeugt. Die Compose-Auswertung
enthielt 13 Dienste; das opt-in OCR-Profil wurde nicht als laufender Worker
getestet. Die Variablenlisten von Knowledge und seinem Worker wurden getrennt
ausgewertet und bestätigten FUN-01.

Ein erfolgreiches `helm template` belegt keine korrekte Hook-Laufzeitreihenfolge.
Der `--wait`-Befund FUN-04 wurde aus Chart, Entrypoints und offizieller
Helm-Lifecycle-Dokumentation abgeleitet, nicht in einem Cluster reproduziert.

## Dependency-Sicherheitsabgleich

Für beide Frontends wurde gegen das jeweilige eingecheckte Lockfile geprüft:

```sh
npm audit --package-lock-only --json
```

Beide Aufrufe: Exit 0, jeweils null Meldungen in sämtlichen Schweregraden.
Anfängliche DNS-Fehler innerhalb der Netzwerksandbox wurden nicht als
erfolgreicher Scan gewertet. Der genehmigte Online-Wiederholungslauf war
erfolgreich. Auch `npm ci` in den temporären Kopien meldete keine Schwachstellen.

`pip-audit` **2.10.1** wurde in einer eigenen temporären Umgebung installiert.
Je Requirements-Datei:

```sh
pip-audit --disable-pip --no-deps --progress-spinner off \
  -r <requirements.txt> -f json --desc off -o <ergebnis.json>
```

| Datei | Geprüfte Paketeinträge | Gemeldete Schwachstellen | Übersprungene Einträge |
|---|---:|---:|---:|
| `services/ingest/backend/requirements.txt` | 64 | 0 | 0 |
| `services/ingest/backend/requirements-worker.txt` | 95 | 0 | 0 |
| `services/knowledge/backend/requirements.txt` | 51 | 0 | 0 |
| `services/retrieval/backend/requirements.txt` | 33 | 0 | 0 |
| `services/runtime/backend/requirements.txt` | 28 | 0 | 0 |
| `services/api/backend/requirements.txt` | 40 | 0 | 0 |
| `services/tools/requirements.txt` | 45 | 0 | 0 |
| `services/embeddings/requirements.txt` | 38 | 0 | 0 |
| `services/reranker/requirements.txt` | 56 | 0 | 0 |

Alle neun Aufrufe endeten mit Exit 0. Geprüft wurden die eingecheckten
Paket-/Versionspaare ohne Neuauflösung und ohne automatische Fixes.
`--no-deps` ist hier eine Grenze der Aussage: nicht deklarierte bzw. nur zur
Laufzeit nachgeladene Komponenten werden dadurch nicht erfasst.
Systempakete in Containerimages, Modellgewichte und unbekannte
Sicherheitslücken waren nicht Teil dieses Abgleichs.

## Zusätzliche isolierte Reproduktionen

### Upload vor Authentifizierung — SEC-02

Ein `TestClient(app)` schickte ein Multipart-PDF an `/api/v1/upload`, ohne
Anmeldecookie. `settings.max_upload_bytes` war auf 1024 gesetzt. Ein Wrapper
um `starlette.formparsers.SpooledTemporaryFile.write` zählte Datei-Bytes:

```text
unauthenticated upload response: 401
configured application byte limit: 1024
file bytes written by parser before authorization: 2097161
```

Die echte Parser-Implementierung blieb aktiv. Der Inhalt war synthetisch und
lokal; kein entfernter Endpunkt wurde angesprochen und kein Lasttest gefahren.

### n8n-Konfiguration — FUN-02/FUN-03

`run_flow()` wurde mit einem validierten Bot (`auth_token` gesetzt,
`streaming=True`) und gemocktem `httpx.post` ausgeführt:

```text
run_flow JSON success: True
configured bearer present: False
streaming=True with SSE response: N8nError … returned a non-JSON response
```

Damit ist der ignorierte Bearer-Header unabhängig von einem echten n8n-System
belegt. Beim Streaming-Test konnte schon der HTTP-Content-Type nicht
verarbeitet werden; Details einzelner SSE-Ereignisse waren dafür unerheblich.

### Konfiguration — SEC-03/FUN-06

`weave_config.render()` mit ausschließlich synthetischen Secrets erzeugte
eine neue Datei mit Modus 0644. Ein SQLAlchemy-URL-Roundtrip ergab:

```text
synthetisches Passwort abc123:
  Passwort korrekt = True; Host korrekt = True
synthetisches Passwort Audit@Secret/42:
  Passwort korrekt = False; Host korrekt = False
```

Die Apostroph-Behandlung im Compose-Init-SQL und die Recovery-ID-Auswahl
wurden aus dem Code abgeleitet; hierfür wurde keine reale Datenbank bzw.
kein laufender Worker manipuliert.

## Verbleibende Prüflücken

- Kein vollständiger Browser-E2E-Test mit zwei realen SSO-Sessions. Die
  fehlende letzte Browser-Bindung ist im Code und im bestehenden
  Callback-Test sichtbar; der vorgeschlagene Zwei-Browser-Test bleibt offen.
- Keine frische Kubernetes-Installation, kein Docker-Image-Build und kein
  realer Providerwechsel mit anschließender Reindexierung.
- Keine vollständige Postgres-/pgvector-/Redis-Verifikation. SQLite und
  gemockte Dienstaufrufe ersetzen diese Laufzeitbedingungen nicht.
- Keine echte OCR-Ausführung oder Realmodellprüfung; die konkreten Skips
  sind oben aufgeführt.
- Keine unabhängige Prüfung produktiver Proxy-, TLS-, Firewall- oder
  Secret-Store-Einstellungen. Diese können einige Risiken begrenzen.

Temporäre Rohprotokolle, JUnit-Ausgaben und Reproduktionsskripte liegen unter
`/private/tmp/weave-audit-2026-09-10/`; die beiden Frontend-Kopien unter
`/private/tmp/weave-audit-frontend-chat-locked/` und
`/private/tmp/weave-audit-frontend-ingest-locked/`. Diese Pfade sind lokale
Arbeitsartefakte und können vom Betriebssystem bereinigt werden. Das
dauerhafte Ergebnis ist diese Dokumentation.
