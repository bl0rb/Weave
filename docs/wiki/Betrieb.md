# Betrieb

Diese Seite ordnet ein und verweist. Die maßgebliche Fassung ist
[docs/betrieb.md](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md)
im Repository — dort stehen die Variablentabellen, die Kommandos und die
stillen Fehlkonfigurationen. Was hier steht, ist die Reihenfolge, in der man
das liest, und die zwei Dinge, die man vorher wissen muss.

## Woraus der Stack besteht

Sechs Anwendungsdienste, jeder ein eigener Prozess mit eigenem Image:

- **Ingest** — Verarbeitung, Freigabe, Wissensportal. Besitzt Nutzer, Gruppen,
  OIDC-Verbindungen, Wissensbereiche und Bots und ist damit der
  Identitätsanbieter der Plattform.
- **Knowledge** — Indexierung: Chunking, Embeddings, Chunk-Store; optional
  das Wissensgedächtnis (Zusammenfassungen je Dokument und Bereich).
- **Retrieval** — hybride Suche. Einziger Dienst ohne eigene Datenbank; er
  liest Knowledges Datenbank über eine reine `SELECT`-Rolle mit.
- **Runtime** — Bots, LLM-Aufrufe, Delegation an n8n.
- **Weave-API** — Gateway für Tokens, Sitzungen, Gespräche.
- **Tools** — MCP-Dienst und REST-Spiegel für rechtegebundene Suche.
  MCP-Clients wie VS Code können sich dort über das SSO der Organisation
  anmelden (Entra ID oder Keycloak).

Dazu zwei Weboberflächen (Wissensportal und Chat), eine Desktop-App
([bl0rb/WeaveDesktop](https://github.com/bl0rb/WeaveDesktop), eigenes
Repository mit eigenen Installern auf `desktop-v*`-Tags) und zwei **optionale**
CPU-Modelldienste, Embeddings und Reranker. Ohne sie läuft der Stack mit
Attrappen-Providern: er startet, indiziert und antwortet, nur eben mit
bedeutungslosen Vektoren und Platzhaltertext. Das ist die Voreinstellung und
Absicht — ohne Zutun geht kein Modellaufruf an einen externen Dienst, der Stack
lässt sich also bewerten, bevor Daten oder Budget das Haus verlassen.

Alle Container laufen in einem flachen Bridge-Netz. Getrennt
werden die Dienste nicht durch das Netz, sondern durch den Bearer-Token, den
jeder von ihnen verlangt; ein nicht gesetzter Token führt zu `503`, nicht zu
einer offenen Tür. Die Details dazu stehen im Abschnitt
[Network design](https://github.com/bl0rb/Weave#network-design) der
Wurzel-README.

## Die Konfiguration entsteht aus weave.yaml

`deploy/.env` wird **nicht von Hand bearbeitet**. Die Datei wird von
`scripts/weave_config.py` aus
[weave.yaml](https://github.com/bl0rb/Weave/blob/main/weave.yaml) erzeugt
— `render` schreibt sie, `check` prüft eine bestehende. Beide Befehle stehen
mit ihren Optionen in
[Abschnitt 11 von betrieb.md](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#11-deklarative-konfiguration-weaveyaml).

Der Grund ist nicht Bequemlichkeit. Mehrere Werte müssen über Dienstgrenzen
hinweg zeichengleich sein, teils unter unterschiedlichen Variablennamen. Wer
sie von Hand einträgt, erzeugt Abweichungen, die sich später als unerklärtes
401 oder 503 an einer Dienstgrenze zeigen — oder gar nicht, sondern nur als
leere Trefferliste. `render` berechnet jeden geteilten Wert genau einmal und
spiegelt ihn in alle betroffenen Variablen; `check` findet die Abweichung,
bevor sie im Betrieb auffällt.

Geheimnisse stehen nie im Klartext in `weave.yaml`. Ein Eintrag mit
`secret: true` trägt nur den Namen der Umgebungsvariable, aus der `render` den
Wert zur Laufzeit liest. `weave.yaml` ist deshalb committet, `deploy/.env` ist
gitignoriert. Wo die Geheimnisse selbst herkommen — direnv, Passwort-Manager,
CI-Secret-Store — ist offen.

Nach jeder Änderung an einem Geheimnis oder einem Endpunkt: `check` laufen
lassen. Die dahinterliegende Secrets-Strategie beschreibt
[ADR-0003](https://github.com/bl0rb/Weave/blob/main/docs/adr/0003-secrets-strategie.md).

## Zwei Dinge vor dem ersten produktiven Einsatz

Beide betreffen die ausgelieferte Konfiguration, nicht einen Fehler. Beide sind
im Abschnitt
[Network design](https://github.com/bl0rb/Weave#network-design)
der Wurzel-README belegt.

### Alle veröffentlichten Host-Ports binden an 0.0.0.0

Der Compose-Stack veröffentlicht zehn Host-Ports, und keiner der
`ports:`-Einträge trägt eine Bind-Adresse. Auf einem erreichbaren Host sind
damit alle zehn im Netz — auch die fünf, die niemand von außen ansprechen soll:
Knowledge, Retrieval, Runtime, Embeddings und Reranker. Für den Betrieb
gedacht sind nur Wissensportal-Oberfläche, Ingest-API, Chat-Oberfläche, das
Gateway und, falls n8n oder ein MCP-Client außerhalb des Stacks läuft, die
Tools-REST-Schnittstelle. Nur PostgreSQL und Redis haben überhaupt keinen
Host-Port.

Wer den Stack nicht auf einem isolierten Host betreibt, setzt den betroffenen
Einträgen ein `127.0.0.1:` voran oder stellt eine Firewall davor. Dass die
Dienste ihrerseits fail-closed einen Token verlangen, ist die zweite Linie,
nicht die erste.

Dass Ingest-API und Wissensportal **beide** für Nutzer erreichbar sein müssen,
ist kein Versehen: die Portal-Oberfläche proxyt nicht, der Browser ruft die API
direkt auf.

### Es gibt nirgends TLS oder einen Reverse Proxy

Das Verzeichnis `deploy/` enthält keinen Reverse Proxy, kein Ingress und keine
Zertifikatsbehandlung. Jeder veröffentlichte Port und jeder interne Sprung ist
einfaches HTTP. TLS vor dem Stack zu terminieren, ist Aufgabe des Betreibers;
[deploy/README.md](https://github.com/bl0rb/Weave/blob/main/deploy/README.md)
führt das unter „Production Notes“ als einen der Schritte auf.

Praktisch heißt das: so wie ausgeliefert setzt der Stack einen abgeschotteten
Host voraus. Für einen direkt erreichbaren Host fehlen ihm die Bindung an
`127.0.0.1` und die TLS-Terminierung; beides muss der Betreiber ergänzen.

## Mehrere Repliken im Cluster (ab v0.7.0)

Seit v0.7.0 hält kein Dienst mehr Zustand, den nur er kennt. Ingest legt
Originale, Ergebnisse und Bilder in seiner PostgreSQL-Datenbank ab statt auf
einem gemeinsamen Dateivolume; Backend- und Worker-Pods teilen sich keine
Dateien mehr, jeder hat nur ein flüchtiges Arbeitsverzeichnis. Damit lässt sich
jeder Dienst im Helm-Chart auf mehrere Repliken stellen, per HPA oder — für den
OCR-Pool, als Schalter — per KEDA nach Länge der Warteschlange.

Drei Dinge, die man dazu wissen sollte:

- **OCR hat einen eigenen Worker-Pool.** Lange OCR-Aufträge laufen auf der
  Queue `weave.ingest.ocr`; Confluence-Import, Webhooks und Freigaben auf
  `weave.ingest.default` in einem zweiten Pool. Ein Import wartet so nie
  hinter einem Scan. Im Compose-Stack bedient der eine Worker weiter beide
  Queues.
- **Abgebrochene Aufträge kommen von selbst wieder.** Jeder laufende Auftrag
  meldet sich alle 30 Sekunden in der Datenbank. Verschwindet ein Pod —
  Absturz, Herunterskalieren, Knotenwechsel —, stellt Weave den Auftrag nach
  etwa zwei Minuten neu ein. Geht derselbe Auftrag mehr als dreimal
  hintereinander verloren, gilt er als fehlgeschlagen.
- **Das Chart rechnet die Datenbankverbindungen nach.** Repliken mal
  Verbindungen je Pod, summiert über alle Dienste, muss unter
  `postgresql.maxConnections` (Standard 200) bleiben. Sonst bricht schon
  `helm template` mit einer Aufstellung je Dienst ab, statt dass im Betrieb
  später Verbindungen fehlen.

Diese Chart-Version ist für **neue Installationen** gedacht: Dateien, die eine
ältere Version auf ihrem Volume abgelegt hat, werden nicht übernommen.
Einzelheiten und alle Stellschrauben:
[betrieb.md, Abschnitt 13](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#13-skalierung-im-cluster-helm);
die Entscheidungen dahinter und was bewusst auf später verschoben ist (S3,
Datenbankoptimierung):
[docs/plan-skalierung-datenbank.md](https://github.com/bl0rb/Weave/blob/main/docs/plan-skalierung-datenbank.md).

## Was seit v0.7.3 dazugekommen ist

Die Punkte, die den Betrieb betreffen, in der Reihenfolge, in der sie einem
begegnen. Die vollständige Liste je Version steht im
[Changelog](https://github.com/bl0rb/Weave/blob/main/services/ingest/CHANGELOG.md),
die fachliche Seite auf [[Was-ist-neu]].

- **Der Registry-Sync braucht keinen persönlichen Admin-Token mehr.**
  `WEAVE_KNOWLEDGE_INGEST_API_TOKEN` ist ein dediziertes Service-Credential,
  das vor dem ersten Start erzeugt wird; der zyklische Schritt der
  Erstinbetriebnahme ist entfallen, und der erste Administrator lässt sich
  über Bootstrap-Variablen automatisch anlegen
  ([betrieb.md, Abschnitt 3](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#3-erstinbetriebnahme)).
  Alte Admin-Tokens, die noch als Service-Secret dienen, nach der Umstellung
  widerrufen.
- **S3 als Objektspeicher** (v0.7.11): mit `OBJECT_STORAGE_BACKEND=s3` gehen
  neue Originale, Bilder und Markdown in einen S3-kompatiblen Bucket;
  Migration in beide Richtungen läuft im Betrieb, unterbrechbar und
  wiederholbar, über `storage migrate`. Jedes Objekt kennt sein Backend, ein
  gemischter Bestand bleibt lesbar. Optional serverseitige Verschlüsselung
  per Header (v0.7.12). Sicherungen lassen sich seit v0.7.16 direkt in den
  Bucket schreiben und von dort wiederherstellen
  ([Abschnitt 6.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#61-objektspeicher-postgresql-oder-s3),
  [Abschnitt 12](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#12-desaster-recovery)).
- **Laufende Konfiguration in der Oberfläche** (v0.7.16): **Administration →
  Betrieb & Sicherheit → Konfiguration** zeigt, womit Ingest tatsächlich läuft
  — jede Einstellung mit „Angepasst“ oder „Standard“, Geheimnisse maskiert —,
  testet Datenbank, Redis, S3 und Sicherungsverzeichnis und exportiert als
  `.env` oder JSON ohne Klartext-Secrets. Die Seite ändert nichts; die Quelle
  bleibt `weave.yaml`
  ([Abschnitt 11.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#111-laufende-konfiguration-in-der-oberfläche)).
- **Wissensgedächtnis** (v0.7.9, Opt-in): `KNOWLEDGE_MEMORY_MODE` in
  `weave.yaml` auf `extractive` oder `llm`; Standard `off`. Knowledge bekommt
  eine neue Tabelle, und in Produktion braucht die Leserolle von Retrieval
  ein `GRANT SELECT` darauf, sofern die Default-Privilegien neue Tabellen
  nicht schon abdecken
  ([ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md)).
- **Desktop-App** (v0.7.8): in Weave-API `NATIVE_LOGIN_ENABLED` setzen (Standard
  aus), damit die App den Login auf einer lokalen Adresse entgegennehmen darf;
  `CHAT_PUBLIC_URL` für „Im Web öffnen“ und `WEAVE_API_PUBLIC_BASE_URL` für die
  Einrichtung per Klick aus dem Web-Chat. Serveradresse und Tastenkürzel
  lassen sich per MDM/Intune vorgeben.
- **Selbstbedienung für persönliche API-Tokens ist aus** (v0.7.14):
  `SELF_SERVICE_API_TOKENS_ENABLED` in Weave-API schaltet „API-Zugang“ im
  Chat wieder frei. Vorher ausgestellte Tokens bleiben gültig.
- **Confluence-Importe laufen von selbst weiter** (v0.7.12): ein Lauf, dessen
  Worker gestorben ist, wird vom Stale-Run-Reaper bei jedem Publication-Tick
  erneut eingereiht und ab seinem gespeicherten Stand fortgesetzt;
  vorübergehende Confluence-Fehler (429, 5xx, Netz) werden mit Backoff
  wiederholt. `IMPORT_MAX_RECOVERIES` (Standard 5) begrenzt das je Lauf.
- **Website-Quellen** (v0.7.17) laufen über denselben SSRF-geschützten
  Fetcher wie Confluence. Adressen im privaten Netz sind gesperrt, bis ein
  Administrator sie an genau dieser Verbindung freigibt; `robots.txt` wird
  beachtet, die Importgrenzen gelten wie bei Confluence.
- **Bots exportieren und importieren** (v0.7.12): Bot-Dokumente ohne
  Geheimnisse und Freigaben, für Versionierung und Übertragung zwischen
  Umgebungen
  ([Abschnitt 9.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#91-bots-exportieren-und-importieren)).
- **Lizenz:** ab v0.7.9 MIT mit Commons Clause. Nutzen, ändern und selbst
  betreiben, auch innerhalb der Organisation, bleibt frei; verkaufen oder als
  Hosting-, Beratungs- oder Supportleistung anbieten nicht. Versionen bis
  v0.7.8 bleiben unter reiner MIT-Lizenz
  ([LICENSE](https://github.com/bl0rb/Weave/blob/main/LICENSE)).

Migrationen laufen wie immer beim Update automatisch mit. Reihenfolge beim
Ausrollen: zuerst Knowledge, dann Ingest-API, -Worker und -Frontend.

## Nach dem Update auf v0.7.1

Zwei einmalige Aufräumschritte, beide gefahrlos wiederholbar:

- **Altbestand ohne Wissensbereich zuordnen.** Dokumente aus der Zeit vor den
  Wissensbereichen tragen keinen Bereich und damit keine Freigaben; neue Bots
  schliessen sie seit v0.7.1 aus. `python -m app.cli reconcile-collections`
  in Weave-Knowledge (zuerst mit `--dry-run`) ordnet sie dem Bereich ihres
  Auftrags zu
  ([betrieb.md, Abschnitt 3.2](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#32-altbestand-ohne-wissensbereich)).
- **Verwaiste Dokumente zurückziehen.** `POST /api/v1/admin/knowledge/orphans`
  gleicht den Wissensindex mit Ingest ab und meldet Dokumente, deren Auftrag
  oder Bereich nicht mehr existiert; erst Probelauf, dann Anwenden
  ([betrieb.md, Abschnitt 3.3](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#33-verwaiste-dokumente-aus-dem-wissensindex-zur%C3%BCckziehen)).

Die neue Migration `0045_llm_endpoints` läuft wie jede andere beim Update
automatisch mit; dafür ist nichts zu tun.

## Welche Frage steht wo

| Frage | Wo sie beantwortet wird |
|---|---|
| Welche Werte sind Pflicht, welche geteilt? | betrieb.md, Abschnitte [4](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#4-pflichtwerte) und [5](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#5-geteilte-werte) |
| Warum funktioniert etwas, ohne dass ein Fehler erscheint? | betrieb.md, [Abschnitt 6 „Stille Fallen“](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#6-stille-fallen) |
| In welcher Reihenfolge nehme ich den Stack in Betrieb? | betrieb.md, [Abschnitt 3](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#3-erstinbetriebnahme) — alle Geheimnisse entstehen vor dem ersten Start, der erste Administrator optional per Bootstrap-Variablen |
| Ist der Wert aus `weave.yaml` beim Container angekommen? | betrieb.md, [Abschnitt 11.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#111-laufende-konfiguration-in-der-oberfläche) — die laufende Konfiguration in der Administration, mit Verbindungstests |
| PostgreSQL oder S3 als Objektspeicher, und wie wechsle ich? | betrieb.md, [Abschnitt 6.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#61-objektspeicher-postgresql-oder-s3) |
| Wie komme ich von den Attrappen zu echten Modellen? | betrieb.md, [Abschnitt 7](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#7-echte-provider), inklusive Umstellung bei bereits befüllter Datenbank |
| Wie richte ich die Anmeldung ein? | betrieb.md, [Abschnitt 8](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#8-anmeldung-einrichten); Hintergrund in [ADR-0006](https://github.com/bl0rb/Weave/blob/main/docs/adr/0006-foederierte-anmeldung.md) |
| Wie hängen n8n und MCP an den Rechten des Fragenden? | betrieb.md, [Abschnitt 9](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#9-n8n-und-mcp) und [contracts/n8n-flow.md](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md) |
| Wie melde ich VS Code per SSO am MCP-Dienst an? | [docs/integrations/vscode-mcp-sso.md](https://github.com/bl0rb/Weave/blob/main/docs/integrations/vscode-mcp-sso.md) — Entra ID und Keycloak, mit den Einstellungen beim Anbieter |
| Wie sichere ich und stelle wieder her? | betrieb.md, [Abschnitt 12 „Desaster-Recovery“](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#12-desaster-recovery) — die Sicherung kommt als verschlüsseltes Archiv in den Browser oder in einen S3-Bucket |
| Wie übertrage ich Bots in eine andere Umgebung? | betrieb.md, [Abschnitt 9.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#91-bots-exportieren-und-importieren) und [contracts/bot-document.md](https://github.com/bl0rb/Weave/blob/main/contracts/bot-document.md) |
| Wie schalte ich das Wissensgedächtnis ein, und was kostet es? | [ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md); Einstellungen und Kennzahlen unter Administration → Bots & KI → Wissensgedächtnis |
| Was braucht die Desktop-App vom Server? | [README von bl0rb/WeaveDesktop](https://github.com/bl0rb/WeaveDesktop#readme) und der Abschnitt oben |
| Wie betreibe ich mehrere Repliken im Cluster? | betrieb.md, [Abschnitt 13](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#13-skalierung-im-cluster-helm) |
| Läuft die Kette wirklich durch? | betrieb.md, [Abschnitt 10 „Selbsttest“](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest) |
| Welche Verbindungen muss die Firewall zulassen? | Zuerst [Network design](https://github.com/bl0rb/Weave#network-design) in der Wurzel-README — das ist das plattformweite Bild. [docs/firewall-requirements.md](https://github.com/bl0rb/Weave/blob/main/docs/firewall-requirements.md) hat die feinere Verbindungsmatrix, **aber** einen engeren Geltungsbereich: sie stammt aus der Zeit vor dem Monorepo und beschreibt nur den Ingest-Dienst mit seinen eigenen Compose-Dateien, nicht den Plattform-Stack unter `deploy/` |
| Warum ist die Architektur so? | [docs/adr/](https://github.com/bl0rb/Weave/tree/main/docs/adr) — Queue-Topologie (0001), Auth (0002), Secrets (0003), Datenhaltung (0004), das geteilte Read-Model von Retrieval (0005), Ingest als Identitätsanbieter (0006), zentrale Chat- und Bot-Konfiguration (0007), Besitz und Freigaben (0008), Wissensgedächtnis (0009) |
| Was gilt als Sicherheitslücke, und wie melde ich sie? | [SECURITY.md](https://github.com/bl0rb/Weave/blob/main/SECURITY.md) — dort auch das kurz gefasste Sicherheitsmodell und der Geltungsbereich |
| Welches Schema darf sich nicht ändern? | [contracts/](https://github.com/bl0rb/Weave/tree/main/contracts), insbesondere [chunk-store.md](https://github.com/bl0rb/Weave/blob/main/contracts/chunk-store.md) — weil Retrieval direkt auf Knowledges Tabellen liest, ist das Schema ein Vertrag zwischen zwei Diensten |

Für die inhaltliche Seite — was ein Wissensbereich ist, wer was sieht, warum
ein verarbeitetes Dokument noch kein auffindbares ist — siehe
[[Wissensbereiche-und-Freigaben]] und [[Rechte-und-Sichtbarkeit]].
