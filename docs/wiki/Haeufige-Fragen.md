# Häufige Fragen

Die Fragen, die im Betrieb tatsächlich gestellt werden, nach Zielgruppe
sortiert. Jede Antwort ist kurz und verweist auf die maßgebliche Stelle im
Repository — die steht dort, nicht hier.

Zwei Quellen beantworten fast alles Weitere:
die [Symptomtabelle im Betriebshandbuch](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest)
für Fehlerbilder aus Betreibersicht und
[Stille Fallen](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#6-stille-fallen)
für die Fehlkonfigurationen, bei denen nichts abstürzt und keine Fehlermeldung
erscheint.

---

## Fachanwender

### Mein Dokument ist verarbeitet, aber der Chat findet es nicht

Der häufigste Fall: die **Freigabe fehlt**. „Verarbeitet" heißt nur, dass der
OCR-Auftrag fertig ist — nicht „für die KI freigegeben" und nicht „indexiert".
Erst die ausdrückliche Freigabe erzeugt den unveränderlichen Snapshot, den
Weave-Knowledge indiziert.

Weg dorthin: **Dokumente** (Voreinstellung **Zu prüfen**) → Text lesen →
„Geprüften Stand freigeben". Der vollständige Ablauf steht in
[wissensportal.md, „Ablauf für Fachbereiche"](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#ablauf-für-fachbereiche);
das Konzept dahinter in [[Wissensbereiche-und-Freigaben]].

### Das Dokument steht auf „Freigegeben", aber nicht auf „Für KI verfügbar"

Das sind zwei verschiedene Zustände. Die Kette ist **Freigegeben → Wird
indiziert → Für KI verfügbar**. „Für KI verfügbar" erscheint erst, wenn
Knowledge genau diese Freigabe samt Snapshot-Hash bestätigt und Textabschnitte
und Embeddings vollständig gespeichert sind.

Die Anzeige aktualisiert sich selbst — bei ausstehender Indexierung alle fünf
Sekunden, bei stabilen Zuständen alle 30. Hintergrund-Tabs pausieren und prüfen
beim Öffnen erneut. Die Seite muss also nicht offen bleiben.

Nicht verwechseln mit dem Zustellstatus (`pending`, `sent`, `failed`): der
beschreibt ausschließlich die Zustellung des Webhooks, nie den Abschluss der
Indexierung. Siehe
[wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#wann-ist-der-inhalt-in-der-ki-suche-verfügbar).

### Im Portal steht „Indexstatus nicht verfügbar"

Das ist weder eine erfolgreiche noch eine fehlgeschlagene Indexierung, sondern
eine fehlende Rückmeldung: fehlende Konfiguration, Timeout, HTTP-Fehler oder
eine Bestätigung, die nicht zu dieser Freigabe passt. Kurze Aussetzer
überbrückt das Portal seit v0.7.3 mit dem zuletzt bestätigten Stand samt
Zeitstempel; die Warnung erscheint erst nach zehn Minuten. Eine ältere grüne
Bestätigung bleibt darüber hinaus nicht stehen.

Für Fachanwender heißt das: später erneut nachsehen. Für Betreiber ist es ein
Hinweis auf die Verbindung Ingest → Knowledge — die Zustände im Einzelnen
stehen im [Vertrag zum Indexierungsstatus](https://github.com/bl0rb/Weave/blob/main/contracts/indexing-status.md).

### Warum antwortet der Bot, er habe nichts gefunden, obwohl das Dokument da ist?

Dafür gibt es drei klar unterscheidbare Gründe, und der Trace im Chat benennt
sie:

- `no_collections` — es gibt keine Collection, die dieser Bot nutzen darf
  **und** du lesen darfst. Gesucht wird dann gar nicht erst.
- `no_context` — gesucht wurde, gefunden wurde nichts Verwertbares. Der Bot
  antwortet lieber mit einem festen Text als mit einer unbelegten Antwort.
- `filter_excluded_all` — deine eigene Auswahl in der Seitenleiste hat das
  Ergebnis geleert, nicht fehlende Rechte. Dieser Fall hat einen eigenen
  Antworttext und ist der einzige der drei, den du selbst beheben kannst.

Ist die Freigabe erfolgt und trotzdem kein Treffer da, siehe die zwei Fragen
oben. Die genauen Definitionen stehen im
[Chat-Vertrag](https://github.com/bl0rb/Weave/blob/main/contracts/internal-chat.md),
Abschnitt zu `GuardTrace`; die Einordnung in [[Chat-und-Bots]].

### Warum sehe ich einen Wissensbereich nicht?

Es gibt zwei verschiedene Sichtbarkeiten, und sie werden regelmäßig
verwechselt:

- **Im Portal** entscheiden Eigentümer- und Gruppenrechte am jeweiligen
  Auftrag. Administratoren sehen alle Aufträge.
- **Im Chat** entscheiden die berechtigten Gruppen des Wissensbereichs. Ein
  Wissensbereich ohne Einschränkung ist für alle lesbar; sonst muss deine
  Gruppe ausdrücklich genannt sein. Wer gar keiner Gruppe zugeordnet ist,
  bekommt nur die uneingeschränkten Bereiche zu sehen.

Berechtigte Gruppen pflegt der Eigentümer beim Anlegen oder im Zugriff des
geöffneten Wissensbereichs, ein Administrator nachträglich unter
**Administration → Konten & Zugriffe → Wissensbereiche & Zugriff**. Ohne
Gruppenzuordnung hilft nur die Administration. Details in [[Rechte-und-Sichtbarkeit]] und in
[wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#wissensbereiche-administrieren).

### Ich habe Wissensbereiche in der Seitenleiste ausgewählt und bekomme nichts mehr

Die Auswahl in der Chat-Seitenleiste ist ein **Filter**, kein Rechtebezug: sie
kann die Menge nur einengen, nie erweitern. Nennst du ausschließlich Bereiche,
die du ohnehin nicht lesen darfst oder die dieser Bot nicht nutzt, bleibt
nichts übrig — erkennbar am Grund `filter_excluded_all`. Auswahl aufheben und
erneut fragen.

### Ich kann ein freigegebenes Dokument nicht mehr bearbeiten oder neu verarbeiten

Das ist so gewollt: eine Freigabe ist ein unveränderlicher Snapshot, pro Auftrag
gibt es genau eine. Bearbeiten und erneutes Verarbeiten eines freigegebenen
Auftrags werden mit HTTP 409 abgewiesen. Für geänderten Inhalt ist eine neue
Dokumentversion nötig.

Vor der Freigabe steht dagegen **Erneut verarbeiten** mit einem anderen
Profil offen — auch dann, wenn die Qualitätsprüfung blockiert. Siehe
[wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#ein-dokument-mit-anderem-profil-neu-verarbeiten).

### Wie nehme ich ein freigegebenes Dokument wieder aus der KI heraus?

Nur auf zwei Wegen, beide nachvollziehbar und beide ohne Löschen des
Auftrags (das bleibt bei HTTP 409):

- Seiten, die ein Confluence- oder Website-Sync nicht mehr findet, meldet der
  Lauf; im Laufdetail bestätigt der Eigentümer **Aus Wissen entfernen**.
  „Löschung ausstehend“ heißt dabei noch nicht gelöscht — erst **Aus Wissen
  entfernt** bestätigt, dass Knowledge Text und Textabschnitte entfernt hat.
- Dokumente, deren Auftrag oder Bereich nicht mehr existiert, zieht ein
  Administrator über den Abgleich verwaister Dokumente zurück (siehe unten).

Ein beliebiges einzelnes freigegebenes Dokument aus der Oberfläche
zurückzuziehen, ist weiterhin nicht vorgesehen. Details in
[wissensportal.md, „Confluence synchronisieren und fehlende Seiten entfernen“](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#confluence-synchronisieren-und-fehlende-seiten-entfernen).

### Der erkannte Text ist unbrauchbar — muss ich neu hochladen?

Nein. In der Dokumentprüfung **Erneut verarbeiten** wählen; die Auswahl
bietet dieselben Profile wie der Quellenablauf. Der Neustart ersetzt den
bisherigen Entwurf und erzeugt selbst keine Freigabe — das Ergebnis wird erneut
geprüft und ausdrücklich freigegeben.

Ausnahme Confluence: Seitentext wird direkt importiert und lässt sich nicht
durch ein OCR-Profil neu aufbereiten. Unterstützte Anhänge schon, nach
abgeschlossenem Import.

### Mein Confluence-Import läuft, aber ich kann nicht freigeben

Bei Confluence muss der **gesamte** Import erfolgreich abgeschlossen sein, damit
abschließende Links und Hierarchiemetadaten im Snapshot enthalten sind. Vorher
verweigert die Freigabe. Ebenso verweigert sie ein Quality-Block.

Zwei weitere Punkte, die regelmäßig überraschen: Confluence-Zugangsdaten sind
nur für den jeweiligen Eigentümer nutzbar, und individuelle Seitenrechte aus
Confluence werden nicht automatisch übertragen — die Zugriffsgrenze in Weave
ist der Wissensbereich.

### Ich habe einen zweiten Confluence-Bereich importiert, jetzt wird der erste nicht mehr synchronisiert

Das war bis v0.7.6 so: eine Verbindung hatte genau einen Auto-Sync, der ihren
jüngsten Lauf wiederholte. Seit v0.7.7 ist jeder Import eine eigene
Konfiguration mit Umfang, Ziel-Wissensbereich und Zeitplan, und eine
Verbindung kann beliebig viele davon haben — unter **Verbindungen →
Confluence** mit Schalter, Intervall, letztem Erfolg und nächstem Sync je
Import. Beim Update hat die Migration aus den vorhandenen Läufen je
Kombination eine Konfiguration angelegt; nur die, die der alte Scheduler
wiederholt hätte, ist eingeschaltet. Die übrigen einmal prüfen und bei Bedarf
aktivieren.

### Wie binde ich eine Website oder eine Intranetseite an?

Unter **Verbindungen → Websites** eine Verbindung mit Basisadresse und, falls
nötig, HTTP-Basic- oder Bearer-Zugang anlegen; dann **Quelle hinzufügen →
Website / Intranet** mit Startadresse und Umfang. Formular-Logins, SSO und
rein per JavaScript gerenderte Seiten gehen nicht und sind im Dialog so
gekennzeichnet. Liegt die Adresse im privaten Netz, scheitert der Abruf, bis
ein Administrator an der Verbindung **Internes Ziel freigeben** setzt; das
gilt nur für diesen Host. Für Gesetze und ähnliche Standardquellen lohnt
zuerst ein Blick in **Wissensbereiche → Quellenkatalog**.

### Kann ich ein Meeting-Transkript hochladen?

Ja, als `.vtt`-Export aus Teams, Zoom, Webex oder Google Meet, über
**Dateien hochladen** wie jede andere Datei. Weave entfernt Plattform-Floskeln
und Regieanweisungen, fasst die Beiträge je Sprecher zusammen und rendert
Teilnehmer, Dauer und einen Absatz pro Redebeitrag. Ein Freitextfeld nimmt
Aufräum-Anweisungen entgegen („Entferne den Smalltalk am Anfang“); dafür muss
der zentrale Chat-Provider eingerichtet sein, sonst scheitert der Auftrag mit
einer klaren Meldung.

### Der Chat sagt „Weave-API ist gerade nicht erreichbar"

Das ist der übersetzte Text für jeden Serverfehler des Gateways; die
Chat-Oberfläche zeigt bewusst nie einen rohen Statuscode. Kurz später erneut
versuchen. Bleibt es dabei, ist es ein Betriebsproblem — dann gilt die Frage
„Der Chat antwortet 502" weiter unten.

Ebenso übersetzt: eine abgelaufene Anmeldung, ein abgelehntes Token und ein
Ratelimit. Die vollständige Zuordnung von Statuscode zu Satz steht in
[services/chat/src/lib/errors.ts](https://github.com/bl0rb/Weave/blob/main/services/chat/src/lib/errors.ts).

### Wo ist „API-Zugang“ im Chat hin?

Seit v0.7.14 ist die Selbstbedienung für persönliche API-Tokens standardmäßig
aus; der Eintrag fehlt dann, und `/v1/me/tokens` antwortet 403. Vorher
ausgestellte Tokens bleiben gültig. Wer die Funktion braucht, bittet den
Betreiber um `SELF_SERVICE_API_TOKENS_ENABLED` in Weave-API.

### Kann ich Weave auch ohne Browser fragen?

Ja: die Desktop-App für macOS und Windows sitzt in der Menüleiste
beziehungsweise im Infobereich und antwortet auf ein Tastenkürzel hin mit
Quellen; **Desktop-App einrichten** im Web-Chat richtet sie mit einem Klick
ein. Und VS Code oder Claude Code erreichen die rechtegebundene Suche per MCP
— [[MCP-mit-SSO-einrichten]]. Einordnung in [[Chat-und-Bots]].

### Die Anmeldung im Chat schlägt fehl

Der reguläre Weg ist **Mit Weave anmelden** — wer sich im Wissensportal anmelden
kann, kann auch chatten. Schlägt das fehl, landet man auf der Login-Seite mit
einem von drei Gründen: der Code fehlte, der Code war ungültig (unbekannt,
abgelaufen oder bereits verbraucht), oder das Gateway war nicht erreichbar. Der
zweite Fall löst sich in der Regel dadurch, dass man den Anmeldevorgang neu
startet, statt einen alten Link erneut zu öffnen.

---

## Administrator

Gemeint ist, wer die Ingest-Oberfläche unter `/admin` verwaltet — in den fünf
Bereichen **Konten & Zugriffe**, **Bots & KI**, **Verarbeitung & Quellen**,
**Betrieb & Sicherheit** und **Systemstatus**.

### Wo sind die Teams hin?

Sie heißen seit v0.7.3 **Gruppen** — unter **Administration → Konten &
Zugriffe → Gruppen**. Geändert hat sich nur die Bezeichnung in Portal und
Administration; API-Felder, Verträge und Datenbank sagen weiterhin `team`.

### Ein Nutzer sieht im Chat gar keine Wissensbereiche

Zuerst prüfen, ob er überhaupt einer Gruppe zugeordnet ist: Ein Nutzer ohne
Gruppe bekommt ausschließlich uneingeschränkt lesbare Bereiche. Danach unter
**Administration → Konten & Zugriffe → Wissensbereiche & Zugriff** die
berechtigten Gruppen des Bereichs kontrollieren. Beides ist eine Einstellung
in Ingest, kein Deployment-Problem.

Bleiben die Wissensbereiche für **alle** Nutzer leer, ist es dagegen der
Registry-Sync — siehe die nächste Frage.

### Alle Wissensbereiche sind leer, für jeden Nutzer

Dann scheitert der Registry-Sync von Ingest nach Knowledge, und das hat fast
immer eine Ursache: `WEAVE_KNOWLEDGE_INGEST_API_TOKEN` kommt bei Ingest und
Knowledge nicht mit demselben Wert an — das Knowledge-Log zeigt dann 401.
`python scripts/weave_config.py check` findet genau das; geänderte Werte
gelangen erst durch `up -d` in die Container, nicht durch `restart`.

Der Wert ist seit der Umstellung ein Service-Credential, das vor dem ersten
Start erzeugt wird, kein persönlicher Token mehr; die Reihenfolge steht in
[betrieb.md, Erstinbetriebnahme](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#3-erstinbetriebnahme).
Wer noch einen alten Admin-Token als Service-Secret verwendet, stellt um und
widerruft ihn.

### Ein zentral gepflegter n8n-Bot taucht in der Bot-Liste nicht auf

Entweder ist er deaktiviert, oder seine Webhook-Adresse liegt außerhalb der
erlaubten Basisadressen. Beides ist eine Einstellung, kein Fehler. Seit
v0.7.11 markiert die Bot-Verwaltung einen aktiven Bot, den Runtime nicht
geladen hat, als **Nicht im Chat** und nennt die wahrscheinliche Ursache.
Belegt in der
[Symptomtabelle](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest).

### Ich habe das Chat-Modell umgestellt — muss der Stack neu starten?

Nein. Aktivierte Änderungen unter **Administration → Bots & KI → Chat & LLM**
gelten ab dem
nächsten direkten Chat-Turn für alle Runtime-Replikate. Wichtig ist die
Ausnahme: n8n-Bots verwenden weiterhin das Modell, das im jeweiligen n8n-Flow
konfiguriert ist — die zentrale Einstellung erreicht sie nicht. Begründung in
[ADR-0007](https://github.com/bl0rb/Weave/blob/main/docs/adr/0007-zentrale-chat-provider-konfiguration.md).

### Kann ein Bot ein anderes Modell nutzen als die übrigen?

Ja, seit v0.7.1. Unter **Administration → Bots & KI → Chat & LLM → Weitere
LLM-Endpunkte** einen Endpunkt anlegen und ihn im Bot-Editor als
**LLM-Endpunkt** wählen.
Sollen Nutzer selbst wechseln dürfen, die Endpunkte zusätzlich unter **Im Chat
zur Auswahl anbieten** eintragen; im Chat erscheint dann eine Auswahl
**Sprachmodell**. Einzelheiten: [[Chat-und-Bots]].

### Ein neuer Bot findet alte Dokumente nicht, ein älterer Bot schon

Die Dokumente stammen vermutlich aus der Zeit vor den Wissensbereichen und
tragen keinen Bereich. Seit v0.7.1 schliessen **neue** Bots solche Dokumente
standardmässig aus, bestehende Bots beziehen sie weiter ein. Abhilfe: einmal
`reconcile-collections` laufen lassen, das ordnet sie ihrem Bereich zu
([[Wissensbereiche-und-Freigaben]]). Wer sie ohne Bereich braucht, schaltet im
Bot „Dokumente ohne Wissensbereich einbeziehen“ ein.

### Gelöschte Dokumente tauchen noch in Antworten auf

Dann liegen sie noch im Wissensindex, meist aus der Zeit, bevor Ingest jeden
gelöschten Auftrag selbst zurückgezogen hat, oder von einem gelöschten
Wissensbereich. Seit v0.7.1 findet und entfernt ein Admin-Job solche verwaisten
Dokumente: erst Probelauf, dann Anwenden. Ablauf und Gründe in
[betrieb.md, Abschnitt 3.3](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#33-verwaiste-dokumente-aus-dem-wissensindex-zur%C3%BCckziehen).

### Der Bot soll auch „Was weißt du?“ beantworten können

Dafür ist das Wissensgedächtnis da, seit v0.7.9 und als Opt-in: der Betreiber
setzt `KNOWLEDGE_MEMORY_MODE` in `weave.yaml` auf `extractive` (ohne
Modellbudget) oder `llm`. Danach stellt ein Administrator unter
**Administration → Bots & KI → Wissensgedächtnis** Modell, Wartezeit und
Grenzen ein und sieht dort Abdeckung und Fehler je Bereich. Bots beantworten
Übersichtsfragen dann aus Zusammenfassungen, mit Quellen und nur im Rahmen der
Leserechte. Einordnung in [[Chat-und-Bots]].

### Kann ein Bot bestimmte Themen ablehnen?

Ja, seit v0.7.15: im Bot-Editor **gesperrte Themen** mit Name, Beschreibung
und Schlüsselwörtern anlegen und einen Antworttext hinterlegen. Die Prüfung
läuft im Router vor jeder Suche und vor jedem Modell- oder n8n-Aufruf. Im
selben Editor lassen sich eine Einstiegsfrage, ein Hinweistext neben dem
Eingabefeld und das Ausblenden der Quellenleiste einstellen.

### Wie bekomme ich einen Bot von Test nach Produktion?

Seit v0.7.12 über **Exportieren** im Bot-Editor: eine YAML- oder JSON-Datei
ohne Geheimnisse und ohne Freigaben. Auf der Zielseite **Importieren**, erst
den Probelauf lesen, dann speichern; n8n-Token und Freigaben anschließend im
Editor ergänzen. Unbekannte Wissensbereiche oder LLM-Endpunkte meldet der
Probelauf als Fehler
([betrieb.md, Abschnitt 9.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#91-bots-exportieren-und-importieren)).

### Muss ich für die Indexierung eine Webhook-Verbindung anlegen?

Nein. Die Freigabe wird über Ingests eigenen internen Veröffentlichungskanal
zugestellt; die beiden nötigen Werte rendert `weave.yaml` bereits. Selbst
angelegte Webhooks bedienen fremde Konsumenten und haben mit dem Index nichts
zu tun.

### Wer darf freigeben?

Der Dokumenteigentümer, der Eigentümer des Wissensbereichs oder ein
Administrator. Leserechte allein genügen nicht — auch nicht für **Mit anderem
Profil neu verarbeiten**.

---

## Betreiber

Gemeint ist, wer das Deployment und `deploy/.env` verantwortet. Die vollständige
Symptomtabelle steht in
[betrieb.md, Abschnitt 10](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest);
hier stehen nur die Fälle, bei denen die Einordnung wichtiger ist als der
Tabelleneintrag.

### Der Chat antwortet 502

Der Standardfall ist ein `RUNTIME_API_TOKEN`, das zwischen Weave-API und
Weave-Runtime auseinanderläuft. Ein 502 heißt also fast immer „das Gateway
kommt nicht durch", nicht „der Dienst ist tot".

Zwei benachbarte Fälle mit demselben Statuscode: ein abweichendes
`RETRIEVAL_API_TOKEN` (Retrieval antwortet 401, die Aufrufer melden 502 — es
sieht aus wie ein Ausfall), und eine gesetzte, aber nicht erreichbare
Chat-Konfigurationsquelle, bei der `/v1/bots` scheitert und die Runtime sich
`degraded` meldet. Letzteres reißt die ganze Bot-Liste mit, auch die lokalen
YAML-Bots; das ist Absicht.

Geteilte Werte ohne Klartext vergleichen: die Fingerabdruck-Schleife in
[betrieb.md, Geteilte Werte](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#5-geteilte-werte).

### Ist ein Wert aus `weave.yaml` überhaupt im Container angekommen?

Seit v0.7.16 ohne `docker exec`: **Administration → Betrieb & Sicherheit →
Konfiguration** zeigt jede Einstellung, mit der Ingest gerade läuft, als
„Angepasst“ oder „Standard“, Geheimnisse maskiert, und testet auf Knopfdruck
Datenbank, Redis, S3 und das Sicherungsverzeichnis. Die Seite gilt nur für
Ingest und ändert nichts
([betrieb.md, Abschnitt 11.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#111-laufende-konfiguration-in-der-oberfläche)).

### Ein Website-Import scheitert mit einem Hinweis auf ein internes Ziel

Adressen im privaten Netz sind für den Fetcher gesperrt, derselbe Schutz wie
bei Confluence. Ein Administrator setzt an der Website-Verbindung **Internes
Ziel freigeben**; die Freigabe gilt nur für den Host dieser einen Verbindung,
nicht pauschal.

### Antworten beginnen mit `[fake-llm]`

`LLM_PROVIDER` steht noch auf `fake`. Die Voreinstellung ist absichtlich die
Attrappe — der Stack läuft damit vollständig, nur antwortet jeder Bot mit
Platzhaltertext. Dasselbe Muster gilt für Embeddings: bleibt
`EMBEDDING_PROVIDER` auf `fake`, indiziert der Dienst klaglos weiter, aber mit
bedeutungslosen Vektoren. Umstellung auf echte Provider:
[betrieb.md, Abschnitt 7](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#7-echte-provider).

### Der Bot findet nie etwas, obwohl Dokumente indexiert sind

Erst im Trace nachsehen. `retrieval.candidates: 0` bei vorhandenen Dokumenten
heißt fast immer, dass die Embedding-Einstellungen von Knowledge und Retrieval
nicht zusammenpassen: verglichen werden nur Chunks, deren gespeicherter
Modellname exakt dem konfigurierten entspricht. Das Ergebnis ist eine leere
Liste, kein Fehler — und der Volltextpfad läuft derweil weiter und verschleiert
es.

`guard.reason: no_collections` dagegen heißt, der Nutzer hat keine Gruppe oder
keine freigegebene Collection: eine Rechte- oder Freigabefrage, kein
Konfigurationsfehler. Und wenn Dokumente überhaupt nicht im Index landen, fehlt
meist schlicht die Freigabe im Wissensportal; sonst weicht das
Webhook-Secret ab, was das Knowledge-Log als 401 zeigt.

### Die Anmeldung schlägt mit 403 fehl, die Browserkonsole zeigt einen CORS-Fehler

`CORS_ORIGINS` steuert nicht nur CORS, sondern die Herkunftsprüfung gegen CSRF.
Fehlt die echte Frontend-Adresse, werden alle schreibenden Anfragen abgewiesen,
der Login eingeschlossen. Lokal hängt der Wert an `FRONTEND_PORT`: wer nur den
Port verschiebt, weil 3000 belegt ist, sieht einen Access-Control-Fehler,
obwohl das eigentliche Problem der Port ist. Beide Werte gehören zusammen
gesetzt. `scripts/weave_config.py check` meldet genau diesen Widerspruch.

### `GET /api/v1/documents` bei Knowledge antwortet 503 — ist der Dienst kaputt?

Nein, er ist geschlossen. Diese Lese-Routen geben den indizierten Bestand
heraus, vorbei an der Gruppenprüfung, durch die jede Suche geht. Ohne gesetztes
`KNOWLEDGE_API_TOKEN` antworten sie deshalb 503. Kein Dienst der Plattform ruft
sie auf — sie sind für Betrieb und Fehlersuche da. Setzen, wenn man sie
braucht, sonst leer lassen. Indizierung, Webhook-Eingang und `/health` sind
davon unberührt.

### Die Antwortqualität ist schlechter geworden, aber nichts meldet einen Fehler

Der wahrscheinlichste Kandidat ist der Reranker. Weave-Retrieval fängt jeden
Reranker-Fehler ab und fällt still auf die unrerankte Reihenfolge zurück — gut
für die Verfügbarkeit, schlecht für die Beobachtbarkeit. Ein falscher Token,
ein nie warm werdendes Modell oder ein zu knappes Dokumentlimit zeigen sich
nicht als Fehler, sondern nur als leise schlechtere Reihenfolge.

Konsequenz für die Überwachung: `warm` aus `/health` aktiv beobachten, nicht
nur den HTTP-Status — der ist by design immer 200, auch während des Ladens.
Begründung und Details in
[services/reranker/README.md](https://github.com/bl0rb/Weave/blob/main/services/reranker/README.md).

### Ein Auftrag hing nach einem Pod-Neustart — muss ich ihn neu anstoßen?

Nein. Jeder laufende Auftrag meldet sich regelmäßig in der Datenbank. Bleibt
das Lebenszeichen aus, weil der Worker abgestürzt ist oder beim
Herunterskalieren beendet wurde, stellt Weave den Auftrag nach etwa zwei
Minuten von selbst neu ein. Erst wenn derselbe Auftrag mehr als dreimal
hintereinander verloren geht, steht er auf „Fehlgeschlagen“ — dann lohnt der Blick in die Worker-Logs, denn dann liegt es
meist am Dokument, nicht am Pod.

### Ein Confluence-Import stand nach einem Worker-Neustart still

Seit v0.7.12 nicht mehr: ein Lauf, dessen Worker verschwunden ist, wird beim
nächsten Publication-Tick erneut eingereiht und ab seinem gespeicherten Stand
fortgesetzt; vorübergehende Confluence-Fehler werden mit Backoff wiederholt.
Erst nach mehr als `IMPORT_MAX_RECOVERIES` Wiederaufnahmen (Standard 5) gilt
der Lauf als fehlgeschlagen — dann liegt es an der Quelle, nicht am Pod.

### `helm upgrade` bricht mit einer Aufstellung der Datenbankverbindungen ab

Das ist die Budgetprüfung des Charts, kein Fehler. Die geplanten Repliken
würden zusammen mehr Verbindungen öffnen, als `postgresql.maxConnections`
zulässt. Entweder Repliken bzw. `dbPool` einzelner Dienste senken, oder
`maxConnections` erhöhen (am Datenbankserver etwa 5–10 MB RAM je Verbindung)
bzw. einen Pooler davorsetzen:
[betrieb.md, Abschnitt 13](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#13-skalierung-im-cluster-helm).

### Womit fange ich an, wenn ich nicht weiß, wo es klemmt?

In dieser Reihenfolge:

1. `python scripts/weave_config.py check` — findet fehlende Pflichtwerte,
   auseinanderlaufende geteilte Werte und ein paar konkrete Widersprüche, ohne
   Geheimnisse im Klartext auszugeben.
2. Der [Selbsttest](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest)
   — vier Schritte von der Erreichbarkeit aller Dienste bis zu einer echten
   Antwort samt Trace.
3. Der `trace` aus dem letzten Aufruf. Er sagt, an welcher Station es
   aufgehört hat, und ist der schnellste Weg von „der Chat spinnt" zu einer
   konkreten Variable.

Die Betriebsseite dieses Wikis ordnet die drei Schritte ein: [[Betrieb]].

---

## Wenn nichts davon passt

- Fehlkonfigurationen, die keine Fehlermeldung erzeugen:
  [Stille Fallen](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#6-stille-fallen)
- Wer was lesen darf und warum:
  [services/retrieval/README.md](https://github.com/bl0rb/Weave/blob/main/services/retrieval/README.md)
  und [services/tools/README.md](https://github.com/bl0rb/Weave/blob/main/services/tools/README.md)
- Leeres Ingest-Dashboard unter Windows und Worker-Warnungen bei
  eingeschränktem Egress:
  [services/ingest/README.md, Troubleshooting](https://github.com/bl0rb/Weave/blob/main/services/ingest/README.md#troubleshooting)
- Unbekannte Begriffe:
  [docs/glossar.html](https://github.com/bl0rb/Weave/blob/main/docs/glossar.html)
- Sicherheitsrelevante Funde:
  [SECURITY.md](https://github.com/bl0rb/Weave/blob/main/SECURITY.md)
